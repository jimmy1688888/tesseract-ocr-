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


def _collect_permit_votes(image_bytes: bytes, roi_coords: tuple, ocr: Ocr,
                          permit_id_list=None) -> list[tuple[str, float]]:
    entries: list[tuple[str, float]] = []
    for cfg in PERMIT_VOTE_CONFIGS:
        text, conf = ocr(image_bytes, cfg, roi_coords)
        id_, _, _, _ = find_permits(text, permit_id_list)
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


def scan_image_mol_only(docx_name: str, img_name: str, image_bytes: bytes,
                        ocr: Ocr = tesseract) -> Reading:
    """small docx 專用：只掃 mol ROI。

    第一階段先跑 PERMIT_VOTE_CONFIGS 多數票；有多數票直接採用並標記 mol_from_vote=True。
    無多數票時退回 first-hit（SCAN_CONFIGS → FALLBACK_CONFIGS）。

    不碰硬碟:命中的裁切圖放在回傳的 crops,由 run_scan 寫出(#18)。
    """
    stem = _crop_stem(docx_name, img_name)
    result = _empty_result(docx_name, img_name, DocxClass.SMALL)
    roi_coords = ROI_REGIONS[Roi.MOL]

    # ── 第一階段：多數票（PERMIT_VOTE_CONFIGS 6組）────────────────────
    vote_crop = _apply_mol_vote(result, image_bytes, stem, ocr)
    if vote_crop:
        return Reading(result, [vote_crop])

    # ── 第二階段：first-hit（SCAN_CONFIGS → FALLBACK_CONFIGS）────────
    crops: list[Crop] = []
    for cfg in SCAN_CONFIGS + FALLBACK_CONFIGS:
        text, conf = ocr(image_bytes, cfg, roi_coords)
        logger.debug(f"  ✗ mol/{cfg['name']}  conf={conf:.0f}  text={text[:400]!r}")
        _, _, mol, mol_layer = find_permits(text)
        if not mol:
            continue
        result.mol       = mol
        result.mol_layer = mol_layer
        result.mol_conf  = conf
        result.hit_config = cfg["name"]
        result.hit_roi   = Roi.MOL
        crops.append(_fill_crop(result, Roi.MOL, stem))
        logger.debug(f"  ★ mol/{cfg['name']}  conf={conf}  mol={mol!r}")
        return Reading(result, crops)

    result.note = "mol 無值"
    logger.info(f"  ⚠ {img_name}: mol 無值")
    return Reading(result, crops)


def scan_image_large(docx_name: str, img_name: str, image_bytes: bytes,
                     roi_filter: str = "", ocr: Ocr = tesseract) -> Reading | None:
    """large docx 專用：掃 mol + permit。全無命中回傳 None。

    不碰硬碟:命中的裁切圖放在回傳的 crops,由 run_scan 寫出(#18)。

    roi_filter：若非空字串，只掃指定的 ROI（對應 CLI --roi）。
    過去版本由 module-level ROI_FILTER 全域變數承載，現改為參數注入，
    讓函數行為僅依輸入決定，方便測試與並行化。
    """
    stem = _crop_stem(docx_name, img_name)
    result = _empty_result(docx_name, img_name, DocxClass.LARGE)
    crops: list[Crop] = []
    fields_found: set[str] = set()
    any_hit = False

    for roi_name, roi_coords in ROI_REGIONS.items():
        if roi_filter and roi_name != roi_filter:
            continue
        field = roi_name.field
        if field in fields_found:
            continue

        # ── mol ROI：先嘗試多數票（PERMIT_VOTE_CONFIGS 6組）────────────
        if roi_name is Roi.MOL:
            vote_crop = _apply_mol_vote(result, image_bytes, stem, ocr)
            if vote_crop:
                crops.append(vote_crop)
                any_hit = True
                fields_found.add(field)
                continue   # 跳過 mol ROI 的 first-hit

        for cfg in SCAN_CONFIGS + FALLBACK_CONFIGS:
            text, conf = ocr(image_bytes, cfg, roi_coords)
            logger.debug(
                f"  ✗ {roi_name}/{cfg['name']}  conf={conf:.0f}"
                f"  text={text[:400]!r}"
            )
            is_permit_roi = roi_name.is_permit
            permit_id_list = permit_id_patterns(roi_name) if is_permit_roi else None
            id_, id_layer, mol, mol_layer = find_permits(
                text, permit_id_list,
                find_mol=not is_permit_roi,
                find_id=is_permit_roi,
            )
            if not (id_ or mol):
                continue
            if id_:
                result.id = id_
                result.id_layer = id_layer
                if not result.id_conf:
                    result.id_conf = conf
            if mol:
                result.mol = mol
                result.mol_layer = mol_layer
                if not result.mol_conf:
                    result.mol_conf = conf
            if not result.hit_config:
                result.hit_config = cfg["name"]
                result.hit_roi = roi_name
            any_hit = True
            fields_found.add(field)
            # 對應欄位的 crop（mol 或 permit）,每欄只寫第一個命中的 ROI
            crops.append(_fill_crop(result, roi_name, stem))
            logger.debug(f"  ★ {roi_name}/{cfg['name']}  conf={conf}  id={id_!r} mol={mol!r}")
            break

    if not any_hit:
        return None

    mol_val    = result.mol
    permit_val = result.id
    permit_vote_avg_conf = 0.0

    if not mol_val or not permit_val:
        upper_entries = _collect_permit_votes(image_bytes, ROI_REGIONS[Roi.PERMIT_UPPER], ocr)
        lower_entries = _collect_permit_votes(
            image_bytes, ROI_REGIONS[Roi.PERMIT_LOWER], ocr,
            permit_id_list=permit_id_patterns(Roi.PERMIT_LOWER))
        all_entries = upper_entries + lower_entries
        vote = majority_vote(all_entries)
        permit_vote, permit_vote_avg_conf = vote.value, vote.conf
        if not vote and all_entries:
            best = max(all_entries, key=lambda x: x[1])
            result.id = best[0]
            result.id_conf = float(best[1])
            result.status = ResultStatus.PERMIT_PARTIAL
            logger.info(
                f"  ⚠ permit 有部分命中({len(all_entries)}筆)但無多數"
                f"  → 候選值={best[0]!r} conf={best[1]:.0f}"
                f"  all={[v for v,_ in all_entries]}"
            )
    else:
        permit_vote = permit_val

    if mol_val and permit_vote and mol_val == permit_vote:
        result.cross_match = True
        logger.info(f"  ✓ 交叉比對吻合：{mol_val}")
    elif not mol_val and permit_vote:
        result.id = permit_vote
        result.id_layer = 0
        result.id_conf = permit_vote_avg_conf
        result.id_from_vote = True
        logger.info(f"  → mol 無值，permit 多數票：{permit_vote}  avg_conf={permit_vote_avg_conf}")

    return Reading(result, crops)


def _empty_result(docx_name: str, img_name: str,
                  docx_class: DocxClass | None) -> ScanResult:
    """一張圖還沒讀到任何東西時的 ScanResult(也用來造佔位列)。"""
    return ScanResult(
        source_docx=docx_name,
        image_name=img_name,
        docx_class=docx_class,
    )
