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
from PIL import Image, ImageFilter, ImageOps
import pytesseract
pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"


# ═══════════════════════════════════════════════════════════════════════════
# 設定
# ═══════════════════════════════════════════════════════════════════════════

INPUT_DIR   = Path("./docs")
REPORT_DIR  = Path("./benchmark_results")
MAX_SAMPLES = 500
TESS_LANG   = "chi_tra+ind+eng"

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".tif"}

# ROI 定義（相對座標，0~1）
# 每筆格式：(x1, y1, x2, y2)
ROI_REGIONS = {
    "mol":    (0.05, 0.04, 0.40, 0.25),  # image2 左上角方格：Agency's MOL License Number
    "permit": (0.40, 0.10, 1.00, 0.85),  # image5 右欄第4項：許可號碼 / No ijin
}
ROI_SAVE_DIR = REPORT_DIR / "roi_preview"   # 儲存裁切預覽圖

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
    # ── 原有組合（PSM 預設 3）──────────────────────────────────
    {"name": "紅通道_2x_中值3",   "channel": "R",    "scale": 2, "median": 3, "contrast": (2, 98), "psm": 3, "sauvola": False},
    {"name": "灰階_2x_中值3",     "channel": "gray", "scale": 2, "median": 3, "contrast": (2, 98), "psm": 3, "sauvola": False},
    {"name": "紅通道_原尺寸",     "channel": "R",    "scale": 1, "median": 0, "contrast": (2, 98), "psm": 3, "sauvola": False},
    {"name": "灰階_原尺寸",       "channel": "gray", "scale": 1, "median": 0, "contrast": (2, 98), "psm": 3, "sauvola": False},
    # ── PSM 6（假設單一文字區塊）─────────────────────────────────
    {"name": "紅通道_2x_PSM6",    "channel": "R",    "scale": 2, "median": 3, "contrast": (2, 98), "psm": 6, "sauvola": False},
    {"name": "灰階_2x_PSM6",      "channel": "gray", "scale": 2, "median": 3, "contrast": (2, 98), "psm": 6, "sauvola": False},
    # ── PSM 11（稀疏文字）────────────────────────────────────────
    {"name": "紅通道_2x_PSM11",   "channel": "R",    "scale": 2, "median": 3, "contrast": (2, 98), "psm": 11, "sauvola": False},
    {"name": "灰階_2x_PSM11",     "channel": "gray", "scale": 2, "median": 3, "contrast": (2, 98), "psm": 11, "sauvola": False},
    # ── 紅通道 + 銳化 ────────────────────────────────────────────
    {"name": "紅通道_銳化_PSM6",   "channel": "R",    "scale": 2, "median": 3, "contrast": (2, 98), "psm": 6,  "sauvola": False, "sharpen": True},
    {"name": "紅通道_銳化_PSM11",  "channel": "R",    "scale": 2, "median": 3, "contrast": (2, 98), "psm": 11, "sauvola": False, "sharpen": True},
]

# ROI × 前處理 笛卡兒積（2 × 15 = 30 組合）
BENCHMARK_CONFIGS = [
    {**cfg, "name": f"{roi_name}__{cfg['name']}", "roi": roi_coords}
    for roi_name, roi_coords in ROI_REGIONS.items()
    for cfg in CONFIGS
]
CONFIG_NAMES = [c["name"] for c in BENCHMARK_CONFIGS]


# ═══════════════════════════════════════════════════════════════════════════
# 結果資料結構
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class BenchmarkRow:
    source_docx: str
    image_name:  str
    results:     dict = field(default_factory=dict)  # name -> {time, len, zh, id, mol, hit}

    def flat_dict(self) -> dict:
        d = {"source_docx": self.source_docx, "image_name": self.image_name}
        for name in CONFIG_NAMES:
            r = self.results.get(name, {})
            d[f"{name}_time"]  = r.get("time", "")
            d[f"{name}_len"]   = r.get("len", "")
            d[f"{name}_zh"]    = r.get("zh", "")
            d[f"{name}_id"]    = r.get("id", "")
            d[f"{name}_mol"]   = r.get("mol", "")
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

# MOL 多重 fallback：依精確度由高到低，第一個命中即採用
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


def find_permits(text: str) -> tuple[str, str, str]:
    zh  = RE_PERMIT_ZH.search(text)
    id_ = None
    for pattern in RE_PERMIT_ID_LIST:
        id_ = pattern.search(text)
        if id_:
            break
    mol = None
    for pattern in RE_MOL_LIST:
        mol = pattern.search(text)
        if mol:
            break
    return (
        zh.group(1).strip()  if zh  else "",
        id_.group(1).strip() if id_ else "",
        mol.group(1).strip() if mol else "",
    )


def crop_roi(img: Image.Image, roi: tuple) -> Image.Image:
    """依相對座標 (x1,y1,x2,y2) 裁切圖片。"""
    w, h = img.size
    x1, y1, x2, y2 = roi
    return img.crop((int(x1 * w), int(y1 * h), int(x2 * w), int(y2 * h)))


# ═══════════════════════════════════════════════════════════════════════════
# 前處理
# ═══════════════════════════════════════════════════════════════════════════

def auto_rotate(img: Image.Image) -> Image.Image:
    """套用 EXIF 方向標籤旋轉（手機/掃描儀常見問題）。"""
    return ImageOps.exif_transpose(img)


def preprocess(image_bytes: bytes, cfg: dict) -> Image.Image:
    img = Image.open(BytesIO(image_bytes)).convert("RGB")

    # 自動修正掃描旋轉（部分文件放反掃描）
    img = auto_rotate(img)

    # ROI 裁切（先旋正再裁，座標才對應）
    if "roi" in cfg:
        img = crop_roi(img, cfg["roi"])

    rgb = np.array(img)

    # 色版選擇
    ch = cfg["channel"]
    if ch == "R":
        arr = rgb[:, :, 0]
    elif ch == "gray":
        arr = np.mean(rgb, axis=2).astype(np.uint8)
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

    if cfg["median"] > 0:
        pil = pil.filter(ImageFilter.MedianFilter(size=cfg["median"]))

    # 銳化（強化文字邊緣，對低解析度或印章干擾有幫助）
    if cfg.get("sharpen", False):
        pil = pil.filter(ImageFilter.SHARPEN)
        pil = pil.filter(ImageFilter.SHARPEN)

    return pil


# ═══════════════════════════════════════════════════════════════════════════
# 主測試流程
# ═══════════════════════════════════════════════════════════════════════════

def benchmark_one(docx_name: str, img_name: str, image_bytes: bytes) -> BenchmarkRow:
    row = BenchmarkRow(source_docx=docx_name, image_name=img_name)

    for cfg in BENCHMARK_CONFIGS:
        name = cfg["name"]
        t0 = time.time()
        try:
            img  = preprocess(image_bytes, cfg)
            tess_cfg = f"--psm {cfg.get('psm', 3)}"
            text = pytesseract.image_to_string(img, lang=TESS_LANG, config=tess_cfg)
        except Exception as e:
            logger.error(f"  [{name}] 失敗:{e}")
            row.results[name] = {"time": 0, "len": 0, "zh": "", "id": "", "mol": "", "hit": False}
            continue

        elapsed      = round(time.time() - t0, 2)
        zh, id_, mol = find_permits(text)
        hit          = bool(zh or id_ or mol)
        row.results[name] = {"time": elapsed, "len": len(text), "zh": zh, "id": id_, "mol": mol, "hit": hit}

        if len(text) > 0:
            for line in text.splitlines():
                if any(kw in line.upper() for kw in ("MOL", "LICENSE", "NUMBER", "AGENCY")):
                    marker = "★" if hit else " "
                    logger.debug(f"  {marker} [{name}] → {line.strip()}")

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
    for cfg in BENCHMARK_CONFIGS:
        name     = cfg["name"]
        hits     = sum(1 for r in rows if r.results.get(name, {}).get("hit"))
        zh_hits  = sum(1 for r in rows if r.results.get(name, {}).get("zh"))
        id_hits  = sum(1 for r in rows if r.results.get(name, {}).get("id"))
        mol_hits = sum(1 for r in rows if r.results.get(name, {}).get("mol"))
        avg_time = sum(r.results.get(name, {}).get("time", 0) for r in rows) / n
        ranking.append((name, hits, zh_hits, id_hits, mol_hits, avg_time))

    ranking.sort(key=lambda x: (-x[1], x[5]))

    lines = [
        "═" * 72,
        "  Tesseract 前處理參數校正報告",
        "═" * 72,
        f"  測試樣本：{n} 張圖片",
        "",
        f"  {'組合名稱':<18}  {'命中':>4}  {'許可證號':>6}  {'No ijin':>7}  {'MOL':>5}  {'平均秒':>6}",
        "  " + "─" * 62,
    ]

    for name, hits, zh, id_, mol, avg_t in ranking:
        lines.append(
            f"  {name:<18}  {hits:>3}/{n}  {zh:>4}/{n}  {id_:>5}/{n}  {mol:>3}/{n}  {avg_t:>5.2f}s"
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


def diagnose(filter_docx: str = "", filter_img: str = ""):
    """印出指定圖片在所有前處理組合下的原始 OCR 文字。

    用法:
      python ocr_benchmark.py diagnose              # 第一張圖
      python ocr_benchmark.py diagnose 31011        # 含 31011 的 docx
      python ocr_benchmark.py diagnose 31011 image5 # 指定 docx + 圖片
    """
    docx_files = sorted(INPUT_DIR.glob("*.docx"))
    if not docx_files:
        print(f"找不到 .docx：{INPUT_DIR}")
        return

    found = False
    for docx_path in docx_files:
        if filter_docx and filter_docx not in docx_path.name:
            continue
        images = extract_images_from_docx(docx_path)
        for img_name, img_bytes in images:
            if filter_img and filter_img not in img_name:
                continue

            print(f"\n{'═'*65}")
            print(f"檔案：{docx_path.name}  圖片：{img_name}")
            print(f"{'═'*65}")

            for cfg in BENCHMARK_CONFIGS:
                img      = preprocess(image_bytes=img_bytes, cfg=cfg)
                tess_cfg = f"--psm {cfg.get('psm', 3)}"
                text     = pytesseract.image_to_string(img, lang=TESS_LANG, config=tess_cfg)
                zh, id_, mol = find_permits(text)
                print(f"\n[{cfg['name']}]  長度:{len(text)}  許可證號:{zh!r}  No ijin:{id_!r}  MOL:{mol!r}")
                print("─" * 40)
                print(text[:800])

            found = True
            return  # 找到第一張符合的就停

    if not found:
        print(f"找不到符合的圖片：docx={filter_docx!r}  img={filter_img!r}")


def roi_diagnose(filter_docx: str = "", filter_img: str = "", filter_roi: str = ""):
    """裁切指定 ROI 並以所有前處理組合跑 OCR，比較各組合命中結果。

    用法:
      python ocr_benchmark.py roi 31011 image5        # 兩個 ROI × 第一種前處理（確認座標）
      python ocr_benchmark.py roi 31015 image2 mol    # mol ROI × 全部 15 種前處理
      python ocr_benchmark.py roi 31011 image5 permit # permit ROI × 全部 15 種前處理
    """
    ROI_SAVE_DIR.mkdir(parents=True, exist_ok=True)
    docx_files = sorted(INPUT_DIR.glob("*.docx"))

    found = False
    for docx_path in docx_files:
        if filter_docx and filter_docx not in docx_path.name:
            continue
        images = extract_images_from_docx(docx_path)
        for img_name, img_bytes in images:
            if filter_img and filter_img not in img_name:
                continue

            print(f"\n{'═'*65}")
            print(f"檔案：{docx_path.name}  圖片：{img_name}")
            print(f"{'═'*65}")
            stem = f"{docx_path.stem}_{Path(img_name).stem}"

            target_rois = {
                k: v for k, v in ROI_REGIONS.items()
                if not filter_roi or filter_roi == k
            }

            for roi_name, roi in target_rois.items():
                print(f"\n{'─'*65}")
                print(f"ROI: {roi_name}  座標:{roi}")
                print(f"{'─'*65}")

                for cfg in CONFIGS:
                    # 裁切 ROI 再前處理
                    combined_cfg = {**cfg, "roi": roi}
                    img = preprocess(image_bytes=img_bytes, cfg=combined_cfg)
                    tess_cfg = f"--psm {cfg.get('psm', 3)}"
                    text = pytesseract.image_to_string(img, lang=TESS_LANG, config=tess_cfg)
                    zh, id_, mol = find_permits(text)
                    hit = bool(zh or id_ or mol)
                    marker = "★" if hit else " "
                    print(f"{marker} [{cfg['name']}]  許可證號:{zh!r}  No izin:{id_!r}  MOL:{mol!r}  len:{len(text)}")
                    print(text[:800])

                # 儲存第一種前處理的預覽圖供確認
                preview_cfg = {**CONFIGS[0], "roi": roi}
                preview_img = preprocess(image_bytes=img_bytes, cfg=preview_cfg)
                preview_path = ROI_SAVE_DIR / f"{stem}_{roi_name}.png"
                preview_img.save(preview_path)
                print(f"\n  預覽圖（{CONFIGS[0]['name']}）：{preview_path.resolve()}")

            found = True
            return

    if not found:
        print(f"找不到符合的圖片：docx={filter_docx!r}  img={filter_img!r}  roi={filter_roi!r}")


if __name__ == "__main__":
    import sys
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("cmd", nargs="?", default="", help="diagnose / roi / (空白=正常執行)")
    parser.add_argument("args", nargs="*", help="附加參數（docx、image、roi 過濾）")
    parser.add_argument("--log-level", default="INFO",
                        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
                        help="日誌等級（DEBUG 會印出 OCR 關鍵字行）")
    opts = parser.parse_args()

    logging.getLogger().setLevel(opts.log_level)

    if opts.cmd == "diagnose":
        diagnose(
            filter_docx=opts.args[0] if len(opts.args) > 0 else "",
            filter_img =opts.args[1] if len(opts.args) > 1 else "",
        )
    elif opts.cmd == "roi":
        roi_diagnose(
            filter_docx=opts.args[0] if len(opts.args) > 0 else "",
            filter_img =opts.args[1] if len(opts.args) > 1 else "",
            filter_roi =opts.args[2] if len(opts.args) > 2 else "",
        )
    else:
        main()
