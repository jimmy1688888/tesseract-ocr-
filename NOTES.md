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

Sheets 每列 **16 欄**，H~P 由 `pipeline.py` 於寫入前自動填入：

- 欄位（標準／OCR 各佔一欄、中英分開）：
  - A–G（同前）｜H=雇主名稱_中 I=雇主名稱_英
  - J=雇主地址_中(標準) K=雇主地址_中(OCR) L=雇主地址_英(標準) M=雇主地址_英(OCR)
  - N=郵遞區號 O=雇主電話 P=疑慮標示（J 欄為何留空，見下）
- 實作方式（`pipeline.py`）：
  - `collect_employer_fields(docx_files)`：主流程步驟 4b 前對每份 docx 跑一次
    `employer_extract.extract_employer_from_docx()`（契約頁偵測=本機 Tesseract、
    只有選中那頁送 Vision，**每 docx 一次 Vision 呼叫**），結果存
    `_EMPLOYER_FIELDS_BY_DOCX` 快取。manual_review 列也擷取（許可證待複核≠雇主資料無效）。
  - `_employer_cols(r)`：組列時依 `source_docx` 查快取；row dict 自帶同名 key 優先。
    擷取失敗/找不到契約頁 → 該列 H~O 留空，絕不中斷主流程。
- **提醒**：Sheet 標題列要手動補上 **E~P**（程式 append 不寫標題列）。
- **P 欄疑慮標示**（[ADR-0003](docs/adr/0003-employer-address-doubt-flag.md)）：中文標準
  地址（J）留空時寫明成因，五選一——縣市比不到／行政區比不到／縣市僅模糊命中／路名不在
  資料庫（可補 `data/custom_roads.json`）／地址無路名（鄉村型，正常）。留空的格子人看得見，
  填錯的格子人看不見，所以系統的工作是把後者變成前者。明細另存
  `scan_results/address_doubts.csv` 供人工一次數完誤報率（pipeline 對 Sheets 只寫不讀）。
- **縣市僅模糊命中時不填郵遞區號與官方英譯**：模糊配錯縣市時，「中山區」「中山路」這類
  全台通用的名字在錯誤縣市底下照樣配得到（實測「台桃市中壢區」→ 臺北市中山區、郵遞 104）。
  但路名配得到就是佐證（「宜藺縣」→ 宜蘭縣羅東鎮中正路），此時照填——模糊命中本身不是錯誤。
- 英文地址**不做**判定：契約英譯沒有單一正確寫法（Panshih/Panshi 都出現得到），分不出
  「拼法不同」與「讀錯」。代價是「中文讀對、英文讀錯」（32448 的 Jinhai/Jinmei）抓不到，
  已知且接受。
- 樓層「之N」英譯已支援：`2樓之1` → `2F.-1`（address_db）。
- **雇主 ROI 截圖**：每份 docx 送 Vision 的契約頁裁切（去紅章**前**原貌）自動存到
  `scan_results/employer_crops/{docx主檔名}_{圖檔名}`，人工複查 H~O 時直接開圖對照。

## 已完成：全無命中 → 仲介電話反查名冊（救援路徑）

許可證 OCR 全無命中時，過去只能直接人工。現在改走**獨立證據管道**：從契約次頁的
仲介欄位反查名冊，救回許可證號。詳解見 README 專節，此處只記脈絡。

- **觸發**：許可證 OCR 全無命中，或「單一 conf」者（mol 無多數票、large permit 無多數票）
  → **不再重掃同一 ROI 區段**，直接走救援。
- **取值**：次頁**整頁** OCR（仍只 1 次 Vision — Vision 按張計費，整頁與裁半同額度），
  取得 `{phones, name, address}`；`block_cache` 去重。次頁也走去紅章
  （`_deink_next_page_png`，比照紅通道_2x_中值3）。
- **台仲區塊定界**：台仲在次頁**左右不定**（32345 在右、32324 在左），原「只裁左半」的
  幾何隔離會在台仲於右側時剛好裁到印尼那格。改為**整頁語意錨定**：
  `_is_agency_label_line`（短行 + 非句號結尾）找起點，`_ID_LINE` 排除印尼行做台印分離；
  台仲與印尼標籤兩欄並排時，定界放寬到乙方／頁尾。
- **名冊反查四級優先序**（`pipeline` rescue）：
  1. 電話精確
  2. **電話前綴容錯** — OCR 少 1~2 碼，一方為另一方前綴、共同 ≥8 碼、唯一命中才採用
  3. 名稱／地址相似度 — `best_by_name` 0.78、`best_by_address` 0.72（quick_ratio 粗篩加速）
  4. 多家並列
  電話前綴刻意**排在名稱之前**：通用後段名稱容易誤命中。
- **電話格式容錯**：`+886` 國際格式、號段間雙連字號／空白（`02-2727-6999`）。
- **設計原則**：這是獨立證據管道，命中後**仍標 `manual_review`**，並在 D 欄 `reason`
  記錄是走哪個管道命中的（追查用）。
- **實測**：32401 交錯版面電話少一碼 → 電話前綴唯一命中 3759 伯樂（避開名稱誤命中的飛越）；
  32397 電話空 → 地址相似度 1.00 命中 2449，雇主名稱括號經 `_balance_brackets` 補齊成對。
- **已知限制**：電話被紅章蓋死就救不回（但保證**不會誤抓成印尼機構**）。
- **⚠️ 待觀察**：commit `80057f4` 留下的問題 —「有多數票但 conf 低」的案子目前行為未定案，
  尚未驗證是否該一併走救援。

## 已完成：台灣文字正規化收歸 address_db（2026-07-28）

`address_db` 與 `permit_lookup` 原本各有一套「地址正規化」且**行為不一致**：前者折疊
臺→台、一段→1段，後者兩者都不做——偏偏後者才是反查救援比相似度用的那一套。名冊機構
地址 38% 用官方體例的「臺」而契約 OCR 印的是「台」，名冊自身段號也國字／阿拉伯各半
（308／313 筆），等於每次比對都憑空被扣分。

- `address_db._norm` 升為公開 **`fold_variants`**（**行為一行未改**，只是換名字；
  它同時是 address_db 自己建索引用的函式，維持行為不變是硬約束）。
- `permit_lookup._norm_addr` ／ `_norm_name` 都先過 `fold_variants`；原本自行處理全形
  數字的 `str.maketrans` 已刪（NFKC 已涵蓋）。
- **門檻刻意不動**（`ADDR_MATCH_MIN` 0.72 ／ `NAME_MATCH_MIN` 0.78）。折疊會系統性推高
  所有相似度，但實測**誤配對門檻不敏感**：拉到 0.88 只少 2 件誤配卻少救 104 件。
  若日後看到「折疊後沒調門檻」以為是漏改——是刻意的，數據見 ADR-0002。
- 實測（真實名冊 2,121 家、樣本 300 筆，模擬契約寫法＋OCR 損壞）：乾淨輸入命中率
  **不變**（本來就 100%），OCR 損壞 15%／20% 時分別多救回 13／25 件，誤配 +0／+1。
  改善全部來自把原本掉在門檻以下的正確答案拉過門檻。
  **這次改動不修正任何既有的錯誤答案**，買的是紅章咬字時的餘裕。
- ⚠ `fold_variants` 是**比對用**中間字串，會把「臺」寫成「台」，**不可拿去填 Sheets
  的 J 欄**；要輸出標準地址一律用 `normalize_address()`（它保留官方正體「臺」）。
  `test_address_norm.py::TestNotForOutput` 釘住這條界線。

### 新增的文件慣例（換手時先讀這兩處）

- **`CONTEXT.md`**（repo 根目錄）：領域詞彙表——許可證／仲介機構／雇主／名冊／契約頁／
  多數票／獨立證據管道／反查救援／異體折疊／地址標準化…，每個詞附 `_Avoid_` 列出
  **不該用**的同義詞。寫 issue、commit、測試命名時照這裡的用語，別漂移。
- **`docs/adr/`**：架構決策紀錄。目前兩份——0001（正規化歸屬與被否決的方案）、
  0002（門檻維持 0.72 的實測依據）。`.gitignore` 的 `docs/*` 原本只白名單
  `docs/agents/`，已補上 `!docs/adr/`，否則 ADR 寫了也進不了版控。
- **`docs/reviews/`**：架構檢視存檔。
  [2026-07-28 那次](../docs/reviews/2026-07-28-architecture-review.md) 共七個候選，
  只做了候選 3（本次），**其餘六個含檔案行號、問題、做法與收穫都寫在裡面**，
  後續維護要接著動時從那裡挑。最有份量的兩個是候選 1（台仲區塊 seam）與
  候選 7（已知清單管道恆空、四個測試指著已死的行為）。

## 目前狀態（2026-07-28）

- 分支 `main`，與 origin 同步，工作區乾淨。`python -m pytest -q` → **183 passed**
  （原 147 ＋ 本次新增 36：`test_address_norm.py` 22、`test_agency_rescue.py` 14）。
- `test_agency_rescue.py` 示範了一個一直存在但沒人用的測試入口：
  `PermitLookup(records)` **接受注入的名冊清單**，故反查行為可用三筆手寫假名冊測完，
  不需要 `data/agency_roster.json`、不連網。
- `.claude/skills/`、`.agents/skills/` 已加入 .gitignore（個人工具，同事不需要，也不需裝 Node.js）；
  `skills-lock.json` 保留版控供換機重裝。共用的 agent 設定在 `docs/agents/`。

### 一句話喚醒新 session

> 台灣移工 OCR 專案（repo 已 clone，data/AllData.json 已備妥）。permit_lookup 許可證查表、
> employer_extract 雇主擷取、address_db 地址標準化、pipeline H~O 整合、
> 全無命中→仲介電話反查救援**全部完成**
> （含雇主 ROI 截圖存 scan_results/employer_crops 供人工複查），
> `python pipeline.py` 即可端到端跑 docs/ → Sheets 15 欄。183 測試全過。
> 領域詞彙見 `CONTEXT.md`，架構決策見 `docs/adr/`。
> 下一步候選：(1) 對 docs/ 新批（32088 起）全跑驗證 H~O 實際寫入品質；
> (2) 釐清 `80057f4` 留下的「有多數票但 conf 低」該不該走救援；
> (3) 架構檢視還有幾項未動的候選，最有份量的是「仲介反查台仲區塊切出純函式 seam」
>     （`_agency_block_lines` 目前把 docx 解壓＋選頁＋去紅章＋Vision＋定界五件事綁在
>     一起，導致最常改的定界規則只能靠真實 docx＋Vision 才測得到）與「許可證文字
>     判讀兩套實作」（`find_permits` vs `run_google_vision` 內嵌的 regex 走訪，
>     兩引擎交叉比對其實沒共用同一套判讀）。
