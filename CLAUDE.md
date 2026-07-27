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
