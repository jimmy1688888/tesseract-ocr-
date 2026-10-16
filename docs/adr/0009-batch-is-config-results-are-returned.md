# 一批只是設定,結果走回傳

[CONTEXT.md](../../CONTEXT.md) 的「一批」是一次啟動要處理的那組 docx,連同這次的輸出
放在哪裡。程式碼裡是 `batch.py` 的 `Batch`。

本 ADR 記的是 `Batch` **不裝什麼**,因為那是最容易被下一個人「順手」加回去的部分。

## 起因

`pipeline.py` 原本靠 import 時綁死的全域描述一次執行:`INPUT_DIR`、`OUTPUT_DIR`、
五個從 `OUTPUT_DIR` 衍生的路徑,以及 process 全域的 `_EMPLOYER_FIELDS_BY_DOCX`。
docx 在流程中被縮成檔名(穿過 `matches.csv` 時只剩 `source_docx`),反查救援要開檔
時再用 `INPUT_DIR / 檔名` 拼回去 —— `--file` 指到 `./docs` 以外時,救援讀錯檔或靜默
跳過。測試則只能逐一 monkeypatch 這些全域,而衍生路徑不會跟著 `OUTPUT_DIR` 走,
得分開 patch。

## 決定

**`Batch` 是不可變的設定:從哪讀(docx 的完整路徑)、往哪寫(輸出目錄與其衍生路徑)。**
處理的結果 —— 雇主欄位、救援結果、要寫進 Sheets 的列 —— 一律由產生它的函式**回傳**,
由 `main` 交給下一步。

`Batch` 明確當參數往下傳,不做成模組層的「目前這一批」。

## 為什麼結果不放進 Batch

放進去很方便:`Batch` 已經被傳到每個需要的地方,多掛一個 dict 就不必改任何簽名。
但那只是把 `_EMPLOYER_FIELDS_BY_DOCX` 換個位置 ——

- 寫入它的函式仍然是「寫進某處」的副作用,讀它的函式仍然有一個簽名上看不見的輸入
- 誰先寫、誰後讀的順序相依,又藏回物件裡
- 測試又得先塞資料、再確認沒有殘留

回傳值讓順序相依浮在 `main` 上,一眼看得到。與 #7(疑慮組裝改成回傳)、#13(救援
回傳新列)同一個方向。

## 為什麼不學 identity 做成模組層全域

`identity.py` 用 `install()` / `current()`,因為 Google 身分是**整個 process 共用的
資源**,誰來要都一樣。一批是**這一次呼叫的資料**:做成模組層全域,等於把 `INPUT_DIR`
換個名字 —— 函式讀哪一批從簽名看不出來,測試又要記得 install / reset。

## 也不裝的

- **名冊查表器**:2.2MB 唯讀資源,跨批共用合理;它是降級來源,不是本批的狀態
- **CLI 選項**(`--image`、`--roi`、`--full-scan`):已經以參數傳給 `run_scan`
- **Google 身分與 client**:[#3](https://github.com/jimmy1688888/tesseract-ocr-/issues/3) 已讓它自成 module

## 後果

- 需要 `Batch` 的函式(約 7 個)簽名都要多一個參數,分三刀改,每刀只動自己那幾個
- 建立 `Batch` 本身不會失敗;「有沒有東西可處理」是 preflight 的閘門看 `Batch` 判斷,
  `main` 在 `--skip-preflight` 時仍以同一個判斷擋空清單
- 想把某個結果「暫存」到 `Batch` 上的時候,先回來讀這份
