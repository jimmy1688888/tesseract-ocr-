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
- [仲介欄位與雇主欄位的處理原理（交接說明）](#仲介欄位與雇主欄位的處理原理交接說明)
- [許可證選值流程（B 欄）——以 32201 為實例](#許可證選值流程b-欄以-32201-為實例)
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
| L | 疑慮標示（哪一格不可盡信）| 抽取/地址庫 |
| M | 雇主地址_英(OCR) | 契約 OCR 原文 |
| N | 郵遞區號 | 官方地址庫 |
| O | 雇主電話 | 契約 OCR |

- E/F/G 由 B 欄許可證查名冊補上（**仲介機構**，非雇主）；查無或無值則留空。
- H~O 為**雇主**資料（契約甲方）：寫入前對每份 docx 的契約頁跑一次 Vision OCR 自動填入；擷取失敗或找不到契約頁時該列留空。中文標準地址（J）未命中官方庫時留空，以 OCR 原文（K）為準。
- **英文地址只輸出 OCR 原文（M）**，不再輸出官方英譯：契約英譯沒有單一正確寫法（Panshih／Panshi 都出現得到），官方譯名並不比契約上的更權威，兩欄並列只是讓人多比對一次卻分不出對錯。騰出的 L 欄改放疑慮標示。M 欄**讀到什麼就是什麼、讀不到就空**，不拿官方譯名補。
- 英文行仍用於**縣市錨定**（印章多蓋中文行首，英文的 County/City 在行尾而倖存）——取消英譯輸出不影響這條救援路徑，輸出與輸入是兩件事。
- L 欄為**疑慮標示**：雇主資料哪一格不可盡信，每條冠上欄名（`H:…；J:…`）。空白＝這列沒有已知疑慮。
  - `H:` 中文雇主名——未擷取到，或字數與英譯音節數不符（截斷／樣板字／雜訊）。
  - `J:` 中文標準地址為何留空——縣市／行政區未判定、縣市僅模糊命中、路名未在該行政區清單中、地址無路名樣式（鄉村型）。
  - 措辭只陳述**確定的事實**，不推斷成因也不給行動建議——這條訊息自己可能是錯的（見 [ADR-0003](docs/adr/0003-employer-address-doubt-flag.md)、[ADR-0004](docs/adr/0004-employer-name-doubt.md)）。
- 明細另存 `scan_results/address_doubts.csv`，供人工一次數完誤報率；契約頁 Vision 全文另存 `scan_results/employer_texts.json`，改了抽取規則可離線重放驗證，不必再花 Vision 呼叫。
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
    └─ 全無命中（先不判 B，進 manual_review 待救援）
    │ ③ Vision 判讀 + 三層交叉比對（雙引擎一致才自動 key-in）
    ▼
④ 許可證查名冊 → 補機構名稱/地址/電話（E~G）
    ▼
⑤ 契約頁 OCR（employer_extract + address_db）→ 雇主名稱/地址/電話 + 官方譯名/郵遞區號（H~O）
    ▼
⑤b 仲介反查救援：契約次頁去紅章 → 台仲『電話精確→電話前綴→名稱/地址相似度』反查名冊回推許可證（B + E~G）
    ▼
⑥ 批次 atomic 寫入 Google Sheets（keyed-in / manual_review）
```

---

## 仲介欄位與雇主欄位的處理原理（交接說明）

兩組欄位走**完全不同的路線**，理由是資料性質不同：

| | 仲介機構（E~G） | 雇主（H~O） |
|---|---|---|
| 是誰 | 私立就業服務機構（台仲公司） | 契約甲方（個人或公司） |
| 有無官方名冊 | **有**（勞動部跨國人力仲介名冊） | **無** |
| 取得方式 | **查表**（不做 OCR） | **OCR + 官方地址庫校正** |
| 正確性取決於 | B 欄許可證號對不對 | 契約頁影像品質（紅章、掃描歪斜） |
| 失敗表現 | E~G 三欄一起空 | 逐欄獨立，可能名稱空但地址在 |
| 人工複查依據 | 名冊網站查 B 欄號碼 | `scan_results/employer_crops/` 截圖 |

### 仲介欄位（E~G）：許可證號查表

**原理**：仲介機構是特許行業，勞動部名冊完整收錄，許可證號是唯一鍵。
查表 100% 準確（前提是 B 欄號碼對），所以**刻意不對仲介框做 OCR**。

流程（`permit_lookup.py` + `pipeline._agency_cols`）：

1. **資料來源**：`data/agency_roster.json` = data.gov.tw dataset 6682 官方 JSON。
   - **只讀本地檔、不連網**（mol.gov.tw 憑證常被 Python SSL 擋）。更新方式：
     瀏覽器開 `permit_lookup.py` 開頭的 `ROSTER_JSON_URL` 下載，覆蓋該檔。
     超過 30 天未更新只記提示，照常執行。
   - **必須用 JSON，不可用 Excel 匯出的 CSV**：Excel 會把分支證號
     `1-73` 判成日期（Jan-73）、把 `0234` 吃成 `234`，資料就毀了。
2. **號碼解析**：`0234` 去前導零 →`234`；`2340-2` 拆成母公司 2340＋分支 2；
   建「母公司」「分公司」兩個索引。
3. **分支感知**：B 欄是 `2340-2` 時**精確查該分公司**，查無就回空——
   **不回退母公司**（分公司地址/電話不同，填母公司資料是錯的）。
4. **同號多筆**（歷史換照）：取「目前有效」者（未廢止且未終止營業）。
5. **降級不中斷**：名冊檔缺失/壞掉 → 記 warning、E~G 全批留空，主流程照跑。

**交接重點**：E~G 錯，第一步先懷疑 B 欄號碼讀錯，不是名冊錯；
E~G 空，先確認 `data/agency_roster.json` 存在、再確認 B 欄有值且名冊查得到。

### 雇主欄位（H~O）：契約頁 OCR + 官方地址庫校正

**原理**：雇主不在任何名冊，只能從契約影像讀。策略是「Vision 讀原文 →
官方地址庫幫地址背書」，**OCR 原文（K/M）永遠保留，標準欄（J/L/N）寧空勿錯**。

流程（`employer_extract.py`，由 `pipeline.collect_employer_fields` 對每份 docx 呼叫一次）：

1. **找契約頁（不花 API）**：對 docx 每張圖的上緣 35% 跑本機 Tesseract，
   數「契約頁特徵字」（PERJANJIAN KERJA、甲方名稱、Nomor Telepon…）命中數，
   取最高分且 ≥2 者。找不到 → 該檔 H~O 留空、存候選頁截圖供人工判斷。
2. **ROI 裁切**：契約頁上緣 35% 全寬（`EMPLOYER_ROI`），涵蓋標題、甲方名稱、
   地址中英、電話。裁切圖（去紅章**前**原貌）存 `scan_results/employer_crops/`
   `{docx}_{圖檔名}`——**人工複查 H~O 就看這裡**，不用回頭開 docx。
3. **去紅章**（`deink_red_stamp`）：紅印章 R 通道高、黑字三通道皆低——取紅通道
   為底＋把「明顯偏紅且夠亮」的像素抹白＋對比拉伸。只清白底上的章，
   不動壓在黑字上的暗紅像素（避免把筆畫抹掉）。
4. **送 Vision**（`DOCUMENT_TEXT_DETECTION`，每份 docx 一次）→ 全文逐行啟發式解析：
   - **先跳過仲介框**：從「甲方名稱」（或 MAJIKAN DENGAN 之後）才開始解析。
     仲介框在標題上方、同樣有名稱/地址/電話，不隔離就會混進雇主欄
     （32098 實例：仲介電話被誤當雇主電話）。**仲介框的內容一律不取用**。
   - **電話**：只統計雇主區塊內；「出現兩次的號碼」優先（中文「電話」與
     印尼文「Nomor Telepon」各印一次是版面特性）。寧可留空也不撈區塊外的號碼。
   - **地址**：中文靠「縣市…路/街/號」pattern；英文靠 No./Rd./Dist. 關鍵字。
     被紅章殘影污染的英文行不輸出，只留給地址庫當「英文錨定」線索。
   - **名稱**：雇主區塊前段取值，排除標籤字、地址行、表格樣板字（SEKTOR…）。
     紅章最常蓋在名稱上，名稱是 H~O 中可靠度最低的欄位。
5. **地址庫校正**（`address_db.normalize_address`，資料=內政部門牌 `data/AllData.json`
   ＋自建補充 `data/custom_roads.json`）：分層比對
   **縣市 → 行政區 → 路名（含段）→ 門牌尾巴**，每層「精確含 → 寬鬆 → 模糊」
   逐步退防，模糊層都有防錯配的守門（方向字、主幹須在原文、取最長命中）：
   - 正規化：全形→半形、臺=台、鍾=鐘、段號「一段/1段/１段」三種寫法等價。
   - **英文錨定備援**：印章常毀中文行首的縣市，而英文地址縣市在行尾常倖存
     → 中文縣市只配到模糊層時，用 OCR 英文行反查縣市/區，再回頭配路名門牌。
   - **保守回填原則**：縣市＋區＋路**全命中**才填 J（標準中文地址）；
     只命中到縣市/區就只補 N（郵遞）與 L（官方英譯），J 留空。
     所以 **J 空白 ≠ K 讀錯**，多半是官方庫沒收錄該路（見常見問題 Q6）
     或鄉村非路型地址，以 K 的 OCR 原文為準即可。
6. **失敗隔離**：任一 docx 擷取失敗只記 warning、該列 H~O 留空，絕不中斷批次；
   `manual_review` 件（許可證待複核）**照樣填 H~O**——許可證讀不到
   不代表雇主資料無效，先填好省一次人工查找。

**交接重點**：
- H~O 有疑問 → 開 `scan_results/employer_crops/` 對應截圖肉眼核對，最快。
- J/L/N 空但 K 有值 → 官方庫涵蓋問題，走 Q6 的 `python address_db.py 路名` 查證，
  確認缺路就補 `data/custom_roads.json`（格式規範見 Q6）。
- 名稱欄（H）錯字/亂碼 → 幾乎都是紅章蓋名，看截圖人工修正即可，OCR 救不了。

---

## 許可證選值流程（B 欄）——以 32201 為實例

B 欄的值怎麼選出來的？核心設計原則一句話：

> **任何值要「自動」寫入，必須有兩個以上互相獨立的證據；
> 只有單一證據時，一律送 Vision 求第二證據或標人工審查。**

「獨立證據」有三種形態：①同文件兩個區域各自印的號碼互相吻合（mol ROI ↔
permit ROI 交叉）②同一號碼在多組前處理設定下重複讀到（多數票）
③兩個不同 OCR 引擎讀到同值（Tesseract ↔ Vision）。

### 第一層：檔案層分流（每份 docx 判一次）

Tesseract 掃完全部圖片後（每張圖 × 多組前處理 config × mol/permit 三個 ROI），
依整份 docx 的命中情形分流。**large docx**（圖片數 > 3）的分支：

| # | 狀況 | 去向 | 為何如此設計 |
|---|---|---|---|
| A | mol 與 permit 兩 ROI 讀到**同一值**，最高信心夠高 | **直接 key-in** | 文件兩處獨立印刷互證，等同雙重確認 |
| A' | 同上但信心不足 | 送 Vision | 值可信但讀得勉強，要第二引擎背書 |
| A'' | mol 與 permit 都有值但**不同**（衝突） | 送 Vision | 值衝突絕不自動選邊；以 permit ROI 最早圖為候選（permit ROI 目的明確，mol 僅輔助） |
| A2 | 僅 permit 有值，**多數票**（同值讀到 ≥2 次）且平均信心 > `CONF_VOTE_MIN`(45) | **直接 key-in** | 多組 config／上下兩 ROI 重複讀到同值，重複性本身就是第二證據；門檻可低於單次讀值的 55，因為多數票已是多重證據 |
| B | permit 有命中但**無多數**（各次讀到的值湊不齊 2 票） | 送 Vision | 單次判讀不可信，取信心最高者當候選送驗 |
| C | 其餘標了 vision_review 的（僅 mol 信心低、互證吻合但信心低…） | 送 Vision | 同上，單一證據不足 |
| D | **全部圖片無命中** | **人工審查**（不對許可證 ROI 送 Vision），但先走**仲介反查救援**（見下節） | 沒有候選值可交叉驗證，硬讀許可證只會是單引擎；改用「仲介電話（優先）／名稱／地址反查名冊」這條獨立管道回推 |

**small docx**（圖片數 ≤ 3，版面固定）較簡單：mol 多數票且平均信心 >
`CONF_MOL_VOTE_MIN`(50) → 直接 key-in；其餘有值 → 送 Vision；全無命中 → 人工審查（同樣先走仲介反查救援）。

### 第二層：送 Vision 件的交叉比對（verify_vision_result）

送 Vision 的件，拿 Vision 讀值與 Tesseract 候選值比對，五種結果：

| 等級 | 意義 | 去向 |
|---|---|---|
| CONFIRMED | 雙引擎**完全一致** | 自動 key-in |
| LIKELY_OCR_CONFUSION | **差 1 字元**（0/O、5/S 型混淆） | 人工審查，**不自動填任何一方的值**，兩候選都寫進 reason |
| VISION_ONLY | Tesseract 無候選，僅 Vision 有值 | 人工審查（單引擎） |
| DISAGREEMENT | 差 2 字元以上 | 人工審查 |
| FORMAT_INVALID | Vision 無值或非 4 位數格式 | 人工審查 |

歷史成功清單（upload_log.csv 累積）只用來在 reason 補一句「此值歷史出現過」
幫人工排優先順序，**不作為自動採用的依據**——避免歷史錯值自我強化。

### 實例：32201.docx 走的是哪條路

`scan_results/matches.csv` 中 32201 的真實紀錄：

```
docx_class = large          （圖片數 > 3）
image_name = image5.jpeg    （命中圖）
mol        = （空）          ← mol ROI 全部沒讀到
id         = 2674           ← permit ROI 讀到
id_from_vote = Y            ← 多數票產生（≥2 次讀到 2674）
id_conf    = 67.1           ← 得票各次的平均信心
hit_roi    = permit_upper   ／ hit_config = 紅通道_2x_中值3
```

逐層對照：mol 無值 → 走「僅 permit」線；`id_from_vote=Y` 且
平均信心 67.1 > 門檻 45 → 命中**規則 A2**：多數票即第二證據，
**直接 key-in、不送 Vision**。寫入 Sheets 時 status=`keyed-in`、
reason=`permit多數票_高信心_最早圖(image5.jpeg) conf=67.1`，
B 欄 = 2674，E~G 由 2674 查名冊自動補上。

> 追查任何一筆的選值過程：先看 Sheets 的 **D 欄 reason**（分流理由都寫在這），
> 再開 `scan_results/matches.csv` 對 `mol / id / *_from_vote / *_conf / note`
> 欄位，即可完整還原該筆走過的分支。

### 仲介反查名冊回推許可證：電話（精確→前綴）優先，名稱／地址後備

**動機**：許可證數字整份掃不到（章糊、影印歪斜）時，B 欄原本只能空著等人工。
但契約**次一頁**通常印有「台灣仲介公司」區塊，含仲介的**電話／名稱／地址**——而名冊
（`agency_roster.json`）本來就有每家仲介的這三項。於是多開一條**與許可證數字無關**
的獨立管道：**讀仲介電話（或名稱／地址）→ 反查名冊 → 回推許可證**。

流程（`pipeline.rescue_manual_review_via_agency_phone` + `employer_extract.agency_block_from_next_page`）：

1. 找契約頁的**次一頁**，**去紅章前處理後**整頁送 Vision OCR（**每件僅 1 次** Vision；
   按張計費，整頁與裁切同額度），一次取回台仲區塊的**電話、名稱、地址**三項。
   - **去紅章**（`_deink_next_page_png`，比照 scan 的「紅通道_2x_中值3」：取紅色通道使紅章
     呈白、黑字保留，再 2x＋對比＋中值）——讓被章蓋／交錯的台仲字更清晰。
   > 同時服務兩種來源：①全無命中件；②非多數票低信心「單一判讀」件（該 ROI 品質差，
   > 略過 ROI Vision 直接走這條，見上節）。
2. **只取「台灣仲介」，排除印尼仲介（P3MI）**：台印格式重疊、無法靠格式或位置分，
   改**語意錨定**——以「台灣仲介公司(Agency Taiwan)」**欄位標籤**為起點，到其後的
   仲介欄位標籤或印尼電話標籤（Telp/Telepon）為止。台仲**左右不定**（32345 在右、
   32324 在左）故不靠幾何裁半；「真標籤」以「短行＋非句號結尾」判定，濾掉 prose 誤配。
   - **交錯版面補強**（32401 教訓）：台仲標籤與印尼標籤**緊鄰**（兩欄並排、資料排在標籤
     之後且左右交錯）時，原「標籤→下一標籤」定界只會圈到空的標籤行 → 改**放寬到
     「乙方(PIHAK KEDUA)／頁尾」**，再用 `_ID_LINE`（JL／KELURAHAN／KEC／KOTA／Telp／
     +62 等印尼詞）**排除印尼行**，只留台仲的名稱／電話。
   - 找不到真台仲標籤 → 回空**不猜**（不誤抓印尼資料）。
3. **反查名冊，優先序四級**（越前面越可靠；任一命中即止）：
   - **① 電話精確**（`agencies_by_phone`；支援 `+886` 國際格式與雙連字號正規化）：
     唯一命中 → 填 B。
   - **② 電話前綴**（`agencies_by_phone_prefix`，OCR 少 1~2 碼容錯：一方為另一方前綴、
     共同 **≥8 碼**、唯一命中）→ 填 B。**排在名稱前**，因電話比「通用後段名稱」可靠：
     32401 電話 `022371760`（少一碼）前綴唯一命中 **3759 伯樂**；若走名稱反而會誤命中
     同後段的「飛越」（見 ③ 的警告）。
   - **③ 名稱／地址相似度**（電話兩條都無）：台仲名稱／地址各與名冊正規化後比**相似度**，
     取**相似度（CONF）高者**填 B——門檻 `NAME_MATCH_MIN`(0.78)／`ADDR_MATCH_MIN`(0.72)，
     名稱先以 `_clean_agency_name` 掐掉英文/符號雜訊（`LTD(裕倉…` → `裕倉…`）。
     > ⚠ **通用後段風險**：像「○○國際人力資源管理顧問有限公司」只有前綴區別，OCR 錯了
     > 前綴仍可能高相似度**誤命中**另一家 → 故電話排前、名稱墊後，且結果一律人工核對。
   - **④ 皆無**：電話命中多家（名冊約 2.6%）→ 不填 B、列出各家供人工擇一；全無 → 維持人工。
   - 任一命中回填時，E~G 一律由**該許可證查名冊得官方機構名/地址**（不受次頁 OCR 糊化影響）。

**設計原則不變**：這是**另一條獨立證據管道**，不違反「單一 OCR 引擎不得直接決定寫入」
——救回的值仍標 `manual_review` 要人工過目，E~G 用名冊官方資料而非 OCR 名稱。

**已知限制**：若電話**整組**被紅章蓋到讀不出（如 32345），①②兩條救不回；但名稱／地址
仍讀得到時可由 ③ 救回（實測 32397 電話對不上名冊，靠名稱相似度 1.00 命中 2449）。
仍保證不誤抓印尼機構——寧可回空，不填錯。

> 追查救援結果：Sheets **D 欄 reason** 會標「[電話反查救回] …」、
> 「[電話前綴反查救回·電話OCR可能少碼] …前綴唯一命中 …」、
> 「[名稱／地址反查救回·電話無唯一命中] …（名冊相似度 Z）」
> 或「[電話反查·需人工擇一] …命中 N 家：…」。

### 雇主名稱括號補齊（`_balance_brackets`）

雇主名稱常帶括號（如自然人雇主 `許榮壽君即許榮壽家庭農場(許榮壽)`、公司 `…(股)`）。
擷取時的 `strip` 會把**頭尾單邊**括號剝掉、或 OCR 只讀到一邊，寫進 Sheets 就少一半。
`extract_employer_fields` 輸出「雇主名稱_中／_英」前一律過 `_balance_brackets`：
全形（）與半形()分別配對，缺閉補尾、缺開補頭，確保括號成對。

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
CONF_NOVOTE_RESCUE_MAX = 50  # 單一判讀 conf 低於此才走次頁仲介反查（否則維持原 Vision 覆核）
SMALL_DOCX_THRESHOLD = 3 # 圖片數 ≤ 此值 → small
```

`permit_lookup.py` 的仲介反查門檻：

```python
NAME_MATCH_MIN = 0.78    # 名稱相似度門檻（反查名冊機構名稱）
ADDR_MATCH_MIN = 0.72    # 地址相似度門檻（反查名冊機構地址）
# 電話前綴反查 agencies_by_phone_prefix(min_len=8)：共同前綴 ≥8 碼且唯一才採用
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
- **寫法統一照官方庫格式**（實例：`臺中市` / `民和路１段` / `Sec. 1, Minhe Rd.`）：
  - 縣市用「臺」（寫「台」也比得到，但統一寫法方便日後對照官方檔）
  - **帶段的路**：中文段號用**全形數字**（`１段`，非 `一段`/`1段`）；
    英譯段號**前置**，格式 `Sec. N, Xxx Rd.`（**不要**寫 `Xxx 1st Rd.`——
    `1st` 是路名本身的序數，如 `長發一路 = Changfa 1st Rd.`，兩者意義不同）
  - 比對端有正規化，寫錯字形通常仍比得到；但 RoadName/RoadEngName
    會**原樣輸出**到 J 欄標準地址，格式不照官方寫，輸出就不標準
- **具名巷**（如「后尾巷」）可以加，英譯結尾用 `Ln.`
- **數字巷**（如「文化路100巷」）**不要加**——巷弄由門牌自動處理，缺的話加主路即可
- 官方庫日後收錄同名路時不會重複，補充條目可留著

---

## 授權

依專案實際情況填寫（私人專案 / MIT / Apache 2.0）。
