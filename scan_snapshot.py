# -*- coding: utf-8 -*-
"""掃描快照:許可證掃描錄一次真 Tesseract,之後重放(#55)。

送審快照讀現成的 matches.csv、十五欄的網(test_sheet_rows)把許可證掃描換成替身 —— 兩張網都守不到
「一份 docx → 它在 matches.csv 的列」。這裡補上那張網:

  錄製  python scan_snapshot.py record
        對 samples/docs/ 每份 docx 跑真的 run_scan(真 Tesseract,約 30~60 分鐘),
        錄下每次 OCR 的輸入識別(圖的 sha1、設定名稱、ROI)與輸出(文字、信心),
        以及當下的 matches.csv 與每個寫出檔(相對路徑 + sha1)作為快照。
        輸出寫在暫存目錄,不碰 scan_results/;錄製結果存 samples/scan_snapshot/。
  重放  test_scan_snapshot.py:不跑 Tesseract,用錄下的 OCR 結果跑 run_scan(幾秒),
        比對 matches.csv 每一格與每個寫出檔。沒錄過的 OCR 呼叫直接失敗、指出是哪一次。

快照是機器輸出,不是真值(CONTEXT「快照」):它只回答「這次改動有沒有讓掃描結果變」。
"""
from __future__ import annotations

import csv
import hashlib
import json
import sys
import tempfile
import time
from functools import partial
from pathlib import Path
from typing import Callable, Iterable

import real_samples

SNAPSHOT_DIR = real_samples.ROOT / "scan_snapshot"
OCR_FILE = SNAPSHOT_DIR / "ocr.json"           # {呼叫識別: [文字, 信心]} 與圖的出處
SNAPSHOT_FILE = SNAPSHOT_DIR / "snapshot.json"  # {matches: [列], files: {相對路徑: sha1}}

Ocr = Callable[[bytes, dict, tuple], tuple[str, float]]


def _sha1(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


def call_key(image_bytes: bytes, cfg: dict, roi: tuple) -> str:
    """一次 OCR 呼叫的識別:哪張圖(內容)× 哪個設定 × 哪個 ROI。"""
    return f"{_sha1(image_bytes)}|{cfg['name']}|{','.join(str(x) for x in roi)}"


class UnrecordedOcrCall(AssertionError):
    """重放時遇到錄製時沒有的 OCR 呼叫:掃描讀了不同的圖、設定或 ROI。"""


class Recorder:
    """包住真的 Ocr,把每次呼叫的結果記下來(同一呼叫只記一次)。"""

    def __init__(self, ocr: Ocr):
        self._ocr = ocr
        self.calls: dict[str, tuple[str, float]] = {}

    def __call__(self, image_bytes: bytes, cfg: dict, roi: tuple) -> tuple[str, float]:
        key = call_key(image_bytes, cfg, roi)
        if key not in self.calls:
            self.calls[key] = self._ocr(image_bytes, cfg, roi)
        return self.calls[key]


class Replayer:
    """只回錄過的結果。沒錄過就拋 UnrecordedOcrCall —— 安靜地回空會讓掃描走上別條路。"""

    def __init__(self, calls: dict[str, tuple[str, float]], sources: dict[str, str]):
        self.calls = calls
        self.sources = sources   # 圖的 sha1 → 「docx/圖檔名」,只給錯誤訊息用

    def __call__(self, image_bytes: bytes, cfg: dict, roi: tuple) -> tuple[str, float]:
        key = call_key(image_bytes, cfg, roi)
        if key not in self.calls:
            where = self.sources.get(_sha1(image_bytes), "(不在錄製的素材裡的圖)")
            raise UnrecordedOcrCall(
                f"沒錄過的 OCR 呼叫:{where} 設定={cfg['name']} ROI={roi}")
        text, conf = self.calls[key]
        return text, conf


def image_sources(docx_files: Iterable[Path]) -> dict[str, str]:
    """每張內嵌圖的 sha1 → 「docx/圖檔名」(同一張圖出現在多份時記第一份)。"""
    import docx_images
    out: dict[str, str] = {}
    for p in docx_files:
        try:
            images = docx_images.read_images(p)
        except Exception:      # 讀不到圖的 docx 沒有 OCR 呼叫
            continue
        for name, data in images:
            out.setdefault(_sha1(data), f"{p.name}/{name}")
    return out


def scan(docx_files: list[Path], out_dir: Path, ocr: Ocr) -> dict:
    """用指定的 Ocr 跑一次正式的許可證掃描,回傳 {matches: [列], files: {相對路徑: sha1}}。

    matches.csv 的截圖欄本來就相對它自己的資料夾(#61),原樣比對即可跨次、跨機器。
    """
    from batch import Batch
    from permit_reading import scan_image_large, scan_image_mol_only
    from permit_scan import run_scan

    batch = Batch(tuple(docx_files), source=out_dir, output_dir=out_dir)
    csv_path = run_scan(
        batch,
        read_large=partial(scan_image_large, ocr=ocr),
        read_small=partial(scan_image_mol_only, ocr=ocr))

    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    files = {p.relative_to(out_dir).as_posix(): _sha1(p.read_bytes())
             for p in sorted(out_dir.rglob("*"))
             if p.is_file() and p != csv_path}
    return {"matches": rows, "files": files}


def differences(saved: dict, actual: dict, *,
                excluded_columns: Iterable[str] = (),
                excluded_dirs: Iterable[str] = ()) -> list[str]:
    """快照與這次的差異(給人看的句子);相同回傳空清單。

    excluded_columns / excluded_dirs:明確排除的欄位、資料夾(刪欄的票用),兩邊都不比。
    """
    cols = set(excluded_columns)
    dirs = tuple(d.rstrip("/") + "/" for d in excluded_dirs)
    out: list[str] = []

    def keep(row: dict) -> dict:
        return {k: v for k, v in row.items() if k not in cols}

    base_rows = [keep(r) for r in saved["matches"]]
    act_rows = [keep(r) for r in actual["matches"]]
    if len(base_rows) != len(act_rows):
        out.append(f"matches.csv 列數:快照 {len(base_rows)}、這次 {len(act_rows)}")
    for i, (b, a) in enumerate(zip(base_rows, act_rows)):
        where = f"第 {i + 1} 列({b.get('source_docx')}/{b.get('image_name')})"
        for k in sorted(set(b) | set(a)):
            if b.get(k) != a.get(k):
                out.append(f"{where} {k}:快照 {b.get(k)!r}、這次 {a.get(k)!r}")

    def files(snap: dict) -> dict[str, str]:
        return {p: h for p, h in snap["files"].items() if not p.startswith(dirs)}

    bf, af = files(saved), files(actual)
    for p in sorted(set(bf) | set(af)):
        if p not in af:
            out.append(f"少寫了 {p}")
        elif p not in bf:
            out.append(f"多寫了 {p}")
        elif bf[p] != af[p]:
            out.append(f"{p} 內容不同")
    return out


def load() -> tuple[Replayer, dict]:
    """錄好的 (重放用 Ocr, 快照)。"""
    rec = json.loads(OCR_FILE.read_text(encoding="utf-8"))
    calls = {k: (v[0], v[1]) for k, v in rec["calls"].items()}
    saved = json.loads(SNAPSHOT_FILE.read_text(encoding="utf-8"))
    return Replayer(calls, rec["sources"]), saved


def is_recorded() -> bool:
    return OCR_FILE.is_file() and SNAPSHOT_FILE.is_file()


def record(docx_files: list[Path]) -> None:
    """真 Tesseract 跑一次掃描,存下每次 OCR 與快照。輸出寫在暫存目錄,事後丟掉。"""
    import pipeline  # noqa: F401  import 時設好 tesseract_cmd(與正式流程同一個)
    from permit_reading import tesseract

    recorder = Recorder(tesseract)
    t0 = time.time()
    with tempfile.TemporaryDirectory(prefix="scan_snapshot_") as tmp:
        snap = scan(docx_files, Path(tmp), recorder)
    elapsed = round(time.time() - t0)

    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    OCR_FILE.write_text(json.dumps(
        {"sources": image_sources(docx_files),
         "calls": {k: list(v) for k, v in recorder.calls.items()}},
        ensure_ascii=False), encoding="utf-8")
    SNAPSHOT_FILE.write_text(json.dumps(
        {"docx": [p.name for p in docx_files], "seconds": elapsed, **snap},
        ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"錄好 {len(docx_files)} 份、{len(recorder.calls)} 次 OCR、"
          f"{len(snap['matches'])} 列、{len(snap['files'])} 個寫出檔;耗時 {elapsed} 秒")


if __name__ == "__main__":
    if sys.argv[1:] != ["record"]:
        sys.exit("用法:python scan_snapshot.py record")
    docs = real_samples.docx_samples()
    if not docs:
        sys.exit("沒有真實素材 samples/docs/*.docx")
    if is_recorded():
        sys.exit(f"{SNAPSHOT_DIR} 已有錄製;要重錄先自己把它移走")
    record(docs)
