# -*- coding: utf-8 -*-
"""一張圖 → 一次判讀(CONTEXT.md「ROI 掃描」「多數票」):讀值整理成一列與要寫出的裁切圖。"""
from __future__ import annotations

from collections import Counter
import logging
from pathlib import Path
from typing import NamedTuple

from matches_csv import DocxClass, ResultStatus, Roi, ScanResult
from permit_patterns import find_permits, permit_id_patterns

from .ocr import FALLBACK_CONFIGS, PERMIT_VOTE_CONFIGS, SCAN_CONFIGS, Ocr, tesseract

logger = logging.getLogger(__name__)

# ─── ROI ──────────────────────────────────────────────────────────────────
ROI_REGIONS = {
    Roi.MOL:           (0.05, 0.04, 0.40, 0.25),
    Roi.PERMIT_UPPER:  (0.40, 0.05, 1.00, 0.55),
    Roi.PERMIT_LOWER:  (0.40, 0.45, 1.00, 0.95),
}


def _collect_permit_votes(image_bytes: bytes, roi: Roi, ocr: Ocr) -> list[tuple[str, float]]:
    """permit 的一半以 PERMIT_VOTE_CONFIGS（6組）逐一掃描，回傳 (值, conf) 清單。"""
    entries: list[tuple[str, float]] = []
    for cfg in PERMIT_VOTE_CONFIGS:
        text, conf = ocr(image_bytes, cfg, ROI_REGIONS[roi])
        id_, _, _, _ = find_permits(text, permit_id_patterns(roi))
        if id_:
            entries.append((id_, conf))
    return entries


class Vote(NamedTuple):
    """多數票的結果。沒有贏家時 value 為空、conf 為 0,且為假值。"""
    value: str      # 贏家;不到 min_count 票為 ""
    conf: float     # 贏家各票的平均信心(四捨五入到 0.1)
    count: int      # 贏家得票數
    total: int      # 總票數(有讀到值的設定數)

    def __bool__(self) -> bool:
        return bool(self.value)


def majority_vote(entries: list[tuple[str, float]], min_count: int = 2) -> Vote:
    """(值, 信心) 清單 → 多數票。同票數時先被讀到的值贏(Counter 的順序)。

    挑贏家與算「得票平均信心」只在這裡;以前三個呼叫端各寫一次平均(#17)。
    """
    if entries:
        best, count = Counter(v for v, _ in entries).most_common(1)[0]
        if count >= min_count:
            confs = [c for v, c in entries if v == best]
            return Vote(best, round(sum(confs) / len(confs), 1), count, len(entries))
    return Vote("", 0.0, 0, len(entries))


def _collect_mol_votes(image_bytes: bytes, ocr: Ocr) -> list[tuple[str, float]]:
    """mol ROI 以 PERMIT_VOTE_CONFIGS（6組）逐一掃描，回傳 (值, conf) 清單。"""
    entries: list[tuple[str, float]] = []
    for cfg in PERMIT_VOTE_CONFIGS:
        text, conf = ocr(image_bytes, cfg, ROI_REGIONS[Roi.MOL])
        _, _, mol, _ = find_permits(text)
        if mol:
            entries.append((mol, conf))
    return entries


class Crop(NamedTuple):
    """一張要寫出的裁切圖:掃描只決定要寫什麼,寫檔由 run_scan 做(#18)。"""
    path: str       # 相對於 matches.csv 所在資料夾(這一批的輸出目錄),posix
    roi: tuple      # 從轉正後的原圖裁哪一塊(相對比例)


class Reading(NamedTuple):
    """一張圖的判讀:讀值整理(ScanResult)+ 要寫出的裁切圖。

    result 是完成的一列:mol_crop / permit_crop 已是裁切圖寫出後的相對路徑;
    crops 還沒寫出,run_scan 照著寫,不回填(#61)。
    """
    result: ScanResult
    crops: list[Crop]


def _crop_stem(docx_name: str, img_name: str) -> str:
    return f"{Path(docx_name).stem}_{Path(img_name).stem}"


def _fill_crop(result: ScanResult, roi: Roi, stem: str) -> Crop:
    """這個 ROI 的裁切圖:路徑當下就填進列(mol 或 permit 那格),回傳要寫出的裁切圖。

    路徑相對 matches.csv 所在資料夾、一律 posix:整個資料夾搬到哪都能用,
    CSV 也不因平台而異(ADR-0013 補充)。
    """
    path = f"{roi.field}_crops/{stem}.png"
    if roi.is_permit:
        result.permit_crop = path
    else:
        result.mol_crop = path
    return Crop(path, ROI_REGIONS[roi])


def _apply_mol_vote(result: ScanResult, image_bytes: bytes, stem: str,
                    ocr: Ocr) -> Crop | None:
    """mol ROI 先跑 PERMIT_VOTE_CONFIGS 多數票;有贏家就寫進 result,回傳要寫出的裁切圖。

    small 與 large 的 mol ROI 共用(以前是兩段複製貼上,#17)。
    """
    vote = majority_vote(_collect_mol_votes(image_bytes, ocr))
    if not vote:
        return None
    result.mol           = vote.value
    result.mol_conf      = vote.conf
    result.mol_from_vote = True
    result.hit_roi       = Roi.MOL
    logger.info(
        f"  ★ mol 多數票：{vote.value}  avg_conf={vote.conf}"
        f"  ({vote.count}/{vote.total} 票)"
    )
    return _fill_crop(result, Roi.MOL, stem)


def _find_in_roi(text: str, roi: Roi) -> tuple[str, int]:
    """這個 ROI 那一欄的值與命中的 pattern 層:permit 的一半只找許可證號,mol 只找 mol。"""
    if roi.is_permit:
        id_, id_layer, _, _ = find_permits(text, permit_id_patterns(roi), find_mol=False)
        return id_, id_layer
    _, _, mol, mol_layer = find_permits(text, find_id=False)
    return mol, mol_layer


def _field_filled(result: ScanResult, roi: Roi) -> bool:
    return bool(result.id if roi.is_permit else result.mol)


def _record_first_hit(result: ScanResult, roi: Roi, value: str, layer: int,
                      conf: float, config_name: str) -> None:
    if roi.is_permit:
        result.id, result.id_layer, result.id_conf = value, layer, conf
    else:
        result.mol, result.mol_layer, result.mol_conf = value, layer, conf
    # 命中設定與 ROI 記整張圖第一個 first-hit;mol 多數票只標了 ROI、沒有設定,
    # 所以之後 permit 的 first-hit 會把命中 ROI 改成 permit 那一半(沿用舊行為)
    if not result.hit_config:
        result.hit_config, result.hit_roi = config_name, roi


def _first_hit(result: ScanResult, roi: Roi, image_bytes: bytes, stem: str,
               ocr: Ocr) -> Crop | None:
    """SCAN_CONFIGS → FALLBACK_CONFIGS 依序讀,第一個讀到值的設定就採用。"""
    for cfg in SCAN_CONFIGS + FALLBACK_CONFIGS:
        text, conf = ocr(image_bytes, cfg, ROI_REGIONS[roi])
        logger.debug(f"  ✗ {roi}/{cfg['name']}  conf={conf:.0f}  text={text[:400]!r}")
        value, layer = _find_in_roi(text, roi)
        if value:
            _record_first_hit(result, roi, value, layer, conf, cfg["name"])
            logger.debug(f"  ★ {roi}/{cfg['name']}  conf={conf}  value={value!r}")
            return _fill_crop(result, roi, stem)
    return None


def _read_roi(result: ScanResult, roi: Roi, image_bytes: bytes, stem: str,
              ocr: Ocr) -> Crop | None:
    """讀一個 ROI,讀到就填進 result 那一欄,回傳要寫出的裁切圖;沒讀到回傳 None。

    每欄只收第一個命中的 ROI:那一欄已有值(permit 上半已命中)就不再讀。
    mol 先跑多數票,沒有贏家才退回 first-hit;small 與 large 共用(以前是兩段複製,#17)。
    """
    if _field_filled(result, roi):
        return None
    if roi is Roi.MOL:
        vote_crop = _apply_mol_vote(result, image_bytes, stem, ocr)
        if vote_crop:
            return vote_crop
    return _first_hit(result, roi, image_bytes, stem, ocr)


def scan_image_mol_only(docx_name: str, img_name: str, image_bytes: bytes,
                        ocr: Ocr = tesseract) -> Reading:
    """small docx 專用：只掃 mol ROI(多數票 → first-hit)。

    不碰硬碟:命中的裁切圖放在回傳的 crops,由 run_scan 寫出(#18)。
    """
    result = empty_result(docx_name, img_name, DocxClass.SMALL)
    crop = _read_roi(result, Roi.MOL, image_bytes, _crop_stem(docx_name, img_name), ocr)
    if crop:
        return Reading(result, [crop])
    result.note = "mol 無值"
    logger.info(f"  ⚠ {img_name}: mol 無值")
    return Reading(result, [])


def _rois_to_scan(roi_filter: str) -> list[Roi]:
    """依序要讀的 ROI;roi_filter(--roi)非空就只讀那一個。"""
    return [roi for roi in ROI_REGIONS if not roi_filter or roi == roi_filter]


def scan_image_large(docx_name: str, img_name: str, image_bytes: bytes,
                     roi_filter: str = "", ocr: Ocr = tesseract) -> Reading | None:
    """large docx 專用：掃 mol + permit。全無命中回傳 None。

    先逐 ROI 找第一次命中(每欄只收第一個 ROI),再以 permit 多數票補缺的那欄、互證兩欄。
    不碰硬碟:命中的裁切圖放在回傳的 crops,由 run_scan 寫出(#18)。

    roi_filter：若非空字串，第一次命中只讀指定的 ROI(對應 CLI --roi;不是 mol 就連 mol 多數票
    也不跑);缺欄時的 permit 上下半多數票照跑。參數注入而非模組全域,行為只依輸入決定。
    """
    stem = _crop_stem(docx_name, img_name)
    result = empty_result(docx_name, img_name, DocxClass.LARGE)
    crops = [crop for roi in _rois_to_scan(roi_filter)
             if (crop := _read_roi(result, roi, image_bytes, stem, ocr))]
    if not crops:
        return None
    _confirm_by_permit_vote(result, image_bytes, ocr)
    return Reading(result, crops)


def _confirm_by_permit_vote(result: ScanResult, image_bytes: bytes, ocr: Ocr) -> None:
    """兩欄互證。兩欄都讀到就直接比;缺一欄才跑 permit 上下半的多數票。

    兩欄都有值卻不同時不在這裡處理 —— 衝突留給送審決策(mol≠permit 衝突)。
    """
    if result.mol and result.id:
        _mark_cross_match_if_agree(result, result.id)
        return
    entries = (_collect_permit_votes(image_bytes, Roi.PERMIT_UPPER, ocr)
               + _collect_permit_votes(image_bytes, Roi.PERMIT_LOWER, ocr))
    vote = majority_vote(entries)
    if not vote:
        if entries:
            _mark_permit_partial(result, entries)
        return
    if result.mol:
        _mark_cross_match_if_agree(result, vote.value)
    else:
        _adopt_permit_vote(result, vote)


def _mark_permit_partial(result: ScanResult, entries: list[tuple[str, float]]) -> None:
    """有讀到值但湊不出多數:取信心最高的當候選值,標 PERMIT_PARTIAL(送反查救援)。"""
    value, conf = max(entries, key=lambda e: e[1])
    result.id, result.id_conf = value, float(conf)
    result.status = ResultStatus.PERMIT_PARTIAL
    logger.info(
        f"  ⚠ permit 有部分命中({len(entries)}筆)但無多數"
        f"  → 候選值={value!r} conf={conf:.0f}"
        f"  all={[v for v, _ in entries]}"
    )


def _mark_cross_match_if_agree(result: ScanResult, permit_value: str) -> None:
    """mol 與 permit 讀到同一個號碼 → 交叉比對吻合。"""
    if result.mol == permit_value:
        result.cross_match = True
        logger.info(f"  ✓ 交叉比對吻合：{result.mol}")


def _adopt_permit_vote(result: ScanResult, vote: Vote) -> None:
    """mol 沒讀到:permit 多數票就是這張圖的許可證讀值(取代 first-hit 那一筆)。"""
    result.id, result.id_layer, result.id_conf = vote.value, 0, vote.conf
    result.id_from_vote = True
    logger.info(f"  → mol 無值，permit 多數票：{vote.value}  avg_conf={vote.conf}")


def empty_result(docx_name: str, img_name: str,
                 docx_class: DocxClass | None) -> ScanResult:
    """一張圖還沒讀到任何東西時的 ScanResult(也用來造佔位列)。

    package 內共用(docx 的佔位列也用它),不經 __init__ 對外轉發。
    """
    return ScanResult(
        source_docx=docx_name,
        image_name=img_name,
        docx_class=docx_class,
    )
