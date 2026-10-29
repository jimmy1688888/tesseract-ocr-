# LinaOCR (tesseract-ocr-)

跨國人力仲介／移工文件 OCR pipeline:docx 契約掃描 → 許可證(B 欄)、
仲介機構(E~G 欄)、雇主資料(H~O 欄)擷取 → 寫入 Google Sheets。
主要模組:pipeline.py / permit_lookup.py / employer_extract.py / address_db.py。

## Agent skills

### Issue tracker

Issues 追蹤於本 repo 的 GitHub Issues(使用 `gh` CLI)。See `docs/agents/issue-tracker.md`.

### Triage labels

採五個標準 triage 標籤(needs-triage / needs-info / ready-for-agent /
ready-for-human / wontfix)。See `docs/agents/triage-labels.md`.

### Domain docs

single-context:根目錄 `CONTEXT.md` + `docs/adr/`。See `docs/agents/domain.md`.

### 核心原則：防止單檔膨脹 (Monolith Prevention)

1. **單檔行數限制**：新建立或重構後的模組，單一檔案原則上不超過 150～200 行。超過時必須依職責拆分為子模組。
2. **禁止在舊巨石檔案追加邏輯**：針對專案中既有的萬行大型檔案，嚴禁直接在該檔案內新增新功能或函式。新邏輯必須建立在獨立的新檔案中，舊檔案僅負責引用或轉發。
3. **分層與模組化**：嚴格落實介面（Interfaces/Types）、資料處理（Services/Data）、純工具（Utils）與呼叫端分離。

---

## 乾淨程式碼指引 (Clean Code Guidelines)

### 1. 以常數取代魔術數字 (Constants Over Magic Numbers)
- 以具名常數取代程式碼中寫死的數值（Hard-coded values）。
- 使用具描述性且能清楚解釋數值用途的常數名稱。
- 將常數統一置於檔案頂端，或放置在專門的常數定義檔（如 `constants/`）中。

### 2. 具實質意義的命名 (Meaningful Names)
- 變數、函式與類別的命名應能直接展現其意圖與用途。
- 命名應清楚交代其存在的理由以及如何被使用。
- 除非是業界通用的簡寫，否則避免使用自創或晦澀的縮寫。

### 3. 聰明且精準的註解 (Smart Comments)
- 不要註解「這段代碼在做什麼」——應讓代碼本身具備自解釋能力（Self-documenting）。
- 註解應著重於解釋「為什麼要採用這種特定做法」。
- 針對 API、複雜演算法以及不明顯的副作用（Side effects）撰寫文件註解。

### 4. 單一職責原則 (Single Responsibility)
- 每個函式只專注做好單一一件事，保持短小且目標聚焦。
- 若一個函式需要額外寫註解才能解釋其行為，就代表它應該被拆分。
- 單一檔案原則上只導出一個主要實體或一組高聚合的功能。

### 5. DRY 原則 (Don't Repeat Yourself / 避免重複)
- 將重複出現的程式碼抽離為可重複使用的函式或獨立模組。
- 透過良好的抽象層共用邏輯，維持單一信任來源（Single Source of Truth）。

### 6. 清晰的架構與組織 (Clean Structure)
- 將高度相關的程式碼組織在一起，依照清晰合理的邏輯層次安排架構。
- 保持檔案與目錄命名規範的一致性。
- 禁止將 UI 元件、業務邏輯與 API 請求混寫在同一個檔案內。

### 7. 封裝 (Encapsulation)
- 隱藏內部實作細節，僅對外暴露清晰明確的介面（Interfaces）。
- 將深層巢狀的條件判斷抽離為命名清晰的獨立函式。

### 8. 程式碼品質維護 (Code Quality Maintenance)
- 持續進行小步重構，及早償還技術債。
- 實踐童軍法則：修改過的代碼，離開時必須比剛看到時更乾淨。

### 9. 測試 (Testing)
- 在修復 Bug 之前，先撰寫能重現問題的測試案例。
- 測試代碼本身同樣要保持高可讀性與可維護性。
- 針對極端邊界條件（Edge cases）與異常錯誤情境進行測試。

### 10. 版本控制規範 (Version Control)
- 撰寫語意清楚的 Commit 訊息。
- 保持 Commit 顆粒度細小且目標單一（一個重構動作一個 Commit）。
- 使用具備明確識別意義的分支名稱。