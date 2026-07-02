# -*- coding: utf-8 -*-
"""
pipeline.py
===========
整合流程：prefilter → scan → vision_submit → Google Sheets key-in

執行步驟：
  1. prefilter  ：依圖片數量將 docx 分類為 small / large
  2. scan       ：Tesseract OCR，掃 mol / permit ROI，輸出 matches.csv
  3. vision_submit：讀取 matches.csv，決定哪些送 Google Vision、哪些直接 key-in
  4. key-in     ：Vision 結果 / 高信心值 → 寫入 Google Sheets；人工審查案件另行標記

用法：
  python pipeline.py
  python pipeline.py --log-level DEBUG
  python pipeline.py --file path/to/single.docx
  python pipeline.py --file path/to/single.docx --image image2.jpeg
  python pipeline.py --roi mol

環境需求：
  pip install pytesseract pillow numpy google-cloud-vision google-auth
              google-api-python-client

Google 認證：
  設定環境變數 GOOGLE_APPLICATION_CREDENTIALS 指向 Service Account JSON 金鑰檔，
  或在程式內改用 OAuth 2.0。
"""

import re
import csv
import time
import zipfile
import logging
import argparse
import os
import functools
from collections import Counter, defaultdict
from dataclasses import dataclass, field, asdict
from enum import Enum
from io import BytesIO
from pathlib import Path
from typing import Callable, Optional

import numpy as np
from PIL import Image, ImageFilter, ImageOps
import pytesseract
from pytesseract import Output

# Google Vision
from google.cloud import vision as gvision
from google.api_core import exceptions as gax_exceptions

# Google Sheets
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from google.oauth2 import service_account

pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
# ① 設定區（依實際環境修改）
# ═══════════════════════════════════════════════════════════════════════════

INPUT_DIR  = Path("./docs")
OUTPUT_DIR = Path("./scan_results")
TESS_LANG  = "ind+eng"
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".tif"}

# Google Sheets
SPREADSHEET_ID = "1zL5sRhaJHHXd-FBcY7rEm28Sfhz48l32gBwn-ATHalM"
SHEET_NAME     = "工作表1"                        # ← 改為實際工作表名稱
# 每列寫入 4 欄：A=source_docx, B=final_value, C=status, D=note
# 欄位順序由 keyin_to_sheets() 內 values 組裝決定；若需改順序在那裡調整

# Service Account 金鑰路徑（或設環境變數 GOOGLE_APPLICATION_CREDENTIALS）
SERVICE_ACCOUNT_JSON = os.environ.get(
    "GOOGLE_APPLICATION_CREDENTIALS", "service_account.json"
)
SHEETS_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

# 信心門檻
CONF_KEY_IN       = 55   # 高於此值直接 key-in；低於則標記送 Google Vision
CONF_VOTE_MIN     = 45   # permit 多數票平均信心低於此值才送 Vision
CONF_MOL_VOTE_MIN = 50   # small docx mol 多數票平均信心高於此值直接 key-in

# ─── prefilter ────────────────────────────────────────────────────────────
SMALL_DOCX_THRESHOLD = 3   # 圖片數 ≤ 此值 → "small"

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

# vision_submit key-in 門檻（與 CONF_KEY_IN 相同，明確宣告供 vision_submit 邏輯使用）
CONF_KEY_IN_VS = 55


# ═══════════════════════════════════════════════════════════════════════════
# ①' 共用工具：重試 decorator / 狀態 enum / queue item dataclass
# ═══════════════════════════════════════════════════════════════════════════

class ResultStatus(str, Enum):
    """OCR 掃描結果的狀態。供下游 build_vision_queue / process_*_vs 做分派判斷。

    用 enum 取代過去用 note 文字比對（例如 "large:全無命中"）的方式：
    note 是給人看的描述，會因措辭調整而變動；status 才是給程式判斷的單一真相來源。
    """
    OK             = "ok"               # 正常命中，走後續決策
    LARGE_NO_HIT   = "large_no_hit"     # large docx 全部圖片都沒命中
    SMALL_NO_HIT   = "small_no_hit"     # small docx 全部圖片都沒命中
    PERMIT_PARTIAL = "permit_partial"   # permit 有部分命中但無多數票


class SheetStatus(str, Enum):
    """寫入 Google Sheets 時的 status 欄值。"""
    KEYED_IN      = "keyed-in"
    VISION        = "vision"
    MANUAL_REVIEW = "manual_review"


@dataclass
class VisionQueueItem:
    """vision_submit 階段產生的工作項目。
    direct_keyin=True 表示信心足夠，直接寫 Sheets；False 則送 Google Vision 再決定。

    candidate_conf:候選值對應的 Tesseract 信心分數。後續 verify_vision_result
    會帶進 rationale 供人工審查參考(差 1 字元時不再用它自動挑值)。
    """
    source_docx: str
    image_name: str
    img_path: str
    candidate_value: str
    reason: str
    direct_keyin: bool = False
    candidate_conf: float = 0.0


# ─── CSV 欄位順序（同時供寫入與讀取使用，避免兩端走樣） ───────────────────
CSV_FIELDS = [
    "source_docx", "image_name", "docx_class",
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
       靠字串比對判斷真假。現在分別是 bool，意圖明確。

    CSV 邊界由 to_csv_row()/from_csv_row() 處理。寫到磁碟仍是字串
    （"Y" / "✓" / "" / "80.0"），人類看 CSV 跟以前一樣。
    """
    # ─── 識別欄位（必填） ─────────────────────────────────────────────────
    source_docx: str
    image_name: str
    docx_class: str            # "small" / "large"

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
    manual_review: str = ""    # 保留 str：現有值有 "Y" 也有"mol 無值，需人工判斷"
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
            "manual_review": self.manual_review,
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
            manual_review = row.get("manual_review", ""),
            low_conf      = row.get("low_conf", ""),
            hit_config    = row.get("hit_config", ""),
            hit_roi       = row.get("hit_roi", ""),
            mol_crop      = row.get("mol_crop", ""),
            permit_crop   = row.get("permit_crop", ""),
            status        = status,
        )


def with_retry(
    max_attempts: int = 5,
    initial_wait: float = 2.0,
    backoff_factor: float = 2.0,
    max_wait: float = 30.0,
    retryable_exceptions: tuple = (Exception,),
    should_retry: Optional[Callable[[BaseException], bool]] = None,
):
    """指數退避重試 decorator（純 stdlib，無需 tenacity）。

    參數：
      max_attempts         ：總嘗試次數（含第一次）。
      initial_wait         ：第一次失敗後等待秒數。
      backoff_factor       ：每次失敗後等待時間乘以此倍數。
      max_wait             ：等待時間上限。
      retryable_exceptions ：哪些 exception 才會觸發重試。
      should_retry         ：額外的判斷函數；回傳 False 表示此次例外不重試（直接 raise）。

    生產環境建議改用 tenacity 套件；此處用 stdlib 是為了不新增相依。
    """
    def decorator(fn: Callable) -> Callable:
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            wait = initial_wait
            for attempt in range(1, max_attempts + 1):
                try:
                    return fn(*args, **kwargs)
                except retryable_exceptions as e:
                    if should_retry is not None and not should_retry(e):
                        raise
                    if attempt == max_attempts:
                        logger.error(
                            f"{fn.__name__} 重試 {max_attempts} 次後仍失敗：{e!r}"
                        )
                        raise
                    logger.warning(
                        f"{fn.__name__} 第 {attempt}/{max_attempts} 次失敗：{e!r}；"
                        f"{wait:.1f}s 後重試"
                    )
                    time.sleep(wait)
                    wait = min(wait * backoff_factor, max_wait)
        return wrapper
    return decorator


def _is_retryable_google_error(exc: BaseException) -> bool:
    """判斷 Google API 例外是否值得重試。

    可重試：
      - 連線 / 逾時錯誤
      - HttpError 429（rate limit）、5xx（伺服器端錯誤）
      - google.api_core 的 ServiceUnavailable / DeadlineExceeded / Aborted
    不可重試（直接拋出）：
      - 4xx 其他錯誤（如 403 權限、404 找不到資源）→ 重試也沒用
    """
    if isinstance(exc, (ConnectionError, TimeoutError)):
        return True
    if isinstance(exc, HttpError):
        status = getattr(exc.resp, "status", 0)
        return status == 429 or 500 <= status < 600
    if isinstance(exc, (gax_exceptions.ServiceUnavailable,
                        gax_exceptions.DeadlineExceeded,
                        gax_exceptions.Aborted,
                        gax_exceptions.ResourceExhausted)):
        return True
    return False


# ═══════════════════════════════════════════════════════════════════════════
# ② prefilter
# ═══════════════════════════════════════════════════════════════════════════

def classify_by_count(image_count: int) -> str:
    """依圖片數量回傳 'small' 或 'large'。"""
    return "small" if image_count <= SMALL_DOCX_THRESHOLD else "large"


# ═══════════════════════════════════════════════════════════════════════════
# ③ 正則表達式
# ═══════════════════════════════════════════════════════════════════════════

RE_PERMIT_ID_LIST = [
    re.compile(r"No\.?\s*i[zjl1]in\s*[:::﹕]\s*(?:NO\.)?(\d{4}(?:-\d)?)(?!\d)", re.IGNORECASE),
    re.compile(r"[Nn]\w{0,5}n\s*[:::﹕]\s*(?:NO\.)?(\d{4}(?:-\d)?)(?!\d)",       re.IGNORECASE),
    re.compile(r"\bi[zjl1]in\s*[:::﹕]\s*(?:NO\.)?(\d{4}(?:-\d)?)(?!\d)",         re.IGNORECASE),
    re.compile(r"\bNO\.(\d{4}(?:-\d)?)(?!\d)",                                    re.IGNORECASE),
]

RE_PERMIT_ID_LIST_LOWER = [
    re.compile(r"No\.?\s*i[zjl1]in\s*[:::﹕]?\s*(?:NO\.)?(\d{4}(?:-\d)?)(?!\d)", re.IGNORECASE),
    re.compile(r"[Nn]\w{0,5}n\s*[:::﹕]\s*(?:NO\.)?(\d{4}(?:-\d)?)(?!\d)",        re.IGNORECASE),
    re.compile(r"\bi[zjl1]in\s*[:::﹕]\s*(?:NO\.)?(\d{4}(?:-\d)?)(?!\d)",          re.IGNORECASE),
    re.compile(r"\bNO\.(\d{4}(?:-\d)?)(?!\d)",                                     re.IGNORECASE),
]

RE_MOL_LIST = [
    re.compile(r"Agency'?s?\s+M[O0]L?\s+(?:L[i1I])?[i]?cense\s+Num\s*ber\s*[:::]\s*(\d{4}(?:-\d)?)(?!\d)", re.IGNORECASE),
    re.compile(r"A\w{3,6}'?s?\s+M[O0]L?\s+(?:L[i1I])?[i]?cense\s+Num\s*ber\s*[:::]\s*(\d{4}(?:-\d)?)(?!\d)", re.IGNORECASE),
    re.compile(r"(?:Num\s*ber|umber|[Nn]amber)\s*[:::]\s*(\d{4}(?:-\d)?)(?!\d)", re.IGNORECASE),
    re.compile(r"M[O0]L\D{0,30}(\d{4}(?:-\d)?)(?!\d)", re.IGNORECASE),
]


def find_permits(text: str, permit_id_list=None,
                 find_mol: bool = True, find_id: bool = True) -> tuple[str, int, str, int]:
    if permit_id_list is None:
        permit_id_list = RE_PERMIT_ID_LIST
    id_, id_layer = None, 0
    if find_id:
        for i, p in enumerate(permit_id_list, 1):
            id_ = p.search(text)
            if id_:
                id_layer = i
                break
    mol, mol_layer = None, 0
    if find_mol:
        for i, p in enumerate(RE_MOL_LIST, 1):
            mol = p.search(text)
            if mol:
                mol_layer = i
                break
    return (
        id_.group(1).strip() if id_ else "",
        id_layer,
        mol.group(1).strip() if mol else "",
        mol_layer,
    )


# ═══════════════════════════════════════════════════════════════════════════
# ④ 影像工具
# ═══════════════════════════════════════════════════════════════════════════

def _image_sort_key(name: str) -> tuple[int, int, str]:
    """為圖檔名提供 natural sort key,讓 image1 < image2 < ... < image10。

    docx 內的圖通常命名為 image{N}.{ext}。預設的 lexical sort 會把
    image10.jpeg 排在 image2.jpeg 之前(因為字串 '1' < '2'),導致 pipeline
    可能先掃到第 10 頁的舊資料而非第 2 頁的當前資料。

    本函數從檔名抽出第一個數字當排序鍵:
      - 有數字 → (0, 數字值, 原名)
      - 無數字 → (1, 0, 原名) (排在所有有數字的後面)

    範例:
      _image_sort_key("image1.jpeg")  → (0, 1, "image1.jpeg")
      _image_sort_key("image10.jpeg") → (0, 10, "image10.jpeg")
      _image_sort_key("cover.png")    → (1, 0, "cover.png")
    """
    m = re.search(r"\d+", name)
    if m:
        return (0, int(m.group()), name)
    return (1, 0, name)


def extract_images_from_docx(docx_path: Path) -> list[tuple[str, bytes]]:
    """從 docx 取出所有圖檔,**保證以 natural sort 順序回傳**。

    順序很重要:同一份 docx 內的 image1, image2, ... image10 應該嚴格依數字序處理,
    避免 lexical sort 把 image10 排在 image2 之前,導致先掃到後面頁的舊資料。
    """
    images = []
    with zipfile.ZipFile(docx_path, "r") as z:
        for fname in z.namelist():
            if fname.startswith("word/media/") and Path(fname).suffix.lower() in IMAGE_EXTENSIONS:
                images.append((Path(fname).name, z.read(fname)))
    # natural sort:image1 < image2 < ... < image10(避免 lexical sort 的 image10 < image2 問題)
    images.sort(key=lambda x: _image_sort_key(x[0]))
    return images


def auto_rotate(img: Image.Image) -> Image.Image:
    return ImageOps.exif_transpose(img)


def crop_roi(img: Image.Image, roi: tuple) -> Image.Image:
    w, h = img.size
    x1, y1, x2, y2 = roi
    return img.crop((int(x1 * w), int(y1 * h), int(x2 * w), int(y2 * h)))


def preprocess(image_bytes: bytes, cfg: dict) -> Image.Image:
    img = Image.open(BytesIO(image_bytes)).convert("RGB")
    img = auto_rotate(img)
    if "roi" in cfg:
        img = crop_roi(img, cfg["roi"])
    rgb = np.array(img)
    ch = cfg["channel"]
    arr = rgb[:, :, 0] if ch == "R" else np.mean(rgb, axis=2).astype(np.uint8)
    pil = Image.fromarray(arr)
    scale = cfg.get("scale", 1)
    if scale > 1:
        pil = pil.resize((pil.width * scale, pil.height * scale), Image.Resampling.LANCZOS)
    lo, hi = cfg.get("contrast", (2, 98))
    a = np.array(pil)
    low, high = np.percentile(a, lo), np.percentile(a, hi)
    if high > low:
        a = np.clip((a - low) * 255.0 / (high - low), 0, 255).astype(np.uint8)
    pil = Image.fromarray(a)
    if cfg.get("median", 0) > 0:
        pil = pil.filter(ImageFilter.MedianFilter(size=cfg["median"]))
    if cfg.get("sharpen", False):
        pil = pil.filter(ImageFilter.SHARPEN).filter(ImageFilter.SHARPEN)
    return pil


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


def roi_field(roi_name: str) -> str:
    return roi_name.split("_")[0]


# ═══════════════════════════════════════════════════════════════════════════
# ⑤ scan 核心
# ═══════════════════════════════════════════════════════════════════════════

def _collect_permit_votes(image_bytes: bytes, roi_coords: tuple,
                          permit_id_list=None) -> list[tuple[str, float]]:
    entries: list[tuple[str, float]] = []
    for cfg in PERMIT_VOTE_CONFIGS:
        img = preprocess(image_bytes, {**cfg, "roi": roi_coords})
        text, conf = ocr_with_conf(img, cfg.get("lang", TESS_LANG), build_tess_config(cfg))
        id_, _, _, _ = find_permits(text, permit_id_list)
        if id_:
            entries.append((id_, conf))
    return entries


def _majority_vote(values: list[str], min_count: int = 2) -> str:
    if not values:
        return ""
    best, count = Counter(values).most_common(1)[0]
    return best if count >= min_count else ""


def _collect_mol_votes(image_bytes: bytes) -> list[tuple[str, float]]:
    """mol ROI 以 PERMIT_VOTE_CONFIGS（6組）逐一掃描，回傳 (值, conf) 清單。"""
    entries: list[tuple[str, float]] = []
    for cfg in PERMIT_VOTE_CONFIGS:
        img = preprocess(image_bytes, {**cfg, "roi": ROI_REGIONS["mol"]})
        text, conf = ocr_with_conf(img, cfg.get("lang", TESS_LANG), build_tess_config(cfg))
        _, _, mol, _ = find_permits(text)
        if mol:
            entries.append((mol, conf))
    return entries


def scan_image_mol_only(docx_name: str, img_name: str, image_bytes: bytes) -> ScanResult:
    """small docx 專用：只掃 mol ROI。

    第一階段先跑 PERMIT_VOTE_CONFIGS 多數票；有多數票直接採用並標記 mol_from_vote=True。
    無多數票時退回 first-hit（SCAN_CONFIGS → FALLBACK_CONFIGS）。
    """
    stem = f"{Path(docx_name).stem}_{Path(img_name).stem}"
    result = _empty_result(docx_name, img_name, "small")
    raw_img = None
    roi_coords = ROI_REGIONS["mol"]

    # ── 第一階段：多數票（PERMIT_VOTE_CONFIGS 6組）────────────────────
    mol_entries = _collect_mol_votes(image_bytes)
    mol_vote = _majority_vote([v for v, _ in mol_entries], min_count=2)
    if mol_vote:
        winning_confs = [c for v, c in mol_entries if v == mol_vote]
        avg_conf = round(sum(winning_confs) / len(winning_confs), 1)
        result.mol          = mol_vote
        result.mol_conf     = avg_conf
        result.mol_from_vote = True
        result.hit_roi      = "mol"
        raw_img = auto_rotate(Image.open(BytesIO(image_bytes)).convert("RGB"))
        crop_dir = OUTPUT_DIR / "mol_crops"
        crop_dir.mkdir(parents=True, exist_ok=True)
        crop_path = crop_dir / f"{stem}.png"
        crop_roi(raw_img, roi_coords).save(crop_path)
        result.mol_crop = str(crop_path)
        logger.info(
            f"  ★ mol 多數票：{mol_vote}  avg_conf={avg_conf}"
            f"  ({len(winning_confs)}/{len(mol_entries)} 票)"
        )
        return result

    # ── 第二階段：first-hit（SCAN_CONFIGS → FALLBACK_CONFIGS）────────
    mol_found = False
    for config_list in (SCAN_CONFIGS, FALLBACK_CONFIGS):
        if mol_found:
            break
        for cfg in config_list:
            img = preprocess(image_bytes, {**cfg, "roi": roi_coords})
            lang = cfg.get("lang", TESS_LANG)
            text, conf = ocr_with_conf(img, lang, build_tess_config(cfg))
            logger.debug(f"  ✗ mol/{cfg['name']}  conf={conf:.0f}  text={text[:400]!r}")
            _, _, mol, mol_layer = find_permits(text)
            if not mol:
                continue
            result.mol       = mol
            result.mol_layer = mol_layer
            result.mol_conf  = conf
            result.hit_config = cfg["name"]
            result.hit_roi   = "mol"
            if raw_img is None:
                raw_img = auto_rotate(Image.open(BytesIO(image_bytes)).convert("RGB"))
            crop_dir = OUTPUT_DIR / "mol_crops"
            crop_dir.mkdir(parents=True, exist_ok=True)
            crop_path = crop_dir / f"{stem}.png"
            crop_roi(raw_img, roi_coords).save(crop_path)
            result.mol_crop = str(crop_path)
            if conf < CONF_KEY_IN:
                low_dir = OUTPUT_DIR / "low_conf_crops"
                low_dir.mkdir(parents=True, exist_ok=True)
                low_path = low_dir / f"{stem}_mol_conf{int(conf)}.png"
                crop_roi(raw_img, roi_coords).save(low_path)
                result.low_conf = str(low_path)
                logger.info(f"  ⚠ 低信心 {conf} < {CONF_KEY_IN}：{low_path.name}")
            logger.debug(f"  ★ mol/{cfg['name']}  conf={conf}  mol={mol!r}")
            mol_found = True
            break

    if not mol_found:
        result.manual_review = "mol 無值，需人工判斷"
        logger.info(f"  ⚠ {img_name}: mol 無值，標記人工審查")
    return result


def scan_image_large(docx_name: str, img_name: str, image_bytes: bytes,
                     roi_filter: str = "") -> ScanResult | None:
    """large docx 專用：掃 mol + permit。

    roi_filter：若非空字串，只掃指定的 ROI（對應 CLI --roi）。
    過去版本由 module-level ROI_FILTER 全域變數承載，現改為參數注入，
    讓函數行為僅依輸入決定，方便測試與並行化。
    """
    stem = f"{Path(docx_name).stem}_{Path(img_name).stem}"
    result = _empty_result(docx_name, img_name, "large")
    raw_img = None
    fields_found: set[str] = set()
    any_hit = False

    for roi_name, roi_coords in ROI_REGIONS.items():
        if roi_filter and roi_name != roi_filter:
            continue
        field = roi_field(roi_name)
        if field in fields_found:
            continue

        # ── mol ROI：先嘗試多數票（PERMIT_VOTE_CONFIGS 6組）────────────
        if roi_name == "mol":
            mol_entries = _collect_mol_votes(image_bytes)
            mol_vote = _majority_vote([v for v, _ in mol_entries], min_count=2)
            if mol_vote:
                winning_confs = [c for v, c in mol_entries if v == mol_vote]
                avg_conf = round(sum(winning_confs) / len(winning_confs), 1)
                result.mol           = mol_vote
                result.mol_conf      = avg_conf
                result.mol_from_vote = True
                result.hit_roi       = "mol"
                if raw_img is None:
                    raw_img = auto_rotate(Image.open(BytesIO(image_bytes)).convert("RGB"))
                crop_dir = OUTPUT_DIR / "mol_crops"
                crop_dir.mkdir(parents=True, exist_ok=True)
                crop_path = crop_dir / f"{stem}.png"
                crop_roi(raw_img, roi_coords).save(crop_path)
                result.mol_crop = str(crop_path)
                any_hit = True
                fields_found.add(field)
                logger.info(
                    f"  ★ mol 多數票：{mol_vote}  avg_conf={avg_conf}"
                    f"  ({len(winning_confs)}/{len(mol_entries)} 票)"
                )
                continue   # 跳過 mol ROI 的 first-hit

        roi_hit = False
        for config_list in (SCAN_CONFIGS, FALLBACK_CONFIGS):
            if roi_hit:
                break
            for cfg in config_list:
                img = preprocess(image_bytes, {**cfg, "roi": roi_coords})
                lang = cfg.get("lang", TESS_LANG)
                text, conf = ocr_with_conf(img, lang, build_tess_config(cfg))
                logger.debug(
                    f"  ✗ {roi_name}/{cfg['name']}  conf={conf:.0f}"
                    f"  text={text[:400]!r}"
                )
                is_permit_roi = roi_name.startswith("permit")
                permit_id_list = RE_PERMIT_ID_LIST_LOWER if roi_name == "permit_lower" else None
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
                roi_hit = True
                fields_found.add(field)
                if raw_img is None:
                    raw_img = auto_rotate(Image.open(BytesIO(image_bytes)).convert("RGB"))
                # 寫對應欄位的 crop（mol_crop 或 permit_crop）
                crop_attr = f"{field}_crop"   # "mol_crop" or "permit_crop"
                if not getattr(result, crop_attr, ""):
                    crop_dir = OUTPUT_DIR / f"{field}_crops"
                    crop_dir.mkdir(parents=True, exist_ok=True)
                    crop_path = crop_dir / f"{stem}.png"
                    crop_roi(raw_img, roi_coords).save(crop_path)
                    setattr(result, crop_attr, str(crop_path))
                if conf < CONF_KEY_IN and not result.low_conf:
                    low_dir = OUTPUT_DIR / "low_conf_crops"
                    low_dir.mkdir(parents=True, exist_ok=True)
                    low_path = low_dir / f"{stem}_{roi_name}_conf{int(conf)}.png"
                    crop_roi(raw_img, roi_coords).save(low_path)
                    result.low_conf = str(low_path)
                    logger.info(f"  ⚠ 低信心 {conf} < {CONF_KEY_IN}：{low_path.name}")
                logger.debug(f"  ★ {roi_name}/{cfg['name']}  conf={conf}  id={id_!r} mol={mol!r}")
                break

    if not any_hit:
        return None

    mol_val    = result.mol
    permit_val = result.id
    permit_vote_avg_conf = 0.0

    if not mol_val or not permit_val:
        upper_entries = _collect_permit_votes(image_bytes, ROI_REGIONS["permit_upper"])
        lower_entries = _collect_permit_votes(
            image_bytes, ROI_REGIONS["permit_lower"],
            permit_id_list=RE_PERMIT_ID_LIST_LOWER)
        all_entries = upper_entries + lower_entries
        all_values  = [v for v, _ in all_entries]
        permit_vote = _majority_vote(all_values, min_count=2)
        if permit_vote:
            winning_confs = [c for v, c in all_entries if v == permit_vote]
            permit_vote_avg_conf = round(sum(winning_confs) / len(winning_confs), 1)
        elif all_entries:
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

    return result


def _empty_result(docx_name: str, img_name: str, docx_class: str) -> ScanResult:
    """建立空白 ScanResult(維持函數名以減少呼叫端 churn,但回傳型別變了)。"""
    return ScanResult(
        source_docx=docx_name,
        image_name=img_name,
        docx_class=docx_class,
    )


# ═══════════════════════════════════════════════════════════════════════════
# ⑥ 決策層（decide_result / aggregate_small_docx）
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


# ═══════════════════════════════════════════════════════════════════════════
# ⑦ vision_submit（決定送 Vision 或直接 key-in）
# ═══════════════════════════════════════════════════════════════════════════

def _best_img_path(r: ScanResult) -> str:
    return r.permit_crop or r.mol_crop or ""


def _describe_vision_reason(r: ScanResult) -> str:
    """描述某列「為何」需送 Vision(vision_review=True),供 reason/rationale 使用。

    有些階段(如 aggregate_small_docx)只設了 vision_review 旗標卻沒寫 note,
    若直接把旗標名稱 "vision_review=True" 塞進 reason 欄,審查者看不出成因。
    這裡優先沿用既有 note;note 為空時,依 ScanResult 現況還原
    「哪個階段、為什麼」判定要送 Vision。
    """
    if r.note:
        return r.note
    conf = r.final_conf or max(r.mol_conf, r.id_conf)
    # small docx:唯一自動 key-in 的路徑是「mol 多數票且高信心」,其餘 winner 都送 Vision
    if r.docx_class == "small":
        if r.mol_from_vote:
            return (
                f"small:mol多數票但平均信心{r.mol_conf}未達直接key-in門檻"
                f"{CONF_MOL_VOTE_MIN},需人工確認"
            )
        return f"small:mol非多數票(單一判讀 conf={conf:.1f}),需人工複核"
    # large docx(正常情況 decide_result 已寫 note,以下為 note 遺漏時的防禦性描述)
    if r.cross_match:
        return f"large:mol與permit交叉比對吻合但信心低(conf={conf:.1f} ≤ {CONF_KEY_IN}),需人工確認"
    if r.mol and r.id:
        return f"large:mol與permit均有值但信心偏低(conf={conf:.1f} ≤ {CONF_KEY_IN}),需人工確認"
    if r.mol:
        return f"large:僅mol有值且信心偏低(conf={conf:.1f} ≤ {CONF_KEY_IN}),需人工確認"
    if r.id:
        return f"large:僅permit有值且信心偏低(conf={conf:.1f} ≤ {CONF_KEY_IN}),需人工確認"
    return f"OCR結果需人工確認(conf={conf:.1f})"


def process_large_vs(rows: list[ScanResult]) -> list[VisionQueueItem]:
    """large docx：回傳要送 Vision 的 VisionQueueItem 清單（含 direct_keyin 旗標）。

    過去用 dict + "permit部分命中無多數" 字串比對來識別部分命中；
    現改成檢查 r.status == ResultStatus.PERMIT_PARTIAL，note 文字可自由調整。
    """
    queue: list[VisionQueueItem] = []
    mol_rows    = [(r, r.mol, r.mol_conf) for r in rows if r.mol]
    permit_rows = [(r, r.id,  r.id_conf)  for r in rows if r.id]

    # 規則A：mol + permit 均有值
    if mol_rows and permit_rows:
        mol_values    = {v for _, v, _ in mol_rows}
        permit_values = {v for _, v, _ in permit_rows}
        common        = mol_values & permit_values
        if common:
            candidates = [(r, v, c) for r, v, c in mol_rows + permit_rows if v in common]
            best_row, best_val, best_conf = max(candidates, key=lambda x: x[2])
            if best_conf > CONF_KEY_IN_VS:
                # 高信心吻合 → 直接 key-in，不送 Vision
                queue.append(VisionQueueItem(
                    source_docx     = best_row.source_docx,
                    image_name      = best_row.image_name,
                    img_path        = _best_img_path(best_row),
                    candidate_value = best_val,
                    candidate_conf  = best_conf,
                    reason          = f"cross_match(file)_高信心 conf={best_conf}",
                    direct_keyin    = True,
                ))
            else:
                queue.append(VisionQueueItem(
                    source_docx     = best_row.source_docx,
                    image_name      = best_row.image_name,
                    img_path        = _best_img_path(best_row),
                    candidate_value = best_val,
                    candidate_conf  = best_conf,
                    reason          = f"cross_match(file)_低信心 conf={best_conf}",
                    direct_keyin    = False,
                ))
            return queue
        else:
            # mol ≠ permit：ROI 優先（permit ROI > mol ROI）+ 位置次要。
            # permit ROI 是目的明確的掃描區域，mol ROI 為輔助 cross-check，
            # 衝突時以 permit ROI 的最早圖為主，避免 mol 誤讀覆蓋正確的 permit 值。
            priority_pool = permit_rows if permit_rows else mol_rows
            earliest_row, earliest_val, earliest_conf = min(
                priority_pool, key=lambda x: _image_sort_key(x[0].image_name)
            )
            queue.append(VisionQueueItem(
                source_docx     = earliest_row.source_docx,
                image_name      = earliest_row.image_name,
                img_path        = _best_img_path(earliest_row),
                candidate_value = earliest_val,
                candidate_conf  = earliest_conf,
                reason          = (
                    f"mol≠permit衝突_permit優先_最早圖({earliest_row.image_name})"
                    f" conf={earliest_conf}"
                ),
                direct_keyin    = False,
            ))
            return queue

    # 規則A2：僅 permit（無 mol）且已有高信心多數票 → 直接 key-in。
    #          此時同 docx 內其餘「部分命中無多數」的圖不需再送 Vision;
    #          多張多數票取最早的圖（image 序號小者優先，如 image5 < image8）。
    if not mol_rows:
        vote_rows = [
            r for r in rows
            if r.id_from_vote and r.id and r.id_conf > CONF_VOTE_MIN
        ]
        if vote_rows:
            best_row = min(vote_rows, key=lambda r: _image_sort_key(r.image_name))
            queue.append(VisionQueueItem(
                source_docx     = best_row.source_docx,
                image_name      = best_row.image_name,
                img_path        = _best_img_path(best_row),
                candidate_value = best_row.id,
                candidate_conf  = best_row.id_conf,
                reason          = (
                    f"permit多數票_高信心_最早圖({best_row.image_name})"
                    f" conf={best_row.id_conf}"
                ),
                direct_keyin    = True,
            ))
            return queue

    # 規則B：permit 部分命中無多數（用 status 判斷，不再依賴 note 文字）
    partial_rows = [r for r in rows if r.status == ResultStatus.PERMIT_PARTIAL]
    for r in partial_rows:
        queue.append(VisionQueueItem(
            source_docx     = r.source_docx,
            image_name      = r.image_name,
            img_path        = r.permit_crop or _best_img_path(r),
            candidate_value = r.id or r.final_value,
            candidate_conf  = r.id_conf or r.final_conf,
            reason          = "permit部分命中無多數",
            direct_keyin    = False,
        ))

    # 規則C：其餘 vision_review=True
    handled = {r.image_name for r in partial_rows}
    for r in rows:
        if r.vision_review and r.image_name not in handled:
            queue.append(VisionQueueItem(
                source_docx     = r.source_docx,
                image_name      = r.image_name,
                img_path        = _best_img_path(r),
                candidate_value = r.final_value,
                candidate_conf  = r.final_conf,
                reason          = _describe_vision_reason(r),
                direct_keyin    = False,
            ))

    # 若完全沒有需 Vision 且也沒有直接 key-in → 取 final_conf 最高的列直接 key-in
    if not queue:
        rows_with_value = [r for r in rows if r.final_value]
        if rows_with_value:
            best_row = max(rows_with_value, key=lambda r: r.final_conf)
            queue.append(VisionQueueItem(
                source_docx     = best_row.source_docx,
                image_name      = best_row.image_name,
                img_path        = _best_img_path(best_row),
                candidate_value = best_row.final_value,
                candidate_conf  = best_row.final_conf,
                reason          = f"高信心直接key-in conf={best_row.final_conf}",
                direct_keyin    = True,
            ))

    return queue


def process_small_vs(rows: list[ScanResult]) -> list[VisionQueueItem]:
    """small docx：全部無命中 → 人工審查；否則依規則送件或直接 key-in。

    決策優先順序：
      1. 全 SMALL_NO_HIT → 回傳 []（上游會歸到 manual_review）
      2. mol 多數票且高信心（mol_from_vote=True, mol_conf > CONF_MOL_VOTE_MIN）
         → direct_keyin=True，不送 Vision
      3. vision_review=True → 送 Vision
      4. [C: defensive fallback] 有 final_value 但 (2)(3) 都沒觸發 → 強制送 Vision
         並記 warning。正常流程不該走進這條;走進來代表上游某個旗標漏設,
         避免該 docx 被靜默吃掉(這是過去 bug 的形態:scan 階段把 mol_from_vote
         設好但忘記寫進 CSV,讀回後兩條件都失敗、row 直接消失)。
    """
    all_no_match = all(r.status == ResultStatus.SMALL_NO_HIT for r in rows)
    if all_no_match:
        return []

    queue: list[VisionQueueItem] = []
    for r in rows:
        if r.mol_from_vote and r.mol_conf > CONF_MOL_VOTE_MIN and r.final_value:
            queue.append(VisionQueueItem(
                source_docx     = r.source_docx,
                image_name      = r.image_name,
                img_path        = _best_img_path(r),
                candidate_value = r.final_value,
                candidate_conf  = r.mol_conf,
                reason          = f"mol多數票_高信心 avg_conf={r.mol_conf}",
                direct_keyin    = True,
            ))
        elif r.vision_review:
            queue.append(VisionQueueItem(
                source_docx     = r.source_docx,
                image_name      = r.image_name,
                img_path        = _best_img_path(r),
                candidate_value = r.final_value,
                candidate_conf  = r.final_conf,
                reason          = _describe_vision_reason(r),
                direct_keyin    = False,
            ))
        # 其他 row(非 winner、無觸發旗標)刻意 skip:winner 已代表整個 docx。

    # ── C: defensive fallback ──────────────────────────────────────────────
    # queue 空但仍有 row 帶 final_value → 上游旗標漏設,撈一張當錨點送 Vision
    # 避免該 docx 在 build_vision_queue 既不進 keyin、也不進 vision、也不進
    # manual_review(status 不是 SMALL_NO_HIT) 而靜默消失。
    if not queue and any(r.final_value for r in rows):
        anchor = next(r for r in rows if r.final_value)
        statuses = [r.status.value for r in rows]
        logger.warning(
            f"  ⚠ process_small_vs fallback:{anchor.source_docx} "
            f"有 final_value={anchor.final_value!r} 但 mol_from_vote / vision_review "
            f"兩條件皆未觸發(statuses={statuses})→ 強制送 Vision"
        )
        queue.append(VisionQueueItem(
            source_docx     = anchor.source_docx,
            image_name      = anchor.image_name,
            img_path        = _best_img_path(anchor),
            candidate_value = anchor.final_value,
            candidate_conf  = anchor.final_conf,
            reason          = (
                f"[fallback] 有 final_value 但無觸發旗標 "
                f"(mol_from_vote={anchor.mol_from_vote}, "
                f"vision_review={anchor.vision_review}, "
                f"mol_conf={anchor.mol_conf})"
            ),
            direct_keyin    = False,   # 狀態未知 → 保守走 Vision,不直接 key-in
        ))

    return queue


def build_vision_queue(csv_path: Path) -> tuple[list[VisionQueueItem], list[dict], list[VisionQueueItem]]:
    """
    讀取 matches.csv，分成三類回傳：
      vision_items  : 需送 Google Vision 的 VisionQueueItem
      manual_review : 人工審查清單，每筆為 {"source_docx": ..., "reason": ...}
      keyin_items   : 直接 key-in 的 VisionQueueItem（direct_keyin=True）

    CSV 讀回後立刻反序列化成 ScanResult,讓下游函數有強型別保證。
    """
    groups: dict[str, list[ScanResult]] = defaultdict(list)
    with open(csv_path, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            sr = ScanResult.from_csv_row(row)
            groups[sr.source_docx].append(sr)

    vision_items: list[VisionQueueItem] = []
    keyin_items:  list[VisionQueueItem] = []
    manual_review: list[dict]           = []

    for docx_name, rows in sorted(groups.items()):
        docx_class = rows[0].docx_class
        if docx_class == "large":
            # large 全無命中 → 人工審查，不送 Vision
            if all(r.status == ResultStatus.LARGE_NO_HIT for r in rows):
                manual_review.append({
                    "source_docx": docx_name,
                    "reason": f"large docx：全 {len(rows)} 張圖掃描無許可證命中",
                })
                continue
            queue = process_large_vs(rows)
        else:
            queue = process_small_vs(rows)
            if not queue and all(r.status == ResultStatus.SMALL_NO_HIT for r in rows):
                manual_review.append({
                    "source_docx": docx_name,
                    "reason": f"small docx：mol ROI 全 {len(rows)} 張圖無命中",
                })
                continue

        for item in queue:
            if item.direct_keyin:
                keyin_items.append(item)
            else:
                vision_items.append(item)

    return vision_items, manual_review, keyin_items


# ═══════════════════════════════════════════════════════════════════════════
# ⑧ Google Vision
# ═══════════════════════════════════════════════════════════════════════════

@functools.lru_cache(maxsize=1)
def get_vision_client() -> gvision.ImageAnnotatorClient:
    """Vision client 在整個進程內共用一個實例（lazy + cached）。

    過去每次 run_google_vision() 都會重新讀取 service_account.json 並建立
    新 client，浪費 IO 與 TLS 連線。改成 lru_cache 後第一次建立、之後重用。
    """
    credentials = service_account.Credentials.from_service_account_file(
        SERVICE_ACCOUNT_JSON,
        scopes=["https://www.googleapis.com/auth/cloud-platform"],
    )
    return gvision.ImageAnnotatorClient(credentials=credentials)


@with_retry(
    max_attempts=4,
    initial_wait=2.0,
    retryable_exceptions=(
        ConnectionError, TimeoutError,
        gax_exceptions.GoogleAPICallError,
        gax_exceptions.RetryError,
    ),
    should_retry=_is_retryable_google_error,
)
def run_google_vision(img_path: str) -> str:
    """
    送圖給 Google Vision OCR，從回傳文字中嘗試萃取 4 位數許可號碼。
    回傳萃取到的值，或空字串。

    Vision API 屬讀取操作（idempotent），重試安全。連線錯誤或 5xx/429 會自動重試；
    4xx 其他錯誤（403/404 等）直接拋出，不會浪費時間重試。
    """
    client = get_vision_client()
    with open(img_path, "rb") as f:
        content = f.read()
    image    = gvision.Image(content=content)
    response = client.text_detection(image=image)
    if response.error.message:
        logger.error(f"Vision API 錯誤: {response.error.message}")
        return ""

    full_text = response.full_text_annotation.text if response.full_text_annotation else ""
    # 用既有 regex 從 Vision 全文萃取，需符合關鍵字錨點才接受
    for pattern_list in (RE_PERMIT_ID_LIST, RE_MOL_LIST):
        for p in pattern_list:
            m = p.search(full_text)
            if m:
                return m.group(1).strip()
    logger.debug(f"  Vision 無正則命中，原始文字：{full_text[:300]!r}")
    return ""


# ═══════════════════════════════════════════════════════════════════════════
# ⑧b Vision 結果交叉比對
# ═══════════════════════════════════════════════════════════════════════════
# 目的:Vision 自己讀到一個值不代表就對。本層用三組獨立證據交叉驗證:
#   1. 格式驗證 — Vision 值必須是 4 位數(基本 sanity check)
#   2. 雙引擎交叉 — Vision 值與 Tesseract 原本讀到的值是否一致
#   3. 已知清單 — 是否在歷史成功 key-in 過的值集合內(來自 upload_log.csv)
#
# 設計原則:保守。**只有當 Tesseract 與 Vision 完全一致時才自動 key-in**,
# 其餘所有情境(包括「Vision 值剛好在已知清單內」)一律標記人工審查。
# 已知清單僅用於補充 rationale,不會單獨作為自動採用的依據。

class VerificationLevel(str, Enum):
    CONFIRMED            = "confirmed"             # 雙引擎完全一致
    LIKELY_OCR_CONFUSION = "likely_ocr_confusion"  # 差 1 字元(視覺相近字常見)
    VISION_ONLY          = "vision_only"           # Tesseract 沒讀到,僅 Vision 有值
    DISAGREEMENT         = "disagreement"          # 兩引擎差異 2 字元以上
    FORMAT_INVALID       = "format_invalid"        # Vision 無值或值不符 4 位數格式


@dataclass
class VerifiedResult:
    """三層交叉比對後的決定。

    should_keyin:是否可自動寫入 Sheets;False 一律進人工審查批次。
    rationale  :給人看的決策理由(會寫到 Sheets 的 reason 欄)。
    """
    final_value: str
    level: VerificationLevel
    should_keyin: bool
    rationale: str
    in_known_list: bool = False


# 4 位數格式檢查:Vision 回傳的值必須是純 4 位數字才接受。
# 這是「最低門檻」:Vision 偶爾會把日期、頁碼、其他不相關文字解析為候選值,
# 此 regex 把這類雜訊濾掉。
# 4 位數格式檢查:Vision 回傳的值必須是純 4 位數字才接受,或 4 位數+破折號+1 位數(xxxx-x)。
# 這是「最低門檻」:Vision 偶爾會把日期、頁碼、其他不相關文字解析為候選值,
# 此 regex 把這類雜訊濾掉。接受格式:
#   1234     (4 位純數字)
#   1234-5   (4 位數+破折號+1 位數)
_RE_PERMIT_VALUE = re.compile(r"^\d{4}(?:-\d)?$")


def _is_valid_permit_format(value: str) -> bool:
    """Vision 值是否符合 4 位數 permit/mol 格式。"""
    return bool(value and _RE_PERMIT_VALUE.match(value))


def _edit_distance(a: str, b: str) -> int:
    """計算 a 與 b 的 Levenshtein 編輯距離。

    用於辨識「視覺相近字」混淆:Vision 跟 Tesseract 對同一張圖讀到的值若僅差 1 字,
    高機率是 0/O、1/l、5/S 等 OCR 常見混淆,而非完全不同的兩個 permit。
    """
    if len(a) < len(b):
        a, b = b, a
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a):
        curr = [i + 1]
        for j, cb in enumerate(b):
            curr.append(min(
                prev[j + 1] + 1,         # 刪除 a[i]
                curr[j] + 1,             # 插入 b[j]
                prev[j] + (ca != cb),    # 替換(相同時 +0)
            ))
        prev = curr
    return prev[-1]


def load_known_permits_from_log(log_path: Path | None = None) -> set[str]:
    """從 upload_log.csv 累積歷史成功值,建立「軟版已知清單」。

    用途:作為交叉比對的補充證據。歷史上出現過的 permit id 比從未出現過的可信度高。
    與決策直接綁定的是 should_keyin 旗標,本清單僅影響 rationale 文字,
    用來告訴審查者「這個值在歷史紀錄裡出現過,可優先處理」。

    回傳:已知 permit value 的集合;檔案不存在或為空時回傳空集合。
    """
    if log_path is None:
        log_path = UPLOAD_LOG_PATH
    if not log_path.exists():
        return set()
    known: set[str] = set()
    try:
        with open(log_path, encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                v = (row.get("final_value") or "").strip()
                if v:
                    known.add(v)
    except Exception as e:
        logger.warning(f"讀取 upload_log.csv 失敗(將視為空清單):{e!r}")
    return known


def verify_vision_result(
    vision_value: str,
    tesseract_candidate: str,
    known_permits: set[str] | None = None,
    *,
    tesseract_conf: float = 0.0,
) -> VerifiedResult:
    """對 Vision 的結果做三層交叉比對。

    決策表(保守版,僅 CONFIRMED 自動 key-in):
      Level                   | Action                | final_value 來源
      ─────────────────────── | ───────────────────── | ─────────────────
      CONFIRMED               | should_keyin=True     | vision_value(等於 tesseract)
      LIKELY_OCR_CONFUSION    | should_keyin=False    | 視 tesseract_conf 而定 ↓
      VISION_ONLY             | should_keyin=False    | vision_value
      DISAGREEMENT            | should_keyin=False    | vision_value
      FORMAT_INVALID          | should_keyin=False    | tesseract_candidate(若有)或空

    LIKELY_OCR_CONFUSION 的 final_value 選擇:
      tesseract_conf >  50:採用 Tesseract 值(Tesseract 信心夠,Vision 可能誤判)
      tesseract_conf <= 50:採用 Vision 值(Tesseract 信心不足,以 Vision 為主)
      不論哪邊勝出,should_keyin 都是 False — 兩引擎不一致仍需人工確認。

    參數:
      vision_value         : Vision OCR 萃取的值。空字串代表 Vision 沒有正則命中。
      tesseract_candidate  : 該圖在 Tesseract 階段讀到的最佳候選(可為空字串)。
      known_permits        : 已知合法 permit 集合;僅影響 rationale,不影響 should_keyin。
      tesseract_conf       : 該 tesseract_candidate 對應的 Tesseract 信心(0-100);
                             僅在 LIKELY_OCR_CONFUSION 分支用到。預設 0.0 → 採 Vision 值。

    回傳:VerifiedResult,呼叫端據此決定該列進 key-in 批次或人工審查批次。
    """
    in_known_list = bool(known_permits and vision_value in known_permits)

    # ── 第一層:格式 ──────────────────────────────────────────────────
    if not _is_valid_permit_format(vision_value):
        return VerifiedResult(
            final_value=vision_value or tesseract_candidate,
            level=VerificationLevel.FORMAT_INVALID,
            should_keyin=False,
            rationale=(
                "Vision 無正則命中,需人工審查"
                if not vision_value
                else f"Vision 值 {vision_value!r} 不符合 4 位數格式"
            ),
            in_known_list=False,
        )

    # ── 第二層:跟 Tesseract 比對 ────────────────────────────────────
    # VISION_ONLY:Tesseract 沒讀到值,只有 Vision 給出候選
    if not tesseract_candidate:
        rationale = (
            f"僅 Vision 讀到 {vision_value!r}(值在已知清單內,可優先處理),需人工確認"
            if in_known_list
            else f"僅 Vision 讀到 {vision_value!r},不在已知清單,需人工審查"
        )
        return VerifiedResult(
            final_value=vision_value,
            level=VerificationLevel.VISION_ONLY,
            should_keyin=False,
            rationale=rationale,
            in_known_list=in_known_list,
        )

    # 兩引擎完全一致 → 唯一自動 key-in 的路徑
    if vision_value == tesseract_candidate:
        rationale = f"Tesseract 與 Vision 雙引擎一致:{vision_value!r}"
        if in_known_list:
            rationale += "(且在已知清單內)"
        return VerifiedResult(
            final_value=vision_value,
            level=VerificationLevel.CONFIRMED,
            should_keyin=True,
            rationale=rationale,
            in_known_list=in_known_list,
        )

    distance = _edit_distance(vision_value, tesseract_candidate)

    # 差 1 字元 → 高機率 OCR 視覺相近字混淆(如 0/O、1/l、6/8)。
    # 兩引擎僅差 1 字時無法可靠判斷誰對(Tesseract 高信心也可能讀錯,
    # Vision 的「修正」也可能是錯誤的修正),因此不自動填入任何值,
    # 一律標人工審查、由人工輸入;rationale 列出兩候選與 Tesseract 信心供參考。
    if distance == 1:
        rationale = (
            f"Tesseract={tesseract_candidate!r}(conf={tesseract_conf:.1f}) vs "
            f"Vision={vision_value!r} 差 1 字元(疑似 OCR 視覺相近字混淆);"
            f"不自動填值,需人工輸入"
        )
        if in_known_list:
            rationale += ";Vision 候選在已知清單內"
        return VerifiedResult(
            final_value="",
            level=VerificationLevel.LIKELY_OCR_CONFUSION,
            should_keyin=False,
            rationale=rationale,
            in_known_list=in_known_list,
        )

    # 差 2 字元以上 → 兩引擎不一致,最可疑
    return VerifiedResult(
        final_value=vision_value,
        level=VerificationLevel.DISAGREEMENT,
        should_keyin=False,
        rationale=(
            f"Tesseract={tesseract_candidate!r} 與 Vision={vision_value!r} "
            f"差異 {distance} 字元,兩引擎不一致,需人工確認"
        ),
        in_known_list=in_known_list,
    )


# ═══════════════════════════════════════════════════════════════════════════
# ⑨ Google Sheets key-in
# ═══════════════════════════════════════════════════════════════════════════

UPLOAD_LOG_PATH = OUTPUT_DIR / "upload_log.csv"


@functools.lru_cache(maxsize=1)
def get_sheets_service():
    """Sheets service 在整個進程內共用（lazy + cached）。"""
    creds = service_account.Credentials.from_service_account_file(
        SERVICE_ACCOUNT_JSON, scopes=SHEETS_SCOPES
    )
    return build("sheets", "v4", credentials=creds)


def _load_upload_log() -> set[tuple[str, str]]:
    """讀取本地上傳日誌，回傳已寫入過的 (source_docx, status) 集合。

    用途：若上一次 pipeline 跑到一半失敗、或意外重跑，可避免相同資料重複寫入 Sheets。
    這是「Sheets 沒有原生 idempotency」的本地補強；不是百分百可靠
    （例如 API 已寫入但回應遺失的極端情境仍可能漏記）。

    向後相容:過去版本曾用 SheetStatus.VISION 寫入 Vision-CONFIRMED 列。
    本版改為把 Vision-CONFIRMED 視為 KEYED_IN(自動寫入的一種)。
    為了讓舊 log 仍能正確 dedup,讀取時把 "vision" 正規化為 "keyed_in"。
    """
    if not UPLOAD_LOG_PATH.exists():
        return set()
    done: set[tuple[str, str]] = set()
    try:
        with open(UPLOAD_LOG_PATH, encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                docx = r.get("source_docx", "")
                status = r.get("status", "")
                # 向後相容:舊版的 "vision" 等同新版的 "keyed_in"
                if status == SheetStatus.VISION.value:
                    status = SheetStatus.KEYED_IN.value
                done.add((docx, status))
    except Exception as e:
        logger.warning(f"讀取上傳日誌失敗（將視為空集合）：{e!r}")
    return done


def _append_upload_log(rows: list[list[str]]) -> None:
    """成功寫入 Sheets 之後，把已上傳的列追加到本地 upload_log.csv 作為審計與重跑保險。"""
    UPLOAD_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    write_header = not UPLOAD_LOG_PATH.exists()
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(UPLOAD_LOG_PATH, "a", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        if write_header:
            w.writerow(["timestamp", "source_docx", "final_value", "status", "reason"])
        for row in rows:
            w.writerow([ts, *row])


@with_retry(
    max_attempts=5,
    initial_wait=2.0,
    retryable_exceptions=(HttpError, ConnectionError, TimeoutError,
                          gax_exceptions.GoogleAPICallError),
    should_retry=_is_retryable_google_error,
)
def _sheets_append_atomic(values: list[list[str]]) -> None:
    """單次 atomic append。values 為已組裝好的二維 list。

    Google Sheets 的 values.append 本身在 API 層就是 atomic：成功就全寫入，
    失敗就完全不寫。配合 with_retry 處理連線抖動 / 429 / 5xx。
    """
    service = get_sheets_service()
    body = {"values": values}
    service.spreadsheets().values().append(
        spreadsheetId=SPREADSHEET_ID,
        range=f"{SHEET_NAME}!A1",
        valueInputOption="RAW",
        insertDataOption="INSERT_ROWS",
        body=body,
    ).execute()


# 一筆要寫入 Sheets 的資料原型，可由 dict 或 VisionQueueItem 轉成。
def _row_to_sheet_values(r, status: SheetStatus) -> list[str]:
    """把一筆紀錄轉成 Sheets 的一列；支援 dict（manual 用）與 VisionQueueItem。"""
    if isinstance(r, VisionQueueItem):
        return [r.source_docx, r.candidate_value, status.value, r.reason]
    # dict 形式（main 內動態組裝的 vision_keyin / manual_rows）
    return [
        r.get("source_docx", ""),
        r.get("final_value") or r.get("candidate_value", ""),
        status.value,
        r.get("reason") or r.get("note", ""),
    ]


def write_sheets_batched(batches: list[tuple[list, SheetStatus]]) -> int:
    """
    一次性把多批資料 atomic 寫入 Google Sheets。

    參數：
      batches：[(rows, status), ...]
        rows 可以是 dict list 或 VisionQueueItem list；
        status 為 SheetStatus 列舉值（keyed-in / vision / manual_review）。

    特性：
      1. 合併成一次 API 呼叫 → 中途網路失敗時不會出現「半成功」狀態。
      2. 5xx / 429 / 連線錯誤自動退避重試；4xx 直接拋出。

    回傳實際寫入 Sheets 的列數。
    """
    values: list[list[str]] = []

    for rows, status in batches:
        for r in rows:
            values.append(_row_to_sheet_values(r, status))

    if not values:
        logger.info("  （此次無新資料需寫入 Sheets）")
        return 0

    try:
        _sheets_append_atomic(values)
    except Exception as e:
        # batch atomic 的好處：失敗 = 完全沒寫入，可直接重跑 pipeline，不會有部份污染。
        # 注意極端例外：API 已寫入但回應遺失 → 重跑會產生重複；可手動以 upload_log.csv 或
        # Sheets 內容比對排查。
        logger.error(
            f"❌ Sheets 批次寫入失敗（{len(values)} 筆未寫入）：{e!r}\n"
            f"   由於是單次 atomic append，Sheets 端應未寫入任何資料；"
            f"修正後可直接重跑 pipeline。"
        )
        raise

    logger.info(f"  ✔ 已 atomic 寫入 Google Sheets {len(values)} 筆（含多種 status）")
    return len(values)


# 保留舊版單批 API 作為向後相容（內部呼叫新版批次函數）
def keyin_to_sheets(rows: list, status: str = "keyed-in"):
    """
    舊介面：將一批資料寫入 Google Sheets。

    內部已切換為 write_sheets_batched，原本三次獨立呼叫的場景請改用 write_sheets_batched
    一次傳入所有 batch，以獲得 atomic 保證。此函數保留以避免外部呼叫者壞掉。
    """
    if not rows:
        return
    # 容錯：把字串 status 對應回 enum
    try:
        sheet_status = SheetStatus(status)
    except ValueError:
        sheet_status = SheetStatus.KEYED_IN
        logger.warning(f"未知的 status={status!r}，預設視為 keyed-in")
    write_sheets_batched([(rows, sheet_status)])


# ═══════════════════════════════════════════════════════════════════════════
# ⑩ 主流程
# ═══════════════════════════════════════════════════════════════════════════

def run_scan(docx_files: list[Path], image_filter: str = "",
             roi_filter: str = "") -> Path:
    """執行 Tesseract 掃描，輸出 matches.csv，回傳 csv 路徑。

    image_filter：若非空，配合單一 docx 只處理該檔名的圖（對應 --image）。
    roi_filter  ：若非空，只掃指定的 ROI（對應 --roi）。
    """
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    csv_path = OUTPUT_DIR / "matches.csv"

    total = hits = upper_hits = lower_hits = 0
    t0 = time.time()

    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()

        for docx_path in docx_files:
            images = extract_images_from_docx(docx_path)
            # --image 過濾
            if image_filter:
                all_names = [n for n, _ in images]
                images = [(n, b) for n, b in images if n == image_filter]
                if not images:
                    logger.error(
                        f"找不到圖檔 {image_filter!r}（{docx_path.name} 內含:{all_names}）"
                    )
                    continue

            docx_class = classify_by_count(len(images))
            logger.debug(f"  {docx_path.name}: {len(images)} 張圖 → {docx_class}")

            small_bucket: list[ScanResult] = []
            large_hit_count = 0

            for img_name, img_bytes in images:
                total += 1
                if docx_class == "small":
                    result = scan_image_mol_only(docx_path.name, img_name, img_bytes)
                    small_bucket.append(result)
                else:
                    result = scan_image_large(
                        docx_path.name, img_name, img_bytes,
                        roi_filter=roi_filter,
                    )
                    if result:
                        decide_result(result)
                        hits += 1
                        large_hit_count += 1
                        if result.hit_roi == "permit_upper":
                            upper_hits += 1
                        elif result.hit_roi == "permit_lower":
                            lower_hits += 1
                        writer.writerow(result.to_csv_row())
                        logger.info(
                            f"★ {docx_path.name}/{img_name}"
                            f"  [{docx_class}|{result.hit_roi}]"
                            f"  mol={result.mol!r} id={result.id!r}"
                            f"  final={result.final_value!r}"
                            f"  vision={result.vision_review!r}"
                        )
                    else:
                        logger.debug(f"  {docx_path.name} / {img_name}  未命中")

            # large 全無命中：寫一列佔位記錄,供後續人工審查
            if docx_class == "large" and large_hit_count == 0:
                first_img_name = images[0][0] if images else ""
                fallback = _empty_result(docx_path.name, first_img_name, "large")
                fallback.note          = "large:全無命中"
                fallback.manual_review = "Y"
                fallback.status        = ResultStatus.LARGE_NO_HIT
                writer.writerow(fallback.to_csv_row())
                logger.info(f"  ⚠ {docx_path.name} large全無命中 → 標記人工審查")

            if docx_class == "small" and small_bucket:
                to_write = aggregate_small_docx(small_bucket)
                for result in to_write:
                    if result.mol or result.id:
                        hits += 1
                    writer.writerow(result.to_csv_row())
                    logger.info(
                        f"★ {docx_path.name} / {result.image_name}"
                        f"  [small|{result.hit_roi}]"
                        f"  mol={result.mol!r}"
                        f"  final={result.final_value!r}"
                        f"  vision={result.vision_review!r}"
                    )

    elapsed = round(time.time() - t0, 1)
    logger.info(f"掃描完成：{total} 張圖，命中 {hits} 張，耗時 {elapsed}s")
    if upper_hits + lower_hits > 0:
        logger.info(f"  permit 上半命中：{upper_hits} 張 / 下半命中：{lower_hits} 張")
    return csv_path


def main(opts: argparse.Namespace) -> None:
    """主流程。接收已解析的 CLI options，不再依賴 module-level 全域變數。"""
    # ── 決定要掃哪些 docx ──────────────────────────────────────────────────
    if opts.file:
        target = Path(opts.file)
        if not target.exists():
            logger.error(f"找不到檔案：{target.resolve()}")
            return
        docx_files = [target]
        logging.getLogger().setLevel(logging.DEBUG)
        logger.debug(f"單檔 DEBUG 模式：{target.name}")
    else:
        docx_files = sorted(INPUT_DIR.glob("*.docx"))
        if not docx_files:
            logger.error(f"找不到 .docx：{INPUT_DIR.resolve()}")
            return

    # ── 步驟 1：prefilter（已在 scan 內呼叫 classify_by_count，此處記錄摘要）
    logger.info("── 步驟 1：prefilter 分類 ──")
    for dp in docx_files:
        imgs = extract_images_from_docx(dp)
        logger.info(f"  {dp.name}: {len(imgs)} 張 → {classify_by_count(len(imgs))}")

    # ── 步驟 2：scan → matches.csv ─────────────────────────────────────────
    logger.info("── 步驟 2：Tesseract 掃描 ──")
    csv_path = run_scan(docx_files, image_filter=opts.image, roi_filter=opts.roi)
    logger.info(f"  matches.csv 已輸出：{csv_path.resolve()}")

    # ── 步驟 3：vision_submit 分流 ─────────────────────────────────────────
    logger.info("── 步驟 3：vision_submit 分流 ──")
    vision_items, manual_review, keyin_items = build_vision_queue(csv_path)
    logger.info(f"  直接 key-in：{len(keyin_items)} 筆")
    logger.info(f"  送 Vision  ：{len(vision_items)} 筆")
    logger.info(f"  人工審查   ：{len(manual_review)} 件")

    # ── 步驟 4a：送 Google Vision + 三層交叉比對 ──────────────────────────
    # 雙引擎(Tesseract + Vision)完全一致才自動 key-in;其餘進人工審查批次。
    # 即便 Vision 值在已知清單內,只要與 Tesseract 不完全一致,仍標為人工審查,
    # 避免任何單一 OCR 引擎的判讀直接決定資料寫入。
    #
    # 額外規則(LIKELY_OCR_CONFUSION):兩引擎差 1 字元時,不自動填任何值,
    # 一律標人工審查、由人工輸入(兩候選與信心寫進 reason 供參考)。
    logger.info("── 步驟 4a：Google Vision 判讀 + 交叉比對 ──")
    known_permits = load_known_permits_from_log()
    logger.info(f"  載入已知清單(來自 upload_log.csv):{len(known_permits)} 筆")

    vision_auto_keyin: list[dict] = []     # 雙引擎一致(CONFIRMED) → 自動寫
    vision_review_rows: list[dict] = []    # 任何不一致 → 人工審查
    for item in vision_items:
        if not item.img_path or not Path(item.img_path).exists():
            logger.warning(f"  ⚠ 找不到圖檔：{item.img_path}（{item.source_docx}）")
            continue
        vision_value = run_google_vision(item.img_path)
        verified = verify_vision_result(
            vision_value=vision_value,
            tesseract_candidate=item.candidate_value,
            known_permits=known_permits,
            tesseract_conf=item.candidate_conf,
        )
        logger.info(
            f"  Vision → {item.source_docx} / {item.image_name}"
            f"  candidate={item.candidate_value!r}(conf={item.candidate_conf:.1f})"
            f"  vision={vision_value!r}"
            f"  → {verified.level.value}(keyin={verified.should_keyin})"
        )
        row = {
            "source_docx":     item.source_docx,
            "candidate_value": verified.final_value,
            "reason":          f"[送Vision原因] {item.reason} ／ [Vision結果] {verified.rationale}",
        }
        if verified.should_keyin:
            vision_auto_keyin.append(row)
        else:
            vision_review_rows.append(row)

    # ── 步驟 4b：統一寫入 Google Sheets ────────────────────────────────────
    # 簡化為兩個 status,理由欄(reason)負責歸因:
    #   KEYED_IN      = Tesseract 直接 key-in(高信心)  +  Vision-CONFIRMED(雙引擎一致)
    #   MANUAL_REVIEW = Vision 交叉比對失敗(各種不一致)  +  全無命中
    #
    # 設計動機:自動 vs 人工的二分讓使用者只需依 status 篩選即可知道「哪些要看」。
    # 每列來源(Tesseract 直接 / Vision 確認 / 差 1 字元 / 僅 Vision / 無命中)
    # 都寫進 reason 欄,審查時看 reason 就知道 context。
    logger.info("── 步驟 4b：合併批次寫入 Google Sheets（atomic）──")
    auto_keyin_batch = keyin_items + vision_auto_keyin   # VisionQueueItem + dict 混合,_row_to_sheet_values 兼容
    review_batch = vision_review_rows + [
        {"source_docx": d["source_docx"], "candidate_value": "", "reason": d["reason"]}
        for d in manual_review
    ]
    written = write_sheets_batched([
        (auto_keyin_batch, SheetStatus.KEYED_IN),
        (review_batch,     SheetStatus.MANUAL_REVIEW),
    ])
    logger.info(
        f"  本次寫入彙總:auto_keyin={len(auto_keyin_batch)} 筆"
        f"(其中 Tesseract 直接 {len(keyin_items)} 筆 + Vision 確認 {len(vision_auto_keyin)} 筆) "
        f"/ manual_review={len(review_batch)} 筆"
        f"(其中 Vision 交叉比對失敗 {len(vision_review_rows)} 筆 + 全無命中 {len(manual_review)} 件)"
        f"(實際寫入 Sheets:{written} 筆)"
    )

    # ── 步驟 4c：列出人工審查清單，方便人類確認 ────────────────────────────
    if review_batch:
        logger.info(f"── 人工審查清單(共 {len(review_batch)} 件,reason 欄已標註原因)：")
        for row in review_batch:
            logger.info(f"    {row['source_docx']}: {row['reason']}")

    logger.info("── 全部完成 ──")


def _parse_args() -> argparse.Namespace:
    """CLI 參數解析；獨立成函數方便外部（如測試）注入。"""
    parser = argparse.ArgumentParser(description="OCR pipeline: prefilter→scan→vision→Sheets")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING"])
    parser.add_argument("--file",  "-f", metavar="DOCX",  default="",
                        help="只處理指定的單一 .docx")
    parser.add_argument("--image", "-i", metavar="IMAGE", default="",
                        help="配合 --file，只處理 docx 內指定的圖檔名")
    parser.add_argument("--roi",   "-r", metavar="ROI",   default="",
                        help=f"只掃指定 ROI：{list(ROI_REGIONS.keys())}")
    opts = parser.parse_args()
    # 驗證 --roi（過去在 __main__ 內驗證後寫到 globals()，現於此處驗證後由 main 直接讀 opts.roi）
    if opts.roi:
        valid_rois = list(ROI_REGIONS.keys())
        if opts.roi not in valid_rois:
            parser.error(f"--roi 必須為 {valid_rois}，收到 {opts.roi!r}")
    return opts


if __name__ == "__main__":
    opts = _parse_args()
    logging.getLogger().setLevel(getattr(logging, opts.log_level))
    main(opts)
