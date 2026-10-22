# -*- coding: utf-8 -*-
"""許可證判讀:一張圖的 image bytes → 一次判讀(CONTEXT.md「ROI 掃描」「多數票」「分層掃描」)。

不碰硬碟、不碰網路、不改全域:OCR 由參數注入(預設 tesseract),裁切圖只回傳
「要寫什麼」(Crop),寫出由 pipeline.run_scan 做(#18)。所以投票、交叉比對、
擴掃決策都能用假 OCR 直接測(#19);以前只能靠 monkeypatch 模組屬性。

  scan_image_large / scan_image_mol_only   一張圖 → Reading(ScanResult, crops)
  read_large_docx                           一份 large docx 的分層掃描(ADR-0005)
  decide_result / aggregate_small_docx      單張圖的初步決策 / small docx 的整份決策

送審決策(一份 docx 一個決定)在 submission_decision,吃這裡的 ScanResult。
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from enum import Enum
import logging
from pathlib import Path
from typing import Callable, NamedTuple

import pytesseract
from pytesseract import Output

import docx_images
from permit_patterns import RE_PERMIT_ID_LIST_LOWER, find_permits, permit_id_patterns
from pixels import preprocess
from submission_decision import (
    CONF_KEY_IN, CONF_MOL_VOTE_MIN, CONF_VOTE_MIN, is_permit_vote)

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
    "mol":           (0.05, 0.04, 0.40, 0.25),
    "permit_upper":  (0.40, 0.05, 1.00, 0.55),
    "permit_lower":  (0.40, 0.45, 1.00, 0.95),
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



class ResultStatus(str, Enum):
    """OCR 掃描結果的狀態。供下游送審決策(submission_decision)做分派判斷。

    用 enum 取代過去用 note 文字比對（例如 "large:全無命中"）的方式：
    note 是給人看的描述，會因措辭調整而變動；status 才是給程式判斷的單一真相來源。
    """
    OK             = "ok"               # 正常命中，走後續決策
    LARGE_NO_HIT   = "large_no_hit"     # large docx 全部圖片都沒命中
    SMALL_NO_HIT   = "small_no_hit"     # small docx 全部圖片都沒命中
    PERMIT_PARTIAL = "permit_partial"   # permit 有部分命中但無多數票
    # docx 本身讀不到圖(#47):沒有可掃的東西,佔位列是它在 matches.csv 的唯一代表
    NO_IMAGES      = "no_images"        # 0 張可讀內嵌圖(沒有 word/media,或只有 .emf 之類)
    UNREADABLE     = "unreadable"       # 打不開(不是有效的 zip,或壓縮資料損壞)



# ─── CSV 欄位順序（同時供寫入與讀取使用，避免兩端走樣） ───────────────────
CSV_FIELDS = [
    "source_docx", "image_name", "docx_class", "scan_tier",
    "mol", "mol_layer", "mol_conf", "mol_from_vote",
    "id",  "id_layer",  "id_conf",  "id_from_vote",
    "cross_match", "final_value", "final_conf", "vision_review",
    "note", "manual_review", "low_conf", "hit_config", "hit_roi",
    "mol_crop", "permit_crop",
    "status",
]


@dataclass
class ScanResult:
    """一張圖的 OCR 掃描結果。

    這個 dataclass 取代了過去用 dict 表達結果的方式，解掉三類問題：

    1. 型別契約：mol_conf 過去可能是 "" / float / "67.3"（從 CSV 讀回時）三種型別，
       下游每次都得呼叫 _to_f() 翻譯。現在它就是 float，永遠是 float。

    2. 拼字防呆：r["mol_cnof"] = 80 過去會默默建立一個沒人讀的新 key 而不報錯；
       r.mol_cnof = 80 現在會在執行時拋 AttributeError，IDE 也標紅。

    3. 隱性狀態：cross_match 過去是 "✓" / ""，vision_review 是 "Y" / ""，
       靠字串比對判斷真假。現在分別是 bool，意圖明確。manual_review 過去混著
       "Y" 與整句理由，現在也是 bool，理由寫在 note。

    CSV 邊界由 to_csv_row()/from_csv_row() 處理。寫到磁碟仍是字串
    （"Y" / "✓" / "" / "80.0"），人類看 CSV 跟以前一樣。
    """
    # ─── 識別欄位（必填） ─────────────────────────────────────────────────
    source_docx: str
    image_name: str
    docx_class: str            # "small" / "large"
    # 這張圖是在第幾層被掃到的:1=第一層(TIER1_IMAGES)，2=第一層沒拿到 permit
    # 多數票後的擴掃。small 與 --full-scan 一律為 1。純觀測欄位，不參與任何決策
    # ——它存在是為了讓「第一層命中率崩掉」這件事看得見(見 ADR-0005)。
    scan_tier: int = 1

    # ─── OCR 結果 ────────────────────────────────────────────────────────
    mol: str = ""
    mol_layer: int = 0
    mol_conf: float = 0.0
    id: str = ""
    id_layer: int = 0
    id_conf: float = 0.0

    # ─── 決策結果 ────────────────────────────────────────────────────────
    cross_match: bool = False
    final_value: str = ""
    final_conf: float = 0.0
    vision_review: bool = False
    note: str = ""
    status: ResultStatus = ResultStatus.OK

    # ─── 詮釋資料 ────────────────────────────────────────────────────────
    manual_review: bool = False  # 要不要人工；「為什麼」寫在 note
    low_conf: str = ""         # 低信心 crop 圖檔路徑
    hit_config: str = ""       # 命中時用的 Tesseract config 名稱
    hit_roi: str = ""          # 命中時的 ROI 名稱
    mol_crop: str = ""         # mol crop 圖檔路徑
    permit_crop: str = ""      # permit crop 圖檔路徑

    # ─── 內部 flag（取代過去的 _id_from_vote） ───────────────────────────
    id_from_vote:  bool = False
    mol_from_vote: bool = False   # mol 值來自多數票（SCAN_CONFIGS 全跑後投票）

    # ─── 序列化 ──────────────────────────────────────────────────────────
    def to_csv_row(self) -> dict[str, str]:
        """轉成 CSV 用的 str dict。

        慣例（與重構前的 CSV 完全一致，使人類觀察 CSV 無感）：
          - bool 欄位 → "Y" / "✓" / ""
          - float 為 0 → ""（保留「未設定」的視覺差異）
          - int 為 0 → ""
          - enum  → 其 .value 字串
        """
        return {
            "source_docx":   self.source_docx,
            "image_name":    self.image_name,
            "docx_class":    self.docx_class,
            "scan_tier":     str(self.scan_tier),
            "mol":           self.mol,
            "mol_layer":     str(self.mol_layer) if self.mol_layer else "",
            "mol_conf":      f"{self.mol_conf:.1f}" if self.mol_conf else "",
            "mol_from_vote": "Y" if self.mol_from_vote else "",
            "id":            self.id,
            "id_layer":      str(self.id_layer) if self.id_layer else "",
            "id_conf":       f"{self.id_conf:.1f}" if self.id_conf else "",
            "id_from_vote":  "Y" if self.id_from_vote else "",
            "cross_match":   "✓" if self.cross_match else "",
            "final_value":   self.final_value,
            "final_conf":    f"{self.final_conf:.1f}" if self.final_conf else "",
            "vision_review": "Y" if self.vision_review else "",
            "note":          self.note,
            "manual_review": "Y" if self.manual_review else "",
            "low_conf":      self.low_conf,
            "hit_config":    self.hit_config,
            "hit_roi":       self.hit_roi,
            "mol_crop":      self.mol_crop,
            "permit_crop":   self.permit_crop,
            "status":        self.status.value,
        }

    @classmethod
    def from_csv_row(cls, row: dict[str, str]) -> "ScanResult":
        """從 CSV 一列重建 ScanResult。

        集中處理 str → 型別轉換，過去這段邏輯散在 _to_f / 各種讀取點。
        現在只此一處需要關心 CSV 格式細節。
        """
        def to_float(s: str) -> float:
            try:
                return float(s) if s else 0.0
            except ValueError:
                return 0.0

        def to_int(s: str) -> int:
            try:
                return int(s) if s else 0
            except ValueError:
                return 0

        status_str = row.get("status", "")
        try:
            status = ResultStatus(status_str) if status_str else ResultStatus.OK
        except ValueError:
            status = ResultStatus.OK

        return cls(
            source_docx   = row.get("source_docx", ""),
            image_name    = row.get("image_name", ""),
            docx_class    = row.get("docx_class", ""),
            # 舊 CSV 沒有這欄 → 視為第一層,不讓缺欄變成無意義的 0
            scan_tier     = to_int(row.get("scan_tier", "")) or 1,
            mol           = row.get("mol", ""),
            mol_layer     = to_int(row.get("mol_layer", "")),
            mol_conf      = to_float(row.get("mol_conf", "")),
            mol_from_vote = (row.get("mol_from_vote", "") == "Y"),
            id            = row.get("id", ""),
            id_layer      = to_int(row.get("id_layer", "")),
            id_conf       = to_float(row.get("id_conf", "")),
            id_from_vote  = (row.get("id_from_vote", "") == "Y"),
            cross_match   = (row.get("cross_match", "") == "✓"),
            final_value   = row.get("final_value", ""),
            final_conf    = to_float(row.get("final_conf", "")),
            vision_review = (row.get("vision_review", "") == "Y"),
            note          = row.get("note", ""),
            # 舊 CSV 除了 "Y" 還有整句理由(「mol 無值，需人工判斷」)——非空一律
            # 算需要人工，不能讓它安靜變成 False、脫離人工審查
            manual_review = bool(row.get("manual_review", "").strip()),
            low_conf      = row.get("low_conf", ""),
            hit_config    = row.get("hit_config", ""),
            hit_roi       = row.get("hit_roi", ""),
            mol_crop      = row.get("mol_crop", ""),
            permit_crop   = row.get("permit_crop", ""),
            status        = status,
        )


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
                        送審決策的規則A2 不會採用它,反而會掉進規則B
                        並以 no_vote_rescue 把整份推去走台仲反查 —— 而真正的
                        多數票可能就在還沒掃的那幾張圖上(見 ADR-0005)。
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


def roi_field(roi_name: str) -> str:
    return roi_name.split("_")[0]


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
        text, conf = ocr(image_bytes, cfg, ROI_REGIONS["mol"])
        _, _, mol, _ = find_permits(text)
        if mol:
            entries.append((mol, conf))
    return entries


class Crop(NamedTuple):
    """一張要寫出的裁切圖:掃描只決定要寫什麼,寫檔由 run_scan 做(#18)。"""
    field: str      # 寫出後路徑記在 ScanResult 的哪一格:mol_crop / permit_crop / low_conf
    path: Path      # 相對於這一批的輸出目錄
    roi: tuple      # 從轉正後的原圖裁哪一塊(相對比例)


class Reading(NamedTuple):
    """一張圖的判讀:讀值整理(ScanResult)+ 要寫出的裁切圖。

    crops 還沒寫出,result 的 mol_crop / permit_crop / low_conf 是空的;
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
    result.hit_roi       = "mol"
    logger.info(
        f"  ★ mol 多數票：{vote.value}  avg_conf={vote.conf}"
        f"  ({vote.count}/{vote.total} 票)"
    )
    return Crop("mol_crop", Path("mol_crops") / f"{stem}.png", ROI_REGIONS["mol"])


def _low_conf_crop(stem: str, roi_name: str, roi_coords: tuple, conf: float) -> Crop:
    path = Path("low_conf_crops") / f"{stem}_{roi_name}_conf{int(conf)}.png"
    logger.info(f"  ⚠ 低信心 {conf} < {CONF_KEY_IN}：{path.name}")
    return Crop("low_conf", path, roi_coords)


def scan_image_mol_only(docx_name: str, img_name: str, image_bytes: bytes,
                        ocr: Ocr = tesseract) -> Reading:
    """small docx 專用：只掃 mol ROI。

    第一階段先跑 PERMIT_VOTE_CONFIGS 多數票；有多數票直接採用並標記 mol_from_vote=True。
    無多數票時退回 first-hit（SCAN_CONFIGS → FALLBACK_CONFIGS）。

    不碰硬碟:命中的裁切圖放在回傳的 crops,由 run_scan 寫出(#18)。
    """
    stem = _crop_stem(docx_name, img_name)
    result = _empty_result(docx_name, img_name, "small")
    roi_coords = ROI_REGIONS["mol"]

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
        result.hit_roi   = "mol"
        crops.append(Crop("mol_crop", Path("mol_crops") / f"{stem}.png", roi_coords))
        if conf < CONF_KEY_IN:
            crops.append(_low_conf_crop(stem, "mol", roi_coords, conf))
        logger.debug(f"  ★ mol/{cfg['name']}  conf={conf}  mol={mol!r}")
        return Reading(result, crops)

    result.manual_review = True
    result.note          = "mol 無值，需人工判斷"
    logger.info(f"  ⚠ {img_name}: mol 無值，標記人工審查")
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
    result = _empty_result(docx_name, img_name, "large")
    crops: list[Crop] = []
    fields_found: set[str] = set()
    any_hit = False

    def planned(crop_field: str) -> bool:
        return any(c.field == crop_field for c in crops)

    for roi_name, roi_coords in ROI_REGIONS.items():
        if roi_filter and roi_name != roi_filter:
            continue
        field = roi_field(roi_name)
        if field in fields_found:
            continue

        # ── mol ROI：先嘗試多數票（PERMIT_VOTE_CONFIGS 6組）────────────
        if roi_name == "mol":
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
            is_permit_roi = roi_name.startswith("permit")
            permit_id_list = permit_id_patterns(roi_name) if roi_name.startswith("permit") else None
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
            # 對應欄位的 crop（mol_crop 或 permit_crop）,每欄只寫第一個命中的 ROI
            crop_field = f"{field}_crop"   # "mol_crop" or "permit_crop"
            if not planned(crop_field):
                crops.append(Crop(crop_field, Path(f"{field}_crops") / f"{stem}.png",
                                  roi_coords))
            if conf < CONF_KEY_IN and not planned("low_conf"):
                crops.append(_low_conf_crop(stem, roi_name, roi_coords, conf))
            logger.debug(f"  ★ {roi_name}/{cfg['name']}  conf={conf}  id={id_!r} mol={mol!r}")
            break

    if not any_hit:
        return None

    mol_val    = result.mol
    permit_val = result.id
    permit_vote_avg_conf = 0.0

    if not mol_val or not permit_val:
        upper_entries = _collect_permit_votes(image_bytes, ROI_REGIONS["permit_upper"], ocr)
        lower_entries = _collect_permit_votes(
            image_bytes, ROI_REGIONS["permit_lower"], ocr,
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
                f"  → 候選值={best[0]!r} conf={best[1]:.0f}，標記送 Vision"
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


def _empty_result(docx_name: str, img_name: str, docx_class: str) -> ScanResult:
    """建立空白 ScanResult(維持函數名以減少呼叫端 churn,但回傳型別變了)。"""
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


class LargeDocxReading(NamedTuple):
    """一份 large docx 的分層掃描結果。"""
    readings: list[Reading]   # 有命中的圖,依掃描順序;已跑過 decide_result、標好 scan_tier
    stopped_at_tier1: bool    # 第一層就拿到 permit 多數票,沒有擴掃
    expanded: bool            # 第一層沒拿到、擴掃了其餘的圖
    scanned: int              # 實際掃了幾張


def _read_tier(docx_name: str, images: list[tuple[str, bytes]], read_image: ReadImage,
               tier: int) -> list[Reading]:
    """掃一層的圖，回傳有命中的判讀（已跑過 decide_result 並標上 scan_tier）。"""
    out: list[Reading] = []
    for img_name, img_bytes in images:
        reading = read_image(img_name, img_bytes)
        if not reading:
            logger.debug(f"  {docx_name} / {img_name}  未命中")
            continue
        result = reading.result
        decide_result(result)
        result.scan_tier = tier
        out.append(reading)
        logger.info(
            f"★ {docx_name}/{img_name}"
            f"  [large|T{tier}|{result.hit_roi}]"
            f"  mol={result.mol!r} id={result.id!r}"
            f"  final={result.final_value!r}"
            f"  vision={result.vision_review!r}"
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
        return LargeDocxReading(readings, True, False, len(tier1))
    if not rest:
        return LargeDocxReading(readings, False, False, len(tier1))
    logger.info(
        f"  ↻ {docx_name} 第一層({len(tier1)}張)無 permit 多數票"
        f" → 擴掃其餘 {len(rest)} 張"
    )
    readings += _read_tier(docx_name, rest, read_image, 2)
    return LargeDocxReading(readings, False, True, len(tier1) + len(rest))


# ═══════════════════════════════════════════════════════════════════════════
# ④ 單張決策（decide_result / aggregate_small_docx）
# ═══════════════════════════════════════════════════════════════════════════

def decide_result(result: ScanResult) -> ScanResult:
    """根據 OCR 結果決定 final_value / vision_review / note。

    注意此函數就地修改並回傳同一個 ScanResult(維持 dict 版時的語意)。
    重構前因為 dict 欄位型別不固定(mol_conf 可能是 "" / float / "67.3"),
    需要 _to_f() 在使用前翻譯;現在 dataclass 強制 float,_to_f 已刪除。
    """
    mol         = result.mol
    id_         = result.id
    mol_conf    = result.mol_conf
    id_conf     = result.id_conf
    cross       = result.cross_match
    docx_class  = result.docx_class
    id_from_vote  = result.id_from_vote
    mol_from_vote = result.mol_from_vote

    final_value = ""
    final_conf  = 0.0
    vision_review = False
    note = result.note

    if mol and id_:
        if mol_conf >= id_conf:
            final_value, final_conf = mol, mol_conf
            note = "mol勝(信心高)" if mol_conf != id_conf else "mol==permit"
        else:
            final_value, final_conf = id_, id_conf
            note = "permit勝(信心高)"
        if final_conf <= CONF_KEY_IN:
            vision_review = True
        if mol_conf > id_conf and mol != id_:
            vision_review = True
            note = "mol≠permit，mol信心高，值衝突"
    elif mol:
        final_value, final_conf = mol, mol_conf
        if docx_class == "large":
            mol_threshold = CONF_MOL_VOTE_MIN if mol_from_vote else CONF_KEY_IN
            if mol_conf <= mol_threshold:
                vision_review = True
                note = "僅mol，多數票信心低" if mol_from_vote else "僅mol，信心低"
    elif id_:
        final_value, final_conf = id_, id_conf
        threshold = CONF_VOTE_MIN if id_from_vote else CONF_KEY_IN
        if id_conf <= threshold:
            vision_review = True
            note = "permit多數票，信心低" if id_from_vote else "僅permit，信心低"

    if cross and final_conf <= CONF_KEY_IN:
        vision_review = True
        note = (note + " 多數決信心低").strip() if note else "多數決信心低"

    if result.status == ResultStatus.PERMIT_PARTIAL:
        vision_review = True
        note = (note + " permit部分命中無多數").strip() if note else "permit部分命中無多數"

    result.final_value   = final_value
    result.final_conf    = round(final_conf, 1) if final_conf else 0.0
    result.vision_review = vision_review
    result.note          = note
    return result


def aggregate_small_docx(results: list[ScanResult]) -> list[ScanResult]:
    """small docx 的批次決策。

    過去因 dict 不知道 mol_conf 是 str 還是 float,需要 _to_f 包一層;
    現在 dataclass 直接保證是 float,程式短了不少。
    """
    hits = [r for r in results if r.mol or r.id]
    if not hits:
        for r in results:
            r.vision_review = True
            r.note   = "small:無命中"
            r.status = ResultStatus.SMALL_NO_HIT
        return results

    def best_conf(r: ScanResult) -> float:
        return max(r.mol_conf, r.id_conf)

    winner  = max(hits, key=best_conf)
    w_value = winner.mol or winner.id
    w_conf  = best_conf(winner)

    # 所有命中列共享同一個 docx 層級的最終值
    for r in hits:
        r.final_value = w_value
        r.final_conf  = round(w_conf, 1) if w_conf else 0.0
        if r is not winner:
            r.mol_from_vote = False   # 只保留 winner 的投票旗標

    # mol 多數票且平均信心高於門檻 → 直接 key-in，無須送 Vision
    if winner.mol_from_vote and winner.mol_conf > CONF_MOL_VOTE_MIN:
        logger.info(
            f"  ✓ mol 多數票高信心（{winner.mol_conf} > {CONF_MOL_VOTE_MIN}）"
            f"  → 直接 key-in：{w_value}"
        )
    else:
        winner.vision_review = True   # Q2 修正：只送 winner 一張
    return hits


