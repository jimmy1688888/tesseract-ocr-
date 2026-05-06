"""
docx_ocr_to_sheets.py
=====================
完整整合版流程:
  1. 批次掃描資料夾內所有 .docx,解壓 word/media/ 中的圖片
  2. ★ 進階影像前處理:
       ① 紅通道萃取  (移除紅色印章)
       ② 過取樣 2x   (放大細節)
       ③ 對比拉伸    (黑字更黑、白底更白)
       ④ 中值去雜訊  (清除胡椒鹽雜訊)
  3. 將處理後的圖片轉為 PDF
  4. 用 OCRmyPDF 對 PDF 加上文字層(中文+印尼文)
  5. 讀取文字層,篩選含「許可證號」或「No ijin」的頁面
  6. 用正則擷取編號,寫入 Google Sheets

使用前準備:
  - pip install ocrmypdf pdfplumber Pillow gspread google-auth numpy
  - 安裝 Tesseract(含 chi_tra、ind 語言包) + Ghostscript
  - Google Cloud Console 建立 Service Account,下載金鑰 JSON
  - Google Sheets 共用權限給 Service Account 的 email(編輯權限)
"""

import re
import zipfile
import logging
from io import BytesIO
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
from PIL import Image, ImageFilter
import ocrmypdf
import pdfplumber
import gspread
from google.oauth2.service_account import Credentials


# ═══════════════════════════════════════════════════════════════════════════
# 設定區(請依實際環境調整)
# ═══════════════════════════════════════════════════════════════════════════

INPUT_DOCX_DIR  = Path("./docs")
WORK_DIR        = Path("./work")
OUTPUT_DIR      = Path("./output")

OCR_LANGUAGES   = "chi_tra+ind+eng"

# ── 前處理參數 ─────────────────────────────────────────────────────────────
ENABLE_PREPROCESS    = True
UPSAMPLE_FACTOR      = 2.0     # 過取樣倍率(1.5~3.0,越大越慢但細節越清晰)
DENOISE_KERNEL       = 3       # 中值濾波核大小(3 或 5,越大去雜訊越強但字會糊)
CONTRAST_PERCENTILE  = (2, 98) # 對比拉伸百分位(避免極端值影響)

# ── Google Sheets 設定 ─────────────────────────────────────────────────────
SERVICE_ACCOUNT_JSON = "./credentials.json"
SPREADSHEET_NAME     = "OCR辨識結果"
WORKSHEET_NAME       = "工作表1"

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tiff", ".tif"}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
# 資料結構
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class OCRResult:
    source_docx: str
    image_name: str
    pdf_path: Path
    permit_no_zh: Optional[str] = None
    permit_no_id: Optional[str] = None
    full_text: str = field(default="", repr=False)

    @property
    def is_match(self) -> bool:
        return self.permit_no_zh is not None or self.permit_no_id is not None


# ═══════════════════════════════════════════════════════════════════════════
# Step 1:從 docx 解壓圖片
# ═══════════════════════════════════════════════════════════════════════════

def extract_images_from_docx(docx_path: Path) -> list[tuple[str, bytes, str]]:
    images = []
    with zipfile.ZipFile(docx_path, "r") as z:
        media_files = sorted(
            f for f in z.namelist() if f.startswith("word/media/")
        )
        for fname in media_files:
            suffix = Path(fname).suffix.lower()
            if suffix in IMAGE_EXTENSIONS:
                images.append((Path(fname).name, z.read(fname), suffix))
    return images


# ═══════════════════════════════════════════════════════════════════════════
# Step 2:進階影像前處理
# ═══════════════════════════════════════════════════════════════════════════

def extract_red_channel(img: Image.Image) -> Image.Image:
    """
    ① 紅通道萃取
    --------------
    取 RGB 中的紅通道作為灰階圖。
    紅色印章在紅通道接近白色(消失),黑字仍為黑色。
    比 HSV 閾值法更乾淨、更不會誤傷淡色文字。
    """
    rgb = np.array(img.convert("RGB"))
    red_channel = rgb[:, :, 0]   # shape: (H, W),uint8
    return Image.fromarray(red_channel, mode="L")


def upsample(img: Image.Image, factor: float = 2.0) -> Image.Image:
    """
    ② 過取樣
    --------
    以 LANCZOS 演算法放大圖片,提升 OCR 對小字的辨識率。
    LANCZOS 是 Pillow 中品質最好的縮放演算法。
    """
    new_size = (int(img.width * factor), int(img.height * factor))
    return img.resize(new_size, Image.Resampling.LANCZOS)


def stretch_contrast(img: Image.Image, low_pct: float = 2, high_pct: float = 98) -> Image.Image:
    """
    ③ 對比拉伸
    ----------
    線性映射:把影像的 [low_pct%, high_pct%] 灰階值拉伸到 [0, 255]。
    比起一般對比增強,這個方法對掃描文件特別有效:
    - 紙張底色雜訊(暗灰)被推到 255(純白)
    - 文字主體(亮灰)被推到 0(純黑)

    Args:
        low_pct:  低端百分位(預設 2,代表把最暗 2% 視為黑)
        high_pct: 高端百分位(預設 98,代表把最亮 2% 視為白)
    """
    arr = np.array(img, dtype=np.uint8)

    # 計算百分位作為映射端點
    low  = np.percentile(arr, low_pct)
    high = np.percentile(arr, high_pct)

    if high <= low:
        return img  # 影像為單色,直接回傳

    # 線性映射 [low, high] → [0, 255]
    stretched = np.clip((arr.astype(np.float32) - low) * 255.0 / (high - low), 0, 255)
    return Image.fromarray(stretched.astype(np.uint8), mode="L")


def denoise(img: Image.Image, kernel_size: int = 3) -> Image.Image:
    """
    ④ 中值濾波去雜訊
    ----------------
    對每個像素取鄰域中位數,有效移除胡椒鹽雜訊(黑白小點)
    同時保留文字邊緣銳利度(這點比高斯模糊好)。

    Args:
        kernel_size: 必須為奇數,3 = 輕度,5 = 中度,7 = 強烈
    """
    return img.filter(ImageFilter.MedianFilter(size=kernel_size))


def preprocess_image(image_bytes: bytes) -> bytes:
    """
    完整前處理流程
    ================
    紅通道 → 過取樣 → 對比拉伸 → 去雜訊 → PNG bytes
    """
    img = Image.open(BytesIO(image_bytes))
    if img.mode != "RGB":
        img = img.convert("RGB")

    # ① 紅通道萃取(同時完成灰階化 + 印章移除)
    img = extract_red_channel(img)

    # ② 過取樣放大
    img = upsample(img, factor=UPSAMPLE_FACTOR)

    # ③ 對比拉伸
    img = stretch_contrast(img, low_pct=CONTRAST_PERCENTILE[0], high_pct=CONTRAST_PERCENTILE[1])

    # ④ 中值濾波去雜訊
    img = denoise(img, kernel_size=DENOISE_KERNEL)

    output = BytesIO()
    img.save(output, format="PNG", optimize=True)
    return output.getvalue()


def save_preprocess_comparison(image_bytes: bytes, output_path: Path) -> None:
    """
    除錯工具:產生「原圖 vs 各階段」對比圖,協助調參。
    主程式不會呼叫,需要時手動呼叫即可。
    """
    img = Image.open(BytesIO(image_bytes)).convert("RGB")
    stage1 = extract_red_channel(img)
    stage2 = upsample(stage1, factor=UPSAMPLE_FACTOR)
    stage3 = stretch_contrast(stage2, *CONTRAST_PERCENTILE)
    stage4 = denoise(stage3, kernel_size=DENOISE_KERNEL)

    # 統一尺寸後拼接
    target_size = (img.width, img.height)
    stages = [
        ("0_original", img.convert("L")),
        ("1_red_channel", stage1),
        ("2_upsample", stage2.resize(target_size)),
        ("3_contrast", stage3.resize(target_size)),
        ("4_denoise", stage4.resize(target_size)),
    ]

    w, h = target_size
    canvas = Image.new("L", (w * len(stages) + 20 * (len(stages) - 1), h), 255)
    for i, (_, im) in enumerate(stages):
        canvas.paste(im, (i * (w + 20), 0))
    canvas.save(output_path)
    logger.info(f"前處理對比圖已存:{output_path}")


# ═══════════════════════════════════════════════════════════════════════════
# Step 3:圖片 → PDF
# ═══════════════════════════════════════════════════════════════════════════

def image_to_pdf(image_bytes: bytes, output_pdf: Path, dpi: int = 300) -> bool:
    """將圖片 bytes 存為單頁 PDF。經過放大的圖建議用 300 DPI。"""
    try:
        img = Image.open(BytesIO(image_bytes))
        if img.mode in ("RGBA", "P", "LA"):
            img = img.convert("RGB")
        img.save(str(output_pdf), "PDF", resolution=dpi)
        return True
    except Exception as e:
        logger.error(f"圖片轉 PDF 失敗 ({output_pdf.name}):{e}")
        return False


# ═══════════════════════════════════════════════════════════════════════════
# Step 4:OCRmyPDF
# ═══════════════════════════════════════════════════════════════════════════

def run_ocr(input_pdf: Path, output_pdf: Path) -> bool:
    try:
        ocrmypdf.ocr(
            input_file=str(input_pdf),
            output_file=str(output_pdf),
            language=OCR_LANGUAGES,
            deskew=True,
            optimize=1,
            progress_bar=False,
        )
        return True
    except ocrmypdf.exceptions.PriorOcrFoundError:
        ocrmypdf.ocr(
            input_file=str(input_pdf),
            output_file=str(output_pdf),
            language=OCR_LANGUAGES,
            redo_ocr=True,
            deskew=True,
            progress_bar=False,
        )
        return True
    except Exception as e:
        logger.error(f"OCR 失敗 ({input_pdf.name}):{e}")
        return False


# ═══════════════════════════════════════════════════════════════════════════
# Step 5:讀文字層 + 關鍵字擷取
# ═══════════════════════════════════════════════════════════════════════════

RE_PERMIT_ZH = re.compile(
    r"許\s*可\s*證\s*號[\s::]*([A-Za-z0-9\-/.]+)",
    re.IGNORECASE,
)
RE_PERMIT_ID = re.compile(
    r"No\.?\s*[Ii]jin[\s::]*([A-Za-z0-9\-/.]+)",
    re.IGNORECASE,
)


def extract_text_from_pdf(pdf_path: Path) -> str:
    text_parts = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text() or ""
            text_parts.append(page_text)
    return "\n".join(text_parts)


def find_permit_numbers(text: str) -> tuple[Optional[str], Optional[str]]:
    zh_match = RE_PERMIT_ZH.search(text)
    id_match = RE_PERMIT_ID.search(text)
    return (
        zh_match.group(1).strip() if zh_match else None,
        id_match.group(1).strip() if id_match else None,
    )


# ═══════════════════════════════════════════════════════════════════════════
# Step 6:Google Sheets
# ═══════════════════════════════════════════════════════════════════════════

def get_worksheet():
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_file(SERVICE_ACCOUNT_JSON, scopes=scopes)
    client = gspread.authorize(creds)
    sheet = client.open(SPREADSHEET_NAME)
    return sheet.worksheet(WORKSHEET_NAME)


def write_results_to_sheet(results: list[OCRResult]) -> None:
    matched = [r for r in results if r.is_match]
    if not matched:
        logger.warning("沒有任何符合條件的結果,跳過寫入 Google Sheets")
        return

    ws = get_worksheet()
    if not ws.get_all_values():
        ws.append_row(["來源檔案", "圖片名稱", "許可證號(中文)", "No ijin(印尼文)"])

    rows = [
        [r.source_docx, r.image_name, r.permit_no_zh or "", r.permit_no_id or ""]
        for r in matched
    ]
    ws.append_rows(rows, value_input_option="RAW")
    logger.info(f"已寫入 {len(rows)} 筆資料到 Google Sheets")


# ═══════════════════════════════════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════════════════════════════════

def process_single_image(
    docx_name: str,
    image_name: str,
    image_bytes: bytes,
    suffix: str,
    index: int,
) -> Optional[OCRResult]:
    base_name = f"{Path(docx_name).stem}_{index:03d}_{Path(image_name).stem}"
    raw_pdf = WORK_DIR / f"{base_name}_raw.pdf"
    ocr_pdf = OUTPUT_DIR / f"{base_name}_ocr.pdf"

    # ★ 進階前處理(紅通道 + 過取樣 + 對比拉伸 + 去雜訊)
    if ENABLE_PREPROCESS:
        try:
            image_bytes = preprocess_image(image_bytes)
        except Exception as e:
            logger.warning(f"  前處理失敗,使用原圖:{e}")

    if not image_to_pdf(image_bytes, raw_pdf):
        return None

    if not run_ocr(raw_pdf, ocr_pdf):
        raw_pdf.unlink(missing_ok=True)
        return None

    try:
        full_text = extract_text_from_pdf(ocr_pdf)
    except Exception as e:
        logger.error(f"讀取文字失敗 ({ocr_pdf.name}):{e}")
        raw_pdf.unlink(missing_ok=True)
        return None

    permit_zh, permit_id = find_permit_numbers(full_text)
    raw_pdf.unlink(missing_ok=True)

    result = OCRResult(
        source_docx=docx_name,
        image_name=image_name,
        pdf_path=ocr_pdf,
        permit_no_zh=permit_zh,
        permit_no_id=permit_id,
        full_text=full_text,
    )

    if not result.is_match:
        ocr_pdf.unlink(missing_ok=True)

    return result


def main():
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    docx_files = sorted(INPUT_DOCX_DIR.glob("*.docx"))
    if not docx_files:
        logger.error(f"在 {INPUT_DOCX_DIR} 找不到 .docx 檔案")
        return

    logger.info(f"找到 {len(docx_files)} 個 .docx,開始處理")
    logger.info(f"前處理:{'啟用' if ENABLE_PREPROCESS else '停用'} "
                f"(放大={UPSAMPLE_FACTOR}x, 去雜訊核={DENOISE_KERNEL})")
    logger.info(f"OCR 語言:{OCR_LANGUAGES}")

    all_results: list[OCRResult] = []

    for docx_path in docx_files:
        logger.info(f"─── 處理 {docx_path.name} ───")

        try:
            images = extract_images_from_docx(docx_path)
        except Exception as e:
            logger.error(f"無法解壓 {docx_path.name}:{e}")
            continue

        if not images:
            logger.warning(f"  {docx_path.name} 內沒有圖片")
            continue

        logger.info(f"  發現 {len(images)} 張圖片")

        for idx, (img_name, img_bytes, suffix) in enumerate(images, start=1):
            logger.info(f"  [{idx}/{len(images)}] 處理 {img_name}")
            result = process_single_image(
                docx_path.name, img_name, img_bytes, suffix, idx
            )
            if result is None:
                continue

            all_results.append(result)
            if result.is_match:
                logger.info(
                    f"    ✓ 符合條件!"
                    f"許可證號={result.permit_no_zh} / "
                    f"No ijin={result.permit_no_id}"
                )

    matched_count = sum(1 for r in all_results if r.is_match)
    logger.info(f"\n═══ 處理完成 ═══")
    logger.info(f"總處理圖片:{len(all_results)}")
    logger.info(f"符合條件:{matched_count}")

    if matched_count > 0:
        try:
            write_results_to_sheet(all_results)
        except Exception as e:
            logger.error(f"寫入 Google Sheets 失敗:{e}")
            backup_path = OUTPUT_DIR / "results_backup.csv"
            import csv
            with open(backup_path, "w", encoding="utf-8-sig", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(["來源檔案", "圖片名稱", "許可證號(中文)", "No ijin(印尼文)"])
                for r in all_results:
                    if r.is_match:
                        writer.writerow([
                            r.source_docx, r.image_name,
                            r.permit_no_zh or "", r.permit_no_id or ""
                        ])
            logger.info(f"已備份至 {backup_path}")


if __name__ == "__main__":
    main()
