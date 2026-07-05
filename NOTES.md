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
3. `samples/employer/` — 測試契約圖（含個資，不上版控；自行放置）
4. `pip install -r requirements.txt`；Tesseract 安裝於 `C:\Program Files\Tesseract-OCR\`

## 已完成：許可證 → 仲介機構查表

- `permit_lookup.py`：讀本地 `data/agency_roster.json`，依許可證查 機構名稱/地址/電話。
  - 分支感知：`2340` → 母公司；`2340-2` → 該分公司（不回退母公司）；查無即查無。
- `pipeline.py` 的 `_row_to_sheet_values`：Sheets 由 4 欄擴為 **7 欄**
  - A=來源 B=許可證 C=status D=reason **E=機構名稱 F=機構地址 G=電話**
  - 已真實寫入 Sheets 驗證通過。**Sheet 標題列需自行補 E/F/G 標題**。

## 進行中：雇主資料擷取（`employer_extract.py`，WIP）

- **雇主 ≠ 仲介機構**，不在名冊 → 必須 OCR。用 Google Vision `DOCUMENT_TEXT_DETECTION`。
- 5 張樣本實測：
  - 電話 **5/5**（「同號出現兩次＝雇主」啟發式，很穩）
  - 地址（中/英）約 **9/10**（偶有紅章雜尾）
  - 名稱較弱：紅章蓋在名稱上時，OCR 層就是亂碼（非解析問題）
- Vision API 已確認開通可用（不需再開）。

### 待決策（下次接手先選）

- 請參考AllData.json，是否可以依照此JSON提出更好的與OCR出的英文地址對照出中文地址

### Sheets 欄位規劃（接在 G 之後）

- H=雇主名稱(中) I=雇主名稱(英) J=地址(中) K=地址(英) L=電話（中英都存）

### 一句話喚醒新 session

> 台灣移工 OCR 專案（repo 已 clone）。permit_lookup 許可證查表完成、Sheets A–G。
> 現在繼續 `employer_extract.py`（Vision DOCUMENT_TEXT_DETECTION 擷取雇主名稱中/英、
> 地址中/英、電話，規劃寫 Sheets H–L）。請幫我確認目前employer_extract.PY 篩選地址的邏輯，並參考AllData.json，是否可以依照此JSON提出更好的與OCR出的英文地址對照出中文地址
