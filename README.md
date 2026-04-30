# Word → OCR → Google Sheets 批次處理工具

把資料夾內所有 `.docx` 檔的圖片解出來,OCR 找出含「許可證號」的那張,把擷取的證號寫進 Google Sheets。

## 一次性環境準備

### 1. 安裝 Tesseract 與語言包

```bash
# Ubuntu / Debian / WSL
sudo apt install tesseract-ocr tesseract-ocr-chi-tra tesseract-ocr-ind

# macOS
brew install tesseract tesseract-lang

# Windows (用 chocolatey)
choco install tesseract
# 額外語言包要從 https://github.com/tesseract-ocr/tessdata 下載
# chi_tra.traineddata 與 ind.traineddata 放到 tessdata 資料夾
```

驗證安裝:
```bash
tesseract --list-langs   # 應看到 chi_tra、eng、ind
```

### 2. 安裝 Python 套件

```bash
pip install -r requirements.txt
```

### 3. 設定 Google Service Account(只做一次)

這部分有點繁瑣但只做一次,每一步都要做才能讓程式自動存取 Sheets:

1. 開啟 Google Cloud Console 建立或選擇一個專案: https://console.cloud.google.com
2. **啟用兩個 API**:在「API 和服務 → 程式庫」搜尋並啟用
   - Google Sheets API
   - Google Drive API
3. **建立 Service Account**:「IAM 與管理 → 服務帳戶 → 建立服務帳戶」
   - 命名隨意,例如 `docx-ocr-bot`
   - 角色給 `編輯者` 即可
4. **產生金鑰**:點進剛建好的服務帳戶 → 金鑰 → 新增金鑰 → JSON,會自動下載一個 JSON 檔
   - 把這個檔案改名為 `service_account.json` 放在程式同目錄
   - **這個檔案等同密碼,不要 commit 進 git**(`.gitignore` 加進去)
5. **把 Sheet 分享給服務帳戶**:打開目標 Google Sheet
   - 右上角「共用」按鈕
   - 把服務帳戶的 email(JSON 檔裡的 `client_email` 欄位,長得像 `xxx@xxx.iam.gserviceaccount.com`)加進去,給編輯權限
   - **這步最容易忘記**,沒做的話程式會報 403

## 使用方式

### 基本用法

```bash
python docx_ocr_to_sheets.py \
    --input-dir ./word_files \
    --sheet-id 1aBcDeFgHiJkLmN0pQrStUvWxYz \
    --creds ./service_account.json \
    --worksheet "Sheet1"
```

`--sheet-id` 取自 Google Sheets 網址中 `/d/` 與 `/edit` 之間那串長字串:

```
https://docs.google.com/spreadsheets/d/【這段就是 sheet id】/edit#gid=0
```

### 先測試不上傳

第一次跑建議先用 `--dry-run` 確認 OCR 結果正確,再正式上傳:

```bash
python docx_ocr_to_sheets.py \
    --input-dir ./word_files \
    --sheet-id 1aBcDeFgHiJkLmN0pQrStUvWxYz \
    --creds ./service_account.json \
    --dry-run
```

### 詳細 log

加 `-v` 看每一張圖的 OCR 細節:

```bash
python docx_ocr_to_sheets.py ... --dry-run -v
```

## 輸出格式

工作表內容:

| 檔名 | 許可證號 |
|---|---|
| 員工A_切結書.docx | 3219 |
| 員工B_切結書.docx | I-45 |
| 員工C_切結書.docx |  |  ← 沒抓到留空

每跑一次會 **append 到工作表末端**,不會清空既有資料。如果要重跑同一批,先到 Sheets 把舊資料清掉。

## 處理流程說明

對每個 docx 檔:

1. 把 docx 當 zip 解開,取出 `word/media/` 下所有圖片
2. 對每張圖做前處理:**取紅色通道**(去除紅章)→ 過取樣 3x → 對比拉伸 → 去雜訊
3. 跑 Tesseract(`chi_tra+eng+ind`,PSM 6,LSTM 引擎)
4. 用關鍵字 `許可證號 / 認可編號 / No ijin` 判斷是否目標圖
5. 用 regex 擷取證號(支援 `3219`、`I-45` 兩種格式)
6. 全部跑完一次性 append 到 Google Sheets

## 客製化

下面這些常見調整都集中在程式上方的「設定區」,直接改即可:

- **語言包**:改 `TESSERACT_LANGS`,例如要加越南文 → `"chi_tra+eng+ind+vie"`(記得先安裝 `tesseract-ocr-vie`)
- **關鍵字**:改 `LICENSE_KEYWORDS`,加新的 label
- **證號格式**:改 `LICENSE_PATTERNS`,例如要支援 5 位以上數字 → 把 `\d{2,6}` 改成 `\d{2,8}`
- **欄位增加**:改 `OcrResult` dataclass 與 `upload_to_sheets()` 中的 `data_rows` 組裝邏輯

## 疑難排解

| 症狀 | 可能原因 |
|---|---|
| `403 The caller does not have permission` | 忘記把 Sheet 分享給 Service Account email |
| `tesseract: not found` | Tesseract 沒裝,或不在 PATH |
| 大量檔案顯示「未找到含許可證號的圖」 | 看 `-v` log,可能是關鍵字偵測太嚴,把實際 OCR 出來的字串貼上來再調 `LICENSE_KEYWORDS` |
| 證號偵測到但有錯字 | 可能字被印章蓋得太嚴重,試試手動人工檢查那張圖,或調整前處理參數 |
| 處理速度慢 | 每張圖 OCR 約 0.5–2 秒,500 個檔案大約 10–30 分鐘是正常的 |
