# -*- coding: utf-8 -*-
"""manual_review 是旗標,「為什麼要人工」寫在 note(#15)。

以前它是 str,值域混著 "Y" 與「mol 無值，需人工判斷」這種整句描述 —— 旗標和
理由擠在同一格。現在 manual_review 只回答「要不要人工」,理由在 note。

舊的 matches.csv 兩種值都有,讀回時一律要是 True:旗標從 True 安靜變成 False,
那份 docx 就悄悄脫離人工審查,不拋錯、不少欄。
"""
import csv
from functools import partial

import pytest

import matches_csv
import pipeline as P
from batch import Batch
from real_samples import old_matches_csv


def _csv_rows(path):
    with open(path, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def _no_ocr_hits(image_bytes, cfg, roi):
    """small 的 mol 掃描每一組設定都讀不到東西(注入的假 OCR)。"""
    return "", 0.0


class TestFlagIsSetWithReasonInNote:

    def test_small_image_without_mol(self):
        r, _ = P.scan_image_mol_only("a.docx", "image1.png", b"", ocr=_no_ocr_hits)

        assert r.manual_review is True
        assert "mol 無值" in r.note

    def test_small_docx_without_any_hit_reaches_csv(self, monkeypatch, tmp_path):
        """寫到 CSV 時理由是整份的「small:無命中」(每張都讀不到 mol 的總結),旗標保留。"""
        monkeypatch.setattr(P.docx_images, "read_images",
                            lambda p: [("image1.png", b""), ("image2.png", b"")])

        P.run_scan(Batch((tmp_path / "a.docx",), source=tmp_path, output_dir=tmp_path),
                   read_small=partial(P.scan_image_mol_only, ocr=_no_ocr_hits))

        rows = _csv_rows(tmp_path / "matches.csv")
        assert len(rows) == 2
        for r in map(matches_csv.ScanResult.from_csv_row, rows):
            assert r.manual_review is True
            assert r.note == "small:無命中"
            assert r.status == matches_csv.ResultStatus.SMALL_NO_HIT

    def test_large_docx_without_any_hit(self, monkeypatch, tmp_path):
        monkeypatch.setattr(P.docx_images, "read_images",
                            lambda p: [(f"image{n}.jpeg", b"") for n in range(1, 8)])

        P.run_scan(Batch((tmp_path / "a.docx",), source=tmp_path, output_dir=tmp_path),
                   read_large=lambda *a, **k: None)

        [row] = _csv_rows(tmp_path / "matches.csv")
        r = matches_csv.ScanResult.from_csv_row(row)
        assert r.manual_review is True
        assert r.note == "large:全無命中"


@pytest.mark.parametrize("on_disk, expected", [
    ("Y", True),
    ("mol 無值，需人工判斷", True),     # #15 之前 small 讀不到 mol 時寫的整句
    ("", False),
])
def test_old_csv_values_read_back(on_disk, expected):
    r = matches_csv.ScanResult.from_csv_row({"source_docx": "a.docx", "manual_review": on_disk})
    assert r.manual_review is expected


def test_real_old_matches_csv(tmp_path):
    """真實的舊 matches.csv:需要人工的列一列都不能變 False;再寫出、讀回逐欄比對。

    素材唯讀(只開來讀),輸出只寫 tmp。不在就 skip。現有備份只有 "Y",整句描述
    那種值目前只有上面的參數化案例在驗 —— 使用者給了含整句的舊檔再換上。
    """
    raw = _csv_rows(old_matches_csv())
    needs_review = lambda v: bool(v.strip())
    assert any(needs_review(row["manual_review"]) for row in raw), "素材裡沒有需要人工的列,驗不到東西"

    results = [matches_csv.ScanResult.from_csv_row(row) for row in raw]
    for row, r in zip(raw, results):
        assert r.manual_review is needs_review(row["manual_review"]), row["source_docx"]

    out = tmp_path / "matches.csv"
    matches_csv.write(out, results)
    rewritten = _csv_rows(out)

    assert [matches_csv.ScanResult.from_csv_row(row) for row in rewritten] == results
    for old, new in zip(raw, rewritten):
        for col in old:
            if col in ("final_value", "final_conf", "vision_review"):
                assert col not in new          # 判斷欄,ADR-0013 起不寫出
            elif col == "manual_review":
                assert needs_review(new[col]) is needs_review(old[col]), old["source_docx"]
            else:
                assert new[col] == old[col], (old["source_docx"], col)
