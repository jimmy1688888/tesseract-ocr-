# -*- coding: utf-8 -*-
"""許可證判讀:一張圖的 image bytes → 一次判讀(CONTEXT.md「ROI 掃描」「多數票」「分層掃描」)。

不碰硬碟、不碰網路、不改全域:OCR 由參數注入(預設 tesseract),裁切圖只回傳
「要寫什麼」(Crop),寫出由 pipeline.run_scan 做(#18)。所以多數票、交叉比對、
擴掃決策都能用假 OCR 直接測(#19);以前只能靠 monkeypatch 模組屬性。

  scan_image_large / scan_image_mol_only   一張圖 → Reading(ScanResult, crops)
  read_large_docx                           一份 large docx 的分層掃描(ADR-0005)
  scan_docx                                 一份 docx 的圖 → 它在 matches.csv 的列(#57)

讀值不在這裡判斷:門檻、small 的 winner、要不要送 Vision 只在送審決策(ADR-0013)。

一張圖的讀值整理成 matches.csv 的一列(ScanResult,在 matches_csv);
送審決策(一份 docx 一個決定)在 submission_decision。
"""
from __future__ import annotations

from collections import Counter
from enum import Enum
import logging
from pathlib import Path
from typing import Callable, NamedTuple

import pytesseract
from pytesseract import Output

import docx_images
from matches_csv import DocxClass, ResultStatus, Roi, ScanResult
from permit_patterns import RE_PERMIT_ID_LIST_LOWER, find_permits, permit_id_patterns
from pixels import preprocess
# 停止擴掃與規則 A2 用同一個多數票謂詞(ADR-0005);送審門檻不在這裡用
from submission_decision import is_permit_vote

logger = logging.getLogger(__name__)

TESS_LANG = "ind+eng"

# ─── 分層掃描（large 專用，見 ADR-0005）────────────────────────────────────
# 許可證印在「切結書」的最後一頁(簽署方欄 VI. Pihak yang menandatangani),而
# docx 內的圖序前段是固定的:
#     image1 雜訊/空白 │ image2 契約頁 │ image3 次頁 │ image4~ 切結書 │ 尾端空白圖
# 切結書長度 2~3 頁不定,故許可證落在 image5 或 image6;mol ROI 則只有契約頁
# (image2)讀得到。21 份實測:19/19 許可證命中都在 {5,6},mol 命中全在 {2}。
#
# 用絕對序號而非「倒數第 N 張」:尾端掛著數量不定的空白圖(32510 的 image6、
# 32509 的 image7 都是空的),倒數會數到空白;前段的內容順序才是穩定的。
#
# 猜錯不會錯值,只會慢 —— 第一層拿不到 permit 多數票就把其餘圖全掃完,回到與
# 全掃相同的輸入。代價是規則會安靜地過期,故 CSV 留 scan_tier 欄供觀測。
TIER1_IMAGES = {2, 5, 6}

# ─── ROI ──────────────────────────────────────────────────────────────────
ROI_REGIONS = {
    Roi.MOL:           (0.05, 0.04, 0.40, 0.25),
    Roi.PERMIT_UPPER:  (0.40, 0.05, 1.00, 0.55),
    Roi.PERMIT_LOWER:  (0.40, 0.45, 1.00, 0.95),
}

# ─── Tesseract configs ────────────────────────────────────────────────────
WHITELIST_LATIN = (
    "0123456789"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "abcdefghijklmnopqrstuvwxyz"
    " :.'-,"
)

SCAN_CONFIGS = [
    {"name": "紅通道_2x_中值3",  "channel": "R",    "scale": 2, "median": 3, "contrast": (2, 98), "psm": 3},
    {"name": "紅通道_原尺寸",    "channel": "R",    "scale": 1, "median": 0, "contrast": (2, 98), "psm": 3},
    {"name": "灰階_2x_中值3",    "channel": "gray", "scale": 2, "median": 3, "contrast": (2, 98), "psm": 3},
    {"name": "紅通道_銳化_PSM6", "channel": "R",    "scale": 2, "median": 3, "contrast": (2, 98), "psm": 6, "sharpen": True},
    {"name": "紅通道_2x_PSM6",   "channel": "R",    "scale": 2, "median": 3, "contrast": (2, 98), "psm": 6},
    {"name": "灰階_2x_PSM6",     "channel": "gray", "scale": 2, "median": 3, "contrast": (2, 98), "psm": 6},
    {"name": "灰階_原尺寸",      "channel": "gray", "scale": 1, "median": 0, "contrast": (2, 98), "psm": 3},
    {"name": "紅通道_2x_PSM11",  "channel": "R",    "scale": 2, "median": 3, "contrast": (2, 98), "psm": 11},
    {"name": "灰階_2x_PSM11",    "channel": "gray", "scale": 2, "median": 3, "contrast": (2, 98), "psm": 11},
]

FALLBACK_CONFIGS = [
    {"name": "英數白名單_PSM6", "channel": "R",    "scale": 2, "median": 3,
     "contrast": (2, 98), "psm": 6, "lang": "eng", "whitelist": WHITELIST_LATIN},
    {"name": "英數白名單_PSM3", "channel": "gray", "scale": 2, "median": 3,
     "contrast": (2, 98), "psm": 3, "lang": "eng", "whitelist": WHITELIST_LATIN},
]

_VOTE_NAMES = {
    "紅通道_2x_中值3", "灰階_2x_中值3", "紅通道_原尺寸",
    "紅通道_2x_PSM6", "灰階_2x_PSM6", "紅通道_銳化_PSM6",
}
PERMIT_VOTE_CONFIGS = [c for c in SCAN_CONFIGS if c["name"] in _VOTE_NAMES]





# ═══════════════════════════════════════════════════════════════════════════
# ① 分層與 Tesseract
# ═══════════════════════════════════════════════════════════════════════════

def split_tiers(images: list[tuple[str, bytes]]
                ) -> tuple[list[tuple[str, bytes]], list[tuple[str, bytes]]]:
    """把圖分成 (第一層, 其餘),兩者聯集恆等於輸入,順序不變。

    命名不是 imageN 的圖一律歸第二層 —— 第一層是一組「賭它在這裡」的位置,
    賭不到就擴掃,把身分不明的檔名放進第一層只會白花時間。
    """
    tier1  = [x for x in images if docx_images.image_index(x[0]) in TIER1_IMAGES]
    rest   = [x for x in images if docx_images.image_index(x[0]) not in TIER1_IMAGES]
    return tier1, rest


def has_permit_vote(rows: list[ScanResult]) -> bool:
    """這批結果裡有沒有「可信的 permit 多數票」——即不必再擴掃的唯一理由。

    刻意排除兩種「看起來有值」的情況,它們都不足以停止擴掃:

      mol 命中          mol 只證明這是契約頁,不是許可證的讀值。32523/32529 的
                        mol 信心是 37 與 28,拿這種值擋掉擴掃等於用雜訊做決策。
      PERMIT_PARTIAL    幾個設定各讀出不同的值、湊不出多數。r.id 雖有值,但
                        送審決策不會把它當成可信讀值 —— 而真正的多數票
                        可能就在還沒掃的那幾張圖上(見 ADR-0005)。
    """
    return any(is_permit_vote(r) for r in rows)


def build_tess_config(cfg: dict) -> str:
    parts = [f"--psm {cfg.get('psm', 3)}"]
    if "whitelist" in cfg and cfg["whitelist"]:
        parts.append(f"-c tessedit_char_whitelist={cfg['whitelist']}")
    return " ".join(parts)


def ocr_with_conf(img, lang: str, tess_cfg: str) -> tuple[str, float]:
    data = pytesseract.image_to_data(img, lang=lang, config=tess_cfg, output_type=Output.DICT)
    valid = [(w, int(c)) for w, c in zip(data["text"], data["conf"])
             if w.strip() and int(c) != -1]
    text = " ".join(w for w, _ in valid)
    avg_conf = round(sum(c for _, c in valid) / len(valid), 1) if valid else 0.0
    return text, avg_conf


# 讀一個 ROI:(image bytes, 設定, ROI 相對比例) → (文字, 信心)。這是本模組唯一的
# 外部依賴;測試傳假的進來,就能不跑 Tesseract、不需要真的影像(#19)。
Ocr = Callable[[bytes, dict, tuple], tuple[str, float]]


def tesseract(image_bytes: bytes, cfg: dict, roi: tuple) -> tuple[str, float]:
    """預設的 Ocr:前處理(pixels.preprocess)後送 Tesseract。"""
    img = preprocess(image_bytes, {**cfg, "roi": roi})
    return ocr_with_conf(img, cfg.get("lang", TESS_LANG), build_tess_config(cfg))


# ═══════════════════════════════════════════════════════════════════════════
# ② 一張圖 → 一次判讀
# ═══════════════════════════════════════════════════════════════════════════

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


class CropKind(Enum):
    """裁切圖的種類 = 寫出後路徑記在 ScanResult 的哪一格。"""
    MOL      = "mol"        # → mol_crop
    PERMIT   = "permit"     # → permit_crop


class Crop(NamedTuple):
    """一張要寫出的裁切圖:掃描只決定要寫什麼,寫檔由 run_scan 做(#18)。"""
    kind: CropKind
    path: Path      # 相對於這一批的輸出目錄
    roi: tuple      # 從轉正後的原圖裁哪一塊(相對比例)


class Reading(NamedTuple):
    """一張圖的判讀:讀值整理(ScanResult)+ 要寫出的裁切圖。

    crops 還沒寫出,result 的 mol_crop / permit_crop 是空的;
    run_scan 寫出後才把路徑填回去。
    """
    result: ScanResult
    crops: list[Crop]


def _crop_stem(docx_name: str, img_name: str) -> str:
    return f"{Path(docx_name).stem}_{Path(img_name).stem}"


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
    return Crop(CropKind.MOL, Path("mol_crops") / f"{stem}.png", ROI_REGIONS[Roi.MOL])


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
        crops.append(Crop(CropKind.MOL, Path("mol_crops") / f"{stem}.png", roi_coords))
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

    def planned(kind: CropKind) -> bool:
        return any(c.kind is kind for c in crops)

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
            kind = CropKind(field)
            if not planned(kind):
                crops.append(Crop(kind, Path(f"{field}_crops") / f"{stem}.png", roi_coords))
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
            permit_id_list=RE_PERMIT_ID_LIST_LOWER)
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


# ═══════════════════════════════════════════════════════════════════════════
# ③ 分層掃描（large 專用，見 ADR-0005）
# ═══════════════════════════════════════════════════════════════════════════

# 讀一張圖:(圖檔名, image bytes) → 判讀;全無命中為 None。docx 名、--roi、Ocr 由呼叫端綁好。
ReadImage = Callable[[str, bytes], "Reading | None"]


class TierOutcome(Enum):
    """一份 large docx 的分層掃描怎麼結束的。"""
    第一層命中 = "第一層命中"   # 第一層就拿到 permit 多數票,沒有擴掃
    無可擴     = "無可擴"       # 第一層沒拿到,但沒有其餘的圖可擴(含 --full-scan)
    擴掃       = "擴掃"         # 第一層沒拿到、擴掃了其餘的圖

    @property
    def scan_tier(self) -> int:
        """matches.csv scan_tier 欄的值:擴掃過為 2,其餘 1。"""
        return 2 if self is TierOutcome.擴掃 else 1


class LargeDocxReading(NamedTuple):
    """一份 large docx 的分層掃描結果。"""
    readings: list[Reading]   # 有命中的圖,依掃描順序;標好 scan_tier
    tier: TierOutcome
    scanned: int              # 實際掃了幾張


def _read_tier(docx_name: str, images: list[tuple[str, bytes]], read_image: ReadImage,
               tier: int) -> list[Reading]:
    """掃一層的圖，回傳有命中的判讀（標上 scan_tier）。"""
    out: list[Reading] = []
    for img_name, img_bytes in images:
        reading = read_image(img_name, img_bytes)
        if not reading:
            logger.debug(f"  {docx_name} / {img_name}  未命中")
            continue
        result = reading.result
        result.scan_tier = tier
        out.append(reading)
        logger.info(
            f"★ {docx_name}/{img_name}"
            f"  [large|T{tier}|{result.hit_roi}]"
            f"  mol={result.mol!r}({result.mol_conf}) id={result.id!r}({result.id_conf})"
        )
    return out


def read_large_docx(docx_name: str, images: list[tuple[str, bytes]],
                    read_image: ReadImage, full_scan: bool = False) -> LargeDocxReading:
    """先掃第一層;拿不到可信的 permit 多數票就把其餘的圖掃完(擴掃決策,ADR-0005)。

    猜錯不會錯值,只會慢:擴掃後的輸入與全掃相同。full_scan 關掉分層,一次掃完。
    """
    tier1, rest = (images, []) if full_scan else split_tiers(images)
    readings = _read_tier(docx_name, tier1, read_image, 1)
    if has_permit_vote([r.result for r in readings]):
        return LargeDocxReading(readings, TierOutcome.第一層命中, len(tier1))
    if not rest:
        return LargeDocxReading(readings, TierOutcome.無可擴, len(tier1))
    logger.info(
        f"  ↻ {docx_name} 第一層({len(tier1)}張)無 permit 多數票"
        f" → 擴掃其餘 {len(rest)} 張"
    )
    readings += _read_tier(docx_name, rest, read_image, 2)
    return LargeDocxReading(readings, TierOutcome.擴掃, len(tier1) + len(rest))


# ═══════════════════════════════════════════════════════════════════════════
# ④ 一份 docx → 它在 matches.csv 的列
# ═══════════════════════════════════════════════════════════════════════════

SMALL_DOCX_THRESHOLD = 3   # 圖片數 ≤ 此值 → small

# 全無命中時存哪幾張原圖(imageN 的 N)給人比對:large 是契約頁與兩張可能的許可證頁,
# small 是契約頁與次頁。
NO_HIT_REVIEW_LARGE = frozenset({2, 5, 6})
NO_HIT_REVIEW_SMALL = frozenset({2, 3})


def classify_by_count(image_count: int) -> DocxClass:
    """依圖片數量(原始張數)分 small / large。"""
    return DocxClass.SMALL if image_count <= SMALL_DOCX_THRESHOLD else DocxClass.LARGE


def unscannable(images: list[tuple[str, bytes]] | None) -> tuple[ResultStatus, str] | None:
    """docx 讀圖的結果沒東西可掃 → (狀態, 給人看的一句);有圖回傳 None。

    images 為 None 表示 docx 打不開(讀圖那端不拋,一份壞檔不能讓整批中斷,#47)。
    """
    if images is None:
        return ResultStatus.UNREADABLE, "docx 無法開啟"
    if not images:
        return ResultStatus.NO_IMAGES, "docx 沒有可讀的內嵌圖"
    return None


class DocxScan(NamedTuple):
    """一份 docx 的許可證掃描:它在 matches.csv 的列,與要存給人比對的原圖。

    只有三種形狀,用對應的建構式造:unreadable(讀不到圖)、small、large(附分層結果)。
    small 不收分層結果 —— 「small 配擴掃」造不出來。
    """
    rows: list[Reading]               # 依寫出順序;至少一列。裁切圖還沒寫出
    review_images: list[tuple[str, bytes]]   # 全無命中時要存給人比對的原圖;其餘為空
    docx_class: DocxClass | None      # 讀不到圖為 None
    tier: TierOutcome | None          # large 的分層結果;其餘為 None
    scanned: int                      # 實際掃了幾張圖

    @classmethod
    def unreadable(cls, placeholder: Reading) -> DocxScan:
        return cls([placeholder], [], None, None, 0)

    @classmethod
    def small(cls, rows: list[Reading], review_images: list[tuple[str, bytes]],
              scanned: int) -> DocxScan:
        return cls(rows, review_images, DocxClass.SMALL, None, scanned)

    @classmethod
    def large(cls, rows: list[Reading], review_images: list[tuple[str, bytes]],
              tier: TierOutcome, scanned: int) -> DocxScan:
        return cls(rows, review_images, DocxClass.LARGE, tier, scanned)


def _review_images(images: list[tuple[str, bytes]], wanted: frozenset[int]
                   ) -> list[tuple[str, bytes]]:
    """要存給人比對的原圖:圖序在 wanted 裡的(檔名沒有數字者略過)。"""
    return [(name, data) for name, data in images
            if docx_images.image_index(name) in wanted]


def scan_docx(docx_name: str, images: list[tuple[str, bytes]] | None,
              read_large: ReadImage, read_small: ReadImage, *,
              full_scan: bool = False, image_filter: str = "") -> DocxScan:
    """一份 docx 的圖 → 它在 matches.csv 的列。寫列的規則只在這裡,不碰硬碟。

    read_large / read_small:讀一張圖(docx 名、--roi、Ocr 由呼叫端綁好)。
    images 是 docx 內全部的圖(None = 打不開);image_filter(--image)只掃那一張,
    對不到是呼叫端該先擋下的錯,這裡拋 ValueError。

    每份 docx 至少一列 —— 沒有列,它在 matches.csv 與 Sheets 上都不存在(#47):
      讀不到圖     一列佔位列(沒有 small/large、標讀不到圖的狀態)
      large 有命中 每張命中圖一列;全無命中一列佔位列(擴掃過標第二層),存 image2/5/6
      small 有命中 只寫命中列;全無命中每列都寫、標 small 全無命中,存 image2/3
    """
    nothing = unscannable(images)
    if nothing:
        placeholder = _empty_result(docx_name, "", None)
        placeholder.status, placeholder.note = nothing
        logger.warning(f"  ⚠ {docx_name} {placeholder.note}")
        return DocxScan.unreadable(Reading(placeholder, []))

    # docx_class 一律用「原始張數」判定，必須在任何過濾之前算：把過濾後
    # 的張數丟進 classify_by_count，5 張圖的 docx 剩 3 張就會被判成
    # small，整份改走 mol-only 的另一條路（ADR-0005）。
    docx_class = classify_by_count(len(images))
    logger.info(f"  {docx_name}: {len(images)} 張 → {docx_class}")

    if image_filter:
        names = [n for n, _ in images]
        images = [(n, b) for n, b in images if n == image_filter]
        if not images:
            raise ValueError(f"找不到圖檔 {image_filter!r}({docx_name} 內含:{names})")

    if docx_class is DocxClass.SMALL:
        return _small_rows(docx_name, images, read_small)
    return _large_rows(docx_name, images, read_large, full_scan)


def _small_rows(docx_name: str, images: list[tuple[str, bytes]],
                read_small: ReadImage) -> DocxScan:
    # small 不分層：最多 3 張圖、只掃一個佔頁面 7% 的 mol ROI，最壞
    # 也才 2.3 分鐘；而 1~2 張圖的 docx 與 TIER1_IMAGES 可能毫無交集，
    # 過濾會把它掃成零張、產出一筆與真的讀不到無從分辨的「全無命中」。
    readings = [read_small(name, data) for name, data in images]
    # 有命中只寫命中列;整份全無命中則每列都寫、標 small 全無命中。
    # winner 與門檻在送審決策(ADR-0013)。
    hits = [r for r in readings if r.result.hit]
    if hits:
        rows, review = hits, []
    else:
        for r in readings:
            r.result.status, r.result.note = ResultStatus.SMALL_NO_HIT, "small:無命中"
        rows, review = readings, _review_images(images, NO_HIT_REVIEW_SMALL)
    for r in rows:
        logger.info(
            f"★ {docx_name} / {r.result.image_name}"
            f"  [small|{r.result.hit_roi}]"
            f"  mol={r.result.mol!r}({r.result.mol_conf})"
        )
    return DocxScan.small(rows, review, len(images))


def _large_rows(docx_name: str, images: list[tuple[str, bytes]],
                read_large: ReadImage, full_scan: bool) -> DocxScan:
    scan = read_large_docx(docx_name, images, read_large, full_scan=full_scan)
    if scan.readings:
        return DocxScan.large(scan.readings, [], scan.tier, scan.scanned)

    # 全無命中：寫一列佔位列
    placeholder = _empty_result(docx_name, images[0][0], DocxClass.LARGE)
    placeholder.note   = "large:全無命中"
    placeholder.status = ResultStatus.LARGE_NO_HIT
    # 全無命中的份不會有任何命中列,這張佔位列是它在 CSV 裡的唯一
    # 代表。擴掃過就得標 2 —— 否則「大量擴掃且仍沒命中」(規則過期
    # 最典型的表現)在 scan_tier 欄上完全看不出來。
    placeholder.scan_tier = scan.tier.scan_tier
    return DocxScan.large([Reading(placeholder, [])], _review_images(images, NO_HIT_REVIEW_LARGE),
                          scan.tier, scan.scanned)


