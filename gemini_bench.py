#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Gemini 雇主欄位離線比對台 —— 量測用,**不被 pipeline.py import,不影響任何寫入**。

設計與被否決的替代方案見 docs/adr/0008-gemini-offline-benchmark-first.md。
術語(安靜錯值、回測集)見 CONTEXT.md。

用法
────
  python gemini_bench.py models            # 列出 Vertex 上可用的模型 id(先跑這個)
  python gemini_bench.py fetch-truth       # 從 Sheets P 欄「已核」的列抓回測集
  python gemini_bench.py run               # 兩模式都跑,輸出報表
  python gemini_bench.py run --mode text   # 只跑文字模式(不必重跑 Vision)
  python gemini_bench.py run --model <id> --failures-from scan_results/gemini_bench.csv \
                             --out scan_results/gemini_bench_pro.csv
                                           # 只把判錯的格重跑一次強模型

為什麼分兩個模式(診斷分解)
──────────────────────────
現有處理順序是 裁切 → 去紅章 → Vision → regex 解析,其中兩個環節可獨立替換:

  文字模式:餵已快取的 Vision 全文(scan_results/employer_texts.json)→ 測**解析**的錯
  圖片模式:餵裁切圖(scan_results/employer_crops/)          → 測**讀值**的錯

32508「契約標題頂上來當雇主名」是解析的錯(值在文字裡,只是被挑錯行);
32510「梅獅路讀成梅翠路」是讀值的錯(文字裡根本沒有正確答案)。
分解的用途是決定改動規模 —— 若文字模式已接近真值,只需換掉 regex、保留 Vision。

文字模式**不必重跑 Vision**(全文已快取),但 Gemini 呼叫本身仍計費 —— 只是 input 是
純文字、比圖片便宜。省下的是 Vision 那一次,不是全部。

驗收硬條件
──────────
報表的 `安靜錯值` 欄任何一格是 Y,該欄位就不得自動寫入,最多只能當疑慮標示的材料。
定義:Gemini 在「本來沒有錯值」的格上(現有留空、或現有讀對)填進一個與真值不符的值。
只看準確率會系統性高估 Gemini —— 它救回 8 格對的、同時弄出 2 格看似正常的錯值,
準確率漂亮上升,而那 2 格正是整套設計在防的東西(見 ADR-0003)。
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import re
import sys
import time
from pathlib import Path

logger = logging.getLogger("gemini_bench")

# ═══════════════════════════════════════════════════════════════════════════
# 設定
# ═══════════════════════════════════════════════════════════════════════════

OUTPUT_DIR   = Path("./scan_results")
DATA_DIR     = Path("./data")

TEXTS_PATH   = OUTPUT_DIR / "employer_texts.json"     # {docx: {"image":…, "text":…}}
CROP_DIR     = OUTPUT_DIR / "employer_crops"          # {docx主檔名}_{圖檔名}
REPORT_PATH  = OUTPUT_DIR / "gemini_bench.csv"
SUMMARY_PATH = OUTPUT_DIR / "gemini_bench_summary.txt"
TRUTH_PATH   = DATA_DIR / "ground_truth.csv"          # 回測集(.gitignore 已明列)

# 模型 id 不寫死猜測值:Vertex 與 AI Studio 的 id 命名不完全一致、且會隨版本增減。
# 先跑 `python gemini_bench.py models` 確認實際可用的 id,再用 --model 或這個環境變數指定。
DEFAULT_MODEL = os.environ.get("GEMINI_BENCH_MODEL", "gemini-flash-lite-latest")

# Vertex 需要 region。用環境變數覆蓋以便日後改到 asia-east1 之類。
LOCATION = os.environ.get("GEMINI_BENCH_LOCATION", "us-central1")

# 明確宣告要為服務帳號換取哪種權杖 —— 同 pipeline 對 SHEETS_SCOPES 的做法,
# 不讓 client library 自己去推斷(那會退回 ADC、也就是執行者本人的身分)。
VERTEX_SCOPES = ("https://www.googleapis.com/auth/cloud-platform",)

# 比對的五個欄位。刻意**只比 OCR 原文層**,不比 J 欄標準地址與 N 欄郵遞區號 ——
# 那兩欄是 address_db 查官方門牌庫的產出,不是 OCR 讀出來的,把它們算進來會
# 把「地址庫命中率」混進「引擎辨識率」裡。對應 Sheets:H I K M O。
FIELDS = ("雇主名稱_中", "雇主名稱_英", "地址_中", "地址_英", "電話")

# Sheets 欄索引(0-based)。權威來源是 pipeline._EMPLOYER_COL_KEYS;
# 注意 L 欄是疑慮標示、不是英文標準地址(pipeline.py 頂部曾有過時註解)。
_SHEET_COL = {
    "source_docx":  0,   # A
    "雇主名稱_中":   7,   # H
    "雇主名稱_英":   8,   # I
    "地址_中":      10,   # K  (OCR 原文;J 是標準地址,不比)
    "地址_英":      12,   # M  (OCR 原文)
    "電話":         14,   # O
}
_VERIFIED_COL = 15       # P = 已核(只由人填)


# ═══════════════════════════════════════════════════════════════════════════
# 等值判準(分欄不同)
# ═══════════════════════════════════════════════════════════════════════════
# 判準本身會安靜地說謊:太鬆會蓋掉 Gemini 的錯,太嚴會把它的對判成錯,而兩種
# 情況在報表上都只是一個數字。故報表另存三欄原文供抽查。

def _norm_addr_cn(s: str) -> str:
    """中文地址:過異體折疊。臺/台、全半形、一段/1段 是寫法差異,不是錯。"""
    from address_db import fold_variants
    return fold_variants(s or "")


def _norm_name_cn(s: str) -> str:
    """中文姓名:**逐字嚴格,只去空白**。

    刻意不用 fold_variants —— 它把 鍾→鐘,而兩者都是台灣常見姓氏,折疊會把
    漢字錯讀直接蓋掉。而漢字正是引擎差距最大的地方(見 ADR-0006:同一格用
    Tesseract 換前處理讀出 滄/濃/澹 三個字,Vision 讀對)。
    """
    return re.sub(r"\s+", "", s or "")


def _norm_en(s: str) -> str:
    """英文欄:統一小寫、標點壓成單一空白。契約英譯沒有單一正確寫法。"""
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def _norm_phone(s: str) -> str:
    """電話:只留數字。-、(0)、全形連字號都是格式差異。"""
    return re.sub(r"\D", "", s or "")


_NORM = {
    "雇主名稱_中": _norm_name_cn,
    "雇主名稱_英": _norm_en,
    "地址_中":    _norm_addr_cn,
    "地址_英":    _norm_en,
    "電話":       _norm_phone,
}


def same(field: str, a: str, b: str) -> bool:
    n = _NORM[field]
    return n(a) == n(b)


def blank(field: str, v: str) -> bool:
    return not _NORM[field](v)


# ═══════════════════════════════════════════════════════════════════════════
# 素材載入
# ═══════════════════════════════════════════════════════════════════════════

def load_texts() -> dict[str, dict]:
    """讀已快取的 Vision 全文。形狀 {docx: {"image":…, "text":…}}。"""
    if not TEXTS_PATH.exists():
        raise SystemExit(
            f"找不到 {TEXTS_PATH} —— 這份快取由 pipeline 的雇主擷取階段產生。\n"
            f"先跑一次 pipeline(或它的雇主階段)讓它長出來。")
    with open(TEXTS_PATH, encoding="utf-8") as f:
        return json.load(f)


def crop_for(docx: str) -> Path | None:
    """找某份 docx 的雇主裁切圖。檔名慣例 {docx主檔名}_{圖檔名}。"""
    stem = Path(docx).stem
    hits = sorted(CROP_DIR.glob(f"{stem}_*")) if CROP_DIR.is_dir() else []
    return hits[0] if hits else None


def load_truth() -> dict[str, dict[str, str]]:
    """讀回測集。缺檔回空 dict —— 此時 run 會退成差異清單模式。"""
    if not TRUTH_PATH.exists():
        return {}
    truth: dict[str, dict[str, str]] = {}
    with open(TRUTH_PATH, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            docx = (r.get("source_docx") or "").strip()
            if docx:
                truth[docx] = {k: (r.get(k) or "").strip() for k in FIELDS}
    return truth


def baseline_fields(text: str) -> dict[str, str]:
    """現有做法的結果 —— 直接拿 employer_extract 的解析器跑同一份全文。

    這是離線可重放的:同一份 Vision 全文進去,現有程式吐什麼就是什麼,
    零 API 成本、且不會因為重跑 Vision 而讀出不一樣的字。
    """
    from employer_extract import extract_employer_fields
    f = extract_employer_fields(text)
    return {k: (f.get(k) or "") for k in FIELDS}


# ═══════════════════════════════════════════════════════════════════════════
# Gemini(Vertex)
# ═══════════════════════════════════════════════════════════════════════════

_PROMPT = """你是台灣勞動契約的資料擷取工具。請從{source}擷取「甲方(雇主)」的五個欄位。

嚴格規則:
1. 只擷取甲方(雇主 / Majikan)的資料。同一頁上方另有「台仲(仲介機構)」的名稱、
   地址、電話方框,那是**不同的當事人**,絕對不可以填進雇主欄位。
2. 讀不到、看不清、或無法確定是否屬於雇主的欄位,value 一律填空字串。
   **不要推測、不要從其他欄位推導、不要補齊殘缺的字。留空是正確答案之一。**
3. evidence 必須是你據以判斷的**原文照抄**(含標籤那一段),不可改寫、不可翻譯、
   不可補字。value 留空時,evidence 寫你找過哪裡、為什麼判定取不到。
4. 「傳真」不是電話。同一行常寫成「電話:03-5310852,傳真:03-5277128」,只取電話。
5. 地址_中 與 地址_英 各取契約上的原文,**不要互相翻譯、不要標準化、不要補郵遞區號**。

欄位:
- 雇主名稱_中:甲方的中文姓名或公司名(雇主可以是個人也可以是公司)
- 雇主名稱_英:甲方的英文/羅馬拼音名(常標為 Nama Majikan)
- 地址_中:甲方的中文地址原文
- 地址_英:甲方的英文地址原文
- 電話:甲方的電話
"""

_FIELD_SCHEMA = {
    "type": "object",
    "properties": {
        "value":    {"type": "string"},
        "evidence": {"type": "string"},
    },
    "required": ["value", "evidence"],
}
_SCHEMA = {
    "type": "object",
    "properties": {f: _FIELD_SCHEMA for f in FIELDS},
    "required": list(FIELDS),
}


def get_client():
    """Vertex client。憑證明確傳入,走與 Vision / Sheets 同一個 IDENTITY。

    走 Vertex 而非 AI Studio 免費層的理由見 ADR-0008:免費層的影像會被用於改善
    Google 產品,而這裡送的是含身分證號、住址、電話的移工契約。
    """
    from google import genai
    from pipeline import IDENTITY, GCP_PROJECT_ID
    return genai.Client(
        vertexai=True,
        project=GCP_PROJECT_ID,
        location=LOCATION,
        credentials=IDENTITY.credentials(VERTEX_SCOPES),
    )


def _ask(client, model: str, parts: list, source_desc: str) -> dict[str, dict]:
    """單次呼叫,回傳 {欄位: {"value":…, "evidence":…}}。失敗重試 3 次。"""
    from google.genai import types

    cfg = types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=_SCHEMA,
        temperature=0,          # 量測要可重現
        system_instruction=_PROMPT.format(source=source_desc),
    )
    last = None
    for attempt in range(3):
        try:
            resp = client.models.generate_content(
                model=model, contents=parts, config=cfg)
            # resp.text 在被安全過濾攔下、或回應為空時是 None。直接 json.loads(None)
            # 會拋 TypeError,而那個訊息完全看不出成因(會像是程式 bug 而非模型拒答)。
            if not resp.text:
                reason = getattr(getattr(resp, "prompt_feedback", None),
                                 "block_reason", None)
                fin = [getattr(c, "finish_reason", None)
                       for c in (resp.candidates or [])]
                raise RuntimeError(
                    f"回應為空(block_reason={reason}, finish_reason={fin})")
            return json.loads(resp.text)
        except Exception as e:                       # noqa: BLE001
            last = e
            logger.warning(f"    呼叫失敗({attempt + 1}/3):{e!r}")
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"三次都失敗:{last!r}")


def gemini_from_text(client, model: str, text: str) -> dict[str, dict]:
    return _ask(client, model, [text],
                "以下這份契約掃描頁的 OCR 全文(欄位順序可能因兩欄版面而錯亂)")


def gemini_from_image(client, model: str, path: Path) -> dict[str, dict]:
    from google.genai import types
    mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
    part = types.Part.from_bytes(data=path.read_bytes(), mime_type=mime)
    return _ask(client, model, [part], "以下這張契約頁上緣的裁切影像")


# ═══════════════════════════════════════════════════════════════════════════
# 判定
# ═══════════════════════════════════════════════════════════════════════════

_REPORT_COLS = ("source_docx", "mode", "field",
                "真值", "現有", "gemini", "gemini_依據",
                "verdict", "安靜錯值")


def judge(field: str, truth: str | None, base: str, gem: str) -> tuple[str, str]:
    """回傳 (verdict, 安靜錯值)。

    truth 為 None 表示這份還沒進回測集 —— 此時只能報「待判」,不猜誰對。

    安靜錯值的定義刻意窄:Gemini 在「本來沒有錯值」的格上(現有留空、或現有讀對)
    填進一個與真值不符的值。現有本來就讀錯的格,Gemini 也錯不算**新增** ——
    那是既有問題,不是這次改動帶進來的風險。
    """
    if truth is None:
        return ("待判" if not same(field, base, gem) else "同值"), ""

    b_ok, g_ok = same(field, truth, base), same(field, truth, gem)
    if b_ok and g_ok:
        verdict = "同對"
    elif not b_ok and g_ok:
        verdict = "救回"
    elif b_ok and not g_ok:
        verdict = "弄壞"
    elif blank(field, base) and blank(field, gem):
        verdict = "同空"
    else:
        verdict = "同錯"

    silent = "Y" if (not g_ok and not blank(field, gem)
                     and (blank(field, base) or b_ok)) else ""
    return verdict, silent


# ═══════════════════════════════════════════════════════════════════════════
# 子命令
# ═══════════════════════════════════════════════════════════════════════════

def cmd_models(_args) -> None:
    """列出 Vertex 上可用的模型 id。

    存在的理由:Vertex 與 AI Studio 的 id 命名不完全一致、且隨版本增減。
    寫死一個猜測的 id 會得到 404,而 404 看起來很像權限問題,浪費時間。
    """
    client = get_client()
    print(f"location={LOCATION}\n")
    for m in client.models.list():
        name = getattr(m, "name", "")
        if "gemini" in name.lower():
            print(f"  {name}")


def cmd_fetch_truth(_args) -> None:
    """從 Sheets 抓 P 欄「已核」非空的列,寫成回測集。

    只讀,不寫 Sheets。只認人簽過的列 —— 理由見 CONTEXT.md「回測集」:
    程式自己寫進去的值是機器輸出,拿它當真值會在「新做法讀對、舊做法讀錯」的
    每一格判新做法錯。
    """
    from pipeline import get_sheets_service, SPREADSHEET_ID, SHEET_NAME

    rng = f"{SHEET_NAME}!A2:P"
    resp = (get_sheets_service().spreadsheets().values()
            .get(spreadsheetId=SPREADSHEET_ID, range=rng).execute())
    rows = resp.get("values", [])

    def cell(row: list, i: int) -> str:
        return (row[i] if i < len(row) else "").strip()

    kept, skipped = [], 0
    for row in rows:
        docx = cell(row, _SHEET_COL["source_docx"])
        if not docx:
            continue
        if not cell(row, _VERIFIED_COL):        # P 欄空 = 沒人簽過
            skipped += 1
            continue
        kept.append({"source_docx": docx,
                     **{f: cell(row, _SHEET_COL[f]) for f in FIELDS},
                     "已核": cell(row, _VERIFIED_COL)})

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(TRUTH_PATH, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["source_docx", *FIELDS, "已核"])
        w.writeheader()
        w.writerows(kept)

    print(f"回測集已寫入 {TRUTH_PATH}:{len(kept)} 列已核、{skipped} 列略過(P 欄空)")
    if not kept:
        print("\n⚠ 一列都沒有。確認 Sheets 的 P 欄標題是「已核」、且審過的列有填值。\n"
              "  程式只 append A~O,永遠不會自己填 P 欄 —— 那是刻意的。")


def _failures_from(path: Path) -> set[str]:
    """從既有報表挑出「判錯」的 docx,供只重跑那幾份用強模型。"""
    bad = {"弄壞", "同錯"}
    out: set[str] = set()
    with open(path, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            if r.get("verdict") in bad or r.get("安靜錯值") == "Y":
                out.add(r["source_docx"])
    return out


def cmd_run(args) -> None:
    texts = load_texts()
    truth = load_truth()
    model = args.model
    modes = ("text", "image") if args.mode == "both" else (args.mode,)

    targets = sorted(texts)
    if args.only:
        want = {s if s.endswith(".docx") else f"{s}.docx"
                for s in args.only.split(",") if s.strip()}
        targets = [d for d in targets if d in want]
    if args.failures_from:
        want = _failures_from(Path(args.failures_from))
        targets = [d for d in targets if d in want]
        print(f"只重跑判錯的 {len(targets)} 份:{', '.join(targets) or '(無)'}")

    if not targets:
        raise SystemExit("沒有要跑的檔案。")
    if not truth:
        print("⚠ 回測集不存在 → 差異清單模式:只並排列出現有與 Gemini 不一致的格,\n"
              "  verdict 一律「待判」。先跑 fetch-truth 才會有計分。\n")

    client = get_client()
    out_path = Path(args.out) if args.out else REPORT_PATH
    rows: list[dict] = []

    for docx in targets:
        text = texts[docx].get("text", "")
        base = baseline_fields(text)
        t = truth.get(docx)
        print(f"[{docx}] {'(有真值)' if t else '(無真值)'}")

        for mode in modes:
            try:
                if mode == "text":
                    got = gemini_from_text(client, model, text)
                else:
                    crop = crop_for(docx)
                    if crop is None:
                        print(f"  image: 找不到裁切圖,略過")
                        continue
                    got = gemini_from_image(client, model, crop)
            except Exception as e:                   # noqa: BLE001
                # 單檔失敗不讓整批掛掉 —— 同 pipeline 對 Vision 失敗的處理。
                print(f"  {mode}: 放棄({e!r})")
                continue

            for field in FIELDS:
                g = got.get(field) or {}
                gv, ge = (g.get("value") or ""), (g.get("evidence") or "")
                tv = t.get(field) if t else None
                verdict, silent = judge(field, tv, base[field], gv)
                if not truth and verdict == "同值":
                    continue          # 差異清單模式:一致的格不列
                rows.append({
                    "source_docx": docx, "mode": mode, "field": field,
                    "真值": tv if tv is not None else "",
                    "現有": base[field], "gemini": gv, "gemini_依據": ge,
                    "verdict": verdict, "安靜錯值": silent,
                })

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(_REPORT_COLS))
        w.writeheader()
        w.writerows(rows)

    _summarise(rows, model, modes, out_path, scored=bool(truth))


def _summarise(rows: list[dict], model: str, modes, out_path: Path,
               *, scored: bool) -> None:
    lines = [f"model={model}  modes={','.join(modes)}  報表={out_path}",
             f"樣本格數={len(rows)}", ""]

    if not scored:
        lines += ["差異清單模式(無回測集):以下只是現有與 Gemini 不一致的格,",
                  "誰對誰錯未判定。跑 fetch-truth 之後重跑才會有計分。"]
    else:
        for mode in modes:
            sub = [r for r in rows if r["mode"] == mode]
            if not sub:
                continue
            tally: dict[str, int] = {}
            for r in sub:
                tally[r["verdict"]] = tally.get(r["verdict"], 0) + 1
            silent = [r for r in sub if r["安靜錯值"] == "Y"]
            base_ok = tally.get("同對", 0) + tally.get("弄壞", 0)
            gem_ok  = tally.get("同對", 0) + tally.get("救回", 0)
            n = len(sub)
            lines += [
                f"── {mode} 模式({n} 格)──",
                f"  現有正確   {base_ok}/{n}",
                f"  Gemini 正確 {gem_ok}/{n}",
                "  " + "  ".join(f"{k}={v}" for k, v in sorted(tally.items())),
                f"  ★ 新增安靜錯值 {len(silent)} 格" + ("" if silent else " ← 硬條件通過"),
            ]
            for r in silent:
                lines.append(f"      {r['source_docx']} {r['field']}: "
                             f"真值={r['真值']!r} 現有={r['現有']!r} "
                             f"gemini={r['gemini']!r}")
            lines.append("")
        lines += [
            "驗收(ADR-0008):準確率提升 **且** 新增安靜錯值為 0。",
            "第二條是硬條件 —— 違反的欄位不得自動寫入,最多當疑慮標示的材料。",
        ]

    text = "\n".join(lines)
    SUMMARY_PATH.write_text(text + "\n", encoding="utf-8")
    print("\n" + text)
    print(f"\n摘要已寫入 {SUMMARY_PATH}")


# ═══════════════════════════════════════════════════════════════════════════

def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")     # Windows 主控台字碼頁

    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("models", help="列出 Vertex 上可用的模型 id").set_defaults(
        func=cmd_models)
    sub.add_parser("fetch-truth", help="從 Sheets P 欄「已核」抓回測集").set_defaults(
        func=cmd_fetch_truth)

    r = sub.add_parser("run", help="跑比對並輸出報表")
    r.add_argument("--mode", choices=("text", "image", "both"), default="both")
    r.add_argument("--model", default=DEFAULT_MODEL)
    r.add_argument("--only", help="只跑這幾份,逗號分隔(可省 .docx)")
    r.add_argument("--failures-from", help="只重跑該報表裡判錯的那幾份")
    r.add_argument("--out", help=f"報表輸出路徑(預設 {REPORT_PATH})")
    r.set_defaults(func=cmd_run)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
