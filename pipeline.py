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
from collections import Counter, defaultdict
from io import BytesIO
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter, ImageOps
import pytesseract
from pytesseract import Output

# Google Vision
from google.cloud import vision as gvision

# Google Sheets
from googleapiclient.discovery import build
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
CONF_KEY_IN   = 55   # 高於此值直接 key-in；低於則標記送 Google Vision
CONF_VOTE_MIN = 45   # permit 多數票平均信心低於此值才送 Vision

# ─── prefilter ────────────────────────────────────────────────────────────
SMALL_DOCX_THRESHOLD = 3   # 圖片數 ≤ 此值 → "small"

# ─── ROI ──────────────────────────────────────────────────────────────────
ROI_REGIONS = {
    "mol":           (0.05, 0.04, 0.40, 0.25),
    "permit_upper":  (0.40, 0.05, 1.00, 0.55),
    "permit_lower":  (0.40, 0.45, 1.00, 0.95),
}
ROI_FILTER: str = ""   # 由 CLI --roi 設定

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
# ② prefilter
# ═══════════════════════════════════════════════════════════════════════════

def classify_by_count(image_count: int) -> str:
    """依圖片數量回傳 'small' 或 'large'。"""
    return "small" if image_count <= SMALL_DOCX_THRESHOLD else "large"


# ═══════════════════════════════════════════════════════════════════════════
# ③ 正則表達式
# ═══════════════════════════════════════════════════════════════════════════

RE_PERMIT_ID_LIST = [
    re.compile(r"No\.?\s*i[zjl1]in\s*[:::﹕]\s*(?:NO\.)?(\d{4})(?!\d)", re.IGNORECASE),
    re.compile(r"[Nn]\w{0,5}n\s*[:::﹕]\s*(?:NO\.)?(\d{4})(?!\d)",       re.IGNORECASE),
    re.compile(r"\bi[zjl1]in\s*[:::﹕]\s*(?:NO\.)?(\d{4})(?!\d)",         re.IGNORECASE),
    re.compile(r"\bNO\.(\d{4})(?!\d)",                                    re.IGNORECASE),
]

RE_PERMIT_ID_LIST_LOWER = [
    re.compile(r"No\.?\s*i[zjl1]in\s*[:::﹕]?\s*(?:NO\.)?(\d{4})(?!\d)", re.IGNORECASE),
    re.compile(r"[Nn]\w{0,5}n\s*[:::﹕]\s*(?:NO\.)?(\d{4})(?!\d)",        re.IGNORECASE),
    re.compile(r"\bi[zjl1]in\s*[:::﹕]\s*(?:NO\.)?(\d{4})(?!\d)",          re.IGNORECASE),
    re.compile(r"\bNO\.(\d{4})(?!\d)",                                     re.IGNORECASE),
]

RE_MOL_LIST = [
    re.compile(r"Agency'?s?\s+M[O0]L?\s+(?:L[i1I])?[i]?cense\s+Num\s*ber\s*[:::]\s*(\d{4})(?!\d)", re.IGNORECASE),
    re.compile(r"A\w{3,6}'?s?\s+M[O0]L?\s+(?:L[i1I])?[i]?cense\s+Num\s*ber\s*[:::]\s*(\d{4})(?!\d)", re.IGNORECASE),
    re.compile(r"(?:Num\s*ber|umber|[Nn]amber)\s*[:::]\s*(\d{4})(?!\d)", re.IGNORECASE),
    re.compile(r"M[O0]L\D{0,30}(\d{4})(?!\d)", re.IGNORECASE),
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

def extract_images_from_docx(docx_path: Path) -> list[tuple[str, bytes]]:
    images = []
    with zipfile.ZipFile(docx_path, "r") as z:
        for fname in sorted(z.namelist()):
            if fname.startswith("word/media/") and Path(fname).suffix.lower() in IMAGE_EXTENSIONS:
                images.append((Path(fname).name, z.read(fname)))
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


def scan_image_mol_only(docx_name: str, img_name: str, image_bytes: bytes) -> dict:
    """small docx 專用：只掃 mol ROI。"""
    stem = f"{Path(docx_name).stem}_{Path(img_name).stem}"
    result = _empty_result(docx_name, img_name, "small")
    raw_img = None
    roi_coords = ROI_REGIONS["mol"]
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
            result.update({"mol": mol, "mol_layer": mol_layer, "mol_conf": conf,
                            "hit_config": cfg["name"], "hit_roi": "mol"})
            if raw_img is None:
                raw_img = auto_rotate(Image.open(BytesIO(image_bytes)).convert("RGB"))
            crop_dir = OUTPUT_DIR / "mol_crops"
            crop_dir.mkdir(parents=True, exist_ok=True)
            crop_path = crop_dir / f"{stem}.png"
            crop_roi(raw_img, roi_coords).save(crop_path)
            result["mol_crop"] = str(crop_path)
            if conf < CONF_KEY_IN:
                low_dir = OUTPUT_DIR / "low_conf_crops"
                low_dir.mkdir(parents=True, exist_ok=True)
                low_path = low_dir / f"{stem}_mol_conf{int(conf)}.png"
                crop_roi(raw_img, roi_coords).save(low_path)
                result["low_conf"] = str(low_path)
                logger.info(f"  ⚠ 低信心 {conf} < {CONF_KEY_IN}：{low_path.name}")
            logger.debug(f"  ★ mol/{cfg['name']}  conf={conf}  mol={mol!r}")
            mol_found = True
            break

    if not mol_found:
        result["manual_review"] = "mol 無值，需人工判斷"
        logger.info(f"  ⚠ {img_name}: mol 無值，標記人工審查")
    return result


def scan_image_large(docx_name: str, img_name: str, image_bytes: bytes) -> dict | None:
    """large docx 專用：掃 mol + permit。"""
    stem = f"{Path(docx_name).stem}_{Path(img_name).stem}"
    result = _empty_result(docx_name, img_name, "large")
    raw_img = None
    fields_found: set[str] = set()
    any_hit = False

    for roi_name, roi_coords in ROI_REGIONS.items():
        if ROI_FILTER and roi_name != ROI_FILTER:
            continue
        field = roi_field(roi_name)
        if field in fields_found:
            continue
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
                    result["id"]       = id_
                    result["id_layer"] = id_layer
                    if not result["id_conf"]:
                        result["id_conf"] = conf
                if mol:
                    result["mol"]       = mol
                    result["mol_layer"] = mol_layer
                    if not result["mol_conf"]:
                        result["mol_conf"] = conf
                if not result["hit_config"]:
                    result["hit_config"] = cfg["name"]
                    result["hit_roi"]    = roi_name
                any_hit = True
                roi_hit = True
                fields_found.add(field)
                if raw_img is None:
                    raw_img = auto_rotate(Image.open(BytesIO(image_bytes)).convert("RGB"))
                crop_key = f"{field}_crop"
                if not result.get(crop_key, ""):
                    crop_dir = OUTPUT_DIR / f"{field}_crops"
                    crop_dir.mkdir(parents=True, exist_ok=True)
                    crop_path = crop_dir / f"{stem}.png"
                    crop_roi(raw_img, roi_coords).save(crop_path)
                    result[crop_key] = str(crop_path)
                if conf < CONF_KEY_IN and not result["low_conf"]:
                    low_dir = OUTPUT_DIR / "low_conf_crops"
                    low_dir.mkdir(parents=True, exist_ok=True)
                    low_path = low_dir / f"{stem}_{roi_name}_conf{int(conf)}.png"
                    crop_roi(raw_img, roi_coords).save(low_path)
                    result["low_conf"] = str(low_path)
                    logger.info(f"  ⚠ 低信心 {conf} < {CONF_KEY_IN}：{low_path.name}")
                logger.debug(f"  ★ {roi_name}/{cfg['name']}  conf={conf}  id={id_!r} mol={mol!r}")
                break

    if not any_hit:
        return None

    mol_val    = result["mol"]
    permit_val = result["id"]
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
            result["id"]      = best[0]
            result["id_conf"] = best[1]
            result["_permit_partial_hit"] = True
            logger.info(
                f"  ⚠ permit 有部分命中({len(all_entries)}筆)但無多數"
                f"  → 候選值={best[0]!r} conf={best[1]:.0f}，標記送 Vision"
                f"  all={[v for v,_ in all_entries]}"
            )
    else:
        permit_vote = permit_val

    if mol_val and permit_vote and mol_val == permit_vote:
        result["cross_match"] = "✓"
        logger.info(f"  ✓ 交叉比對吻合：{mol_val}")
    elif not mol_val and permit_vote:
        result["id"]            = permit_vote
        result["id_layer"]      = 0
        result["id_conf"]       = permit_vote_avg_conf
        result["_id_from_vote"] = True
        logger.info(f"  → mol 無值，permit 多數票：{permit_vote}  avg_conf={permit_vote_avg_conf}")

    return result


def _empty_result(docx_name: str, img_name: str, docx_class: str) -> dict:
    return {
        "source_docx": docx_name, "image_name": img_name, "docx_class": docx_class,
        "mol": "", "mol_layer": "", "mol_conf": "",
        "id": "", "id_layer": "", "id_conf": "",
        "cross_match": "", "final_value": "", "final_conf": "",
        "vision_review": "", "note": "", "low_conf": "",
        "hit_config": "", "hit_roi": "", "mol_crop": "", "permit_crop": "",
        "manual_review": "",
    }


# ═══════════════════════════════════════════════════════════════════════════
# ⑥ 決策層（decide_result / aggregate_small_docx）
# ═══════════════════════════════════════════════════════════════════════════

def _to_f(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def decide_result(result: dict) -> dict:
    mol         = result.get("mol", "")
    id_         = result.get("id", "")
    mol_conf_f  = _to_f(result.get("mol_conf", ""))
    id_conf_f   = _to_f(result.get("id_conf", ""))
    cross       = result.get("cross_match", "")
    docx_class  = result.get("docx_class", "")
    id_from_vote = result.get("_id_from_vote", False)

    final_value = vision_review = ""
    final_conf  = 0.0
    note        = result.get("note", "")

    if mol and id_:
        if mol_conf_f >= id_conf_f:
            final_value, final_conf = mol, mol_conf_f
            note = "mol勝(信心高)" if mol_conf_f != id_conf_f else "mol==permit"
        else:
            final_value, final_conf = id_, id_conf_f
            note = "permit勝(信心高)"
        if final_conf <= CONF_KEY_IN:
            vision_review = "Y"
        if mol_conf_f > id_conf_f and mol != id_:
            vision_review = "Y"
            note = "mol≠permit，mol信心高，值衝突"
    elif mol:
        final_value, final_conf = mol, mol_conf_f
        if docx_class == "large" and mol_conf_f <= CONF_KEY_IN:
            vision_review = "Y"
            note = "僅mol，信心低"
    elif id_:
        final_value, final_conf = id_, id_conf_f
        threshold = CONF_VOTE_MIN if id_from_vote else CONF_KEY_IN
        if id_conf_f <= threshold:
            vision_review = "Y"
            note = "permit多數票，信心低" if id_from_vote else "僅permit，信心低"

    if cross == "✓" and final_conf <= CONF_KEY_IN:
        vision_review = "Y"
        note = (note + " 多數決信心低").strip() if note else "多數決信心低"

    if result.get("_permit_partial_hit"):
        vision_review = "Y"
        note = (note + " permit部分命中無多數").strip() if note else "permit部分命中無多數"

    result["final_value"]   = final_value
    result["final_conf"]    = round(final_conf, 1) if final_conf else ""
    result["vision_review"] = vision_review
    result["note"]          = note
    return result


def aggregate_small_docx(results: list[dict]) -> list[dict]:
    hits = [r for r in results if r.get("mol") or r.get("id")]
    if not hits:
        for r in results:
            r["vision_review"] = "Y"
            r["note"] = "small:無命中"
        return results

    def best_conf(r: dict) -> float:
        return max(_to_f(r.get("mol_conf")), _to_f(r.get("id_conf")))

    winner  = max(hits, key=best_conf)
    w_value = winner.get("mol") or winner.get("id")
    w_conf  = best_conf(winner)
    for r in hits:
        r["final_value"]   = w_value
        r["final_conf"]    = round(w_conf, 1) if w_conf else ""
        r["vision_review"] = "Y"
    return hits


# ═══════════════════════════════════════════════════════════════════════════
# ⑦ vision_submit（決定送 Vision 或直接 key-in）
# ═══════════════════════════════════════════════════════════════════════════

def _best_img_path(row: dict) -> str:
    return row.get("permit_crop") or row.get("mol_crop") or ""


def process_large_vs(rows: list[dict]) -> list[dict]:
    """large docx：回傳要送 Vision 的列（含 direct_keyin 旗標）。"""
    queue = []
    mol_rows    = [(r, r["mol"],  _to_f(r["mol_conf"]))  for r in rows if r.get("mol")]
    permit_rows = [(r, r["id"],   _to_f(r["id_conf"]))   for r in rows if r.get("id")]

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
                queue.append({
                    "source_docx":     best_row["source_docx"],
                    "image_name":      best_row["image_name"],
                    "img_path":        _best_img_path(best_row),
                    "candidate_value": best_val,
                    "reason":          f"cross_match(file)_高信心 conf={best_conf}",
                    "direct_keyin":    True,
                })
                return queue
            else:
                queue.append({
                    "source_docx":     best_row["source_docx"],
                    "image_name":      best_row["image_name"],
                    "img_path":        _best_img_path(best_row),
                    "candidate_value": best_val,
                    "reason":          f"cross_match(file)_低信心 conf={best_conf}",
                    "direct_keyin":    False,
                })
                return queue
        else:
            all_candidates = mol_rows + permit_rows
            best_row, best_val, best_conf = max(all_candidates, key=lambda x: x[2])
            queue.append({
                "source_docx":     best_row["source_docx"],
                "image_name":      best_row["image_name"],
                "img_path":        _best_img_path(best_row),
                "candidate_value": best_val,
                "reason":          f"mol≠permit衝突_最高conf={best_conf}",
                "direct_keyin":    False,
            })
            return queue

    # 規則B：permit 部分命中無多數
    partial_rows = [r for r in rows if "permit部分命中無多數" in r.get("note", "")]
    for r in partial_rows:
        queue.append({
            "source_docx":     r["source_docx"],
            "image_name":      r["image_name"],
            "img_path":        r.get("permit_crop") or _best_img_path(r),
            "candidate_value": r.get("id") or r.get("final_value", ""),
            "reason":          "permit部分命中無多數",
            "direct_keyin":    False,
        })

    # 規則C：其餘 vision_review=Y
    handled = {r["image_name"] for r in partial_rows}
    for r in rows:
        if r.get("vision_review") == "Y" and r["image_name"] not in handled:
            queue.append({
                "source_docx":     r["source_docx"],
                "image_name":      r["image_name"],
                "img_path":        _best_img_path(r),
                "candidate_value": r.get("final_value", ""),
                "reason":          r.get("note", "vision_review=Y"),
                "direct_keyin":    False,
            })

    # 若完全沒有需 Vision 且也沒有直接 key-in → 取 final_conf 最高的列直接 key-in
    if not queue:
        rows_with_value = [r for r in rows if r.get("final_value")]
        if rows_with_value:
            best_row = max(rows_with_value, key=lambda r: _to_f(r.get("final_conf")))
            queue.append({
                "source_docx":     best_row["source_docx"],
                "image_name":      best_row["image_name"],
                "img_path":        _best_img_path(best_row),
                "candidate_value": best_row["final_value"],
                "reason":          f"高信心直接key-in conf={best_row.get('final_conf','')}",
                "direct_keyin":    True,
            })

    return queue


def process_small_vs(rows: list[dict]) -> list[dict]:
    """small docx：全部無命中 → 人工審查；否則依 vision_review 送件。"""
    all_no_match = all("small:無命中" in r.get("note", "") for r in rows)
    if all_no_match:
        return []  # 人工審查，不送 Vision

    queue = []
    for r in rows:
        if r.get("vision_review") == "Y":
            queue.append({
                "source_docx":     r["source_docx"],
                "image_name":      r["image_name"],
                "img_path":        _best_img_path(r),
                "candidate_value": r.get("final_value", ""),
                "reason":          r.get("note", "vision_review=Y"),
                "direct_keyin":    False,
            })
    return queue


def build_vision_queue(csv_path: Path) -> tuple[list[dict], list[str], list[dict]]:
    """
    讀取 matches.csv，分成三類回傳：
      vision_items  : 需送 Google Vision 的項目
      manual_review : 人工審查 docx 名稱清單
      keyin_items   : 直接 key-in 的項目（direct_keyin=True）
    """
    groups: dict[str, list[dict]] = defaultdict(list)
    with open(csv_path, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            groups[row["source_docx"]].append(row)

    vision_items: list[dict]  = []
    keyin_items:  list[dict]  = []
    manual_review: list[str]  = []

    for docx_name, rows in sorted(groups.items()):
        docx_class = rows[0]["docx_class"]
        if docx_class == "large":
            queue = process_large_vs(rows)
        else:
            queue = process_small_vs(rows)
            if not queue and all("small:無命中" in r.get("note", "") for r in rows):
                manual_review.append(docx_name)
                continue

        for item in queue:
            if item.pop("direct_keyin", False):
                keyin_items.append(item)
            else:
                vision_items.append(item)

    return vision_items, manual_review, keyin_items


# ═══════════════════════════════════════════════════════════════════════════
# ⑧ Google Vision
# ═══════════════════════════════════════════════════════════════════════════

def run_google_vision(img_path: str) -> str:
    """
    送圖給 Google Vision OCR，從回傳文字中嘗試萃取 4 位數許可號碼。
    回傳萃取到的值，或空字串。
    """
    credentials = service_account.Credentials.from_service_account_file(
        SERVICE_ACCOUNT_JSON,
        scopes=["https://www.googleapis.com/auth/cloud-platform"],
    )
    client = gvision.ImageAnnotatorClient(credentials=credentials)
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
    return ""


# ═══════════════════════════════════════════════════════════════════════════
# ⑨ Google Sheets key-in
# ═══════════════════════════════════════════════════════════════════════════

def _get_sheets_service():
    creds = service_account.Credentials.from_service_account_file(
        SERVICE_ACCOUNT_JSON, scopes=SHEETS_SCOPES
    )
    return build("sheets", "v4", credentials=creds)


def keyin_to_sheets(rows: list[dict], status: str = "keyed-in"):
    """
    將一批資料寫入 Google Sheets。
    rows 格式：[{"source_docx": ..., "final_value": ..., "note": ...}, ...]
    每列附加 status 欄。
    """
    if not rows:
        return
    service = _get_sheets_service()
    sheet   = service.spreadsheets()

    values = []
    for r in rows:
        values.append([
            r.get("source_docx", ""),
            r.get("final_value") or r.get("candidate_value", ""),
            status,
            r.get("reason") or r.get("note", ""),
        ])

    body = {"values": values}
    range_name = f"{SHEET_NAME}!A1"   # 從 A1 開始 append

    sheet.values().append(
        spreadsheetId=SPREADSHEET_ID,
        range=range_name,
        valueInputOption="RAW",
        insertDataOption="INSERT_ROWS",
        body=body,
    ).execute()
    logger.info(f"  ✔ 已寫入 Google Sheets {len(values)} 筆（status={status}）")


# ═══════════════════════════════════════════════════════════════════════════
# ⑩ 主流程
# ═══════════════════════════════════════════════════════════════════════════

def run_scan(docx_files: list[Path], image_filter: str = "") -> Path:
    """執行 Tesseract 掃描，輸出 matches.csv，回傳 csv 路徑。

    image_filter：若非空，配合單一 docx 只處理該檔名的圖（對應 --image）。
    """
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    csv_path = OUTPUT_DIR / "matches.csv"
    fieldnames = [
        "source_docx", "image_name", "docx_class",
        "mol", "mol_layer", "mol_conf",
        "id", "id_layer", "id_conf",
        "cross_match", "final_value", "final_conf", "vision_review",
        "note", "manual_review", "low_conf", "hit_config", "hit_roi",
        "mol_crop", "permit_crop",
    ]

    total = hits = upper_hits = lower_hits = 0
    t0 = time.time()

    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for docx_path in docx_files:
            images = extract_images_from_docx(docx_path)
            # --image 過濾
            if image_filter:
                all_names = [n for n, _ in images]
                images = [(n, b) for n, b in images if n == image_filter]
                if not images:
                    logger.error(
                        f"找不到圖檔 {image_filter!r}（{docx_path.name} 內含：{all_names}）"
                    )
                    continue

            docx_class = classify_by_count(len(images))
            logger.debug(f"  {docx_path.name}: {len(images)} 張圖 → {docx_class}")

            small_bucket: list[dict] = []

            for img_name, img_bytes in images:
                total += 1
                if docx_class == "small":
                    result = scan_image_mol_only(docx_path.name, img_name, img_bytes)
                    small_bucket.append(result)
                else:
                    result = scan_image_large(docx_path.name, img_name, img_bytes)
                    if result:
                        decide_result(result)
                        hits += 1
                        hit_roi = result.get("hit_roi", "")
                        if hit_roi == "permit_upper":
                            upper_hits += 1
                        elif hit_roi == "permit_lower":
                            lower_hits += 1
                        writer.writerow({k: result.get(k, "") for k in fieldnames})
                        logger.info(
                            f"★ {docx_path.name}/{img_name}"
                            f"  [{docx_class}|{hit_roi}]"
                            f"  mol={result.get('mol')!r} id={result.get('id')!r}"
                            f"  final={result.get('final_value')!r}"
                            f"  vision={result.get('vision_review')!r}"
                        )
                    else:
                        logger.debug(f"  {docx_path.name} / {img_name}  未命中")

            if docx_class == "small" and small_bucket:
                to_write = aggregate_small_docx(small_bucket)
                for result in to_write:
                    hit_roi = result.get("hit_roi", "")
                    if result.get("mol") or result.get("id"):
                        hits += 1
                    writer.writerow({k: result.get(k, "") for k in fieldnames})
                    logger.info(
                        f"★ {docx_path.name} / {result.get('image_name')}"
                        f"  [small|{hit_roi}]"
                        f"  mol={result.get('mol')!r}"
                        f"  final={result.get('final_value')!r}"
                        f"  vision={result.get('vision_review')!r}"
                    )

    elapsed = round(time.time() - t0, 1)
    logger.info(f"掃描完成：{total} 張圖，命中 {hits} 張，耗時 {elapsed}s")
    if upper_hits + lower_hits > 0:
        logger.info(f"  permit 上半命中：{upper_hits} 張 / 下半命中：{lower_hits} 張")
    return csv_path


def main():
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
    csv_path = run_scan(docx_files, image_filter=opts.image)
    logger.info(f"  matches.csv 已輸出：{csv_path.resolve()}")

    # ── 步驟 3：vision_submit 分流 ─────────────────────────────────────────
    logger.info("── 步驟 3：vision_submit 分流 ──")
    vision_items, manual_review, keyin_items = build_vision_queue(csv_path)
    logger.info(f"  直接 key-in：{len(keyin_items)} 筆")
    logger.info(f"  送 Vision  ：{len(vision_items)} 筆")
    logger.info(f"  人工審查   ：{len(manual_review)} 件")

    # ── 步驟 4a：直接 key-in 高信心結果 ────────────────────────────────────
    logger.info("── 步驟 4a：直接 key-in 至 Google Sheets ──")
    keyin_to_sheets(keyin_items, status="keyed-in")

    # ── 步驟 4b：送 Google Vision，再 key-in ───────────────────────────────
    logger.info("── 步驟 4b：Google Vision 判讀 ──")
    vision_keyin = []
    for item in vision_items:
        img_path = item["img_path"]
        if not img_path or not Path(img_path).exists():
            logger.warning(f"  ⚠ 找不到圖檔：{img_path}（{item['source_docx']}）")
            continue
        vision_value = run_google_vision(img_path)
        logger.info(
            f"  Vision → {item['source_docx']} / {item['image_name']}"
            f"  candidate={item['candidate_value']!r}  vision={vision_value!r}"
        )
        final = vision_value or item["candidate_value"]
        vision_keyin.append({
            "source_docx":     item["source_docx"],
            "candidate_value": final,
            "reason":          f"vision:{item['reason']}",
        })
    keyin_to_sheets(vision_keyin, status="vision")

    # ── 步驟 4c：人工審查清單 ──────────────────────────────────────────────
    if manual_review:
        manual_rows = [{"source_docx": d, "candidate_value": "", "reason": "small:全無命中"}
                       for d in manual_review]
        keyin_to_sheets(manual_rows, status="manual_review")
        logger.info(f"── 人工審查（{len(manual_review)} 件）：")
        for d in manual_review:
            logger.info(f"    {d}")

    logger.info("── 全部完成 ──")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="OCR pipeline: prefilter→scan→vision→Sheets")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING"])
    parser.add_argument("--file",  "-f", metavar="DOCX",  default="",
                        help="只處理指定的單一 .docx")
    parser.add_argument("--image", "-i", metavar="IMAGE", default="",
                        help="配合 --file，只處理 docx 內指定的圖檔名")
    parser.add_argument("--roi",   "-r", metavar="ROI",   default="",
                        help=f"只掃指定 ROI：{list(ROI_REGIONS.keys())}")
    opts = parser.parse_args()
    logging.getLogger().setLevel(getattr(logging, opts.log_level))
    if opts.roi:
        valid_rois = list(ROI_REGIONS.keys())
        if opts.roi not in valid_rois:
            print(f"錯誤：--roi 必須為 {valid_rois}，收到 {opts.roi!r}")
            raise SystemExit(1)
        globals()["ROI_FILTER"] = opts.roi
    main()
