# DOCX OCR to Google Sheets

批次處理 Word (.docx) 內嵌圖片，以 **Tesseract** 辨識移工證件上的「mol 號碼 / 許可證號」，
再用 **Google Cloud Vision** 交叉比對，最後把結果（含依許可證查到的**仲介機構名稱／地址／電話**）
寫入 Google Sheets。

針對含**紅色印章**的雙語文件設計，採 ROI 區域掃描 + 多數票 + 雙引擎交叉驗證，只有兩引擎一致才自動 key-in，
其餘標記人工審查，避免單一 OCR 誤判直接寫入。

> 目前版本為**印尼版**（Tesseract 語言 `ind+eng`）。泰國／其他國籍為對應的具名版本檔。

---

## 📋 目錄

- [功能特色](#功能特色)
- [系統需求](#系統需求)
- [安裝步驟](#安裝步驟)
- [Google 服務設定](#google-服務設定)
- [仲介名冊設定](#仲介名冊設定)
- [使用方式](#使用方式)
- [輸出欄位](#輸出欄位)
- [處理流程](#處理流程)
- [參數調整](#參數調整)
- [檔案結構](#檔案結構)
- [常見問題](#常見問題)

---

## 功能特色

- 🗂 **批次處理**：自動掃描 `docs/` 內所有 `.docx`
- 🖼 **圖片擷取**：從 `.docx`（zip）解出每份文件的所有內嵌圖片
- 📐 **prefilter 分類**：依圖片數量分為 `small` / `large`，走不同掃描策略
- 🔍 **ROI 掃描 + 多數票**：只掃 mol / permit 指定區域，多組設定投票取共識值
- 🤝 **雙引擎交叉比對**：Tesseract 值與 Google Vision 值一致才自動 key-in；差 1 字元／不一致 → 人工審查
- 🏢 **許可證查名冊**：以許可證號查勞動部名冊，自動補上機構**名稱／地址／電話**
- 👤 **雇主資料擷取**：契約頁 OCR（Google Vision）取雇主**名稱／地址／電話**（雇主≠仲介，不在名冊）
- 🏠 **地址標準化**：用官方全國門牌庫校正 OCR 地址，補上**官方中英譯名 + 郵遞區號**
- 📊 **自動寫入 Google Sheets**：分 `keyed-in` 與 `manual_review` 兩種狀態，理由寫在備註欄
- 🧾 **matches.csv**：Tesseract 掃描結果留存本地，可重跑後段而不必重掃

---

## 系統需求

### Python
- Python **3.10 以上**（使用 `tuple[str, ...]`、`X | None` 等新型別語法）

### 外部程式
| 程式 | 用途 | 下載 |
|---|---|---|
| Tesseract OCR | 文字辨識引擎（需 `ind`、`eng` 語言包） | https://github.com/UB-Mannheim/tesseract/wiki |

程式預設 Tesseract 路徑為 `C:\Program Files\Tesseract-OCR\tesseract.exe`（可於 `pipeline.py` 頂部修改）。

### 硬體
- **CPU**：i5 / Ryzen 5 以上　**RAM**：8GB 以上
- **GPU**：用不到（Tesseract 不吃 GPU；Vision 在雲端運算）

---

## 安裝步驟

### Step 1：Python 套件

```bash
pip install -r requirements.txt
```

### Step 2：Tesseract OCR

**Windows**
1. 至 https://github.com/UB-Mannheim/tesseract/wiki 下載安裝檔
2. 安裝時勾選語言包：✅ Indonesian (`ind`)、✅ English (`eng`)
3. 確認：
   ```bash
   tesseract --version
   tesseract --list-langs   # 應含 ind、eng
   ```

**macOS** `brew install tesseract tesseract-lang`　**Linux** `sudo apt install tesseract-ocr tesseract-ocr-ind`

---

## Google 服務設定

需要一個 **Service Account**，並啟用兩個 API。

### Step 1：建立 / 選擇 Google Cloud 專案
前往 https://console.cloud.google.com/

### Step 2：啟用 API
「API 和服務」→「程式庫」啟用：
- **Google Sheets API**
- **Cloud Vision API**

### Step 3：建立 Service Account 金鑰
「憑證」→「建立憑證」→「服務帳戶」→ 建好後 →「金鑰」→「新增金鑰」→ JSON。
下載後存為 **`service_account.json`** 放專案根目錄，或設環境變數 `GOOGLE_APPLICATION_CREDENTIALS` 指向它。

### Step 4：分享試算表
把金鑰內 `client_email` 加入你的 Google Sheet 共用，權限**編輯者**。
並在 `pipeline.py` 設定 `SPREADSHEET_ID` 與 `SHEET_NAME`（預設 `工作表1`）。

---

## 仲介名冊設定

許可證 → 機構名稱／地址／電話 由 `permit_lookup.py` 查本地名冊，需手動下載一次：

1. 瀏覽器開啟（來源：data.gov.tw dataset 6682「跨國人力仲介公司許可名冊」JSON）：
   `https://apiservice.mol.gov.tw/OdService/download/A17000000J-020001-QHy`
2. 存成 **`data/agency_roster.json`**

> 刻意用官方 JSON 而非 Excel 匯出的 CSV：Excel 會把分支許可證（如 `1-73`）誤判成日期、並吃掉前導零。
> 程式不自動連網下載（mol.gov.tw 憑證缺 Subject Key Identifier，Python SSL 會擋），更新方式為手動重新下載覆蓋。
> 名冊檔缺少時只是機構欄留空，不會中斷主流程。

---

## 使用方式

### 資料夾結構

```
專案根目錄/
├── pipeline.py                 # 主程式
├── permit_lookup.py            # 許可證 → 機構資料 查表
├── service_account.json        # Google 金鑰（自行放置）
├── docs/                       # 放要處理的 .docx
│   └── *.docx
├── data/
│   └── agency_roster.json      # 名冊（自行下載）
└── scan_results/               # 自動建立：matches.csv、裁切圖
```

### 執行

```bash
python pipeline.py                       # 掃 docs/ 全部
python pipeline.py --log-level DEBUG      # 詳細日誌
python pipeline.py --file path/to/x.docx  # 單檔（自動 DEBUG）
python pipeline.py --file x.docx --image image2.jpeg   # 指定單張圖
python pipeline.py --roi mol              # 只掃某個 ROI（mol / permit_upper …）
```

### 不熟終端機？雙擊批次檔即可

| 檔案 | 時機 | 動作 |
|---|---|---|
| `安裝.bat` | 首次安裝 | 檢查 Python / Tesseract / 語言包 → 裝套件 → 檢查本機檔是否齊全 |
| `執行.bat` | 日常使用 | 把 .docx 丟進 `docs\` 後雙擊，跑完視窗停留可看結果 |
| `更新.bat` | 取得新版 | `git pull` + 同步套件（專案需以 `git clone` 取得，ZIP 解壓版無法一鍵更新） |

> 三個 .bat 為 **Big5 編碼 + CRLF 換行**（cmd 原生格式），請勿用 UTF-8 編輯器另存，否則中文會變亂碼。

---

## 輸出欄位

寫入 Google Sheets 每列 **15 欄**：

| 欄 | 內容 | 來源 |
|---|---|---|
| A | 來源 docx | |
| B | 許可證 / final_value | OCR 投票 |
| C | status（`keyed-in` 或 `manual_review`）| 決策 |
| D | reason（歸因說明：多數票、雙引擎一致、部分命中…）| 決策 |
| E | 機構名稱 | 許可證查名冊 |
| F | 機構地址 | 許可證查名冊 |
| G | 電話（仲介機構）| 許可證查名冊 |
| H | 雇主名稱_中 | 契約 OCR |
| I | 雇主名稱_英 | 契約 OCR |
| J | 雇主地址_中(標準) | 官方地址庫校正 |
| K | 雇主地址_中(OCR) | 契約 OCR 原文 |
| L | 雇主地址_英(標準) | 官方地址庫校正 |
| M | 雇主地址_英(OCR) | 契約 OCR 原文 |
| N | 郵遞區號 | 官方地址庫 |
| O | 雇主電話 | 契約 OCR |

- E/F/G 由 B 欄許可證查名冊補上（**仲介機構**，非雇主）；查無或無值則留空。
- H~O 為**雇主**資料（契約甲方）：寫入前對每份 docx 的契約頁跑一次 Vision OCR 自動填入；擷取失敗或找不到契約頁時該列留空。標準地址（J/L）未命中官方庫時留空，以 OCR 原文（K/M）為準。
- **Sheet 標題列需自行補上 E~O 各欄標題**（程式為 append，不會寫標題列）。

---

## 處理流程

```
docs/*.docx
    │ ① zipfile 解出內嵌圖片
    ▼
prefilter 依圖片數分 small / large
    │ ② Tesseract ROI 掃描（mol / permit）+ 多數票 → scan_results/matches.csv
    ▼
vision_submit 分流
    ├─ 直接 key-in（高信心 / 多數票）
    ├─ 送 Google Vision（低信心 / 部分命中 / 需複核）
    └─ 人工審查（全無命中）
    │ ③ Vision 判讀 + 三層交叉比對（雙引擎一致才自動 key-in）
    ▼
④ 許可證查名冊 → 補機構名稱/地址/電話（E~G）
    ▼
⑤ 契約頁 OCR（employer_extract + address_db）→ 雇主名稱/地址/電話 + 官方譯名/郵遞區號（H~O）
    ▼
⑥ 批次 atomic 寫入 Google Sheets（keyed-in / manual_review）
```

---

## 參數調整

集中在 `pipeline.py` 上方設定區：

```python
INPUT_DIR  = Path("./docs")          # 來源 .docx
OUTPUT_DIR = Path("./scan_results")  # matches.csv 與裁切圖
TESS_LANG  = "ind+eng"               # Tesseract 語言

SPREADSHEET_ID = "..."               # 目標試算表 ID
SHEET_NAME     = "工作表1"            # 分頁名稱

CONF_KEY_IN       = 55   # 高於此值直接 key-in，低於則送 Vision
CONF_VOTE_MIN     = 45   # permit 多數票平均信心低於此值才送 Vision
CONF_MOL_VOTE_MIN = 50   # small docx mol 多數票高於此值直接 key-in
SMALL_DOCX_THRESHOLD = 3 # 圖片數 ≤ 此值 → small
```

---

## 檔案結構

```
pipeline.py            # 主程式（prefilter→scan→vision_submit→Sheets）
permit_lookup.py       # 許可證 → 機構名稱/地址/電話 查表
employer_extract.py    # 契約 OCR → 雇主名稱/地址/電話（Google Vision）
address_db.py          # 地址標準化：用官方地址庫校正 OCR 地址、補中英譯名+郵遞區號
test_*.py, conftest.py # pytest 測試（decide / verify / aggregate / helpers）
docs/                  # 輸入 .docx（不進版控）
data/agency_roster.json# 仲介名冊（自行下載，不進版控）
data/AllData.json      # 全國門牌地址庫（自行下載/複製，不進版控）
data/custom_roads.json # 自建補充路名（官方庫缺的新路加這裡，進版控）
scan_results/          # matches.csv 與裁切圖（不進版控）
service_account.json   # Google 金鑰（不進版控）
```

執行測試：

```bash
python -m pytest -q
```

---

## 常見問題

**Q1：`tesseract is not installed or it's not in your PATH`**
Tesseract 沒裝或路徑不對。確認已安裝，並檢查 `pipeline.py` 頂部 `tesseract_cmd` 路徑。

**Q2：`Could not find a language data file for "ind"`**
安裝 Tesseract 時沒勾印尼語言包。重裝勾選，或下載 `ind.traineddata` 放 `tessdata/`。

**Q3：Sheets 寫入失敗 / 403**
- `service_account.json` 未設定，或試算表沒分享給該 `client_email`
- 未啟用 Google Sheets API / Cloud Vision API

**Q4：機構名稱/地址/電話 欄空白**
名冊檔 `data/agency_roster.json` 不存在或許可證查無。前者請依「仲介名冊設定」下載。

**Q5：想重跑後段而不重掃**
`scan_results/matches.csv` 已保留 Tesseract 結果，可只跑 vision_submit → Sheets 段落（見程式內 `build_vision_queue`）。

**Q6：J 欄（標準地址）空白但 K 欄有值——路名不在官方庫怎麼辦？**

先用查詢指令確認庫內真的沒有（**不要手動搜 JSON**：庫用「臺」「鍾」等正體字，
拿 OCR 的「台」「鐘」去搜會誤判；指令走程式同一套正規化，異體字自動吸收）：

```bash
python address_db.py <路名關鍵字> [縣市] [行政區]

python address_db.py 長發              # 全國含「長發」的路
python address_db.py 大眾 宜蘭 五結     # 限縣市/行政區
```

查無、且確認 K 欄的 OCR 拼字沒錯 → 在 **`data/custom_roads.json`** 加一條
（照檔內既有格式：縣市/行政區/路名/英譯，英譯用郵局拼音如 `Changfa 1st Rd.`），
下次執行自動生效。注意：
- **具名巷**（如「后尾巷」）可以加，英譯結尾用 `Ln.`
- **數字巷**（如「文化路100巷」）**不要加**——巷弄由門牌自動處理，缺的話加主路即可
- 官方庫日後收錄同名路時不會重複，補充條目可留著

---

## 授權

依專案實際情況填寫（私人專案 / MIT / Apache 2.0）。
