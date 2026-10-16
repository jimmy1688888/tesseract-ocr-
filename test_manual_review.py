# -*- coding: utf-8 -*-
"""manual_review 是旗標,「為什麼要人工」寫在 note(#15)。

以前它是 str,值域混著 "Y" 與「mol 無值，需人工判斷」這種整句描述 —— 旗標和
理由擠在同一格。現在 manual_review 只回答「要不要人工」,理由在 note。

舊的 matches.csv 兩種值都有,讀回時一律要是 True:旗標從 True 安靜變成 False,
那份 docx 就悄悄脫離人工審查,不拋錯、不少欄。
"""
import csv
from pathlib import Path

import pytest

import pipeline as P
from batch import Batch


SCAN_RESULTS = Path(__file__).resolve().parent / "scan_results"
BACKUP_CSV = SCAN_RESULTS / "matches.21份.backup.csv"


class TestFlagIsSetWithReasonInNote:

    def test_small_image_without_mol(self, monkeypatch, tmp_path):
        monkeypatch.setattr(P, "_collect_mol_votes", lambda data: [])
        monkeypatch.setattr(P, "preprocess", lambda data, cfg: None)
        monkeypatch.setattr(P, "ocr_with_conf", lambda img, lang, cfg: ("", 0.0))

        r = P.scan_image_mol_only("a.docx", "image1.png", b"", tmp_path)

        assert r.manual_review is True
        assert "mol 無值" in r.note

    def test_large_docx_without_any_hit(self, monkeypatch, tmp_path):
        monkeypatch.setattr(P.docx_images, "read_images",
                            lambda p: [(f"image{n}.jpeg", b"") for n in range(1, 8)])
        monkeypatch.setattr(P, "scan_image_large", lambda *a, **k: None)
        monkeypatch.setattr(P, "decide_result", lambda r: r)

        P.run_scan(Batch((tmp_path / "a.docx",), source=tmp_path, output_dir=tmp_path))

        with open(tmp_path / "matches.csv", encoding="utf-8-sig") as f:
            [row] = list(csv.DictReader(f))
        r = P.ScanResult.from_csv_row(row)
        assert r.manual_review is True
        assert r.note == "large:全無命中"


@pytest.mark.parametrize("on_disk, expected", [
    ("Y", True),
    ("mol 無值，需人工判斷", True),     # #15 之前 small 讀不到 mol 時寫的整句
    ("", False),
])
def test_old_csv_values_read_back(on_disk, expected):
    r = P.ScanResult.from_csv_row({"source_docx": "a.docx", "manual_review": on_disk})
    assert r.manual_review is expected


def test_real_old_matches_csv(tmp_path):
    """真實的舊 matches.csv:需要人工的列一列都不能變 False;再寫出、讀回逐欄比對。

    素材唯讀(只開來讀),輸出只寫 tmp。不在就 skip。
    """
    if not BACKUP_CSV.exists():
        pytest.skip(f"沒有 {BACKUP_CSV.name}")
    with open(BACKUP_CSV, encoding="utf-8-sig") as f:
        raw = list(csv.DictReader(f))
    assert any(row["manual_review"] for row in raw), "素材裡沒有需要人工的列,驗不到東西"

    results = [P.ScanResult.from_csv_row(row) for row in raw]
    for row, r in zip(raw, results):
        assert r.manual_review is bool(row["manual_review"]), row["source_docx"]

    out = tmp_path / "matches.csv"
    with open(out, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=P.CSV_FIELDS)
        writer.writeheader()
        for r in results:
            writer.writerow(r.to_csv_row())
    with open(out, encoding="utf-8-sig") as f:
        rewritten = list(csv.DictReader(f))

    assert [P.ScanResult.from_csv_row(row) for row in rewritten] == results
    for old, new in zip(raw, rewritten):
        for col in old:
            if col == "manual_review":
                assert bool(new[col]) is bool(old[col]), old["source_docx"]
            else:
                assert new[col] == old[col], (old["source_docx"], col)
