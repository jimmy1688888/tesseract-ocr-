"""
ocr_benchmark.py
================
OCR 辨識率測試腳本

用途:
  並排測試 Tesseract 與 PaddleOCR 在你實際文件上的表現,
  輸出對照報告,協助決定是否升級引擎。

測試方式:
  1. 從 ./docs 資料夾的 .docx 解出圖片
  2. 同一張圖分別跑兩個引擎
  3. 各自嘗試擷取「許可證號」與「No ijin」
  4. 統計命中率、耗時、辨識文字內容

輸出:
  - benchmark_report.csv  逐張對照表(可用 Excel 開啟)
  - benchmark_summary.txt 統計摘要

安裝:
  pip install -r requirements.txt
  pip install paddlepaddle paddleocr   # 額外安裝 PaddleOCR

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
from dataclasses import dataclass, asdict
from typing import Optional

import numpy as np
from PIL import Image, ImageFilter


# ═══════════════════════════════════════════════════════════════════════════
# 設定
# ═══════════════════════════════════════════════════════════════════════════

INPUT_DIR    = Path("./docs")
REPORT_DIR   = Path("./benchmark_results")
MAX_SAMPLES  = 30                # 最多測試幾張圖片(設小一點先試)

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".tif"}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
# 結果資料結構
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class BenchmarkRow:
    """單張圖片的測試結果。"""
    source_docx: str
    image_name: str

    # Tesseract 結果
    tess_time:        float = 0.0     # 耗時(秒)
    tess_text_len:    int = 0          # 辨識文字總長度
    tess_permit_zh:   str = ""         # 抓到的許可證號
    tess_permit_id:   str = ""         # 抓到的 No ijin
    tess_hit:         bool = False     # 是否命中任一關鍵字

    # PaddleOCR 結果
    paddle_time:      float = 0.0
    paddle_text_len:  int = 0
    paddle_permit_zh: str = ""
    paddle_permit_id: str = ""
    paddle_hit:       bool = False

    # 一致性
    zh_match:         bool = False     # 兩者抓到相同許可證號
    id_match:         bool = False     # 兩者抓到相同 No ijin


# ═══════════════════════════════════════════════════════════════════════════
# 共用:從 docx 解圖、前處理、正則
# ═══════════════════════════════════════════════════════════════════════════

def extract_images_from_docx(docx_path: Path) -> list[tuple[str, bytes]]:
    images = []
    with zipfile.ZipFile(docx_path, "r") as z:
        for fname in sorted(z.namelist()):
            if fname.startswith("word/media/") and Path(fname).suffix.lower() in IMAGE_EXTENSIONS:
                images.append((Path(fname).name, z.read(fname)))
    return images


def preprocess_image(image_bytes: bytes) -> Image.Image:
    """紅通道 + 對比拉伸 + 去雜訊(兩引擎共用相同前處理)。"""
    img = Image.open(BytesIO(image_bytes)).convert("RGB")
    rgb = np.array(img)

    # ① 紅通道
    gray = rgb[:, :, 0]

    # ② 過取樣 2x
    pil_gray = Image.fromarray(gray)
    new_size = (pil_gray.width * 2, pil_gray.height * 2)
    pil_gray = pil_gray.resize(new_size, Image.Resampling.LANCZOS)

    # ③ 對比拉伸
    arr = np.array(pil_gray)
    low, high = np.percentile(arr, 2), np.percentile(arr, 98)
    if high > low:
        arr = np.clip((arr - low) * 255.0 / (high - low), 0, 255).astype(np.uint8)
    pil_gray = Image.fromarray(arr)

    # ④ 中值濾波
    pil_gray = pil_gray.filter(ImageFilter.MedianFilter(size=3))

    return pil_gray


RE_PERMIT_ZH = re.compile(r"許\s*可\s*證\s*號[\s::]*([A-Za-z0-9\-/.]+)", re.IGNORECASE)
RE_PERMIT_ID = re.compile(r"No\.?\s*[Ii]jin[\s::]*([A-Za-z0-9\-/.]+)", re.IGNORECASE)


def find_permits(text: str) -> tuple[str, str]:
    zh = RE_PERMIT_ZH.search(text)
    id_ = RE_PERMIT_ID.search(text)
    return (zh.group(1).strip() if zh else "", id_.group(1).strip() if id_ else "")


# ═══════════════════════════════════════════════════════════════════════════
# Tesseract 引擎
# ═══════════════════════════════════════════════════════════════════════════

def ocr_with_tesseract(img: Image.Image) -> tuple[str, float]:
    """用 pytesseract 跑 OCR,回傳 (文字, 耗時)。"""
    import pytesseract
    pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
    t0 = time.time()
    try:
        text = pytesseract.image_to_string(img, lang="chi_tra+ind+eng")
    except Exception as e:
        logger.error(f"  Tesseract 失敗:{e}")
        text = ""
    return text, time.time() - t0


# ═══════════════════════════════════════════════════════════════════════════
# EasyOCR 引擎(全域單例,避免每次重新載入模型)
# ═══════════════════════════════════════════════════════════════════════════

_easy_ocr_instance = None


def get_easy_ocr():
    """延遲初始化 EasyOCR(模型首次載入很慢)。"""
    global _easy_ocr_instance
    if _easy_ocr_instance is None:
        import easyocr
        logger.info("初始化 EasyOCR(首次會下載模型,可能需 1-2 分鐘)...")
        _easy_ocr_instance = easyocr.Reader(["ch_tra", "en"], gpu=False)
        logger.info("EasyOCR 載入完成")
    return _easy_ocr_instance


def ocr_with_paddle(img: Image.Image) -> tuple[str, float]:
    """用 EasyOCR 跑 OCR,回傳 (文字, 耗時)。"""
    reader = get_easy_ocr()
    t0 = time.time()

    img_array = np.array(img.convert("RGB"))

    try:
        result = reader.readtext(img_array, detail=0)
    except Exception as e:
        logger.error(f"  EasyOCR 失敗:{e}")
        return "", time.time() - t0

    return "\n".join(result), time.time() - t0


# ═══════════════════════════════════════════════════════════════════════════
# 主測試流程
# ═══════════════════════════════════════════════════════════════════════════

def benchmark_one(docx_name: str, img_name: str, image_bytes: bytes) -> BenchmarkRow:
    """測試單一圖片。"""
    row = BenchmarkRow(source_docx=docx_name, image_name=img_name)

    img = preprocess_image(image_bytes)

    text_t, t_t = ocr_with_tesseract(img)
    row.tess_time = round(t_t, 2)
    row.tess_text_len = len(text_t)
    row.tess_permit_zh, row.tess_permit_id = find_permits(text_t)
    row.tess_hit = bool(row.tess_permit_zh or row.tess_permit_id)

    text_p, t_p = ocr_with_paddle(img)
    row.paddle_time = round(t_p, 2)
    row.paddle_text_len = len(text_p)
    row.paddle_permit_zh, row.paddle_permit_id = find_permits(text_p)
    row.paddle_hit = bool(row.paddle_permit_zh or row.paddle_permit_id)

    row.zh_match = (
        bool(row.tess_permit_zh)
        and bool(row.paddle_permit_zh)
        and row.tess_permit_zh == row.paddle_permit_zh
    )
    row.id_match = (
        bool(row.tess_permit_id)
        and bool(row.paddle_permit_id)
        and row.tess_permit_id == row.paddle_permit_id
    )

    return row


def write_csv_report(rows: list[BenchmarkRow], output_path: Path):
    """逐張詳細結果輸出為 CSV。"""
    if not rows:
        return
    with open(output_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(asdict(rows[0]).keys()))
        writer.writeheader()
        for r in rows:
            writer.writerow(asdict(r))


def write_summary(rows: list[BenchmarkRow], output_path: Path):
    """統計摘要輸出。"""
    n = len(rows)
    if n == 0:
        return

    tess_hit       = sum(1 for r in rows if r.tess_hit)
    paddle_hit     = sum(1 for r in rows if r.paddle_hit)
    tess_zh_hit    = sum(1 for r in rows if r.tess_permit_zh)
    paddle_zh_hit  = sum(1 for r in rows if r.paddle_permit_zh)
    tess_id_hit    = sum(1 for r in rows if r.tess_permit_id)
    paddle_id_hit  = sum(1 for r in rows if r.paddle_permit_id)
    both_agree     = sum(1 for r in rows if r.zh_match or r.id_match)

    tess_total_time   = sum(r.tess_time for r in rows)
    paddle_total_time = sum(r.paddle_time for r in rows)

    lines = [
        "═" * 60,
        "  OCR 引擎辨識率對照報告",
        "═" * 60,
        f"  測試樣本數:{n} 張圖片",
        "",
        "  ── 整體命中率(任一關鍵字)──",
        f"    Tesseract  : {tess_hit:>3}/{n}  ({tess_hit/n*100:.1f}%)",
        f"    PaddleOCR  : {paddle_hit:>3}/{n}  ({paddle_hit/n*100:.1f}%)",
        "",
        "  ── 「許可證號」中文擷取率 ──",
        f"    Tesseract  : {tess_zh_hit:>3}/{n}  ({tess_zh_hit/n*100:.1f}%)",
        f"    PaddleOCR  : {paddle_zh_hit:>3}/{n}  ({paddle_zh_hit/n*100:.1f}%)",
        "",
        "  ── 「No ijin」印尼文擷取率 ──",
        f"    Tesseract  : {tess_id_hit:>3}/{n}  ({tess_id_hit/n*100:.1f}%)",
        f"    PaddleOCR  : {paddle_id_hit:>3}/{n}  ({paddle_id_hit/n*100:.1f}%)",
        "",
        "  ── 兩引擎一致性 ──",
        f"    結果完全一致:{both_agree}/{n}  ({both_agree/n*100:.1f}%)",
        "",
        "  ── 效能比較 ──",
        f"    Tesseract  總時間:{tess_total_time:>6.1f} 秒  (平均 {tess_total_time/n:.2f} 秒/張)",
        f"    PaddleOCR  總時間:{paddle_total_time:>6.1f} 秒  (平均 {paddle_total_time/n:.2f} 秒/張)",
        f"    速度比:    Paddle 是 Tesseract 的 {tess_total_time/paddle_total_time:.2f}x" if paddle_total_time > 0 else "",
        "",
        "═" * 60,
        "  建議",
        "═" * 60,
    ]

    tess_rate = tess_hit / n
    paddle_rate = paddle_hit / n
    diff = paddle_rate - tess_rate

    if tess_rate >= 0.85:
        lines.append("  ✓ Tesseract 已達 85% 以上,維持現狀即可")
    elif diff >= 0.15:
        lines.append("  ⚡ PaddleOCR 顯著優於 Tesseract(差距 ≥ 15%),建議升級")
    elif diff >= 0.05:
        lines.append("  ↑ PaddleOCR 略優,可考慮升級")
    elif diff <= -0.05:
        lines.append("  ↓ Tesseract 表現較佳(可能是印尼文佔多數),維持現狀")
    else:
        lines.append("  ≈ 兩者表現接近,維持 Tesseract(部署較簡單)")

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

    logger.info(f"找到 {len(docx_files)} 個 .docx")

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
    logger.info("(首次執行會下載 PaddleOCR 模型,請耐心等待)")

    results = []
    for i, (docx_name, img_name, img_bytes) in enumerate(samples, 1):
        logger.info(f"[{i}/{len(samples)}] {docx_name} - {img_name}")
        try:
            row = benchmark_one(docx_name, img_name, img_bytes)
            results.append(row)
            logger.info(
                f"  Tess  ({row.tess_time:.1f}s): "
                f"許可證號={row.tess_permit_zh or '✗'} / No ijin={row.tess_permit_id or '✗'}"
            )
            logger.info(
                f"  Paddle({row.paddle_time:.1f}s): "
                f"許可證號={row.paddle_permit_zh or '✗'} / No ijin={row.paddle_permit_id or '✗'}"
            )
        except Exception as e:
            logger.error(f"  測試失敗:{e}")

    csv_path = REPORT_DIR / "benchmark_report.csv"
    txt_path = REPORT_DIR / "benchmark_summary.txt"
    write_csv_report(results, csv_path)
    write_summary(results, txt_path)

    logger.info(f"\n詳細報告:{csv_path.resolve()}")
    logger.info(f"統計摘要:{txt_path.resolve()}")


if __name__ == "__main__":
    main()
