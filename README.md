# DOCX OCR to Google Sheets

批次處理 Word (.docx) 檔內嵌圖片，透過 OCR 辨識「許可證號（中文）」與「No ijin（印尼文）」，並自動寫入 Google Sheets。

針對含**紅色印章**的雙語切結書文件設計，整合進階影像前處理（紅通道萃取、過取樣、對比拉伸、中值去雜訊）大幅提升 OCR 辨識率。

---

## 📋 目錄

- [功能特色](#功能特色)
- [系統需求](#系統需求)
- [安裝步驟](#安裝步驟)
- [Google Sheets 設定](#google-sheets-設定)
- [使用方式](#使用方式)
- [參數調整](#參數調整)
- [常見問題](#常見問題)
- [檔案結構](#檔案結構)

---

## 功能特色

- 🗂 **批次處理**：自動掃描資料夾內所有 `.docx`
- 🖼 **圖片擷取**：從 `word/media/` 解出每份文件內的所有圖片
- 🎨 **進階前處理**：四步驟強化 OCR 準確率
  - 紅通道萃取（移除紅色印章）
  - 過取樣 2x（放大細節）
  - 對比拉伸（黑字更黑、白底更白）
  - 中值濾波（去除掃描雜訊）
- 🔍 **多語言 OCR**：繁體中文 + 印尼文 + 英文
- 📝 **關鍵字篩選**：自動找出含「許可證號」與「No ijin」的頁面
- 📊 **自動上傳**：辨識結果直接寫入 Google Sheets
- 💾 **離線備份**：上傳失敗時自動存 CSV 到本地

---

## 系統需求

### Python
- Python 3.10 以上（使用 `tuple[str, ...]` 等新型別語法）

### 外部程式（必裝）

| 程式 | 用途 | 下載 |
|---|---|---|
| Tesseract OCR | 文字辨識引擎 | https://github.com/UB-Mannheim/tesseract/wiki |
| Ghostscript | PDF 處理（OCRmyPDF 依賴） | https://www.ghostscript.com/releases/gsdnld.html |

### 硬體建議

- **CPU**：i5 / Ryzen 5 以上（多核心可加速）
- **RAM**：至少 8GB，推薦 16GB
- **硬碟**：SSD 強烈推薦
- **GPU**：用不到（Tesseract 不支援 GPU 加速）

---

## 安裝步驟

### Step 1：安裝 Python 套件

```bash
pip install -r requirements.txt
```

### Step 2：安裝 Tesseract OCR

**Windows：**
1. 至 https://github.com/UB-Mannheim/tesseract/wiki 下載安裝檔
2. 安裝時務必勾選以下語言包：
   - ✅ Chinese (Traditional) - `chi_tra`
   - ✅ Indonesian - `ind`
3. 安裝後將 Tesseract 加入系統 PATH（預設路徑：`C:\Program Files\Tesseract-OCR`）
4. 開啟 CMD 確認：
   ```bash
   tesseract --version
   tesseract --list-langs
   ```
   應看到 `chi_tra` 與 `ind` 在語言清單中。

**macOS：**
```bash
brew install tesseract tesseract-lang
```

**Linux：**
```bash
sudo apt install tesseract-ocr tesseract-ocr-chi-tra tesseract-ocr-ind
```

### Step 3：安裝 Ghostscript

**Windows：**
1. 至 https://www.ghostscript.com/releases/gsdnld.html 下載 64-bit 版本
2. 安裝後加入 PATH
3. 開啟 CMD 確認：
   ```bash
   gswin64c --version
   ```

**macOS：**
```bash
brew install ghostscript
```

**Linux：**
```bash
sudo apt install ghostscript
```

---

## Google Sheets 設定

需要建立 Service Account 才能讓程式自動寫入試算表。

### Step 1：建立 Google Cloud 專案

1. 前往 https://console.cloud.google.com/
2. 建立一個新專案（或選用現有專案）

### Step 2：啟用 API

1. 左側選單 → **API 和服務** → **程式庫**
2. 啟用以下兩個 API：
   - Google Sheets API
   - Google Drive API

### Step 3：建立 Service Account

1. 左側選單 → **API 和服務** → **憑證**
2. 點 **建立憑證** → **服務帳戶**
3. 填寫名稱（如：`ocr-sheets-bot`），其他可略過
4. 建立完成後點進去 → **金鑰** → **新增金鑰** → **JSON**
5. 下載金鑰檔，重新命名為 `credentials.json`，放在程式同資料夾

### Step 4：分享試算表權限

1. 開啟 `credentials.json`，找到 `client_email` 欄位
   ```json
   "client_email": "ocr-sheets-bot@xxx.iam.gserviceaccount.com"
   ```
2. 開啟你的 Google Sheets
3. 點右上角「共用」按鈕
4. 把上述 email 加入，權限設為**編輯者**

### Step 5：修改程式設定

開啟 `docx_ocr_to_sheets.py`，修改設定區：

```python
SPREADSHEET_NAME = "你的試算表名稱"   # 跟 Google Sheets 標題完全一致
WORKSHEET_NAME   = "工作表1"           # 分頁名稱
```

---

## 使用方式

### 1. 準備資料夾結構

```
專案資料夾/
├── docx_ocr_to_sheets.py
├── requirements.txt
├── README.md
├── credentials.json          ← Google Service Account 金鑰
├── docs/                      ← 放要處理的 .docx
│   ├── 文件A.docx
│   ├── 文件B.docx
│   └── 文件C.docx
├── work/                      ← 自動建立（中間檔）
└── output/                    ← 自動建立（結果 PDF）
```

### 2. 執行程式

```bash
python docx_ocr_to_sheets.py
```

### 3. 預期輸出

```
14:23:01 [INFO] 找到 3 個 .docx,開始處理
14:23:01 [INFO] 前處理:啟用 (放大=2.0x, 去雜訊核=3)
14:23:01 [INFO] OCR 語言:chi_tra+ind+eng
14:23:01 [INFO] ─── 處理 文件A.docx ───
14:23:01 [INFO]   發現 5 張圖片
14:23:01 [INFO]   [1/5] 處理 image1.jpeg
14:23:18 [INFO]     ✓ 符合條件!許可證號=3219 / No ijin=I-45
...
14:28:35 [INFO]
14:28:35 [INFO] ═══ 處理完成 ═══
14:28:35 [INFO] 總處理圖片:15
14:28:35 [INFO] 符合條件:8
14:28:36 [INFO] 已寫入 8 筆資料到 Google Sheets
```

### 4. 結果檢視

- **Google Sheets**：自動新增資料列
- **`output/` 資料夾**：保留符合條件的 OCR PDF（可開啟驗證）
- **`output/results_backup.csv`**：上傳失敗時的備份

---

## 參數調整

所有可調參數集中在 `docx_ocr_to_sheets.py` 上方設定區：

```python
# ── 路徑設定 ────────────────────────────────────────────────
INPUT_DOCX_DIR  = Path("./docs")          # 來源 .docx 資料夾
WORK_DIR        = Path("./work")          # 中間檔暫存
OUTPUT_DIR      = Path("./output")        # 結果 PDF 輸出

# ── OCR 設定 ────────────────────────────────────────────────
OCR_LANGUAGES   = "chi_tra+ind+eng"       # 語言（用 + 串接）

# ── 前處理參數 ──────────────────────────────────────────────
ENABLE_PREPROCESS    = True               # 是否啟用前處理
UPSAMPLE_FACTOR      = 2.0                # 放大倍率（1.5~3.0）
DENOISE_KERNEL       = 3                  # 去雜訊核（3 或 5）
CONTRAST_PERCENTILE  = (2, 98)            # 對比拉伸百分位
```

### 調參指引

| 狀況 | 建議調整 |
|---|---|
| 小字辨識不出來 | `UPSAMPLE_FACTOR` 改 2.5 或 3.0 |
| 圖片雜訊明顯 | `DENOISE_KERNEL` 改 5 |
| 文字被去雜訊糊掉 | `DENOISE_KERNEL` 改 1（即關閉）|
| 紙質差、底色不均 | `CONTRAST_PERCENTILE` 改 `(5, 95)` |
| 跑太慢 | `UPSAMPLE_FACTOR` 改 1.5 |
| 完全跑不動 | `ENABLE_PREPROCESS = False` |

---

## 常見問題

### Q1：`tesseract is not installed or it's not in your PATH`

A：Tesseract 沒裝好或沒加入 PATH。Windows 上重新安裝時要勾選「Add to PATH」，或手動到「環境變數」加入 `C:\Program Files\Tesseract-OCR`。

### Q2：`Could not find a language data file for "chi_tra"`

A：安裝 Tesseract 時沒勾選繁中語言包。重新安裝並勾選，或手動到 https://github.com/tesseract-ocr/tessdata 下載 `chi_tra.traineddata` 與 `ind.traineddata`，放到 Tesseract 的 `tessdata/` 資料夾。

### Q3：`gspread.exceptions.SpreadsheetNotFound`

A：兩種可能：
- `SPREADSHEET_NAME` 跟試算表標題不完全一致（含全形空格也算）
- 沒把試算表分享給 Service Account email

### Q4：OCR 結果亂碼或抓不到關鍵字

A：依序檢查：
1. 用 `save_preprocess_comparison()` 看前處理是否過度
2. 把 `result.full_text` 印出來看實際 OCR 文字
3. 必要時調整正則 `RE_PERMIT_ZH` / `RE_PERMIT_ID`

### Q5：執行很慢

A：100 張圖片 30-50 分鐘屬正常範圍。要加速可：
- 減少 OCR 語言（如只留 `chi_tra+eng`）
- 用 `multiprocessing.Pool` 平行處理（**不能用 threading**）
- 關閉前處理測試是否值得

### Q6：Windows 上跑出現大量子程序視窗

A：必須要有 `if __name__ == "__main__":` 保護（程式已內建）。如果你修改了程式，務必保留這段。

---

## 檔案結構

```
專案資料夾/
├── docx_ocr_to_sheets.py     # 主程式
├── requirements.txt           # Python 依賴清單
├── README.md                  # 本檔
├── credentials.json           # Google Service Account 金鑰（自行建立）
│
├── docs/                      # 輸入資料夾
│   └── *.docx
│
├── work/                      # 中間檔（自動清理）
│
└── output/                    # 結果輸出
    ├── *_ocr.pdf              # 含 OCR 文字層的 PDF
    └── results_backup.csv     # 失敗備份
```

---

## 處理流程圖

```
.docx 檔案
    │
    │ ① zipfile 解壓 word/media/
    ▼
原始圖片 bytes
    │
    │ ② 前處理流程
    │    ├─ 紅通道萃取（印章消失）
    │    ├─ 過取樣 2x（細節放大）
    │    ├─ 對比拉伸（黑白分明）
    │    └─ 中值濾波（去雜訊）
    ▼
乾淨的灰階圖片
    │
    │ ③ Pillow 轉 PDF (300 DPI)
    ▼
單頁 PDF（無文字層）
    │
    │ ④ OCRmyPDF + Tesseract
    │    語言：chi_tra + ind + eng
    ▼
含 OCR 文字層的 PDF
    │
    │ ⑤ pdfplumber 讀文字
    │ ⑥ 正則匹配「許可證號」與「No ijin」
    ▼
結構化資料
    │
    │ ⑦ gspread 批次上傳
    ▼
Google Sheets ✓
```

---

## 授權

依專案實際情況填寫（MIT / Apache 2.0 / 私人專案皆可）。
