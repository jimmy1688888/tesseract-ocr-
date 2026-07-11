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
3. `data/custom_roads.json` — **自建補充路名**（進版控，clone 即有，不用另外準備）。
   政府開放資料未收錄的新路（如大園區長發一路、新屋區富聯路）加在這裡，
   `address_db._load()` 會自動併入對應行政區；**不要直接改 AllData.json**
   （重新下載會洗掉手改）。格式見檔內既有條目。
   加之前先查庫確認真的沒有：`python address_db.py <路名關鍵字> [縣市] [行政區]`
   （經正規化比對，臺/台、鍾/鐘異體都吸收，比手動搜 JSON 可靠）。
   具名巷（后尾巷型）可加；數字巷（文化路100巷型）不要加，巷弄由門牌尾巴處理。
4. `data/AllData.json` — 全國門牌地址庫（2.4MB，供 `address_db.py` 地址標準化）
   - 來源：data.gov.tw 全國「縣市→行政區→路街」中英對照 JSON。
   - **最省事作法：直接把舊機的 `data/AllData.json` 複製過來**（此檔 gitignore、內容穩定、不常變）。三個本機檔（service_account / agency_roster / AllData）都建議一起用隨身碟或雲端硬碟帶過來，比重新下載可靠。
   - 缺此檔時 `address_db` 會安靜跳過，雇主地址標準欄留空，不中斷主流程。
4. `samples/employer/` — 測試契約圖（含個資，不上版控；自行放置）
5. `pip install -r requirements.txt`；Tesseract 安裝於 `C:\Program Files\Tesseract-OCR\`

### 同事機（不熟終端機者）

以 `git clone` 取得專案後，日常操作全靠三個批次檔：`安裝.bat`（首次：環境檢查＋裝套件）、
`執行.bat`（日常：docx 丟 `docs\` 後雙擊）、`更新.bat`（git pull＋同步套件）。
⚠️ .bat 為 **Big5 編碼＋CRLF**（cmd 原生），勿用 UTF-8 另存；`.gitattributes` 已鎖 `*.bat eol=crlf`。

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
- 已處理的坑：臺↔台 異體字、縣市/行政區同名衝突、段號(N段/中文數字二段)、英文門牌取「號」碼、樓層英譯置前(3F.、樓之N→3F.-N)、門牌數字與單位間的 Vision 斷詞空格（「38 號」）。中文標準地址依官方庫輸出（保留正體「臺」，不轉台，此為使用者拍板）。
- **英文行錨定**（防紅章行首汙染，使用者提出的洞見）：印章常蓋中文行首毀掉縣市
  （「中共 雄市左營區…」），但英文地址的縣市/區慣例在**行尾**而倖存。
  `normalize_address(cn, en_text=…)`：中文縣市僅模糊命中或比不到時，用英文行
  「官方英名精確含（取最長，防 Taipei/New Taipei 子字串）」反查縣市+區為錨，
  回頭用中文行配路名/門牌（中文路名通常完好；契約英譯常非官方，英文路名只做
  ≥0.9 高門檻備援）。殘影中文字黏進英文行時該行不當 地址_英 輸出，
  但仍傳給錨定用（employer_extract 的 en_hint）。實測救回 32099/32100 全欄位。
- Vision 版路名命中率約 6/7（~86%）。

## 已完成：雇主欄位接進 Google Sheets（H~O 整合）

Sheets 每列 **15 欄**，H~O 由 `pipeline.py` 於寫入前自動填入：

- 欄位（標準／OCR 各佔一欄、中英分開）：
  - A–G（同前）｜H=雇主名稱_中 I=雇主名稱_英
  - J=雇主地址_中(標準) K=雇主地址_中(OCR) L=雇主地址_英(標準) M=雇主地址_英(OCR)
  - N=郵遞區號 O=雇主電話
- 實作方式（`pipeline.py`）：
  - `collect_employer_fields(docx_files)`：主流程步驟 4b 前對每份 docx 跑一次
    `employer_extract.extract_employer_from_docx()`（契約頁偵測=本機 Tesseract、
    只有選中那頁送 Vision，**每 docx 一次 Vision 呼叫**），結果存
    `_EMPLOYER_FIELDS_BY_DOCX` 快取。manual_review 列也擷取（許可證待複核≠雇主資料無效）。
  - `_employer_cols(r)`：組列時依 `source_docx` 查快取；row dict 自帶同名 key 優先。
    擷取失敗/找不到契約頁 → 該列 H~O 留空，絕不中斷主流程。
- **提醒**：Sheet 標題列要手動補上 **E~O**（程式 append 不寫標題列）。
- 樓層「之N」英譯已支援：`2樓之1` → `2F.-1`（address_db）。
- **雇主 ROI 截圖**：每份 docx 送 Vision 的契約頁裁切（去紅章**前**原貌）自動存到
  `scan_results/employer_crops/{docx主檔名}_{圖檔名}`，人工複查 H~O 時直接開圖對照。

### 一句話喚醒新 session

> 台灣移工 OCR 專案（repo 已 clone，data/AllData.json 已備妥）。permit_lookup 許可證查表、
> employer_extract 雇主擷取、address_db 地址標準化、pipeline H~O 整合**全部完成**
> （含雇主 ROI 截圖存 scan_results/employer_crops 供人工複查），
> `python pipeline.py` 即可端到端跑 docs/ → Sheets 15 欄。
> 下一步候選：對 docs/ 新批（32088 起）全跑驗證 H~O 實際寫入品質。
