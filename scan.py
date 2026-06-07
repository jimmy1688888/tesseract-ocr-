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
from io import BytesIO
from pathlib import Path

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

RE_PERMIT_ZH = re.compile(r"(?:許\s*可\s*(?:證\s*號|號\s*碼)|號)\s*[:::﹕]\s*(\d{4})", re.IGNORECASE)

RE_PERMIT_ID_LIST = [
    re.compile(r"No\.?\s*i[zj]in\s*[:::﹕]\s*(\d{4})", re.IGNORECASE),
    re.compile(r"[Nn]\w{0,5}n\s*[:::﹕]\s*(\d{4})", re.IGNORECASE),
    re.compile(r"\bi[zj]in\s*[:::﹕]\s*(\d{4})", re.IGNORECASE),
]

RE_MOL_LIST = [
    re.compile(r"Agency'?s?\s+M[O0]L?\s+(?:L[i1I])?[i]?cense\s+Num\s*ber\s*[:::]\s*(\d{4})", re.IGNORECASE),
    re.compile(r"A\w{3,6}'?s?\s+M[O0]L?\s+(?:L[i1I])?[i]?cense\s+Num\s*ber\s*[:::]\s*(\d{4})", re.IGNORECASE),
    re.compile(r"(?:Num\s*ber|umber|[Nn]amber)\s*[:::]\s*(\d{4})", re.IGNORECASE),
    re.compile(r"M[O0]L\D{0,30}(\d{4})", re.IGNORECASE),
]


def find_permits(text: str) -> tuple[str, str, int, str, int]:
    zh = RE_PERMIT_ZH.search(text)
    id_, id_layer = None, 0
    for i, p in enumerate(RE_PERMIT_ID_LIST, 1):
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


CONF_LOW_THRESHOLD = 60   # 低於此值另存圖供人工複核


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

                zh, id_, id_layer, mol, mol_layer = find_permits(text)

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
                if conf < CONF_LOW_THRESHOLD and not result["low_conf"]:
                    low_dir = OUTPUT_DIR / "low_conf_crops"
                    low_dir.mkdir(parents=True, exist_ok=True)
                    low_path = low_dir / f"{stem}_{roi_name}_conf{int(conf)}.png"
                    crop_roi(raw_img, roi_coords).save(low_path)
                    result["low_conf"] = str(low_path)
                    logger.info(f"  ⚠ 低信心 {conf} < {CONF_LOW_THRESHOLD}：{low_path.name}")

                logger.debug(f"  ★ {roi_name}/{cfg['name']}  conf={conf}  zh={zh!r} id={id_!r} mol={mol!r}")
                break

    return result if any_hit else None

# ═══════════════════════════════════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════════════════════════════════

def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    csv_path = OUTPUT_DIR / "matches.csv"
    fieldnames = [
        "source_docx", "image_name",
        "zh", "id", "id_layer", "mol", "mol_layer",
        "conf", "low_conf",
        "hit_config", "hit_roi",
        "mol_crop", "permit_crop",
    ]

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
            for img_name, img_bytes in images:
                total += 1
                result = scan_image(docx_path.name, img_name, img_bytes)
                if result:
                    hits += 1
                    if result["hit_roi"] == "permit_upper":
                        upper_hits += 1
                    elif result["hit_roi"] == "permit_lower":
                        lower_hits += 1
                    writer.writerow(result)
                    logger.info(
                        f"★ {docx_path.name} / {img_name}  [{result['hit_roi']}]"
                        f"  zh={result['zh']!r} id={result['id']!r} mol={result['mol']!r}"
                    )
                else:
                    logger.debug(f"  {docx_path.name} / {img_name}  未命中")

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
    opts = parser.parse_args()
    logging.getLogger().setLevel(getattr(logging, opts.log_level))
    main()
