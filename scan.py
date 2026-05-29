"""
scan.py
=======
Tesseract 粗篩工具（兩階段流程第一階段）

從 ./docs 的 .docx 批量掃描圖片，找出含許可證號 / No izin / MOL 的圖片，
輸出裁切好的原始 ROI 圖檔供 Google Vision 精讀。

輸出：
  scan_results/matches.csv       命中清單（docx、圖片、各欄位初步數字、ROI 圖路徑）
  scan_results/mol_crops/        mol ROI 原始裁切圖
  scan_results/permit_crops/     permit ROI 原始裁切圖

用法：
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
TESS_LANG  = "chi_tra+ind+eng"
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".tif"}

ROI_REGIONS = {
    "mol":    (0.05, 0.04, 0.40, 0.25),  # image2 左上角：Agency's MOL License Number
    "permit": (0.40, 0.10, 1.00, 0.85),  # image5 右欄：許可號碼 / No izin
}

# 依命中率由高到低排列；找到第一個命中即停
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

# ═══════════════════════════════════════════════════════════════════════════
# 正則（與 ocr_benchmark.py 保持一致）
# ═══════════════════════════════════════════════════════════════════════════

RE_PERMIT_ZH = re.compile(r"(?:許\s*可\s*(?:證\s*號|號\s*碼)|號)\s*[:：﹕]\s*(\d{4})", re.IGNORECASE)

# permit ID 多重 fallback：依精確度由高到低，第一個命中即採用
RE_PERMIT_ID_LIST = [
    # 第一層：完整 No izin / No ijin
    re.compile(r"No\.?\s*i[zj]in\s*[:：﹕]\s*(\d{4})", re.IGNORECASE),
    # 第二層：N 開頭 + 任意 5 字 + n 結尾（izin 變體容錯）
    re.compile(r"[Nn]\w{0,5}n\s*[:：﹕]\s*(\d{4})", re.IGNORECASE),
    # 第三層：只靠末尾 n : XXXX 保底
    re.compile(r"n\s*[:：﹕]\s*(\d{4})", re.IGNORECASE),
]

RE_MOL_LIST = [
    # 第一層：Agency's 完整錨點
    re.compile(r"Agency'?s?\s+M[O0]L?\s+(?:L[i1I])?[i]?cense\s+Num\s*ber\s*[:：]\s*(\d{4})", re.IGNORECASE),
    # 第二層：Agency's 容錯（中間字元允許誤讀）
    re.compile(r"A\w{3,6}'?s?\s+M[O0]L?\s+(?:L[i1I])?[i]?cense\s+Num\s*ber\s*[:：]\s*(\d{4})", re.IGNORECASE),
    # 第三層：Number / umber / Namber : XXXX（Number 各種 OCR 變體）
    re.compile(r"(?:Num\s*ber|umber|[Nn]amber)\s*[:：]\s*(\d{4})", re.IGNORECASE),
    # 第五層：MOL 出現後，後面接續任意字元直到 4 位數字
    re.compile(r"M[O0]L\D{0,30}(\d{4})", re.IGNORECASE),
]


def find_permits(text: str) -> tuple[str, str, int, str, int]:
    zh  = RE_PERMIT_ZH.search(text)
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
        zh.group(1).strip()  if zh  else "",
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
    ch  = cfg["channel"]
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

# ═══════════════════════════════════════════════════════════════════════════
# 掃描核心
# ═══════════════════════════════════════════════════════════════════════════

def scan_image(docx_name: str, img_name: str, image_bytes: bytes) -> dict | None:
    """
    對單張圖片掃描所有 ROI。
    有任一 ROI 命中即回傳結果 dict，全部未命中回傳 None。
    ROI 圖檔儲存的是未經前處理的原始裁切圖（供 Google Vision 使用）。
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
        "mol_crop":     "",
        "permit_crop":  "",
    }
    any_hit = False

    # 原始圖（旋轉後）延遲初始化，只在首次命中時載入
    raw_img: Image.Image | None = None

    for roi_name, roi_coords in ROI_REGIONS.items():
        for cfg in SCAN_CONFIGS:
            combined_cfg = {**cfg, "roi": roi_coords}
            img      = preprocess(image_bytes, combined_cfg)
            tess_cfg = f"--psm {cfg.get('psm', 3)}"
            text     = pytesseract.image_to_string(img, lang=TESS_LANG, config=tess_cfg)
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
            any_hit = True

            # 儲存原始 ROI 裁切圖（未前處理，給 Google Vision 用）
            crop_key = f"{roi_name}_crop"
            if not result[crop_key]:
                crop_dir = OUTPUT_DIR / f"{roi_name}_crops"
                crop_dir.mkdir(parents=True, exist_ok=True)
                crop_path = crop_dir / f"{stem}.png"
                if raw_img is None:
                    raw_img = auto_rotate(Image.open(BytesIO(image_bytes)).convert("RGB"))
                crop_roi(raw_img, roi_coords).save(crop_path)
                result[crop_key] = str(crop_path)

            logger.debug(f"  ★ {roi_name}/{cfg['name']}  zh={zh!r} id={id_!r} mol={mol!r}")
            break  # 此 ROI 已命中，不再試其他 config

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
        "hit_config",
        "mol_crop", "permit_crop",
    ]

    docx_files = sorted(INPUT_DIR.glob("*.docx"))
    if not docx_files:
        logger.error(f"找不到 .docx：{INPUT_DIR.resolve()}")
        return

    total = hits = 0
    t0 = time.time()

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
                    writer.writerow(result)
                    logger.info(
                        f"★ {docx_path.name} / {img_name}"
                        f"  zh={result['zh']!r} id={result['id']!r} mol={result['mol']!r}"
                    )
                else:
                    logger.debug(f"  {docx_path.name} / {img_name}  未命中")

    elapsed = round(time.time() - t0, 1)
    logger.info("")
    logger.info(f"掃描完成：{total} 張圖，命中 {hits} 張（{hits / max(total, 1) * 100:.1f}%），耗時 {elapsed}s")
    logger.info(f"命中清單：{csv_path.resolve()}")
    logger.info(f"ROI 圖檔：{OUTPUT_DIR.resolve()}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING"])
    opts = parser.parse_args()
    logging.getLogger().setLevel(getattr(logging, opts.log_level))
    main()
