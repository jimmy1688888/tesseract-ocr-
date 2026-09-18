# 架構檢視 — 2026-09-18

檢視基準:`main` @ `2118fcc`(2,665 + 1,042 + 616 + 354 行、371 測試)。
上一次是 `76a1000`(2,195 + 620 + 469 + 262 行、147 測試)—— 兩個月長了約 900 行、
測試從 147 到 371。範圍鎖定近 60 個 commit 的熱區:`employer_extract` ×23、
`pipeline` ×20、`address_db` ×17、`permit_lookup` ×8。

領域用語照 [CONTEXT.md](../../CONTEXT.md)。架構用語固定使用:
**module／interface／implementation／depth／deep／shallow／seam／adapter／leverage／locality**
——不要換成 component、service、API、boundary、layer、wrapper。

> **deletion test**:設想把這個 module 刪掉。複雜度若消失,它本來就只是穿透層;
> 複雜度若在 N 個呼叫端重新長出來,它有在做事。

## 上一次檢視的七個候選,只完成一個

| 上次 # | 候選 | 現況 |
|---|---|---|
| 3 | 台灣地址正規化只留一套 | ✅ 完成 `78f5ee8`。本次驗證 `permit_lookup._norm_addr`/`_norm_name` 確實委由 `address_db.fold_variants`(ADR-0001 已遵守) |
| 4 | 進 Sheets 的列只有一種形狀 | ⚠️ **變差**:3 種形狀 → **4 種**,外加 `main:2606` 一次現場翻譯 |
| 5 | 獨立的影像前處理 module | ⚠️ **變差**:循環 import 從 1 處 → **3 處**;且本次發現框架錯了(見候選 6) |
| 2 | 許可證文字只留一套判讀 | 未動。三個 regex 清單 + `_RE_PERMIT_VALUE` |
| 6 | 多數票純化 | 未動。得票平均信心仍三份(`:752`／`:838`／`:932`) |
| 7 | 已知清單管道 | 未動。**逐字未動**,三個函式仍零呼叫者 |
| 1 | 切開仲介反查台仲區塊的 seam | 未動 |

上次寫的「維持現狀是唯一糟糕的選項」(候選 7),兩個月後維持了現狀。

## 候選一覽

| # | 候選 | 強度 | 分類 | 來歷 |
|---|---|---|---|---|
| 1 | [Google 身分成為一個 module](#1-google-身分成為一個-module) | Strong | 缺 seam | **新** |
| 2 | [進 Sheets 的列只有一種形狀](#2-進-sheets-的列只有一種形狀) | Strong | shallow dispatch | 上次候選 4 |
| 3 | [已知清單管道:接起來或刪掉](#3-已知清單管道接起來或刪掉) | Strong | deletion test 失敗 | 上次候選 7 |
| 4 | [投票不碰硬碟](#4-投票不碰硬碟) | Strong | 缺 seam | 上次候選 6 |
| 5 | [雇主欄位切三刀](#5-雇主欄位切三刀) | Strong | 缺 seam | **新** |
| 6 | [一個手法蓋著兩個 seam](#6-一個手法蓋著兩個-seam) | Worth exploring | 缺 seam ×2 | 上次候選 5,重新框 |
| 7 | [docx 內嵌圖只留一套](#7-docx-內嵌圖只留一套) | Strong | 穿透層 | **新** |
| 8 | [CSV 字面值的對稱性](#8-csv-字面值的對稱性) | Worth exploring | 缺對稱測試 | **新** |

**建議動手順序**:1 →(7 與 3 同一輪順手做掉)→ 5 → 2 → 8 → 6 → 4。
4 手術面最大,留到最後並分階段。

---

## 1. Google 身分成為一個 module

**強度** Strong ｜ **依賴類別** ports & adapters ｜ **新面積**

**檔案** `pipeline.py:84`、`152–156`、`1373–1384`、`1642–1651`、`2253–2468`;
`conftest.py:31–36`、`66–81`;`C:\projects\gcp-identity\gcp_identity\credentials.py:89`

### 問題

認證與 preflight 是上次檢視之後最大的一塊新程式碼(commit `46ffc1c`／`98d364a`／
`740a613`,約 +215 行淨增),**零測試**。而它不是疏漏 —— 是結構造成的:

`conftest.py:31–36` 的 `_ensure_module` 先試真 import、失敗才補 stub。於是兩邊都不成立:

- **有裝 `gcp-identity` 的機器**(本機實測:裝在 `C:\projects\gcp-identity`,`pip install -e`):
  `__import__` 成功,**stub 從不生效**。`_StubProjectIdentity.credentials()` 那道
  `AssertionError("測試不應真的取得 Google 憑據")` 護欄是死的 —— 任何測試若走到
  `IDENTITY.credentials()`,會真的去打 Google。
- **沒裝的機器**:`_StubChecks` 只有 `SELF` 與 `ADMIN`,缺 `check_adc`／`check_token`／
  `quota_project_fix`／`CheckResult`。任何測試碰到 `_check_adc()`、`_check_token()`、
  `_check_sheets()` 或 `run_preflight()` 會炸 `AttributeError`,不是乾淨 skip。

### 三層無法重設的 process 全域快取

全 repo **零處** `cache_clear()`:

| 層 | 位置 |
|---|---|
| `IDENTITY = ProjectIdentity(...)` | `pipeline.py:156` —— **import 時就實例化** |
| `@lru_cache(maxsize=1) get_vision_client` | `pipeline.py:1373` |
| `@lru_cache(maxsize=1) get_sheets_service` | `pipeline.py:1642` |
| `ProjectIdentity._cache[scopes]` | `gcp_identity/credentials.py:89` —— 私有屬性 |

直接讀 `IDENTITY` 的地方:`:1384`、`:1651`、`:2336`、`:2343`、`:2354`、`:2369`。
`get_vision_client`／`get_sheets_service` 的 interface 是 `() -> client`,沒有參數能注入身分。
實務上大家繞道:`test_address_doubt.py:343` 直接
`monkeypatch.setattr(pipeline, "get_vision_client", lambda: None)` —— 繞過 lru_cache
而不是穿過 seam。

### 七個 preflight 檢查裡,三個通不過 deletion test

| 檢查 | 行號 | 診斷 |
|---|---|---|
| `_check_impersonation` | `:2329–2343` | 唯一邏輯是「兩個模組常數是否非空」,而 `:152–154` 的預設值是非空字面值 → **實質恆真**。刪掉即消失 |
| `_check_vision_client` | `:2397–2404` | `run_preflight:2441` 只在 `tok.ok` 時才跑它;`:2264–2266` 註解自承「Vision 的權限問題會在 Sheets 檢查先被抓到」。剩下能抓的是「套件沒裝」,而 `:73` 的 module-level import 在載入期就會炸。近乎刪掉即消失 |
| `_check_adc` | `:2319–2326` | 七個裡唯一回傳 `tuple[CheckResult, object]`,而第二元素在唯一呼叫端 `:2435` 被 `adc_result, _ = ...` 丟掉。`:2432–2434` 的註解甚至解釋了為何不該用它,卻仍留在 interface 上 |

### 略過被算成失敗

共用套件 `gcp_identity.checks.CheckResult` 有 **`blame` 與 `skipped`**;本檔 `:2268–2273`
重造一份只有 `name/ok/detail/fix` 四個字串的。`_adapt`(`:2307–2316`)做降型:
`blame` 折成 `fix` 的文字前綴(`:2315`),**`skipped` 整個丟掉**。

因為沒有 `skipped`,`run_preflight` 只能用假的失敗列表達「略過」:

```
:2445–2446   CheckResult("Vision client", False, "（因無法取得權杖而略過）")
:2448–2449   for n in (...): CheckResult(n, False, "（因 ADC 憑證不可用而略過）")
:2457        failed = [r for r in results if not r.ok]
:2462        logger.error(f"── preflight 未通過（{len(failed)} 項）──")
```

一個根因(ADC 不可用)印成「未通過(5 項)」五個 ✘。commit `98d364a` 的訊息正是
「修掉 preflight 掩蓋根因的閘門」—— **排序閘門修好了,計數與標記還在掩蓋根因**。

另外 `gcp_identity.checks` 已有 `render(results, title) -> bool`,做的事與
`run_preflight:2454–2467` 逐字相同。**deletion test**(本檔 `CheckResult` + `_adapt`
+ 渲染段):刪掉 → 移回共用套件,不重生。`_adapt` 是純為降型而存在的 adapter,
而降型本身就是資訊流失。

### ⚠ 一併查到一個會改變執行結果的矛盾(是 bug,不是架構問題)

```
:2414   if _permit_lookup() is None:
            problems.append("仲介名冊載入失敗（…不影響許可證判讀）")
:2416   → ok=False
:2457   failed = [r for r in results if not r.ok]
:2468   → return False
:2478   if not run_preflight(...): return          ← 整批不跑
```

`_permit_lookup`(`:100`／`:114`)的設計與註解、以及 `_check_dirs` 自己的 detail 文字
都寫著「降級留空、不中斷」,但 preflight 讓它變成**硬中斷**。

`_check_dirs`(`:2407–2419`)還把三件事(輸入資料夾存在／有 docx／名冊載得起來)
塞進一個 `CheckResult`,用 `"；".join(problems)`(`:2417`)攤平成字串。

### 做法

一個 module 擁有「Google 身分」,interface 只有 `credentials(scopes)`。
`pipeline`／`employer_extract`／未來的量測工具都依賴它,誰也不依賴 `pipeline` 的全域。
preflight 的渲染交還 `gcp_identity.checks.render`。

### 收穫

- 215 行從不可測變可測
- leverage:一個 interface,三個呼叫端
- locality:身分知識離開 `pipeline`
- `conftest` 的 sys.modules 謊言消失
- 刪掉 2 個恆真檢查與 1 個多餘回傳值
- `skipped` 不再被壓成假失敗

### 為什麼它擋在下一步前面

`gemini_bench.py`(在 `feat/gemini-offline-bench` 分支)必須
`from pipeline import IDENTITY, GCP_PROJECT_ID, get_sheets_service` 才拿得到
「Google 身分」與「Sheets 讀取」—— 一個純量測工具得 import 2,665 行的 pipeline。
那不是 `gemini_bench` 寫壞了,是這個 seam 不存在。

---

## 2. 進 Sheets 的列只有一種形狀

**強度** Strong ｜ **依賴類別** in-process ｜ 上次候選 4,**3 種 → 4 種**

**檔案** `pipeline.py:251–270`、`1345–1357`、`1854–1970`、`1975–2007`、
`2535–2542`、`2560–2564`、`2603–2609`

### 問題

四種形狀匯進 `_row_to_sheet_values`:

| # | 來源 | 行號 | 形狀 |
|---|---|---|---|
| 1 | `VisionQueueItem` | `:251–270` | dataclass,值在 `candidate_value` |
| 2 | manual_review dict | `:1345–1348`、`:1354–1357` | `{source_docx, reason}`,**事後被塞 `final_value`** |
| 3 | vision outcome dict | `:2560–2564` | `{source_docx, candidate_value, reason}` |
| 4 | **`novote_rescue` dict(新)** | `:2535–2542` | 與 3 同形但生命週期不同:先進 list、再被 rescue 就地改寫、再與 3 併批 |

外加 `main:2606–2609` 一次**現場翻譯**(把形狀 2 重組成形狀 3,只為了讓
`_row_to_sheet_values` 讀得到值)。`:2603` 的註解自承混裝。

判型與 `or` 鏈,一處都沒少:

```
:1995  if isinstance(r, VisionQueueItem):
:1975  if isinstance(r, dict):
:1982  own = r.get(k, "") if isinstance(r, dict) else ""   ← 非 dict 分支永遠 ""，死分支
:2000  value = r.get("final_value") or r.get("candidate_value", "")
:2005  r.get("reason") or r.get("note", "")
:1983  str(own or cached.get(k, "") or "")                  ← 三段 or
```

### 最尖的一點:`:1914` 不是覆寫,是創造一個不在任何型別宣告裡的 key

`build_vision_queue:1345–1348` 建的 manual_review dict 只有 `source_docx` 與 `reason`,
沒有 `final_value`。救援成功時 `:1914` 憑 `value_key="final_value"` 的**預設參數值**
憑空加上這個 key,`main:2607` 再 `.get("final_value", "")` 讀回來。
**跨三個階段、靠一個預設參數字串傳值。**

`rescue_manual_review_via_agency_phone`(`:1854–1970`,117 行,與上次檢視一字未動)
仍就地竄改傳入的 list:`item[value_key] = ...` 在 `:1914`／`:1933`／`:1949`,
`item["reason"] = ...` 在 `:1915`／`:1934`／`:1950`／`:1964`,回傳值只有筆數(`:1970`)。
**零測試** —— 四級反查優先序全靠副作用表達結果,只能用真 docx + 真 Vision 測。

### 做法

一種 `SheetRow`,在結果已知的四個地方就建好;救援**回傳**新列而非竄改參數。

### 收穫

- 117 行救援變可測(回傳而非副作用)
- interface 縮小:`value_key` 參數消失
- 跨階段就地竄改消失
- locality:欄位對應集中一處
- 判型與 `or` 鏈全部消失

---

## 3. 已知清單管道:接起來或刪掉

**強度** Strong ｜ **依賴類別** in-process ｜ 上次候選 7,**逐字未動**

**檔案** `pipeline.py:1498`、`1654`、`1682`、`2053`、`1555–1631`、`2525`;
`test_verify.py:564–599`

### 本次驗證(含 `.py`／`.bat`／`.md`,排除定義行與舊檢視存檔)

```
_append_upload_log    0 處非定義引用
_load_upload_log      0 處非定義引用
keyin_to_sheets       0 處非定義引用
pytest -k KnownPermits → 4 passed
```

於是:

1. `upload_log.csv` **從來沒有被寫出過**
2. `load_known_permits_from_log`(`:1498`,於 `:2525` 被呼叫)**恆回傳空集合**
3. `verify_vision_result` 裡 `in_known_list` 被用了 **10 次**
   (`:1456`、`:1555`、`:1568`、`:1576`、`:1584`、`:1590`、`:1597`、`:1612`、`:1619`、`:1631`)
   —— 全部不可到達
4. `test_verify.py:564–599` 的 4 支測試對著一個**輸入永遠不存在**的讀取函式亮綠燈

套 deletion test:刪掉它,複雜度不會在任何呼叫端重新長出來 —— 它只是消失。

### 做法(二選一,不要維持現狀)

- **A 接起來**:在 Sheets append 成功後呼叫 `_append_upload_log`。已知清單開始替
  D 欄 reason 補一句「此值歷史出現過」,幫人工排序。
- **B 刪掉**:移除三個無呼叫者的函式、`in_known_list` 欄位與其四支測試。
  約 120 行與一個惰性概念離開 codebase。

### 對 Gemini 工作的關聯

ADR-0008(在 `feat/gemini-offline-bench` 分支)已明確否決「用 `upload_log.csv` 當回測集
真值」,理由之一正是它從未被寫出。若選 A 案,那條否決理由要重新評估
(記的是機器輸出這一點仍然成立,所以結論大概不變)。

---

## 4. 投票不碰硬碟

**強度** Strong ｜ **依賴類別** in-process ｜ 上次候選 6,升級為 Strong

**檔案** `pipeline.py:736–808`(73 行)、`811–956`(**146 行**)、
`1112–1243`(**132 行**)、`2120–2250`(131 行)

### 問題

六個 `mkdir`+`save` 點交織在 OCR 迴圈裡:`:759+761`、`:790+792`、`:796+798`(低信心)、
`:846+848`、`:901+903`、`:907+909`(低信心)。

**沒有任何 seam 能在不碰硬碟的情況下跑投票邏輯。** `scan_image_large` 的多數票與
交叉比對在 `:918–956`,而要抵達那裡必須先走完 `:825–913` 的 ROI 迴圈,該迴圈每次
命中就寫檔。唯一無硬碟的純函式是 `_majority_vote`(`:717–721`),但它只挑贏家、
不算信心,而且自己也沒有測試。

**deletion test(裁切圖寫出)**:刪掉 `:759–761`／`:790–798`／`:846–848`／`:901–911`
→ 複雜度**不會**在呼叫端重新長出來。`run_scan:2128` 本來就擁有 `OUTPUT_DIR`,
`_scan_tier:2097` 本來就逐列拿到 `ScanResult`。這是穿透在錯的層。

### 重複

- 「得票平均信心」`round(sum(winning_confs) / len(winning_confs), 1)` 寫了三次:
  `:752`、`:838`、`:932` —— **與上次檢視所記完全相同,一次都沒收斂**
- `:748–767` 與 `:834–856` 是整段 20/23 行複製貼上,只差結尾 `return` / `continue`

### 超過 80 行的函式與其測試狀態

| 函式 | 行號 | 行數 | 測試 |
|---|---|---|---|
| `main` | `2471–2630` | 160 | 無 |
| `scan_image_large` | `811–956` | 146 | **無**(`test_tiered_scan.py:57` monkeypatch 掉) |
| `process_large_vs` | `1112–1243` | 132 | **無** |
| `run_scan` | `2120–2250` | 131 | 有,但四個依賴全被 stub |
| `rescue_manual_review_via_agency_phone` | `1854–1970` | 117 | **無** |
| `verify_vision_result` | `1523–1632` | 110 | **有,44 支,全 repo 覆蓋最好** |

**分佈本身就是診斷**:六支裡唯一有測試的 `verify_vision_result` 是唯一一支
「參數進、dataclass 出、不碰硬碟、不碰網路、不改全域」的。其餘五支各違反至少兩項。

### ⚠ 與 ADR-0005 相關但不衝突

分層掃描是這兩個月裡**唯一帶著 seam 與測試一起進來**的改動:`TIER1_IMAGES`(`:182`)、
`_image_index`(`:597`)、`split_tiers`(`:603`)、`has_permit_vote`(`:615`)都是純函式,
`test_tiered_scan.py` 25 支測試。

但 ADR-0005 自己明示接受的殘餘風險 ——「`mol_values ∩ permit_values` 的交集分層後
只由第一層三張圖組成」,唯一可能讓最終值不同的那條路徑 —— 落在
`process_large_vs:1124–1126`,而那個 132 行函式**全 repo 零測試**。
ADR 寫了 9 行分析它,程式碼上沒有一行測試釘住它。

不可測的那半(控制流):擴掃決策 `:2178–2188`、`classify_by_count` 必須在過濾之前
`:2144`、`_scan_tier:2097–2117`、佔位列的 `scan_tier = 2 if expanded`(`:2210`)。
只能靠 monkeypatch 四個模組屬性來測(`test_tiered_scan.py:38–59`)——
**那是 monkeypatch 縫,不是 seam**。

### 做法

一個 module 把 image bytes 變成 reading;裁切圖的寫出移到 `run_scan`
—— 它本來就擁有輸出目錄。

### 收穫

- 投票邏輯不碰硬碟即可測
- 刪掉複製貼上的 mol 區塊
- 得票平均信心只算一次
- ADR-0005 的風險路徑變可測
- ⚠ 手術面最大,建議分階段

---

## 5. 雇主欄位切三刀

**強度** Strong ｜ **依賴類別** in-process ｜ **新** ｜ 全 repo 最熱的檔案

**檔案** `employer_extract.py:341–459`(118 行,近 60 個 commit 改了 23 次)、
`604–612`、`615–662`

### 問題

七個子步驟卡在一個函式裡,其中三個已經是**純的、彼此無耦合**:

| 子步驟 | 行號 | 純? | 備註 |
|---|---|---|---|
| 斷行 | `:346` | 是 | |
| 區塊定位 | `:361–370` | 是 | **唯一有錯誤模式**(`:365–366` 提早返回 + `_note`) |
| 電話統計與挑選 | `:381–396` | 是 | **零耦合** —— 唯一輸入 `seg`、唯一輸出一個字串 |
| 地址抽取 | `:399–413` | 是 | 輸出三值,其中 `en_hint` 只被 `:455` 用掉 |
| 姓名抽取 | `:419–444` | 是 | **唯一耦合在 `:421`** 讀 `addr_cn`／`addr_en` |
| 組 dict | `:446–454` | 是 | |
| 標準化 + 三種疑慮 | `:455–458` | 否 | 就地改寫 |

**deletion test**:刪掉整個 `extract_employer_fields` → 複雜度不會消失,它是唯一持有
「雇主區塊的上下界在哪」這條知識的地方。它在做事。但它的**內部**沒有任何 seam,
所以每次改一個子步驟(改了 23 次)都要重讀 118 行才知道會不會踩到別的。

### 為什麼電話那一刀值得先切

`:374–380` 那七行註解記了**三個實測教訓**:32098(仲介電話洩漏成雇主電話)、
32262(農業契約地號被 PHONE regex 誤當電話)、32447/32417(傳真靠 labeled 優先勝出)。
這三件事現在只能透過完整 `extract_employer_fields` 去驗 ——
`test_employer_block.py:162–194` 為了測「切傳真」得餵四行假 OCR 並繞過錨定。
切開之後,每一條教訓變成一行測試。這是本檔知識密度最高的一段。

### 兩個 shallow 的疑慮組裝

- `_compose_doubt`(`:604–612`)收一整個 dict 只為讀三個鍵(`:610–611`)——
  interface(「呼叫端必須先把三條疑慮各自塞進這三個鍵」)比 implementation(一行 join)
  複雜。**後果直接可見**:`_apply_pledge_rescue`(`:985–988`)用不了它,只能自己再手寫
  一次 `"；".join(...)`。「一條疑慮長什麼樣」這條知識住在兩個地方。
- `_standardize_address`(`:615–662`)就地改寫:`raw_cn` 由參數進來(`:455`),
  `地址_英` 卻從 dict 讀(`:639`),再寫回四個鍵(`:629–632`)。呼叫端必須同時知道
  「哪些鍵它會讀」和「哪些鍵它會寫」。`test_address_doubt.py:18–21` 的 `_std()`
  就是這個洩漏的證據。

### ⚠ 順帶:ADR-0006 的核心決定從未被任何測試執行過

`_PLEDGE_FRONT`(`:115` + `:202`)是 ADR-0006 全篇最核心的決定
(「認頁只能靠 Tesseract 讀得穩的印刷體大標題,不用 `Majikan R.O.C`」,ADR-0006:50–73),
被實測 6/6 逼出來。

但 `test_pledge_rescue.py:80–85` 的 `pledge_front` fixture 把整個 `_page_marks` 換成
`lambda b: (0, b.decode() == front)`,`test_contract_page.py:118–122` 換成
`lambda b: (0, False)`。**這支 regex 在任何測試裡都沒有被執行過** ——
測試只釘住了「假設它認對之後會怎樣」。

同類空洞:`deink_red_stamp`(`:299`)的四個常數(`whiten_thresh=45`／`min_red=120`／
`contrast=(2,98)`)零測試,三個測試檔都把它 monkeypatch 掉;而 CONTEXT.md 把「去紅章」
定為領域概念、ADR-0004 記錄了它的已知失效條件。

### 做法

在 `:361` 上方與 `:381`／`:399`／`:419` 各切一刀。一個 module 吃 lines 吐區塊上下界,
三個 module 各吃 `seg` 吐一個值。

### 收穫

- 三個實測教訓各變一行測試
- the interface is the test surface:行進、值出
- locality:改一個子步驟只讀一個 module
- 疑慮組裝的第二份實作消失

---

## 6. 一個手法蓋著兩個 seam

**強度** Worth exploring ｜ **依賴類別** in-process ｜ 上次候選 5,**重新框**

**檔案** `employer_extract.py:700`、`772`、`998`;`pipeline.py:1769`、`1877`、`656–678`

### 上次的框架是錯的:今天並不存在靜態循環

`pipeline` 對 `employer_extract` 的兩處 import **也都是延遲的**:
`pipeline.py:1769`(在 `collect_employer_fields` 內)與 `pipeline.py:1877`
(在 `rescue_manual_review_via_agency_phone` 內)。**兩邊都在 module level 不 import 對方。**

所以 `employer_extract.py:771` 註解裡那個「延遲 import 避免循環」防的循環,
是由對面自己的延遲 import 共同維持的一個**約定**。後果是:這個 seam 沒有 owner ——
任一側把自己改成 eager 就會真的造出循環,所以**沒有任何一側能單獨修掉它**,
而兩側都沒有測試釘住這個約定。

### 真正的問題:一個手法底下藏著兩個不同的關注點

| 位置 | 實際需要 pipeline 的什麼 | 關注點 |
|---|---|---|
| `:772` | `preprocess`(`pipeline.py:656–678`)及其內部 `auto_rotate`／`crop_roi` | **像素** |
| `:700` | `get_vision_client` → 背後真正要的是 module 級的 `IDENTITY` | **憑證** |
| `:998` | 同 `:700`(且只是開發用 A/B 工具) | **憑證** |

抽出一個擁有裁切／去紅章／preprocess 的 leaf module,**只解掉 `:772` 一處**。
`:700` 與 `:998` 要的是帶憑證的 client,那條依賴掛在 `pipeline.py:156` 的 module 級
`IDENTITY` 上,與像素毫無交集 —— **3 處裡 2 處留著**。所以本候選與候選 1 要一起看。

上次檢視時只有 `:772` 一處;認證那塊新面積讓 `pipeline` 的全域身分**反向滲進**
`employer_extract`,變成 3 處。

### 去紅章有三種寫法,而 CONTEXT.md 把它定義成一個概念

- `deink_red_stamp`(`employer_extract.py:299–329`):紅通道為底 + 抹白遮罩 + 百分位拉伸
- `_deink_next_page_png`(`employer_extract.py:768–777`):
  `{"channel":"R","scale":2,"median":3,"contrast":(2,98)}` 餵 `pipeline.preprocess`
  —— **這個 dict 就是 `pipeline.py:200` 的「紅通道_2x_中值3」那筆 SCAN_CONFIGS 抄成字面值**
- `pipeline.py:200–212` 的 SCAN_CONFIGS 本體

契約頁走第一種、次頁走第二種、許可證掃描走第三種。ADR-0006:31–41 的那張表
(同一格讀出滄/濃/澹三個字)講的正是「換個前處理讀出的東西就不一樣」——
**這三份實作之間的差異會直接改變輸出**,卻沒有任何一處把三者的關係寫下來。

相對比例 ROI 裁切也有三份:`crop_fraction`(`employer_extract.py:143–151`)、
`pipeline.py:646–653` 的 `auto_rotate`+`crop_roi`、`pipeline.py:659–660` `preprocess` 內建。

### 錯誤模式不對稱(而錯誤模式是 interface 的一部分)

`vision_full_text`(`:695–705`,看起來只有 10 行)在 `client=None` 時會於 `:700`
執行 `pipeline` 的**整個 module body** —— 2,665 行、`google.cloud.vision`、
`googleapiclient`、`gcp_identity`、`permit_lookup`,以及 `:156` 的 `ProjectIdentity(...)` 建構。
憑證失敗因此從「import 時」搬到「第一次呼叫時」,而它落在
`rescue_name_from_pledge` 的 try/except(`:567–568`)裡會被降級成一行 warning。

對照 `pipeline.py:1770–1772`:pipeline 對自己那側有 try/except 並明確降級成
「H~O 本次全部留空」。**同一條 seam 的兩個方向,錯誤模式不對稱,而兩側都沒記下來。**

### 做法

兩個 leaf module:`imaging`(裁切／去紅章／preprocess)與 `identity`(候選 1)。
兩邊都依賴它們,誰也不依賴誰。三處延遲 import 是**刪掉**而非搬家。

### 收穫

- 兩個關注點各得一個 seam
- 一套去紅章,三個呼叫端
- 像素邏輯可單獨測(目前零測試)
- 延遲 import 被刪除而非繞過

---

## 7. docx 內嵌圖只留一套

**強度** Strong ｜ **依賴類別** in-process ｜ **新** ｜ 最便宜

**檔案** `employer_extract.py:726–734`;`pipeline.py:129`、`630–643`

### 問題

同一個「讀 docx 內嵌圖」的概念有兩份實作,而**副檔名集合已經分歧**:

| | `.png` | `.jpg` | `.jpeg` | `.gif` | `.bmp` | `.tiff` | `.tif` |
|---|---|---|---|---|---|---|---|
| `pipeline.IMAGE_EXTENSIONS`(`:129`) | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ |
| `employer_extract._docx_images`(`:727`) | ✔ | ✔ | ✔ | ✔ | **✘** | **✘** | **✘** |

一張 `.tif` 或 `.bmp` 內嵌契約頁 → **許可證掃描看得到(B 欄正常)、雇主擷取看不到
(H~O 全空)**。兩個欄位群對同一份文件給出不一致的結果,而沒有任何地方會報錯。

這正是 commit `e1d9789`(GIF 被靜默略過)那個 bug 的形狀,教訓寫在
`employer_extract.py:730` 的註解裡 —— **而當時只補了一份**。

**deletion test**:刪掉 `_docx_images` → 複雜度不會重新長出來,`pipeline.py:630`
那份已經是超集。它是穿透層。

### 順帶:同一份 docx 一次執行至少被 unzip 三次

`extract_employer_from_docx:928`、`_agency_block_lines:801`、`pledge_name_pages:283`
各自重新解壓(`employer_extract.py:250` 明說 bytes 不快取)。「zip 解壓遠比 Tesseract
便宜」是這條設計的承重假設,沒有量測支撐它。收斂成一套之後,有一個地方可以談快取。

### 同類重複:圖序自然排序也有兩份

`employer_extract.py:137` `_natural_key` 與 `pipeline.py:564` `_image_sort_key`,
回傳形狀不同(`(1<<30, name)` vs `(1, 0, name)`),而**有測試的是 pipeline 那份**
(`test_helpers.py:24–59`)。CONTEXT.md:50–59 把「圖序」定成一個領域概念、
還特別交代「一律用絕對序號」,這個概念的實作卻有兩份。

### 收穫

- 一個潛在安靜錯值消失
- locality:圖序與副檔名一處定義
- 刪掉一個穿透層
- 幾行的改動,Strong 的回報

---

## 8. CSV 字面值的對稱性

**強度** Worth exploring ｜ **依賴類別** in-process ｜ **新**

**檔案** `pipeline.py:297–298`、`341–375`、`378–428`

### 問題

`:297–298` 的 docstring 寫:「隱性狀態:cross_match 過去是 "✓"/"",vision_review 是
"Y"/"",靠字串比對判斷真假。現在分別是 bool,意圖明確。」

內部確實是 bool 了。但那些字面值沒有消失,**只是被搬到兩個必須手動保持對齊的地方**:

| 欄位 | 寫出 | 讀回 | round-trip 測試 |
|---|---|---|---|
| `mol_from_vote` | `:358` `"Y" if …` | `:411` `== "Y"` | **無** |
| `id_from_vote` | `:362` `"Y"` | `:415` `== "Y"` | **無** |
| `cross_match` | `:363` `"✓"` | `:416` `== "✓"` | **無** |
| `vision_review` | `:366` `"Y"` | `:419` `== "Y"` | **無** |
| `scan_tier` | `:364` | `:407` | 有(`test_tiered_scan.py:302`、`306`) |
| `conf` | `:356` | `:405` | 有(`test_decide.py:339–381`) |

### 失效的形狀

把 `:363` 改成 `"v"` 而忘了改 `:416` → `cross_match` 永遠讀回 `False` →
`process_large_vs:1123` 的規則 A 從此配不上 → **整批安靜地改走衝突路徑**。
不會拋錯、不會少欄,371 支測試全綠。

### 其餘由序列化造成的隱性狀態

- **`0` 與「未設定」同格**:`:356–357`、`:360–361`、`:365` 把 `0` 與 `0.0` 一律寫成 `""`。
  docstring `:347–348` 說這是「保留『未設定』的視覺差異」,代價是 `conf` 真的是 0.0 時
  在 CSV 上與「沒讀到」無從分辨。
- **壞值靜默吞成預設值**:`from_csv_row` 的 `to_float:387–388`、`to_int:393–394`、
  `status:399–400` 全部 `except → 預設`。手改 `matches.csv` 的 status 欄一個字 →
  `ResultStatus.OK`,那份 docx 從「全無命中」變成「正常命中」、**悄悄脫離人工審查佇列**。
- **`:407 scan_tier = to_int(...) or 1`**:同一行同時處理「舊 CSV 沒這欄」與「值是 0」,
  而 ADR-0005「對帳時補的一個洞」整段就是在講這一格失真會讓規則過期看不見。
- **`manual_review`(`:329`)仍是 `str` 不是 bool**,值域混著 `"Y"`(`:2205`)與
  `"mol 無值,需人工判斷"`(`:806`)。`:329` 的註解自己承認。這是 `:297–298` 宣稱
  解掉的那類問題唯一沒處理的殘留。

### deletion test:這一組體質是健康的

刪掉 `to_csv_row`／`from_csv_row` → 複雜度在 `run_scan:2196/2211/2223` 與
`build_vision_queue:1333` 重新長出來,**它有在做事,而且 locality 是對的**
(CSV 格式細節只在這兩個方法裡)。

所以本候選不是重構,是**補一組對稱性測試** —— 最小的改動、擋掉一整類安靜失效。

### 收穫

- 四對字面值有單一真相來源
- 一整類安靜失效被測試擋住
- 不動 implementation,只補測試
- locality 已經對了,只缺驗證

---

## 體質健康的部分(別動)

**`address_db.py`** —— 616 行,只有 3 個公開函式(`fold_variants`／`normalize_address`／
`find_roads`),25 個私有 helper。全 repo 最 deep 的 module。
唯一摩擦:`_load()`(`:99`)自己建相依(硬寫 `_DB_PATH` + `lru_cache`),
所以 32 個測試綁著一個 2.4MB 的 gitignore 資料檔 ——
本次實測把 `data/AllData.json` 藏起來,**32 個測試大聲失敗**(不是安靜通過,
只有 `test_address_norm.py:103` 一處有靜默 gate)。失敗比靜默好,但新機器 clone
下來測試會是紅的,直到有人手動下載政府資料檔。

**`permit_lookup.py`** —— ADR-0001 被確實遵守:`_norm_name`(`:224`)與 `_norm_addr`(`:233`)
都委由 `address_db.fold_variants`,沒有另寫一套。

**`verify_vision_result`** —— 110 行、44 支測試、全 repo 覆蓋最好。
它是六支 >80 行函式裡**唯一**「參數進、dataclass 出、不碰硬碟、不碰網路、不改全域」的。

**分層掃描(ADR-0005)** —— 這兩個月裡唯一帶著 seam 與測試一起進來的改動。

## 一併查證但不屬架構層的問題

**`conftest.py:14` 的 sys.path 仍然是錯的**(上次檢視已記,未動)

```python
ROOT = Path(__file__).resolve().parent.parent
```

`conftest.py` 位於 repo 根目錄,所以這行插入的是 `C:\projects` —— repo 的上一層。
`import pipeline` 目前能成立,靠的是 pytest 自己的 rootdir 插入在做真正的工作。
一旦改用 `--import-mode=importlib`,或有人把 conftest 移進 `tests/` 目錄,就會無聲失效。

**`with_retry`(`pipeline.py:431–473`)**

`:470` 直接 `time.sleep(wait)`,**沒有注入點**;裝飾在 `run_google_vision`(`:1387`)與
`_sheets_append_atomic`(`:1695`)上,import 時就綁定 —— 要測「4 次失敗後放棄」
得真的等 2+4+8 秒。`wrapper`(`:453–471`)的 for 迴圈之後沒有 return,
`max_attempts=0` 會安靜回 `None`。`:449` 的 docstring 自己寫「生產環境建議改用 tenacity」。
**0 測試。** shallow(6 個參數 / 20 行實作)且缺時間 seam。

**死符號與零引用**

- `CONTRACT_MARKERS`(`employer_extract.py:105`)—— 只被兩段 docstring 引用(`:177`、`:214`),
  而 `:214` 的 docstring 還在用它描述早已改成加權計分的行為
- `find_contract_image`(`employer_extract.py:209`)—— 零產品呼叫端(只有
  `test_contract_page.py:71–87` 在測),且自帶第二套 tie/零分語意
- `agency_phones_from_next_page`(`employer_extract.py:903`)—— 零引用
- `extract_employer`(`employer_extract.py:708`)—— 零引用(只有 `__main__`)
- `_ab_compare`(`employer_extract.py:996`)—— 零引用、零測試
- `SheetStatus.VISION`(`pipeline.py:247`)—— 寫入路徑不再用它,
  只剩無呼叫者的 `_load_upload_log:1674` 為向後相容把它正規化掉

## 如何重跑這份檢視

```
/improve-codebase-architecture
```

會重新走一次熱區探索並輸出 HTML 報告到系統暫存區。本檔是 2026-09-18 那次的
markdown 存檔(HTML 在暫存區會被清掉,故納入版控供後續維護參考)。

重跑時記得:
- 上次(2026-07-28)的候選 3 已完成,候選 4、5 變差,候選 2、6、7 未動
- `docs/adr/` 有 7 份決策紀錄(main 上);`feat/gemini-offline-bench` 分支另有 ADR-0008
- **檢視結果若與 ADR 衝突,要明確標示並說明為何值得重開,不要靜默推翻**
