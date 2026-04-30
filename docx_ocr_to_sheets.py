"""
批次處理 Word 檔 → 擷取圖片 → OCR 辨識許可證號 → 上傳 Google Sheets

使用方式:
    python docx_ocr_to_sheets.py \
        --input-dir ./word_files \
        --sheet-id 1aBcDeFgHiJkLmN... \
        --creds ./service_account.json \
        --worksheet "Sheet1"

需求套件:
    pip install opencv-python numpy gspread google-auth

需求系統工具:
    tesseract-ocr 與語言包 chi_tra, eng, ind
    Ubuntu/Debian: sudo apt install tesseract-ocr tesseract-ocr-chi-tra tesseract-ocr-ind
    macOS:        brew install tesseract tesseract-lang
"""

from __future__ import annotations

import argparse
import io
import logging
import re
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import cv2
import numpy as np

# -----------------------------------------------------------------------------
# 設定區 — 主要可調參數集中在此
# -----------------------------------------------------------------------------

# DOCX 內圖片預設放置路徑(Word 規範)
DOCX_MEDIA_PREFIX = "word/media/"
ALLOWED_IMAGE_EXT = {".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".gif"}

# OCR 設定
TESSERACT_LANGS = "chi_tra+eng+ind"   # 繁中 + 英文 + 印尼文
TESSERACT_PSM = "6"                   # 多行文字統一區塊
TESSERACT_OEM = "1"                   # LSTM 引擎

# 關鍵字 — 用來判斷一張圖是否為「含許可證號」的目標圖
LICENSE_KEYWORDS = ["許可證號", "認可編號", "No ijin", "No.ijin", "Noijin"]

# 許可證號擷取 regex
# 支援:「3219」、「I-45」這類格式
LICENSE_PATTERNS = [
    r"許\s*可\s*證\s*號\s*[:：]?\s*([A-Z]?-?\d{2,6})",
    r"認\s*可\s*編\s*號\s*[:：]?\s*([A-Z]?-?\d{2,6})",
    r"No\s*\.?\s*ijin\s*[:：]?\s*([A-Z]?-?\d{2,6})",
]

logger = logging.getLogger("docx_ocr")


# -----------------------------------------------------------------------------
# 資料結構
# -----------------------------------------------------------------------------

@dataclass
class OcrResult:
    """單一 docx 的處理結果"""
    filename: str
    license_no: str | None
    note: str = ""        # 失敗時的說明文字


# -----------------------------------------------------------------------------
# 步驟 1:從 docx 解出圖片
# -----------------------------------------------------------------------------

def extract_images_from_docx(docx_path: Path) -> Iterator[tuple[str, bytes]]:
    """
    Yield (image_name, image_bytes) for every embedded image.
    docx 本質是 zip,圖片放在 word/media/ 下。
    """
    try:
        with zipfile.ZipFile(docx_path, "r") as z:
            for name in z.namelist():
                if not name.startswith(DOCX_MEDIA_PREFIX):
                    continue
                ext = Path(name).suffix.lower()
                if ext not in ALLOWED_IMAGE_EXT:
                    continue
                yield name, z.read(name)
    except zipfile.BadZipFile:
        logger.error("檔案不是有效的 docx (zip 格式損毀): %s", docx_path)


# -----------------------------------------------------------------------------
# 步驟 2:影像前處理 — 針對「紅章蓋黑字」場景
# -----------------------------------------------------------------------------

def preprocess_for_stamped_scan(img_bgr: np.ndarray) -> np.ndarray:
    """
    紅章覆蓋黑字的掃描圖前處理。流程:
      紅色通道萃取 → 過取樣 3x → 對比拉伸 → 殘留淡紅置白 → 去雜訊

    為何取紅色通道:
      紅色印章在 R 通道呈高亮(接近白),黑字在三通道都呈暗。
      所以只看 R 通道 ≒ 把紅章「物理性」消除。
    """
    if img_bgr is None or img_bgr.size == 0:
        raise ValueError("空白影像")

    # 若是灰階或單通道,先升成 3 通道
    if len(img_bgr.shape) == 2:
        img_bgr = cv2.cvtColor(img_bgr, cv2.COLOR_GRAY2BGR)

    b, g, r = cv2.split(img_bgr)
    h, w = r.shape

    # 過取樣 3x — 對應 OCRmyPDF 的 --oversample,150 dpi 掃描檔特別需要
    up = cv2.resize(r, (w * 3, h * 3), interpolation=cv2.INTER_CUBIC)

    # 對比拉伸 + 殘留淡紅置白
    up = cv2.normalize(up, None, 0, 255, cv2.NORM_MINMAX)
    up[up > 180] = 255

    # 去雜訊(對應 OCRmyPDF 的 --clean)
    up = cv2.fastNlMeansDenoising(up, h=10)
    return up


def decode_image_bytes(data: bytes) -> np.ndarray | None:
    """把 bytes 解成 OpenCV 影像。失敗回 None。"""
    arr = np.frombuffer(data, np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    return img


# -----------------------------------------------------------------------------
# 步驟 3:OCR
# -----------------------------------------------------------------------------

def ocr_image(img: np.ndarray) -> str:
    """對前處理後的影像跑 Tesseract,回傳辨識文字。"""
    # Tesseract 吃檔案路徑,所以寫入暫存檔
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        cv2.imwrite(tmp.name, img)
        tmp_path = tmp.name

    try:
        result = subprocess.run(
            [
                "tesseract", tmp_path, "-",
                "-l", TESSERACT_LANGS,
                "--psm", TESSERACT_PSM,
                "--oem", TESSERACT_OEM,
            ],
            capture_output=True, text=True, timeout=60,
        )
        return result.stdout
    except subprocess.TimeoutExpired:
        logger.warning("Tesseract 逾時")
        return ""
    finally:
        Path(tmp_path).unlink(missing_ok=True)


# -----------------------------------------------------------------------------
# 步驟 4:從 OCR 文字擷取許可證號
# -----------------------------------------------------------------------------

def has_license_keyword(text: str) -> bool:
    """判斷這張圖是否為「含許可證號」的目標圖。"""
    return any(kw in text for kw in LICENSE_KEYWORDS)


def find_license_number(text: str) -> str | None:
    """從 OCR 文字擷取許可證號。回傳 '3219' 或 'I-45' 之類。"""
    for pat in LICENSE_PATTERNS:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            return m.group(1).upper()
    return None


# -----------------------------------------------------------------------------
# 步驟 5:處理單一 docx
# -----------------------------------------------------------------------------

def process_docx(docx_path: Path) -> OcrResult:
    """
    處理單一 docx:遍歷所有圖片,找出「含許可證號」的那張,回傳擷取結果。
    """
    images = list(extract_images_from_docx(docx_path))
    if not images:
        return OcrResult(docx_path.name, None, note="docx 中無圖片")

    candidates: list[tuple[str, str, str]] = []  # (image_name, ocr_text, license)

    for image_name, blob in images:
        img = decode_image_bytes(blob)
        if img is None:
            logger.debug("  圖片解碼失敗: %s", image_name)
            continue

        try:
            processed = preprocess_for_stamped_scan(img)
        except ValueError as e:
            logger.debug("  前處理失敗 %s: %s", image_name, e)
            continue

        text = ocr_image(processed)
        if not has_license_keyword(text):
            continue

        license_no = find_license_number(text)
        if license_no:
            candidates.append((image_name, text, license_no))
            logger.info("  ✓ %s → 許可證號: %s", image_name, license_no)

    if not candidates:
        return OcrResult(docx_path.name, None, note="未找到含許可證號的圖")

    # 若多張圖都偵測到,取第一張(通常 docx 內圖片順序穩定)
    _, _, license_no = candidates[0]
    note = "" if len(candidates) == 1 else f"找到 {len(candidates)} 張候選圖,取第一張"
    return OcrResult(docx_path.name, license_no, note=note)


# -----------------------------------------------------------------------------
# 步驟 6:上傳 Google Sheets
# -----------------------------------------------------------------------------

def upload_to_sheets(
    rows: list[OcrResult],
    sheet_id: str,
    creds_path: Path,
    worksheet_name: str,
    write_header: bool = True,
) -> None:
    """
    用 Service Account 寫入 Google Sheets。
    - 如果工作表是空的,先寫表頭
    - 否則 append 到最後一列
    """
    import gspread
    from google.oauth2.service_account import Credentials

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_file(str(creds_path), scopes=scopes)
    client = gspread.authorize(creds)

    sh = client.open_by_key(sheet_id)
    try:
        ws = sh.worksheet(worksheet_name)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=worksheet_name, rows=1000, cols=10)
        logger.info("已建立新工作表: %s", worksheet_name)

    # 準備資料列
    data_rows = [[r.filename, r.license_no or ""] for r in rows]

    # 表頭處理:若工作表是空的,加入表頭
    if write_header and not ws.get_all_values():
        ws.append_row(["檔名", "許可證號"])
        logger.info("已寫入表頭")

    # 一次性 batch 寫入,減少 API quota 消耗
    if data_rows:
        ws.append_rows(data_rows, value_input_option="USER_ENTERED")
        logger.info("已寫入 %d 列至 Google Sheets", len(data_rows))


# -----------------------------------------------------------------------------
# 主程式
# -----------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="批次 OCR Word 檔內的許可證號圖片並上傳 Google Sheets",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--input-dir", required=True, type=Path,
                        help="放置 .docx 檔案的資料夾")
    parser.add_argument("--sheet-id", required=True,
                        help="Google Sheets 的 ID(URL 中 /d/ 後面那串)")
    parser.add_argument("--creds", required=True, type=Path,
                        help="Service Account JSON 檔路徑")
    parser.add_argument("--worksheet", default="Sheet1",
                        help="目標工作表名稱(預設 Sheet1)")
    parser.add_argument("--dry-run", action="store_true",
                        help="只跑 OCR 不上傳 Sheets,結果印到 stdout")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="顯示詳細 log")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    if not args.input_dir.is_dir():
        logger.error("資料夾不存在: %s", args.input_dir)
        sys.exit(1)

    docx_files = sorted(args.input_dir.glob("*.docx"))
    # 過濾掉 Word 暫存檔(以 ~$ 開頭)
    docx_files = [f for f in docx_files if not f.name.startswith("~$")]

    if not docx_files:
        logger.error("找不到任何 .docx 檔案於 %s", args.input_dir)
        sys.exit(1)

    logger.info("找到 %d 個 docx 檔,開始處理", len(docx_files))

    results: list[OcrResult] = []
    for i, docx_path in enumerate(docx_files, 1):
        logger.info("[%d/%d] %s", i, len(docx_files), docx_path.name)
        try:
            result = process_docx(docx_path)
        except Exception as e:
            logger.exception("處理失敗 %s", docx_path.name)
            result = OcrResult(docx_path.name, None, note=f"例外: {e}")
        results.append(result)

    # 印出結果摘要
    print("\n" + "=" * 60)
    print(f"處理完成:{len(results)} 個檔案")
    print("=" * 60)
    success = sum(1 for r in results if r.license_no)
    print(f"  成功擷取許可證號: {success}")
    print(f"  未擷取到:        {len(results) - success}")
    print()
    for r in results:
        status = "✓" if r.license_no else "✗"
        line = f"  {status} {r.filename:<40} {r.license_no or '-'}"
        if r.note:
            line += f"  ({r.note})"
        print(line)
    print()

    # 上傳 Sheets
    if args.dry_run:
        logger.info("--dry-run 模式,不上傳 Google Sheets")
        return

    if not args.creds.is_file():
        logger.error("找不到認證檔: %s", args.creds)
        sys.exit(1)

    try:
        upload_to_sheets(results, args.sheet_id, args.creds, args.worksheet)
    except Exception:
        logger.exception("上傳 Google Sheets 失敗")
        sys.exit(1)

    logger.info("全部完成 ✓")


if __name__ == "__main__":
    main()
