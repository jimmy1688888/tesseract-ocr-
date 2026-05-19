"""
ocr_benchmark.py
================
Tesseract 前處理參數校正工具

用途:
  測試不同影像前處理組合對 Tesseract 辨識率的影響，
  找出最佳前處理設定。

測試方式:
  1. 從 ./docs 資料夾的 .docx 解出圖片
  2. 同一張圖分別套用各前處理組合後跑 Tesseract
  3. 嘗試擷取「許可證號」與「No ijin」
  4. 統計各組合命中率與耗時

輸出:
  - benchmark_report.csv  逐張對照表(可用 Excel 開啟)
  - benchmark_summary.txt 各組合命中率排名

安裝:
  pip install pytesseract pillow numpy

使用:
  python ocr_benchmark.py
"""

import re
import csv
import time
import zipfile
import logging
from io import BytesIO
from pathlib import Path
from dataclasses import dataclass, field, asdict

import numpy as np
from PIL import Image, ImageFilter

import pytesseract
pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"


# ═══════════════════════════════════════════════════════════════════════════
# 設定
# ═══════════════════════════════════════════════════════════════════════════

INPUT_DIR   = Path("./docs")
REPORT_DIR  = Path("./benchmark_results")
MAX_SAMPLES = 30
TESS_LANG   = "chi_tra+ind+eng"

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".tif"}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
# 前處理參數組合
# ═══════════════════════════════════════════════════════════════════════════

CONFIGS = [
    {"name": "紅通道_2x_中值3",   "channel": "R",    "scale": 2, "median": 3, "contrast": (2, 98)},
    {"name": "紅通道_2x_無濾波",  "channel": "R",    "scale": 2, "median": 0, "contrast": (2, 98)},
    {"name": "紅通道_3x_中值3",   "channel": "R",    "scale": 3, "median": 3, "contrast": (2, 98)},
    {"name": "灰階_2x_中值3",     "channel": "gray", "scale": 2, "median": 3, "contrast": (2, 98)},
    {"name": "灰階_2x_無濾波",    "channel": "gray", "scale": 2, "median": 0, "contrast": (2, 98)},
    {"name": "最小通道_2x_中值3", "channel": "min",  "scale": 2, "median": 3, "contrast": (2, 98)},
    {"name": "紅通道_原尺寸",     "channel": "R",    "scale": 1, "median": 0, "contrast": (2, 98)},
    {"name": "灰階_原尺寸",       "channel": "gray", "scale": 1, "median": 0, "contrast": (2, 98)},
]

CONFIG_NAMES = [c["name"] for c in CONFIGS]


# ═══════════════════════════════════════════════════════════════════════════
# 結果資料結構
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class BenchmarkRow:
    source_docx: str
    image_name:  str
    results:     dict = field(default_factory=dict)  # name -> {time, len, zh, id, hit}

    def flat_dict(self) -> dict:
        d = {"source_docx": self.source_docx, "image_name": self.image_name}
        for name in CONFIG_NAMES:
            r = self.results.get(name, {})
            d[f"{name}_time"]  = r.get("time", "")
            d[f"{name}_len"]   = r.get("len", "")
            d[f"{name}_zh"]    = r.get("zh", "")
            d[f"{name}_id"]    = r.get("id", "")
            d[f"{name}_hit"]   = r.get("hit", "")
        return d


# ═══════════════════════════════════════════════════════════════════════════
# 共用：從 docx 解圖、正則
# ═══════════════════════════════════════════════════════════════════════════

def extract_images_from_docx(docx_path: Path) -> list[tuple[str, bytes]]:
    images = []
    with zipfile.ZipFile(docx_path, "r") as z:
        for fname in sorted(z.namelist()):
            if fname.startswith("word/media/") and Path(fname).suffix.lower() in IMAGE_EXTENSIONS:
                images.append((Path(fname).name, z.read(fname)))
    return images


RE_PERMIT_ZH = re.compile(r"許\s*可\s*證\s*號[\s::]*([A-Za-z0-9\-/.]+)", re.IGNORECASE)
RE_PERMIT_ID = re.compile(r"No\.?\s*[Ii]jin[\s::]*([A-Za-z0-9\-/.]+)", re.IGNORECASE)


def find_permits(text: str) -> tuple[str, str]:
    zh  = RE_PERMIT_ZH.search(text)
    id_ = RE_PERMIT_ID.search(text)
    return (zh.group(1).strip() if zh else "", id_.group(1).strip() if id_ else "")


# ═══════════════════════════════════════════════════════════════════════════
# 前處理
# ═══════════════════════════════════════════════════════════════════════════

def preprocess(image_bytes: bytes, cfg: dict) -> Image.Image:
    img = Image.open(BytesIO(image_bytes)).convert("RGB")
    rgb = np.array(img)

    # 色版選擇
    ch = cfg["channel"]
    if ch == "R":
        arr = rgb[:, :, 0]
    elif ch == "gray":
        arr = np.mean(rgb, axis=2).astype(np.uint8)
    elif ch == "min":
        arr = rgb.min(axis=2)
    else:
        arr = rgb[:, :, 0]

    pil = Image.fromarray(arr)

    # 放大
    scale = cfg["scale"]
    if scale > 1:
        pil = pil.resize((pil.width * scale, pil.height * scale), Image.Resampling.LANCZOS)

    # 對比拉伸
    lo, hi = cfg["contrast"]
    a = np.array(pil)
    low, high = np.percentile(a, lo), np.percentile(a, hi)
    if high > low:
        a = np.clip((a - low) * 255.0 / (high - low), 0, 255).astype(np.uint8)
    pil = Image.fromarray(a)

    # 中值濾波
    if cfg["median"] > 0:
        pil = pil.filter(ImageFilter.MedianFilter(size=cfg["median"]))

    return pil


# ═══════════════════════════════════════════════════════════════════════════
# 主測試流程
# ═══════════════════════════════════════════════════════════════════════════

def benchmark_one(docx_name: str, img_name: str, image_bytes: bytes) -> BenchmarkRow:
    row = BenchmarkRow(source_docx=docx_name, image_name=img_name)

    for cfg in CONFIGS:
        name = cfg["name"]
        t0 = time.time()
        try:
            img  = preprocess(image_bytes, cfg)
            text = pytesseract.image_to_string(img, lang=TESS_LANG)
        except Exception as e:
            logger.error(f"  [{name}] 失敗:{e}")
            row.results[name] = {"time": 0, "len": 0, "zh": "", "id": "", "hit": False}
            continue

        elapsed   = round(time.time() - t0, 2)
        zh, id_   = find_permits(text)
        hit       = bool(zh or id_)
        row.results[name] = {"time": elapsed, "len": len(text), "zh": zh, "id": id_, "hit": hit}

    return row


def write_csv_report(rows: list[BenchmarkRow], output_path: Path):
    if not rows:
        return
    fieldnames = list(rows[0].flat_dict().keys())
    with open(output_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in rows:
            writer.writerow(r.flat_dict())


def write_summary(rows: list[BenchmarkRow], output_path: Path):
    n = len(rows)
    if n == 0:
        return

    ranking = []
    for cfg in CONFIGS:
        name     = cfg["name"]
        hits     = sum(1 for r in rows if r.results.get(name, {}).get("hit"))
        zh_hits  = sum(1 for r in rows if r.results.get(name, {}).get("zh"))
        id_hits  = sum(1 for r in rows if r.results.get(name, {}).get("id"))
        avg_time = sum(r.results.get(name, {}).get("time", 0) for r in rows) / n
        ranking.append((name, hits, zh_hits, id_hits, avg_time))

    ranking.sort(key=lambda x: (-x[1], x[4]))

    lines = [
        "═" * 65,
        "  Tesseract 前處理參數校正報告",
        "═" * 65,
        f"  測試樣本：{n} 張圖片",
        "",
        f"  {'組合名稱':<18}  {'命中':>4}  {'許可證號':>6}  {'No ijin':>7}  {'平均秒':>6}",
        "  " + "─" * 55,
    ]

    for name, hits, zh, id_, avg_t in ranking:
        lines.append(
            f"  {name:<18}  {hits:>3}/{n}  {zh:>4}/{n}  {id_:>5}/{n}  {avg_t:>5.2f}s"
        )

    best = ranking[0][0]
    lines += ["", f"  ★ 建議前處理：{best}", "═" * 65]

    summary = "\n".join(lines)
    print("\n" + summary)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(summary)


def main():
    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    docx_files = sorted(INPUT_DIR.glob("*.docx"))
    if not docx_files:
        logger.error(f"在 {INPUT_DIR} 找不到 .docx 檔案")
        return

    logger.info(f"找到 {len(docx_files)} 個 .docx，前處理組合：{len(CONFIGS)} 種")

    samples = []
    for docx_path in docx_files:
        try:
            images = extract_images_from_docx(docx_path)
        except Exception as e:
            logger.warning(f"無法解壓 {docx_path.name}:{e}")
            continue
        for img_name, img_bytes in images:
            samples.append((docx_path.name, img_name, img_bytes))
            if len(samples) >= MAX_SAMPLES:
                break
        if len(samples) >= MAX_SAMPLES:
            break

    if not samples:
        logger.error("沒有可測試的圖片")
        return

    logger.info(f"開始測試 {len(samples)} 張圖片...")

    results = []
    for i, (docx_name, img_name, img_bytes) in enumerate(samples, 1):
        logger.info(f"[{i}/{len(samples)}] {docx_name} - {img_name}")
        try:
            row = benchmark_one(docx_name, img_name, img_bytes)
            results.append(row)
            hits = [n for n in CONFIG_NAMES if row.results.get(n, {}).get("hit")]
            logger.info(f"  命中組合：{hits if hits else '無'}")
        except Exception as e:
            logger.error(f"  測試失敗:{e}")

    csv_path = REPORT_DIR / "benchmark_report.csv"
    txt_path = REPORT_DIR / "benchmark_summary.txt"
    write_csv_report(results, csv_path)
    write_summary(results, txt_path)

    logger.info(f"\n詳細報告：{csv_path.resolve()}")
    logger.info(f"統計摘要：{txt_path.resolve()}")


if __name__ == "__main__":
    main()
