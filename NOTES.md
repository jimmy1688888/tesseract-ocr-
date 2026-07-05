# 接手筆記（換機／下次繼續用）

台灣移工 OCR 專案：勞動契約 DOCX → OCR → Google Sheets。此檔記錄跨機器接手所需脈絡（不含任何機密）。

## Repo 拓撲（重要）

- **單一來源**：此 repo（github.com/jimmy1688888/tesseract-ocr-）同時是**執行目錄 + 版控**。
- 過去曾有第二個本機 clone 造成版本分歧，已收斂刪除。**往後只在一個目錄編輯 / commit / push**，別再另開 clone。

## 換機／首次設定：git 不含的本機檔

以下都被 `.gitignore`，clone 後需自行補齊：

1. `service_account.json` — Google Service Account 金鑰（放專案根目錄）
2. `data/agency_roster.json` — 仲介名冊
   - 手動下載：瀏覽器開 `https://apiservice.mol.gov.tw/OdService/download/A17000000J-020001-QHy`（data.gov.tw dataset 6682 的 JSON），存到 `data/agency_roster.json`
   - 不在程式內連網下載（mol.gov.tw 憑證缺 Subject Key Identifier，Python SSL 會擋）
3. `data/AllData.json` — 全國門牌地址庫（2.4MB，供 `address_db.py` 地址標準化）
   - 來源：data.gov.tw 全國「縣市→行政區→路街」中英對照 JSON。
   - **最省事作法：直接把舊機的 `data/AllData.json` 複製過來**（此檔 gitignore、內容穩定、不常變）。三個本機檔（service_account / agency_roster / AllData）都建議一起用隨身碟或雲端硬碟帶過來，比重新下載可靠。
   - 缺此檔時 `address_db` 會安靜跳過，雇主地址標準欄留空，不中斷主流程。
4. `samples/employer/` — 測試契約圖（含個資，不上版控；自行放置）
5. `pip install -r requirements.txt`；Tesseract 安裝於 `C:\Program Files\Tesseract-OCR\`

## 已完成：許可證 → 仲介機構查表

- `permit_lookup.py`：讀本地 `data/agency_roster.json`，依許可證查 機構名稱/地址/電話。
  - 分支感知：`2340` → 母公司；`2340-2` → 該分公司（不回退母公司）；查無即查無。
- `pipeline.py` 的 `_row_to_sheet_values`：Sheets 由 4 欄擴為 **7 欄**
  - A=來源 B=許可證 C=status D=reason **E=機構名稱 F=機構地址 G=電話**
  - 已真實寫入 Sheets 驗證通過。**Sheet 標題列需自行補 E/F/G 標題**。

## 已完成：雇主資料擷取（`employer_extract.py`）

- **雇主 ≠ 仲介機構**，不在名冊 → 必須 OCR。用 Google Vision `DOCUMENT_TEXT_DETECTION`。
- 契約頁偵測（特徵字命中）→ ROI 上緣 35% 裁切 → 去紅章 → Vision 全文 → 逐行解析。
- 產出欄位：`雇主名稱_中/英`、`地址_中/英`(OCR 原文)、`電話`（「同號出現兩次＝雇主」啟發式，很穩）。
- 5 張樣本實測：電話 5/5、地址中/英約 9/10；名稱在紅章蓋住時 OCR 層即亂碼（非解析問題）。

## 已完成：地址標準化（`address_db.py`，用 `data/AllData.json`）

- 以官方地址庫逐層（縣市→行政區→路街→段/號樓）比對，還原標準地址並附**官方中英譯名 + 郵遞區號**。
- `employer_extract._standardize_address()` 非破壞式補上 4 欄：`地址_中_標準`、`地址_英_標準`、`郵遞區號`、`地址_比對`(bool)。原 OCR 地址保留不覆寫。
- 已處理的坑：臺↔台 異體字、縣市/行政區同名衝突、段號(N段/中文數字二段)、英文門牌取「號」碼、樓層英譯置前(3F.)。中文標準地址依官方庫輸出（保留正體「臺」，不轉台，此為使用者拍板）。
- Vision 版路名命中率約 6/7（~86%）。

## 進行中：把雇主欄位接進 Google Sheets（**下次主要工作**）

Sheets 已由 7 欄擴為 **15 欄**，但 **H~O 目前只是預留、恆為空字串** —— `employer_extract` 尚未接進 `pipeline.py`（pipeline 內沒有 import 它）。

- **已完成的部分**：`pipeline.py` 的 `_row_to_sheet_values` 尾端已 `+ _employer_cols(r)`，並定義 `_EMPLOYER_COL_KEYS` 常數作為**唯一填值點**。欄位規劃（標準／OCR 各佔一欄、中英分開）：
  - A–G（同前）｜H=雇主名稱_中 I=雇主名稱_英
  - J=雇主地址_中(標準) K=雇主地址_中(OCR) L=雇主地址_英(標準) M=雇主地址_英(OCR)
  - N=郵遞區號 O=雇主電話
- **下次要做的整合三步**：
  1. 在 pipeline 對每份 docx 的契約頁跑一次 `employer_extract.extract_employer_fields()`（多一次 Vision，注意成本/流程順序）；
  2. 把回傳的雇主欄位（key 已對齊 `_EMPLOYER_COL_KEYS`）掛到該 docx 的 row dict 上；
  3. row 帶了這些 key，`_employer_cols` 就會自動填入，**不必再改組列邏輯**。
- **提醒**：Sheet 標題列要手動補上 **E~O**（程式 append 不寫標題列）。

### 一句話喚醒新 session

> 台灣移工 OCR 專案（repo 已 clone，data/AllData.json 已備妥）。permit_lookup 許可證查表、
> employer_extract 雇主擷取、address_db 地址標準化都已完成；pipeline 的 Sheets 也已預留 H~O 15 欄。
> 現在要做**最後整合**：把 `employer_extract.extract_employer_fields()` 接進 `pipeline.py`，
> 讓雇主欄位（key 對齊 `_EMPLOYER_COL_KEYS`）流入 row dict、自動填滿 H~O（見 NOTES「整合三步」）。
