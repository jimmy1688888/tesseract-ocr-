"""
scan.py
=======
Tesseract 粗篩工具(兩階段流程第一階段)

從 ./docs 的 .docx 批量掃描圖片,找出含許可證號 / No izin / MOL 的圖片,
輸出裁切好的原始 ROI 圖檔供 Google Vision 精讀。

輸出:
  scan_results/matches.csv       命中清單(docx、圖片、各欄位初步數字、ROI 圖路徑)
  scan_results/mol_crops/        mol ROI 原始裁切圖
  scan_results/permit_crops/     permit ROI 原始裁切圖

用法:
  python scan.py
  python scan.py --log-level DEBUG
"""

import re
import csv
import time
import zipfile
import logging
import argparse
from collections import Counter
from io import BytesIO
from pathlib import Path

from prefilter import SMALL_DOCX_THRESHOLD, classify_by_count

import numpy as np
from PIL import Image, ImageFilter, ImageOps
import pytesseract
from pytesseract import Output

pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════
# 設定
# ═══════════════════════════════════════════════════════════════════════════

INPUT_DIR  = Path("./docs")
OUTPUT_DIR = Path("./scan_results")
TESS_LANG  = "ind+eng"
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".tif"}

# 白名單:拉丁字母 + 數字 + 常見標點
WHITELIST_LATIN = (
    "0123456789"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "abcdefghijklmnopqrstuvwxyz"
    " :.'-,"
)

# ─── ROI 設定 ──────────────────────────────────────────────────────────────
# 命名規則:同一邏輯欄位用相同前綴(以 "_" 分隔)
#   - "mol"           → 欄位 = "mol"
#   - "permit_upper"  → 欄位 = "permit"  (上半部嘗試)
#   - "permit_lower"  → 欄位 = "permit"  (下半部備援)
#
# 同一欄位內依字典順序嘗試,第一個命中即停;欄位已找到後跳過該欄位剩餘 ROI
ROI_REGIONS = {
    "mol":           (0.05, 0.04, 0.40, 0.25),   # 左上角:Agency's MOL License Number
    "permit_upper":  (0.40, 0.05, 1.00, 0.55),   # 右欄上半:許可號碼可能在此
    "permit_lower":  (0.40, 0.45, 1.00, 0.95),   # 右欄下半:或在此(10% 重疊避免切到關鍵字)
}

# 依命中率由高到低排列;找到第一個命中即停
SCAN_CONFIGS = [
    {"name": "紅通道_2x_中值3",  "channel": "R",    "scale": 2, "median": 3, "contrast": (2, 98), "psm": 3},
    {"name": "灰階_2x_中值3",    "channel": "gray", "scale": 2, "median": 3, "contrast": (2, 98), "psm": 3},
    {"name": "紅通道_原尺寸",    "channel": "R",    "scale": 1, "median": 0, "contrast": (2, 98), "psm": 3},
    {"name": "灰階_原尺寸",      "channel": "gray", "scale": 1, "median": 0, "contrast": (2, 98), "psm": 3},
    {"name": "紅通道_2x_PSM6",   "channel": "R",    "scale": 2, "median": 3, "contrast": (2, 98), "psm": 6},
    {"name": "灰階_2x_PSM6",     "channel": "gray", "scale": 2, "median": 3, "contrast": (2, 98), "psm": 6},
    {"name": "紅通道_銳化_PSM6", "channel": "R",    "scale": 2, "median": 3, "contrast": (2, 98), "psm": 6,  "sharpen": True},
    {"name": "紅通道_2x_PSM11",  "channel": "R",    "scale": 2, "median": 3, "contrast": (2, 98), "psm": 11},
    {"name": "灰階_2x_PSM11",    "channel": "gray", "scale": 2, "median": 3, "contrast": (2, 98), "psm": 11},
]

# 9 個主設定全部未命中時才嘗試的白名單備援
FALLBACK_CONFIGS = [
    {
        "name":      "英數白名單_PSM6",
        "channel":   "R",    "scale": 2, "median": 3, "contrast": (2, 98), "psm": 6,
        "lang":      "eng",
        "whitelist": WHITELIST_LATIN,
    },
    {
        "name":      "英數白名單_PSM3",
        "channel":   "gray", "scale": 2, "median": 3, "contrast": (2, 98), "psm": 3,
        "lang":      "eng",
        "whitelist": WHITELIST_LATIN,
    },
]

# ═══════════════════════════════════════════════════════════════════════════
# 正則
# ═══════════════════════════════════════════════════════════════════════════

# 許可證號：第 1213 號  →  (?:第\s*)? 吸收「第」字
RE_PERMIT_ZH = re.compile(
    r"(?:許\s*可\s*(?:證\s*號|號\s*碼)|號)\s*[:::﹕]\s*(?:第\s*)?(\d{4})(?!\d)",
    re.IGNORECASE,
)

# permit_upper：冒號必須存在；NO\.XXXX 格式作第四層保底
RE_PERMIT_ID_LIST = [
    re.compile(r"No\.?\s*i[zjl1]in\s*[:::﹕]\s*(?:NO\.)?(\d{4})(?!\d)", re.IGNORECASE),
    re.compile(r"[Nn]\w{0,5}n\s*[:::﹕]\s*(?:NO\.)?(\d{4})(?!\d)",       re.IGNORECASE),
    re.compile(r"\bi[zjl1]in\s*[:::﹕]\s*(?:NO\.)?(\d{4})(?!\d)",         re.IGNORECASE),
    re.compile(r"\bNO\.(\d{4})(?!\d)",                                    re.IGNORECASE),
]

# permit_lower：第一層冒號可省略，第二三層必填；同樣加入 NO\. 第四層
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


def find_permits(text: str, permit_id_list=None) -> tuple[str, str, int, str, int]:
    if permit_id_list is None:
        permit_id_list = RE_PERMIT_ID_LIST
    zh = RE_PERMIT_ZH.search(text)
    id_, id_layer = None, 0
    for i, p in enumerate(permit_id_list, 1):
        id_ = p.search(text)
        if id_:
            id_layer = i
            break
    mol, mol_layer = None, 0
    for i, p in enumerate(RE_MOL_LIST, 1):
        mol = p.search(text)
        if mol:
            mol_layer = i
            break
    return (
        zh.group(1).strip() if zh else "",
        id_.group(1).strip() if id_ else "",
        id_layer,
        mol.group(1).strip() if mol else "",
        mol_layer,
    )

# ═══════════════════════════════════════════════════════════════════════════
# 共用
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
    if ch == "R":
        arr = rgb[:, :, 0]
    elif ch == "gray":
        arr = np.mean(rgb, axis=2).astype(np.uint8)
    else:
        arr = rgb[:, :, 0]
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
        pil = pil.filter(ImageFilter.SHARPEN)
        pil = pil.filter(ImageFilter.SHARPEN)
    return pil


def build_tess_config(cfg: dict) -> str:
    """依設定組合 Tesseract config 字串。"""
    parts = [f"--psm {cfg.get('psm', 3)}"]
    if "whitelist" in cfg and cfg["whitelist"]:
        parts.append(f"-c tessedit_char_whitelist={cfg['whitelist']}")
    return " ".join(parts)


CONF_KEY_IN   = 55   # 高於此值直接 key in；低於則標記送 Google Vision
CONF_VOTE_MIN = 45   # permit 多數票平均信心低於此值才送 Vision


def ocr_with_conf(img, lang: str, tess_cfg: str) -> tuple[str, float]:
    """執行 OCR，回傳 (全文, 平均信心值 0~100)。"""
    data = pytesseract.image_to_data(img, lang=lang, config=tess_cfg, output_type=Output.DICT)
    valid = [(w, int(c)) for w, c in zip(data["text"], data["conf"])
             if w.strip() and int(c) != -1]
    text = " ".join(w for w, _ in valid)
    avg_conf = round(sum(c for _, c in valid) / len(valid), 1) if valid else 0.0
    return text, avg_conf


def roi_field(roi_name: str) -> str:
    """
    從 ROI 名稱取出邏輯欄位名(取底線前綴)。
    例:
      "mol"          → "mol"
      "permit_upper" → "permit"
      "permit_lower" → "permit"
    """
    return roi_name.split("_")[0]

PERMIT_VOTE_N = 3   # 交叉比對時使用的前 N 個 config


def _collect_permit_votes(image_bytes: bytes, roi_coords: tuple,
                          permit_id_list=None) -> list[tuple[str, float]]:
    """對 permit ROI 跑前 PERMIT_VOTE_N 個 config，回傳所有 (命中值, 信心) pair（含重複）。"""
    entries: list[tuple[str, float]] = []
    for cfg in SCAN_CONFIGS[:PERMIT_VOTE_N]:
        img = preprocess(image_bytes, {**cfg, "roi": roi_coords})
        text, conf = ocr_with_conf(img, cfg.get("lang", TESS_LANG), build_tess_config(cfg))
        _, id_, _, _, _ = find_permits(text, permit_id_list)
        if id_:
            entries.append((id_, conf))
    return entries


def _majority_vote(values: list[str], min_count: int = 2) -> str:
    """若最高票值出現次數 ≥ min_count，回傳該值；否則回傳空字串。"""
    if not values:
        return ""
    best, count = Counter(values).most_common(1)[0]
    return best if count >= min_count else ""


# ═══════════════════════════════════════════════════════════════════════════
# 掃描核心
# ═══════════════════════════════════════════════════════════════════════════

def scan_image(docx_name: str, img_name: str, image_bytes: bytes) -> dict | None:
    """
    對單張圖片掃描所有 ROI。

    新增邏輯:
      使用 roi_field() 將同前綴的 ROI 視為同一欄位。
      欄位一旦找到就跳過該欄位剩餘 ROI,避免重複工作。
      例:permit_upper 命中 → 直接跳過 permit_lower。

    ROI 圖檔儲存原始裁切圖(未經前處理),供 Google Vision 使用。
    """
    stem = f"{Path(docx_name).stem}_{Path(img_name).stem}"
    result = {
        "source_docx":  docx_name,
        "image_name":   img_name,
        "zh":           "",
        "id":           "",
        "id_layer":     "",
        "mol":          "",
        "mol_layer":    "",
        "hit_config":   "",
        "hit_roi":      "",          # ★ 新增:記錄是哪個 ROI 命中(upper/lower)
        "conf":         "",          # 命中當下的平均信心值
        "low_conf":     "",          # 信心值 < 60 時的另存圖路徑
        "mol_crop":     "",
        "permit_crop":  "",
    }
    any_hit = False
    fields_found: set[str] = set()    # ★ 已從某個 ROI 命中的欄位名

    raw_img: Image.Image | None = None

    for roi_name, roi_coords in ROI_REGIONS.items():
        field = roi_field(roi_name)

        # 該欄位已從前序 ROI 命中,直接跳過(這就是「找到就跳過」邏輯)
        if field in fields_found:
            logger.debug(f"  ⏭ 跳過 {roi_name}({field} 已從前序 ROI 命中)")
            continue

        roi_hit = False
        for config_list in (SCAN_CONFIGS, FALLBACK_CONFIGS):
            if roi_hit:
                break
            for cfg in config_list:
                combined_cfg = {**cfg, "roi": roi_coords}
                img = preprocess(image_bytes, combined_cfg)

                lang = cfg.get("lang", TESS_LANG)
                tess_cfg = build_tess_config(cfg)
                text, conf = ocr_with_conf(img, lang, tess_cfg)

                permit_id_list = RE_PERMIT_ID_LIST_LOWER if roi_name == "permit_lower" else None
                zh, id_, id_layer, mol, mol_layer = find_permits(text, permit_id_list)

                logger.debug(
                    f"  ✗ {roi_name}/{cfg['name']}  conf={conf:.0f}"
                    f"  text={text[:400]!r}"
                )

                if not (zh or id_ or mol):
                    continue

                # 有命中 — 填入結果
                if zh:  result["zh"]  = zh
                if id_:
                    result["id"]       = id_
                    result["id_layer"] = id_layer
                if mol:
                    result["mol"]       = mol
                    result["mol_layer"] = mol_layer
                if not result["hit_config"]:
                    result["hit_config"] = cfg["name"]
                    result["hit_roi"]    = roi_name
                    result["conf"]       = conf
                any_hit = True
                roi_hit = True
                fields_found.add(field)

                if raw_img is None:
                    raw_img = auto_rotate(Image.open(BytesIO(image_bytes)).convert("RGB"))

                crop_key = f"{field}_crop"
                if not result[crop_key]:
                    crop_dir = OUTPUT_DIR / f"{field}_crops"
                    crop_dir.mkdir(parents=True, exist_ok=True)
                    crop_path = crop_dir / f"{stem}.png"
                    crop_roi(raw_img, roi_coords).save(crop_path)
                    result[crop_key] = str(crop_path)

                # 信心值低於門檻 → 另存圖供人工複核
                if conf < CONF_KEY_IN and not result["low_conf"]:
                    low_dir = OUTPUT_DIR / "low_conf_crops"
                    low_dir.mkdir(parents=True, exist_ok=True)
                    low_path = low_dir / f"{stem}_{roi_name}_conf{int(conf)}.png"
                    crop_roi(raw_img, roi_coords).save(low_path)
                    result["low_conf"] = str(low_path)
                    logger.info(f"  ⚠ 低信心 {conf} < {CONF_KEY_IN}：{low_path.name}")

                logger.debug(f"  ★ {roi_name}/{cfg['name']}  conf={conf}  zh={zh!r} id={id_!r} mol={mol!r}")
                break

    return result if any_hit else None

def scan_image_mol_only(docx_name: str, img_name: str, image_bytes: bytes) -> dict:
    """
    小型 docx（≤ SMALL_DOCX_THRESHOLD 張）專用。
    只掃 mol ROI；mol 無值時標記人工審查，仍回傳 dict（不回傳 None）。
    """
    stem = f"{Path(docx_name).stem}_{Path(img_name).stem}"
    result = {
        "source_docx":   docx_name,
        "image_name":    img_name,
        "docx_class":    "small",
        "mol":           "",
        "mol_layer":     "",
        "mol_conf":      "",
        "id":            "",
        "id_layer":      "",
        "id_conf":       "",
        "cross_match":   "",
        "final_value":   "",
        "final_conf":    "",
        "vision_review": "",
        "note":          "",
        "low_conf":      "",
        "hit_config":    "",
        "hit_roi":       "",
        "mol_crop":      "",
        "permit_crop":   "",
        "manual_review": "",
    }
    raw_img: Image.Image | None = None
    roi_coords = ROI_REGIONS["mol"]

    mol_found = False
    for config_list in (SCAN_CONFIGS, FALLBACK_CONFIGS):
        if mol_found:
            break
        for cfg in config_list:
            img = preprocess(image_bytes, {**cfg, "roi": roi_coords})
            lang = cfg.get("lang", TESS_LANG)
            tess_cfg = build_tess_config(cfg)
            text, conf = ocr_with_conf(img, lang, tess_cfg)
            logger.debug(f"  ✗ mol/{cfg['name']}  conf={conf:.0f}  text={text[:400]!r}")

            _, _, _, mol, mol_layer = find_permits(text)
            if not mol:
                continue

            result["mol"]        = mol
            result["mol_layer"]  = mol_layer
            result["mol_conf"]   = conf
            result["hit_config"] = cfg["name"]
            result["hit_roi"]    = "mol"

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
    """
    大型 docx（> SMALL_DOCX_THRESHOLD 張）專用。
    掃 mol + permit（upper/lower）並交叉比對：
      1. mol == permit vote → 輸出該值，cross_match=✓
      2. mol 有值，permit vote 無值 → 輸出 mol
      3. mol 無值，permit vote 有多數票（≥2） → 輸出 permit vote
      4. 兩者皆無 → 回傳 None（未命中）
    """
    stem = f"{Path(docx_name).stem}_{Path(img_name).stem}"
    result = {
        "source_docx":   docx_name,
        "image_name":    img_name,
        "docx_class":    "large",
        "mol":           "",
        "mol_layer":     "",
        "mol_conf":      "",
        "id":            "",
        "id_layer":      "",
        "id_conf":       "",
        "cross_match":   "",
        "final_value":   "",
        "final_conf":    "",
        "vision_review": "",
        "note":          "",
        "low_conf":      "",
        "hit_config":    "",
        "hit_roi":       "",
        "mol_crop":      "",
        "permit_crop":   "",
        "manual_review": "",
    }
    raw_img: Image.Image | None = None

    # ── 步驟 1：掃 mol（邏輯與 scan_image 相同）─────────────────────────
    fields_found: set[str] = set()
    any_hit = False

    for roi_name, roi_coords in ROI_REGIONS.items():
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
                tess_cfg = build_tess_config(cfg)
                text, conf = ocr_with_conf(img, lang, tess_cfg)
                logger.debug(
                    f"  ✗ {roi_name}/{cfg['name']}  conf={conf:.0f}"
                    f"  text={text[:400]!r}"
                )
                permit_id_list = RE_PERMIT_ID_LIST_LOWER if roi_name == "permit_lower" else None
                zh, id_, id_layer, mol, mol_layer = find_permits(text, permit_id_list)

                if not (zh or id_ or mol):
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

                logger.debug(f"  ★ {roi_name}/{cfg['name']}  conf={conf}  zh={zh!r} id={id_!r} mol={mol!r}")
                break

    if not any_hit:
        return None

    # ── 步驟 2：permit 交叉比對（mol 無值，或需要驗證時）────────────────
    mol_val  = result["mol"]
    permit_val = result["id"]   # 第一輪已命中的 permit ID

    permit_vote_avg_conf = 0.0
    if not mol_val or not permit_val:
        # 收集 permit_upper 與 permit_lower 的多數票（含信心值）
        upper_entries = _collect_permit_votes(
            image_bytes, ROI_REGIONS["permit_upper"])
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
            # 有符合正則但不足多數 → 取最高信心的 entry 作為候選，標記送 Vision
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

    # ── 步驟 3：比對結果────────────────────────────────────────────────
    if mol_val and permit_vote and mol_val == permit_vote:
        result["cross_match"] = "✓"
        logger.info(f"  ✓ 交叉比對吻合：{mol_val}")
    elif mol_val and not permit_vote:
        pass  # 只有 mol，直接使用
    elif not mol_val and permit_vote:
        result["id"]          = permit_vote
        result["id_layer"]    = 0
        result["id_conf"]     = permit_vote_avg_conf
        result["_id_from_vote"] = True   # 內部旗標，不輸出至 CSV
        logger.info(f"  → mol 無值，permit 多數票：{permit_vote}  avg_conf={permit_vote_avg_conf}")
    # mol 有值但與 permit_vote 不同時，保留 mol，不覆蓋（可視需求調整）

    return result


# ═══════════════════════════════════════════════════════════════════════════
# 決策層
# ═══════════════════════════════════════════════════════════════════════════

def decide_result(result: dict) -> dict:
    """
    規則1: mol + id 皆有值 → 取信心高者；>CONF_KEY_IN 直接 key in，≤ 則標記 Vision
           衝突條件：mol_conf > id_conf 且 mol值 ≠ permit值 → 一律標記 Vision
    規則3: large docx，只有 mol，conf ≤ CONF_KEY_IN → 標記 Vision
    規則4: 多數決吻合（cross_match=✓）但信心 ≤ CONF_KEY_IN → 標記 Vision
    多數票: id 來自 _collect_permit_votes 時，信心門檻改用 CONF_VOTE_MIN(45)
    """
    mol        = result.get("mol", "")
    id_        = result.get("id", "")
    mol_conf   = result.get("mol_conf", "")
    id_conf    = result.get("id_conf", "")
    cross      = result.get("cross_match", "")
    docx_class = result.get("docx_class", "")
    id_from_vote = result.get("_id_from_vote", False)

    def to_f(v) -> float:
        try:
            return float(v)
        except (TypeError, ValueError):
            return 0.0

    mol_conf_f = to_f(mol_conf)
    id_conf_f  = to_f(id_conf)

    final_value   = ""
    final_conf    = 0.0
    vision_review = ""
    note          = result.get("note", "")

    if mol and id_:
        # 規則1: 兩者皆有值，取信心高者
        if mol_conf_f >= id_conf_f:
            final_value = mol
            final_conf  = mol_conf_f
            note = "mol勝(信心高)" if mol_conf_f != id_conf_f else "mol==permit"
        else:
            final_value = id_
            final_conf  = id_conf_f
            note = "permit勝(信心高)"
        if final_conf <= CONF_KEY_IN:
            vision_review = "Y"
        # 問題2：mol信心高於permit，但值不同 → 衝突，送Vision
        if mol_conf_f > id_conf_f and mol != id_:
            vision_review = "Y"
            note = "mol≠permit，mol信心高，值衝突"
    elif mol and not id_:
        final_value = mol
        final_conf  = mol_conf_f
        # 規則3: large docx 只有 mol，信心低 → Vision
        if docx_class == "large" and mol_conf_f <= CONF_KEY_IN:
            vision_review = "Y"
            note = "僅mol，信心低"
    elif id_ and not mol:
        final_value = id_
        final_conf  = id_conf_f
        # 多數票使用較寬鬆門檻 CONF_VOTE_MIN；直接掃描使用 CONF_KEY_IN
        threshold = CONF_VOTE_MIN if id_from_vote else CONF_KEY_IN
        if id_conf_f <= threshold:
            vision_review = "Y"
            note = "permit多數票，信心低" if id_from_vote else "僅permit，信心低"

    # 規則4: 多數決吻合但信心低 → Vision
    if cross == "✓" and final_conf <= CONF_KEY_IN:
        vision_review = "Y"
        note = (note + " 多數決信心低").strip() if note else "多數決信心低"

    # permit 有部分命中但無多數 → 一律送 Vision
    if result.get("_permit_partial_hit"):
        vision_review = "Y"
        note = (note + " permit部分命中無多數").strip() if note else "permit部分命中無多數"

    result["final_value"]   = final_value
    result["final_conf"]    = round(final_conf, 1) if final_conf else ""
    result["vision_review"] = vision_review
    result["note"]          = note
    return result


def aggregate_small_docx(results: list[dict]) -> list[dict]:
    """
    規則2：small docx 檔案層級聚合
    若有任一圖有值 → 取信心最高者作為全檔代表值，並標記所有命中圖送 Vision 驗證。
    若全部無值 → 所有圖標記 Vision（人工審查）。
    """
    hits = [r for r in results if r.get("mol") or r.get("id")]
    if not hits:
        for r in results:
            r["vision_review"] = "Y"
            r["note"] = "small:無命中"
        return results

    # 取信心最高的命中作為代表
    def best_conf(r: dict) -> float:
        try:
            return max(float(r.get("mol_conf") or 0), float(r.get("id_conf") or 0))
        except (TypeError, ValueError):
            return 0.0

    winner = max(hits, key=best_conf)
    w_value = winner.get("mol") or winner.get("id")
    w_conf  = best_conf(winner)

    for r in results:
        r["final_value"] = w_value
        r["final_conf"]  = round(w_conf, 1) if w_conf else ""
        if r.get("mol") or r.get("id"):
            r["vision_review"] = "Y"  # 有值 → 送 Vision 驗證
        else:
            r["note"] = "small:此圖無命中，參照其他圖"
    return results


# ═══════════════════════════════════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════════════════════════════════

def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    csv_path = OUTPUT_DIR / "matches.csv"
    fieldnames = [
        "source_docx", "image_name", "docx_class",
        "mol", "mol_layer", "mol_conf",
        "id", "id_layer", "id_conf",
        "cross_match",
        "final_value", "final_conf", "vision_review",
        "note", "manual_review",
        "low_conf",
        "hit_config", "hit_roi",
        "mol_crop", "permit_crop",
    ]

    if opts.file:
        target = Path(opts.file)
        if not target.exists():
            logger.error(f"找不到檔案:{target.resolve()}")
            return
        docx_files = [target]
        logging.getLogger().setLevel(logging.DEBUG)
        logger.debug(f"單檔 DEBUG 模式：{target.name}")
    else:
        docx_files = sorted(INPUT_DIR.glob("*.docx"))
        if not docx_files:
            logger.error(f"找不到 .docx:{INPUT_DIR.resolve()}")
            return

    total = hits = 0
    t0 = time.time()

    # 統計上/下半部分別命中次數
    upper_hits = lower_hits = 0

    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for docx_path in docx_files:
            images = extract_images_from_docx(docx_path)
            if opts.image:
                all_names = [n for n, _ in images]
                images = [(n, b) for n, b in images if n == opts.image]
                if not images:
                    logger.error(f"找不到圖檔 {opts.image!r}（docx 內含：{all_names}）")
                    return

            docx_class = classify_by_count(len(images))
            logger.debug(f"  {docx_path.name}: {len(images)} 張圖 → {docx_class}")

            small_bucket: list[dict] = []

            for img_name, img_bytes in images:
                total += 1
                if docx_class == "small":
                    result = scan_image_mol_only(docx_path.name, img_name, img_bytes)
                    # small docx 先收集，最後統一聚合
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
                            f"★ {docx_path.name} / {img_name}"
                            f"  [{docx_class}|{hit_roi}]"
                            f"  mol={result.get('mol')!r} id={result.get('id')!r}"
                            f"  final={result.get('final_value')!r}"
                            f"  vision={result.get('vision_review')!r}"
                        )
                    else:
                        logger.debug(f"  {docx_path.name} / {img_name}  未命中")

            # small docx 規則2：聚合後寫出
            if docx_class == "small" and small_bucket:
                aggregate_small_docx(small_bucket)
                for result in small_bucket:
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
    logger.info("")
    logger.info(f"掃描完成:{total} 張圖,命中 {hits} 張({hits / max(total, 1) * 100:.1f}%),耗時 {elapsed}s")
    if upper_hits + lower_hits > 0:
        logger.info(f"  permit 上半命中:{upper_hits} 張")
        logger.info(f"  permit 下半命中:{lower_hits} 張")
    logger.info(f"命中清單:{csv_path.resolve()}")
    logger.info(f"ROI 圖檔:{OUTPUT_DIR.resolve()}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING"])
    parser.add_argument("--file", "-f", metavar="DOCX", default="",
                        help="只掃描指定的單一 .docx（自動啟用 DEBUG 輸出）")
    parser.add_argument("--image", "-i", metavar="IMAGE", default="",
                        help="配合 --file，只處理 docx 內指定的圖檔名（如 image2.jpeg）")
    opts = parser.parse_args()
    logging.getLogger().setLevel(getattr(logging, opts.log_level))
    main()
