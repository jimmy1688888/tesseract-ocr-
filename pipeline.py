# -*- coding: utf-8 -*-
"""
pipeline.py
===========
整合流程：prefilter → scan → 送審決策 → Google Sheets key-in

執行步驟：
  1. prefilter  ：依圖片數量將 docx 分類為 small / large
  2. scan       ：Tesseract OCR，掃 mol / permit ROI，輸出 matches.csv
  3. 送審決策  ：讀取 matches.csv，每份 docx 一個去向(直接 key-in／送 Vision／反查救援／直接人工審查)
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
  共用套件 gcp-identity 由下述設定腳本安裝（pip install -e），不列在上面——
  它靠本機路徑安裝，而路徑在每台機器上不同。

Google 認證（程式內服務帳戶模擬，不使用金鑰檔）：
  本程式不讀取 service_account.json。操作者本人登入一次取得 ADC，程式再模擬
  服務帳戶換取短期權杖。模擬目標是 identity.py 的 SA_EMAIL 常數。

  新機器一次性設定：執行共用腳本 setup-google-adc.bat（每台機器一次，
  供該機器上所有專案共用）。它做的事等同於：
    gcloud auth application-default login
  ⚠️ 刻意「不加」--impersonate-service-account：ADC 是機器全域的單一檔案、
     只能記一個模擬目標，寫進去多專案就無法各用不同服務帳戶。模擬改由程式做，
     計費專案由 with_quota_project() 設定，不需要改 ADC 的 JSON 檔。

  前置條件：
    1. 操作者需具備該 SA 的 roles/iam.serviceAccountTokenCreator
       （授權後最長需 7 分鐘生效）
    2. 專案需啟用 IAM Service Account Credentials API（iamcredentials.googleapis.com）
    3. 目標試算表需分享給該 SA 的信箱（Drive 層級權限，與 IAM 無關）

  驗證憑證是否可用：
    python pipeline.py --preflight-only

  改用模擬而非金鑰檔的理由：不再有長期憑證檔案需要在機器間複製與保管，
  且稽核日誌會記錄「哪個真人模擬了這個 SA」，而非只看到 SA 本身。
  完整決策脈絡見 foreign-worker-query 的 docs/adr/0002。
"""

import csv
import json
import time
import logging
import argparse
import functools
import zipfile
import zlib
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Callable, NamedTuple, Optional

import pytesseract

# Google Vision
from google.cloud import vision as gvision
from google.api_core import exceptions as gax_exceptions

# Google Sheets
from googleapiclient.errors import HttpError

# Google 身分與用它建好的 client（不讀取任何金鑰檔，見 identity.py）。
# 本檔不持有身分：要 client 跟 google_clients 要，要身分跟 identity 要。
import identity
import google_clients

# 許可證 → 機構資料(名稱／地址／電話)查表(來源:data.gov.tw 6682 名冊)
from permit_lookup import PermitLookup

# 一批:這次要處理哪些 docx(完整路徑)。只是設定,結果不放進去(ADR-0009)。
from batch import Batch
from employer_result import EMPLOYER_COLUMNS, EmployerExtraction, ExtractionStatus
# 寫進 Sheets 的一列(A~D),結果在哪一步確定就在哪一步建好(#12)。
from sheet_row import SheetRow, SheetStatus

# docx 內嵌圖與圖序只有一份,與 employer_extract 共用(#5)。
import docx_images
# 像素前處理(轉正、裁切、前處理)也只有一份,與 employer_extract 共用(#16)。
from pixels import crop, open_upright

if TYPE_CHECKING:   # 只給型別標註看;執行期在 preflight 的函式裡才 import(見該段說明)
    from gcp_identity.checks import CheckResult

pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


# 名冊查表器惰性單例:整個 pipeline 只載一次;載入失敗則降級為 None(機構欄留空,不中斷)。
_PERMIT_LOOKUP: "PermitLookup | bool | None" = None


def _permit_lookup() -> "PermitLookup | None":
    global _PERMIT_LOOKUP
    if _PERMIT_LOOKUP is None:
        try:
            _PERMIT_LOOKUP = PermitLookup()
            logger.info(
                f"  ✔ 載入仲介名冊:母公司 {len(_PERMIT_LOOKUP.main_index)} 筆"
                f"／分支 {len(_PERMIT_LOOKUP.branch_index)} 筆"
            )
        except Exception as e:
            logger.warning(f"  ⚠ 名冊載入失敗,機構名稱/地址/電話欄將留空:{e!r}")
            _PERMIT_LOOKUP = False   # 降級標記,避免每列重試
    return _PERMIT_LOOKUP or None


# ═══════════════════════════════════════════════════════════════════════════
# ① 設定區（依實際環境修改）
# ═══════════════════════════════════════════════════════════════════════════

INPUT_DIR  = Path("./docs")
# 輸出目錄與它衍生的路徑在 batch.py(Batch.output_dir),跟著一批往下傳(ADR-0009)。
# 會讀哪些內嵌圖(副檔名集合)與圖序在 docx_images.py。

# Google Sheets：試算表 ID 與工作表名稱在 google_clients.py。
# 每列寫入 15 欄：A=source_docx, B=許可證, C=status, D=reason(A~D 即 SheetRow),
#              E=機構名稱, F=機構地址, G=電話（E~G 由 permit_lookup 查名冊補上）,
#              H=雇主名稱_中, I=雇主名稱_英, J=雇主地址_中(標準), K=雇主地址_中(OCR),
#              L=雇主地址_英(標準), M=雇主地址_英(OCR), N=郵遞區號, O=雇主電話
#              （H~O 由 employer_extract 於寫入前逐 docx 擷取；契約頁 OCR 失敗該列留空）
# 欄位順序由 _row_to_sheet_values() 組裝決定；若需改順序在那裡調整。
# 注意：Sheet 標題列需自行補上 E~O 各欄標題（程式 append 不會寫標題列）。
# 要模擬哪個服務帳號、用量記到哪個 GCP 專案：見 identity.py。

# 本檔只 import 自己用到的名字;別的 module 的名字到主人那裡取,不經這裡轉手(#59):
#   matches.csv 的列與寫讀      matches_csv
#   許可證判讀(ROI、多數票、分層掃描、一份 docx → 它的列)  permit_reading
#   送審決策與門檻              submission_decision
#   Vision 驗證                 vision_verification
# 送審決策:每份 docx 一個去向(門檻與判斷只在那裡,#40)。
from submission_decision import Decision, Destination, read_decisions  # noqa: E402
import matches_csv  # noqa: E402
from matches_csv import DocxClass, Roi  # noqa: E402
from permit_reading import (  # noqa: E402
    ROI_REGIONS, TESS_LANG, TIER1_IMAGES, Crop, Reading, TierOutcome,
    scan_docx, scan_image_large, scan_image_mol_only, unscannable)



# ═══════════════════════════════════════════════════════════════════════════
# ①' 共用工具：重試 decorator
# ═══════════════════════════════════════════════════════════════════════════

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
# ② 讀 docx 的圖(small/large 分類與寫列規則在 permit_reading.scan_docx)
# ═══════════════════════════════════════════════════════════════════════════

def read_docx_images(docx_path: Path) -> list[tuple[str, bytes]] | None:
    """docx 的內嵌圖;打不開回傳 None,不拋。

    一份壞檔不能讓整批中斷(#47):它照樣要在 matches.csv 與 Sheets 各有一列。
    「打不開」= 不是有效的 zip(BadZipFile,含 CRC 錯),或目錄完好、壓縮資料損壞
    (zlib.error,讀到那張圖才炸)。檔案被鎖、權限不足不算 —— 那不是「不是有效的
    docx 檔」,照舊拋出。
    """
    try:
        return docx_images.read_images(docx_path)
    except (zipfile.BadZipFile, zlib.error):
        return None


# ═══════════════════════════════════════════════════════════════════════════
# ⑦ 送審決策（每份 docx 一個決定，見 submission_decision）
# ═══════════════════════════════════════════════════════════════════════════

class SubmissionQueue(NamedTuple):
    """一批的送審決策依去向分好(build_vision_queue 的回傳)。每份 docx 恰好在其中一處。"""
    vision: list[Decision]          # 送 Google Vision 的決定(只有去向是送 Vision 的)
    rescue: list[SheetRow]          # 反查救援的種子列:B 欄是候選值(單一判讀低信心)或空(全無命中)
    keyin: list[SheetRow]           # 直接 key-in:結果已定,列已建好
    direct_review: list[SheetRow]   # docx 讀不到圖,直接人工審查(不送 Vision、不反查)


def build_vision_queue(csv_path: Path) -> SubmissionQueue:
    """
    讀取 matches.csv,每份 docx 做一個送審決策(submission_decision.decide),依去向分好
    回傳(見 SubmissionQueue)。

    一份 docx 只有一個決定(一份契約只有一個許可證,Sheets 上只有一列)。
    """
    vision_decisions: list[Decision] = []
    keyin_rows:       list[SheetRow] = []
    rescue_rows:      list[SheetRow] = []
    direct_review:    list[SheetRow] = []

    for d in read_decisions(csv_path):
        if d.destination is Destination.直接KEYIN:
            keyin_rows.append(SheetRow(d.source_docx, d.candidate_value,
                                       SheetStatus.KEYED_IN, d.reason))
            continue
        if d.destination is Destination.直接人工審查:
            direct_review.append(SheetRow(d.source_docx, "", SheetStatus.MANUAL_REVIEW, d.reason))
            continue
        if d.destination is Destination.反查救援:
            # 種子列:救不回就照原樣寫(B 欄是候選值或空)
            rescue_rows.append(SheetRow(d.source_docx, d.candidate_value,
                                        SheetStatus.MANUAL_REVIEW, d.reason))
            continue
        vision_decisions.append(d)

    return SubmissionQueue(vision_decisions, rescue_rows, keyin_rows, direct_review)


# ═══════════════════════════════════════════════════════════════════════════
# ⑧ Google Vision(驗證本身在 vision_verification;這裡只有 client)
# ═══════════════════════════════════════════════════════════════════════════

from vision_verification import run_vision_items  # noqa: E402


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
def vision_text(img_path: str) -> str:
    """送截圖給 Google Vision OCR,回傳全文(讀不到或 API 回報錯誤時為空字串)。

    這是 Vision 驗證的 client(seam 後的 adapter):只負責「截圖 → 全文」,
    取號交給 permit_patterns.permit_in_text,與 Tesseract 同一把尺。

    Vision API 屬讀取操作(idempotent),重試安全。連線錯誤或 5xx/429 會自動重試;
    4xx 其他錯誤(403/404 等)直接拋出,不會浪費時間重試。
    拿不到憑證也直接拋出、整批中止(不降級),見 test_no_credentials.py。
    """
    client = google_clients.vision_client()
    with open(img_path, "rb") as f:
        content = f.read()
    image    = gvision.Image(content=content)
    response = client.text_detection(image=image)
    if response.error.message:
        logger.error(f"Vision API 錯誤: {response.error.message}")
        return ""
    return response.full_text_annotation.text if response.full_text_annotation else ""


# ═══════════════════════════════════════════════════════════════════════════
# ⑨ Google Sheets key-in
# ═══════════════════════════════════════════════════════════════════════════

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
    service = google_clients.sheets_service()
    body = {"values": values}
    service.spreadsheets().values().append(
        spreadsheetId=google_clients.SPREADSHEET_ID,
        range=f"{google_clients.SHEET_NAME}!A1",
        valueInputOption="RAW",
        insertDataOption="INSERT_ROWS",
        body=body,
    ).execute()


def _agency_cols(permit_value: str) -> list[str]:
    """依許可證查名冊,回傳 [機構名稱, 機構地址, 電話];查無或無值 → 三個空字串。"""
    pl = _permit_lookup()
    info = pl.lookup(permit_value) if (pl and permit_value) else None
    if not info:
        return ["", "", ""]
    return [info["機構名稱"], info["機構地址"], info["電話"]]


# H~O 欄：雇主資料（employer_extract 產出）。寫入 Sheets 前先對每份 docx 跑一次
# collect_employer_fields()（契約頁偵測 → ROI → 去紅章 → Vision），回傳
# {檔名: 雇主擷取結果}，由 main 交給組列（依 source_docx 查表填入）與疑慮報表。
# 哪一欄放哪個值見 employer_result.EMPLOYER_COLUMNS。沒擷取時(擷取狀況不是擷取成功)該 docx 的 H~O 留空、L 寫原因。
# 結果走回傳值，不寄放在模組層或 Batch 上（見 ADR-0009、#25）。
#
# L 欄原為「英文標準地址」,已取消輸出:契約英譯沒有單一正確寫法,官方譯名並不比
# 契約上的更權威,兩欄並列只是讓人多比對一次卻分不出對錯。英文一律以 OCR 原文
# (M 欄)為準,騰出的 L 欄改放疑慮標示。
# 與 D 欄 reason 仍刻意分開:D 欄專講許可證判讀,一個欄位不承載兩種語意(ADR-0003)。

def collect_employer_fields(batch: Batch) -> dict[str, EmployerExtraction]:
    """對這一批每份 docx 擷取雇主欄位，回傳 {檔名: 雇主擷取結果}（每份一次 Vision 呼叫）。

    契約頁偵測用本機 Tesseract 特徵字計分,只有選中的那一頁送 Vision。
    送 Vision 的 ROI 裁切圖(去紅章前原貌)同步存到 batch.employer_crop_dir,
    檔名 {docx主檔名}_{圖檔名},供人工複查。
    任何單檔失敗只記 warning、該檔 H~O 留空,絕不中斷主流程;
    employer_extract 或其相依(address_db/AllData.json)缺失時整段安靜跳過。
    例外:拿不到憑證(建不起 Vision client)不降級,往上拋讓整批中止 ——
    與許可證判讀同一個決定,見 test_no_credentials.py。

    Vision 全文在這裡就存檔(write_employer_texts),不放進回傳值:它只供離線重放,
    下游沒有任何一步要讀它,而回傳的欄位每組一列都會查一次。疑慮報表則要讀回傳
    的欄位,所以交給 main 接。
    """
    try:
        from employer_extract import extract_employer_from_docx
    except Exception as e:
        logger.warning(f"  ⚠ employer_extract 載入失敗,雇主欄(H~O)本次全部留空:{e!r}")
        return {}
    client = google_clients.vision_client()
    employer_by_docx: dict[str, EmployerExtraction] = {}
    texts: dict[str, dict] = {}
    for dp in batch.docx_files:
        name = dp.name
        try:
            result = extract_employer_from_docx(
                str(dp), client=client, crop_dir=str(batch.employer_crop_dir))
        except Exception as e:
            logger.warning(f"  ⚠ {name}: 雇主擷取失敗({e!r}),該列 H~O 留空")
            employer_by_docx[name] = EmployerExtraction(ExtractionStatus.擷取失敗, detail=repr(e))
            continue
        # 全文另存,不留在快取裡:快取每列都會查,夾一大塊文字只是負擔。
        src = result.source
        if src.text:
            texts[name] = {"image": src.image, "text": src.text}
        employer_by_docx[name] = replace(result, source=replace(src, text=""))
        if result.status is not ExtractionStatus.擷取成功:
            got = result.values.雇主名稱_中
            filled = f"H 取自切結書頁={got!r},其餘留空" if got else "H~O 留空"
            logger.info(f"  {name}: {result.status.value}({result.detail}) → {filled}"
                        f"（截圖 {src.crop or '無'}）")
        else:
            doubt = result.疑慮標示
            v = result.values
            logger.info(
                f"  {name}: 雇主={v.雇主名稱_中!r}"
                f" 電話={v.電話!r}"
                f" 郵遞={v.郵遞區號!r}"
                f"（契約頁 {src.image} → 截圖 {src.crop}）"
                + (f" ⚠ {doubt}" if doubt else "")
            )
    write_employer_texts(batch, texts)
    return employer_by_docx


def write_employer_texts(batch: Batch, texts: dict[str, dict]) -> int:
    """把本次各 docx 的 Vision 全文存成 JSON,回傳筆數。

    供離線重放:改了抽取規則後,不必再花 Vision 呼叫就能對真實資料驗到底。
    寫檔失敗只記 warning——這是輔助產物,不該拖垮已經跑完的主流程。
    """
    if not texts:
        return 0
    try:
        batch.employer_texts_json.parent.mkdir(parents=True, exist_ok=True)
        batch.employer_texts_json.write_text(
            json.dumps(texts, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception as e:
        logger.warning(f"  ⚠ Vision 全文存檔失敗({e!r}),本次無法離線重放")
        return 0
    logger.info(f"  Vision 全文 {len(texts)} 份 → {batch.employer_texts_json}")
    return len(texts)


def write_doubt_report(batch: Batch, employer_by_docx: dict[str, EmployerExtraction]) -> int:
    """把本次所有帶疑慮標示的 docx 寫成 CSV,回傳筆數(無疑慮則不產檔)。

    欄位刻意帶上 OCR 原文與標準值,讓人不必開 Sheets 就能判斷是不是誤報。
    """
    rows = [(name, r.疑慮標示,
             r.values.雇主名稱_中, r.values.雇主名稱_英, r.values.地址_中,
             r.values.地址_中_標準, r.values.郵遞區號)
            for name, r in sorted(employer_by_docx.items())
            if r.疑慮標示]
    if not rows:
        return 0
    try:
        batch.doubt_report_csv.parent.mkdir(parents=True, exist_ok=True)
        with open(batch.doubt_report_csv, "w", encoding="utf-8-sig", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["docx", "疑慮", "雇主名稱_中", "雇主名稱_英",
                    "地址_中(OCR)", "地址_中(標準)", "郵遞區號"])
            w.writerows(rows)
    except Exception as e:      # 報表產不出來不該拖垮主流程
        logger.warning(f"  ⚠ 疑慮報表寫入失敗({e!r}),本次僅記於 log")
        return len(rows)
    logger.info(f"  疑慮標示 {len(rows)} 筆 → {batch.doubt_report_csv}")
    return len(rows)


@dataclass(frozen=True)
class AgencyPick:
    """反查救援從名冊挑一家的結果。

    permit:救回的許可證;電話多家要人工擇一、或救不回時為空。
    note  :接在 D 欄理由後面的反查管道說明;救不回時為空。
    """
    permit: str = ""
    note: str = ""


def pick_agency(block: dict, roster) -> AgencyPick:
    """次頁台仲區塊({phones, name, address})→ 名冊挑一家。純函式:不開檔、不打 Vision。

    roster 要有 agencies_by_phone / agencies_by_phone_prefix / best_by_name /
    best_by_address(PermitLookup)。反查優先序(結果一律 manual_review、D 欄註明管道
    供人工核對):
      ① 電話精確唯一命中 → 最可靠,直接填 B。
      ② 電話前綴唯一命中(OCR 少 1~2 碼容錯)→ 填 B;比通用後段名稱可靠,故排名稱前。
      ③ (後備)名稱、地址各比名冊相似度,取相似度(CONF)高者填 B。
      ④ 電話多家、且前綴/名稱/地址皆救不回 → 不填 B,列出各家供人工擇一。
    """
    # ── 電話反查(最可靠):唯一命中優先,多家暫記待人工 ──
    ambiguous = None    # (matches, ph):第一支命中多家的電話
    for ph in block["phones"]:
        matches = roster.agencies_by_phone(ph)
        if len(matches) == 1:
            # ① 電話精確唯一命中
            info = matches[0]
            return AgencyPick(info["許可證"], (
                f"[電話反查救回] 次頁台仲電話 {ph} → "
                f"許可證 {info['許可證']}／{info['機構名稱']}(仍請人工核對)"))
        if matches and ambiguous is None:
            ambiguous = (matches, ph)

    # ② 電話『前綴』唯一命中(OCR 少 1~2 碼容錯;比通用後段名稱可靠,故排在名稱前)
    for ph in block["phones"]:
        mp = roster.agencies_by_phone_prefix(ph)
        if len(mp) == 1:
            info = mp[0]
            return AgencyPick(info["許可證"], (
                f"[電話前綴反查救回·電話OCR可能少碼] 次頁台仲電話 {ph} "
                f"前綴唯一命中 → 許可證 {info['許可證']}／{info['機構名稱']}(仍請人工核對)"))

    # ③ 電話後備:名稱、地址各與名冊比相似度,取「相似度(CONF)高者」填 B
    cand_n = roster.best_by_name(block["name"]) if block.get("name") else None
    cand_a = roster.best_by_address(block["address"]) if block.get("address") else None
    picks = [(c, lbl) for c, lbl in ((cand_n, "名稱"), (cand_a, "地址")) if c]
    if picks:
        best_c, via = max(picks, key=lambda x: x[0]["_ratio"])
        return AgencyPick(best_c["許可證"], (
            f"[{via}反查救回·電話無唯一命中] 次頁台仲{via} → "
            f"許可證 {best_c['許可證']}／{best_c['機構名稱']}"
            f"(名冊相似度 {best_c['_ratio']},仍請人工核對)"))

    # ④ 電話多家(模糊)且前綴/名稱/地址都救不回 → 列出台仲供人工擇一,不填 B
    if ambiguous:
        matches, ph = ambiguous
        cand = "、".join(f"{m['許可證']}／{m['機構名稱']}" for m in matches)
        return AgencyPick("", f"[電話反查·需人工擇一] 台仲電話 {ph} 命中{len(matches)} 家:{cand}")
    return AgencyPick()


def rescue_manual_review_via_agency_phone(
        batch: Batch, rows: list[SheetRow]) -> list[SheetRow]:
    """仲介反查救援:整頁 OCR 契約次一頁(1 次 Vision),以『台灣仲介』欄位標籤
    錨定區塊(台仲在左在右皆可,靠標籤非位置),取其「電話／名稱／地址」反查名冊
    回推許可證(挑哪一家見 pick_agency)。列進、列出:回傳與 rows 同序的列 —— 救回
    或要人工擇一的換成新列(B 欄換成救回的許可證、D 欄接上反查管道),救不回的原列
    照回;不改傳入的列。_agency_cols 會據許可證補 E~G。

    種子列是送審決策判成反查救援的 docx:B 欄是候選值(單一判讀低信心)或空(全無命中),
    救回就換成救回的許可證,救不回就留著。

    名冊/Vision 缺失時安靜跳過,不影響主流程。
    要開的 docx 跟 batch 查完整路徑(列只帶檔名),見 Batch.path_of。
    同一 docx 的次頁只送一次 Vision(block_cache),多列共用不重複計費。"""
    pl = _permit_lookup()
    if pl is None or not rows:
        return list(rows)
    try:
        from employer_extract import agency_block_from_next_page
        client = google_clients.vision_client()
    except Exception as e:
        logger.warning(f"  ⚠ 電話反查救援不可用(略過):{e!r}")
        return list(rows)
    out: list[SheetRow] = []
    rescued = 0
    block_cache: dict[str, dict] = {}   # source_docx → {phones,name,address}(避免重複送 Vision)
    for row in rows:
        docx_name = row.source_docx
        docx_path = batch.path_of(docx_name)
        if docx_path is None:
            out.append(row)
            continue
        if docx_name not in block_cache:
            try:
                block_cache[docx_name] = agency_block_from_next_page(str(docx_path), client=client)
            except Exception as e:
                logger.warning(f"  ⚠ {docx_name} 次頁仲介區塊擷取失敗:{e!r}")
                block_cache[docx_name] = {"phones": [], "name": "", "address": ""}
        pick = pick_agency(block_cache[docx_name], pl)
        if pick.note:
            # 要人工擇一時沒有許可證:B 欄留種子值(全無命中是空、單一判讀是候選值)
            row = replace(row, permit=pick.permit or row.permit,
                          reason=f"{row.reason}；{pick.note}")
            rescued += bool(pick.permit)
            logger.info(f"  {'↻' if pick.permit else '⚠'} {docx_name} {pick.note}")
        out.append(row)
    logger.info(f"  反查救回 {rescued}/{len(rows)} 筆(填許可證+機構欄)")
    return out


def _employer_cols(docx: str, employer_by_docx: dict[str, EmployerExtraction]) -> list[str]:
    """依 EMPLOYER_COLUMNS 取雇主 8 欄;這份 docx 沒有雇主擷取結果就全空。"""
    result = employer_by_docx.get(docx)
    return [result.cell(k) if result else "" for _col, k in EMPLOYER_COLUMNS]


def _row_to_sheet_values(row: SheetRow,
                         employer_by_docx: dict[str, EmployerExtraction]) -> list[str]:
    """把一列轉成 Sheets 的十五欄(A~D 即 SheetRow、E~G 機構、H~O 雇主)。十五欄的對應只在這裡。

    E~G 欄由 B 欄的許可證值查名冊補上;H~O 為雇主欄位,依 source_docx 查
    collect_employer_fields() 回傳的 employer_by_docx,未擷取或失敗則留空。其中 L 欄不是
    資料而是疑慮標示,說明中文標準地址(J)為何留空(見 ADR-0003)。
    """
    return ([row.source_docx, row.permit, row.status.value, row.reason]
            + _agency_cols(row.permit) + _employer_cols(row.source_docx, employer_by_docx))


def write_sheets_batched(rows: list[SheetRow],
                         employer_by_docx: dict[str, EmployerExtraction]) -> int:
    """
    一次性把所有列 atomic 寫入 Google Sheets(依 rows 的順序;status 在列上)。

    特性：
      1. 合併成一次 API 呼叫 → 中途網路失敗時不會出現「半成功」狀態。
      2. 5xx / 429 / 連線錯誤自動退避重試；4xx 直接拋出。

    employer_by_docx 是 collect_employer_fields() 的回傳值，用來填 H~O 欄。

    回傳實際寫入 Sheets 的列數。
    """
    values = [_row_to_sheet_values(r, employer_by_docx) for r in rows]

    if not values:
        logger.info("  （此次無新資料需寫入 Sheets）")
        return 0

    try:
        _sheets_append_atomic(values)
    except Exception as e:
        # batch atomic 的好處：失敗 = 完全沒寫入，可直接重跑 pipeline，不會有部份污染。
        # 注意極端例外：API 已寫入但回應遺失 → 重跑會產生重複；可手動比對 Sheets 內容排查。
        logger.error(
            f"❌ Sheets 批次寫入失敗（{len(values)} 筆未寫入）：{e!r}\n"
            f"   由於是單次 atomic append，Sheets 端應未寫入任何資料；"
            f"修正後可直接重跑 pipeline。"
        )
        raise

    logger.info(f"  ✔ 已 atomic 寫入 Google Sheets {len(values)} 筆（含多種 status）")
    return len(values)


# ═══════════════════════════════════════════════════════════════════════════
# ⑩ 主流程
# ═══════════════════════════════════════════════════════════════════════════

def _save_review_images(review_dir: Path, docx_path: Path,
                        images: list[tuple[str, bytes]]) -> list[str]:
    """把原圖存到人工比對資料夾，回傳已存檔名。

    用於「全無命中」的 docx：留下關鍵頁原圖，人工可直接開來對照缺漏原因。
    存哪幾張由 permit_reading.scan_docx 決定;存整張原圖（非 ROI 裁切）。
    """
    review_dir.mkdir(parents=True, exist_ok=True)
    saved: list[str] = []
    for name, data in images:
        dst = review_dir / f"{docx_path.stem}_{name}"
        dst.write_bytes(data)
        saved.append(dst.name)
    return saved


def _write_crops(out_dir: Path, image_bytes: bytes, crops: list[Crop]) -> None:
    """把一張圖判讀出的裁切圖寫到這一批的輸出目錄(路徑掃描端已填進列,#61)。

    掃描只決定要寫什麼;寫檔(與它需要的解碼、轉正)只在這裡(#18)。
    """
    if crops:
        upright = open_upright(image_bytes)
        for c in crops:
            path = out_dir / c.path
            path.parent.mkdir(parents=True, exist_ok=True)
            crop(upright, c.roi).save(path)


def run_scan(batch: Batch, image_filter: str = "",
             roi_filter: str = "", full_scan: bool = False, *,
             read_large: Callable[..., Reading | None] = scan_image_large,
             read_small: Callable[..., Reading] = scan_image_mol_only) -> Path:
    """掃這一批的 docx，輸出 matches.csv，回傳 csv 路徑。

    每份 docx 只讀一次;它要寫哪些列由 permit_reading.scan_docx 決定,這裡只做
    I/O(寫裁切圖、比對原圖、matches.csv)、計數與 log。
    所有產出（matches.csv、裁切圖、無命中複查圖）都寫在 batch.output_dir 底下。

    image_filter：若非空，配合單一 docx 只處理該檔名的圖（對應 --image）。
                  對不到要在呼叫前擋下(main 的 _image_filter_miss);這裡遇到就拋。
    roi_filter  ：若非空，只掃指定的 ROI（對應 --roi）。
    full_scan   ：True 則關掉分層，每張圖都掃（對應 --full-scan）。
    read_large / read_small：一張圖 → 判讀,預設 permit_reading 的 Tesseract 版本。
                  測試傳假的進來,就不必跑 Tesseract。
    """
    out_dir = batch.output_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = batch.matches_csv

    total = hits = upper_hits = lower_hits = 0
    large_docs = tier1_docs = expanded_docs = 0
    t0 = time.time()

    with matches_csv.writer(csv_path) as writer:
        for docx_path in batch.docx_files:
            docx_name = docx_path.name
            images = read_docx_images(docx_path)
            scan = scan_docx(
                docx_name, images,
                read_large=lambda name, data: read_large(docx_name, name, data,
                                                         roi_filter=roi_filter),
                read_small=lambda name, data: read_small(docx_name, name, data),
                full_scan=full_scan, image_filter=image_filter)

            image_bytes = dict(images or [])
            for result, crops in scan.rows:
                _write_crops(out_dir, image_bytes.get(result.image_name, b""), crops)
                writer.write(result)
                hits += result.hit
                upper_hits += result.hit_roi is Roi.PERMIT_UPPER
                lower_hits += result.hit_roi is Roi.PERMIT_LOWER

            total += scan.scanned
            if scan.docx_class is DocxClass.LARGE:
                large_docs += 1
                tier1_docs += scan.tier is TierOutcome.第一層命中
                expanded_docs += scan.tier is TierOutcome.擴掃

            if scan.review_images:
                saved = _save_review_images(batch.no_hit_review_dir, docx_path,
                                            scan.review_images)
                logger.info(
                    f"  ⚠ {docx_name} {scan.docx_class}全無命中"
                    f"，存原圖供比對 {saved}"
                )

    elapsed = round(time.time() - t0, 1)
    logger.info(f"掃描完成：{total} 張圖，命中 {hits} 張，耗時 {elapsed}s")
    if upper_hits + lower_hits > 0:
        logger.info(f"  permit 上半命中：{upper_hits} 張 / 下半命中：{lower_hits} 張")
    if large_docs:
        # 這行是分層規則的體檢報告：命中率一旦崩掉,代表文件版面變了、第一層
        # 押錯位置,此時每份都會擴掃、總時間反而比全掃更長(ADR-0005)。
        mode = "全掃模式" if full_scan else f"第一層{sorted(TIER1_IMAGES)}"
        logger.info(
            f"  {mode}：{tier1_docs}/{large_docs} 份一次命中，"
            f"擴掃 {expanded_docs} 份"
        )
    return csv_path


# ═══════════════════════════════════════════════════════════════════════════
# ⑨' preflight：啟動前環境與憑證檢查
# ═══════════════════════════════════════════════════════════════════════════
# 這一段存在的理由：改用 ADC 模擬之後，「能不能跑」取決於六件散在不同地方的事
# （Tesseract、ADC 憑證、模擬身分、TokenCreator 權限、iamcredentials API、
# 試算表分享設定）。任何一項沒設好，程式原本都會在跑到一半時才炸，而且錯誤
# 訊息長得都很像 403，很難分辨是哪一層。
#
# preflight 把這些檢查提前到啟動的前三秒，並且——這才是重點——每個失敗都直接
# 印出對應的修復指令。新機器上手時，照著錯誤訊息貼指令就會通。
#
# 刻意不做的事：不呼叫 Vision API。建 client 不花額度，實際送圖要花 1 unit，
# 為了健康檢查每次啟動燒一格額度不划算。Vision 的權限問題會在 Sheets 檢查
# （同一組憑證、同一條模擬鏈路）就先被抓到。

# 檢查結果一律用共用套件的 CheckResult：它帶著 blame（該由執行者或管理者處理）
# 與 skipped（因上游失敗而略過），render 靠這兩個欄位分區、計數。本檔曾經重造過
# 一份只有四個字串的，於是 skipped 整個丟掉、略過只能印成失敗（#4）。
# 全部在函式裡延遲 import：pipeline 不該因為只有 preflight 用到的套件沒裝就
# 整個 import 不了。

def _check_tesseract() -> "CheckResult":
    """Tesseract 執行檔與語言包。"""
    from gcp_identity.checks import CheckResult
    exe = pytesseract.pytesseract.tesseract_cmd
    if not Path(exe).exists():
        return CheckResult(
            "Tesseract 執行檔", False, f"找不到 {exe}",
            "安裝 Tesseract-OCR，或修改 pipeline.py 頂端的 "
            "pytesseract.pytesseract.tesseract_cmd 指向實際安裝路徑",
        )
    try:
        ver = pytesseract.get_tesseract_version()
    except Exception as e:
        return CheckResult("Tesseract 執行檔", False, repr(e),
                           "確認 Tesseract 可正常執行")
    # 語言包：TESS_LANG 形如 "ind+eng"
    want = set(TESS_LANG.split("+"))
    try:
        have = set(pytesseract.get_languages(config=""))
    except Exception:
        # 舊版 pytesseract 沒有 get_languages()，跳過語言包檢查而非誤報失敗
        return CheckResult("Tesseract 執行檔", True, f"v{ver}（語言包未檢查）")
    missing = want - have
    if missing:
        return CheckResult(
            "Tesseract 語言包", False, f"缺少 {sorted(missing)}",
            "重跑 Tesseract 安裝程式並勾選對應語言，或手動把 .traineddata "
            "放進 tessdata 資料夾",
        )
    return CheckResult("Tesseract 執行檔", True, f"v{ver}，語言包 {TESS_LANG} 齊全")


def _gchecks():
    """共用套件的檢查（gcp_identity.checks），用到時才 import。

    pipeline 本身不該因為一個只有 preflight 用到的套件沒裝，就整個 import 不了。
    """
    from gcp_identity import checks
    return checks


def _check_adc() -> "CheckResult":
    """ADC 憑證是否存在、且是「執行者本人的使用者憑據」。

    型別判斷（金鑰檔殘留／雙層模擬／本人憑據）全在共用套件裡，因為每個專案都
    會踩到同一批坑，不該有多份副本。

    共用套件另外回傳 credentials，這裡刻意丟掉：「解析到金鑰檔」這類情況下它
    仍是一個物件，只是型別不對。下游該看的是檢查結果，不是有沒有拿到 creds。
    """
    result, _ = _gchecks().check_adc()
    return result


def _check_token() -> "CheckResult":
    """實際換一次權杖。

    這是唯一能同時驗證「TokenCreator 角色」與「iamcredentials API 已啟用」
    的檢查——前面的檢查都只看本機檔案，這一項才真的打到 Google。
    注意換的是「模擬後」的權杖，不是執行者本人的憑據——後者 refresh 成功不代表
    你有權限模擬服務帳號，所以本函式刻意不接受 credentials 參數。
    """
    return _gchecks().check_token(identity.current())


def _check_sheets() -> "CheckResult":
    """讀取試算表 metadata：一次驗證 scope、Drive 分享權限、工作表名稱。"""
    from gcp_identity.checks import ADMIN, CheckResult
    spreadsheet_id, sheet_name = google_clients.SPREADSHEET_ID, google_clients.SHEET_NAME
    try:
        service = google_clients.sheets_service()
        meta = service.spreadsheets().get(
            spreadsheetId=spreadsheet_id,
            fields="properties.title,sheets.properties.title",
        ).execute()
    except HttpError as e:
        status = getattr(e.resp, "status", 0)
        # 計費專案不可用也是 403，但修的是 GCP IAM 而不是 Drive 共用。
        # 必須排在分享那一支之前，否則會叫人去重做一件已經做對的事。
        qfix = status == 403 and _gchecks().quota_project_fix(identity.current(), str(e))
        if qfix:
            return CheckResult(_SHEETS_CHECK, False, f"HTTP {status}: {e}"[:200],
                               qfix, blame=ADMIN)
        if status == 403:
            fix = ("開啟該試算表 →「共用」→ 把服務帳戶的信箱加為「編輯者」。"
                   "（這是 Google Drive 的分享權限，與 GCP IAM 是兩套系統）")
        elif status == 404:
            fix = f"確認 google_clients.py 的 SPREADSHEET_ID 是否正確：{spreadsheet_id}"
        else:
            fix = "檢查 Sheets API 是否已在專案中啟用"
        return CheckResult(_SHEETS_CHECK, False, f"HTTP {status}: {e}"[:200], fix)
    except Exception as e:
        return CheckResult(_SHEETS_CHECK, False, repr(e)[:200],
                           "檢查網路與憑證設定")

    titles = [s["properties"]["title"] for s in meta.get("sheets", [])]
    if sheet_name not in titles:
        return CheckResult(
            "工作表名稱", False,
            f"找不到工作表 {sheet_name!r}，實際有：{titles}",
            "修改 google_clients.py 的 SHEET_NAME 為上列其中之一",
        )
    return CheckResult(
        "Google Sheets 存取", True,
        f"《{meta['properties']['title']}》→ 工作表 {sheet_name!r} 可存取",
    )


def _check_input(batch: Batch) -> "CheckResult":
    """這一批有沒有東西可處理。沒有就不必開跑，所以這一項是閘門。

    判斷本身是 Batch.problem()，main 在 --skip-preflight 時也看同一個，兩處才
    不會說出對不起來的話。

    刻意不檢查仲介名冊：名冊是降級來源不是閘門，見 _warn_if_roster_unavailable。
    """
    from gcp_identity.checks import CheckResult
    problem = batch.problem()
    if problem is None:
        return CheckResult("輸入資料", True, f"{len(batch.docx_files)} 份 .docx 待處理")
    if batch.single_file:
        fix = "確認 --file 指定的路徑，或改放進 ./docs 後不帶 --file 重跑"
    else:
        fix = f"建立 {batch.source.resolve()} 並放入待處理的 .docx"
    return CheckResult("輸入資料", False, problem, fix)


def _warn_if_roster_unavailable() -> None:
    """名冊載不起來就說出來，但不擋。

    為什麼這不是一個 CheckResult：preflight 的結果列只放「不通過就不該開跑」的
    項目，而名冊不屬於那一類。_permit_lookup 的降級設計、_agency_cols 查無時回
    三個空字串、以及這行訊息本身，三處都承諾「E~G 留空但不中斷」——把它放進結果
    列會讓 run_preflight 回傳 False、main 直接 return，整批不跑，與承諾相反。

    但降級也不等於沉默：E~G 會整欄是空的，人得先知道，否則會以為是名冊查無此證。
    """
    if _permit_lookup() is None:
        logger.warning(
            "  ⚠ 仲介名冊載入失敗：機構名稱／地址／電話（E~G 欄）本批將留空。"
            "許可證判讀不受影響，整批照常處理。"
        )


# 憑證檢查失敗時，下游「略過」的那幾列要用和真正跑起來時同一個名稱，否則同一項
# 在不同情況下會印成兩個名字。模擬服務帳號那一項的名稱是共用套件 check_token
# 寫死的，test_google_identity 釘住兩邊一致。
_TOKEN_CHECK  = "模擬服務帳號"
_SHEETS_CHECK = "Google Sheets 存取"


def _skipped(name: str, because: str) -> "CheckResult":
    """因上游失敗而沒跑的那一項。不算失敗：根因已經在上游那一列。"""
    from gcp_identity.checks import CheckResult
    return CheckResult(name, True, f"（因{because}而略過）", skipped=True)


def run_preflight(batch: Batch) -> bool:
    """依序執行所有檢查，全部通過回傳 True。

    憑證相關的檢查是有順序相依的（沒有 ADC 就談不上換權杖，換不到權杖就談不上
    Sheets），所以前一項失敗時後續標記為「略過」而不是失敗：一個根因只印一行，
    使用者一眼看到真正要修的是哪一項。

    渲染交給共用套件的 render：它依 blame 把失敗分成「自己處理」與「找管理者」
    兩區，略過的不計入。順序則刻意不用共用套件的 google_checks —— 它多跑一個
    check_gcloud，沒裝 gcloud CLI 的機器會被擋，而執行期根本不需要 gcloud。
    """
    checks = _gchecks()
    results = [_check_tesseract()]

    adc = _check_adc()
    results.append(adc)
    if adc.ok:
        tok = _check_token()
        results.append(tok)
        if tok.ok:
            results.append(_check_sheets())
        else:
            results.append(_skipped(_SHEETS_CHECK, "無法取得權杖"))
    else:
        results.append(_skipped(_TOKEN_CHECK, "ADC 憑證不可用"))
        results.append(_skipped(_SHEETS_CHECK, "ADC 憑證不可用"))

    results.append(_check_input(batch))

    passed = checks.render(results, "preflight：環境與憑證檢查")

    # 降級提醒不是檢查項（見 _warn_if_roster_unavailable），但人要在開跑前看到。
    # 即使 --file 指定單一檔案也照印——E~G 一樣會留空。
    _warn_if_roster_unavailable()

    if not passed:
        logger.error("preflight 未通過：排除後重跑；只想重新檢查可用 --preflight-only")
    return passed


def _image_filter_miss(docx_files: list[Path], image: str) -> str | None:
    """--image 在哪份 docx 對不到圖就說,連同那份有哪些圖檔名;全對得到回傳 None。"""
    for dp in docx_files:
        imgs = read_docx_images(dp)
        nothing = unscannable(imgs)
        if nothing:
            return f"找不到圖檔 {image!r}({dp.name}:{nothing[1]})"
        names = [n for n, _ in imgs]
        if image not in names:
            return f"找不到圖檔 {image!r}({dp.name} 內含:{names})"
    return None


def main(opts: argparse.Namespace) -> None:
    """主流程。接收已解析的 CLI options，不再依賴 module-level 全域變數。"""
    # 一批:這次要掃哪些 docx。指定 --file 時就是那一份,不看 ./docs。
    batch = Batch.from_file(Path(opts.file)) if opts.file else Batch.from_dir(INPUT_DIR)

    # ── 步驟 0：preflight ──────────────────────────────────────────────────
    # 放在最前面的理由：掃描階段可能跑數十分鐘，跑完才發現 Sheets 沒權限、
    # 結果寫不進去，等於整批白做。憑證問題要在花時間之前就攔下來。
    if not opts.skip_preflight:
        if not run_preflight(batch):
            return
    if opts.preflight_only:
        return

    # preflight 的輸入閘門看的也是這一句;--skip-preflight 時只剩這裡擋。
    problem = batch.problem()
    if problem:
        logger.error(problem)
        return
    docx_files = list(batch.docx_files)
    if batch.single_file:
        logging.getLogger().setLevel(logging.DEBUG)
        logger.debug(f"單檔 DEBUG 模式：{batch.source.name}")

    # --image 只在單檔除錯用:對不到就是指令打錯,直接結束 —— 不寫 Sheets、不跑雇主
    # 擷取(那要花 Vision),不套用「每份 docx 一列」的規則。要在掃描之前擋:
    # 掃描一開始就覆寫 matches.csv。small/large 分類在掃描時邊掃邊印。
    if opts.image:
        missing = _image_filter_miss(docx_files, opts.image)
        if missing:
            logger.error(missing)
            return

    # ── 步驟 2：scan → matches.csv ─────────────────────────────────────────
    logger.info("── 步驟 2：Tesseract 掃描 ──")
    csv_path = run_scan(batch, image_filter=opts.image, roi_filter=opts.roi,
                        full_scan=opts.full_scan)
    logger.info(f"  matches.csv 已輸出：{csv_path.resolve()}")

    # ── 步驟 3：送審決策 ─────────────────────────────────────────────────
    logger.info("── 步驟 3：送審決策 ──")
    queue = build_vision_queue(csv_path)
    vision_decisions, rescue_rows, keyin_rows = queue.vision, queue.rescue, queue.keyin
    direct_review = queue.direct_review
    logger.info(f"  直接 key-in：{len(keyin_rows)} 筆")
    logger.info(f"  送 Vision  ：{len(vision_decisions)} 筆")
    logger.info(f"  反查救援   ：{len(rescue_rows)} 件")
    logger.info(f"  直接人工審查(讀不到圖):{len(direct_review)} 件")

    # ── 步驟 4a：送 Google Vision + 交叉比對 ──────────────────────────
    # 雙引擎(Tesseract + Vision)完全一致才自動 key-in;其餘進人工審查批次,
    # 避免任何單一 OCR 引擎的判讀直接決定資料寫入。
    #
    # 額外規則(LIKELY_OCR_CONFUSION):兩引擎差 1 字元時,不自動填任何值,
    # 一律標人工審查、由人工輸入(兩候選與信心寫進 reason 供參考)。
    logger.info("── 步驟 4a：Google Vision 判讀 + 交叉比對 ──")
    vision_auto_keyin, vision_review_rows = run_vision_items(
        vision_decisions, read_text=vision_text)

    # ── 步驟 4b：統一寫入 Google Sheets ────────────────────────────────────
    # 簡化為兩個 status,理由欄(reason)負責歸因:
    #   KEYED_IN      = Tesseract 直接 key-in(高信心)  +  Vision-CONFIRMED(雙引擎一致)
    #   MANUAL_REVIEW = Vision 交叉比對失敗(各種不一致)  +  反查救援  +  讀不到圖(#47)
    #
    # 設計動機:自動 vs 人工的二分讓使用者只需依 status 篩選即可知道「哪些要看」。
    # 每列來源(Tesseract 直接 / Vision 確認 / 差 1 字元 / 僅 Vision / 無命中)
    # 都寫進 reason 欄,審查時看 reason 就知道 context。
    # ── 步驟 4b 前置①：雇主資料擷取（H~O 欄）──────────────────────────────
    # 每份 docx 一次 Vision 呼叫(契約頁偵測是本機 Tesseract,不花 API)。
    # 這裡對「本次要寫入的所有 docx」都擷取,含 manual_review 列——
    # 許可證待人工複核不代表雇主資料無效,先填好可省一次人工查找。
    logger.info("── 步驟 4b 前置①：雇主資料擷取（契約頁 OCR → H~O 欄）──")
    employer_by_docx = collect_employer_fields(batch)
    write_doubt_report(batch, employer_by_docx)

    # ── 步驟 4b 前置②：反查救援（次頁台仲區塊 → 反查名冊 → 回推許可證）──
    # 全無命中或單一判讀低信心:整頁 OCR 契約次一頁(1 次 Vision)、以『台灣仲介』標籤
    # 錨定取電話／名稱／地址(台仲在左在右皆可),反查名冊回推許可證。救回者 B 欄換成
    # 救回的許可證、_agency_cols 補 E~G,仍標 manual_review、D 欄註明,供人工快速核對。
    if rescue_rows:
        logger.info("── 步驟 4b 前置②：反查救援 → 次頁台仲區塊反查名冊 ──")
        rescue_rows = rescue_manual_review_via_agency_phone(batch, rescue_rows)

    logger.info("── 步驟 4b：合併批次寫入 Google Sheets（atomic）──")
    auto_keyin_rows = keyin_rows + vision_auto_keyin
    # 讀不到圖(direct_review):B 空,D 是事實;E~G 跟著空
    review_rows = vision_review_rows + rescue_rows + direct_review
    written = write_sheets_batched(auto_keyin_rows + review_rows, employer_by_docx)
    logger.info(
        f"  本次寫入彙總:auto_keyin={len(auto_keyin_rows)} 筆"
        f"(其中 Tesseract 直接 {len(keyin_rows)} 筆 + Vision 確認 {len(vision_auto_keyin)} 筆) "
        f"/ manual_review={len(review_rows)} 筆"
        f"(其中 Vision 交叉比對失敗 {len(vision_review_rows)} 筆 "
        f"+ 反查救援 {len(rescue_rows)} 件 "
        f"+ 讀不到圖 {len(direct_review)} 件)"
        f"(實際寫入 Sheets:{written} 筆)"
    )

    # ── 步驟 4c：列出人工審查清單，方便人類確認 ────────────────────────────
    if review_rows:
        logger.info(f"── 人工審查清單(共 {len(review_rows)} 件,reason 欄已標註原因)：")
        for row in review_rows:
            logger.info(f"    {row.source_docx}: {row.reason}")

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
    parser.add_argument("--full-scan", action="store_true",
                        help=(f"關掉分層掃描，large docx 的每張圖都掃"
                              f"（平時只掃 image{sorted(TIER1_IMAGES)}，"
                              f"拿不到 permit 多數票才擴掃）"))
    parser.add_argument("--preflight-only", action="store_true",
                        help="只執行環境與憑證檢查後結束，不處理任何 docx")
    parser.add_argument("--skip-preflight", action="store_true",
                        help="略過啟動檢查（確定環境沒問題、想省幾秒時使用）")
    opts = parser.parse_args()
    if opts.preflight_only and opts.skip_preflight:
        parser.error("--preflight-only 與 --skip-preflight 互斥")
    # 驗證 --roi（過去在 __main__ 內驗證後寫到 globals()，現於此處驗證後由 main 直接讀 opts.roi）
    if opts.roi:
        valid_rois = [str(r) for r in ROI_REGIONS]
        if opts.roi not in valid_rois:
            parser.error(f"--roi 必須為 {valid_rois}，收到 {opts.roi!r}")
    # --image 只在單檔除錯用(#47):整批帶 --image,任何一份沒那張圖就整批不跑
    if opts.image and not opts.file:
        parser.error("--image 只能配合 --file 使用")
    return opts


if __name__ == "__main__":
    opts = _parse_args()
    logging.getLogger().setLevel(getattr(logging, opts.log_level))
    main(opts)
