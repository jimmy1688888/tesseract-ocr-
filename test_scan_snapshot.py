# -*- coding: utf-8 -*-
"""掃描快照(#55):一份 docx → 它在 matches.csv 的列與寫出的檔。

重放錄好的 OCR 結果跑 run_scan(不跑 Tesseract,幾秒),matches.csv 逐格、寫出檔逐一
與錄製當下相同。錄製方式見 scan_snapshot.py;素材或錄製不在就 skip。
"""
import pytest

import scan_snapshot as S
import real_samples as rs

# 明確排除的欄位/資料夾:刪欄、不再寫出某類檔的票才加,並寫明是哪張票。
EXCLUDED_COLUMNS: tuple[str, ...] = ("manual_review", "low_conf")   # #58 刪欄
EXCLUDED_DIRS: tuple[str, ...] = ("low_conf_crops",)                # #58 不再寫低信心截圖


@pytest.fixture(scope="module")
def recorded():
    if not rs.REAL_DOCX:
        pytest.skip("沒有真實素材 samples/docs/*.docx")
    if not S.is_recorded():
        pytest.skip("沒有掃描快照錄製:python scan_snapshot.py record"
                    "(真 Tesseract,約 30~60 分鐘,存 samples/scan_snapshot/)")
    return S.load()


def test_replay_matches_snapshot(recorded, tmp_path):
    replayer, saved = recorded
    assert [p.name for p in rs.REAL_DOCX] == saved["docx"], "素材和錄製時不同,要重錄"

    actual = S.scan(rs.REAL_DOCX, tmp_path, replayer)

    assert S.differences(saved, actual, excluded_columns=EXCLUDED_COLUMNS,
                         excluded_dirs=EXCLUDED_DIRS) == []


# ─── 不需要素材:重放與比對本身的行為 ─────────────────────────────────────

CFG = {"name": "紅通道_2x_中值3"}
ROI = (0.05, 0.04, 0.4, 0.25)


class TestReplayer:

    def test_recorded_call_replays(self):
        calls = {S.call_key(b"img", CFG, ROI): ("MOL 1234", 80.0)}
        assert S.Replayer(calls, {})(b"img", CFG, ROI) == ("MOL 1234", 80.0)

    def test_unrecorded_call_fails_naming_the_image(self):
        sources = {S._sha1(b"img"): "30766.docx/image2.jpeg"}
        replayer = S.Replayer({}, sources)
        with pytest.raises(S.UnrecordedOcrCall, match=r"30766\.docx/image2\.jpeg.*紅通道_2x_中值3"):
            replayer(b"img", CFG, ROI)

    def test_recorder_calls_through_once_per_call(self):
        seen = []

        def ocr(data, cfg, roi):
            seen.append(data)
            return "x", 1.0

        rec = S.Recorder(ocr)
        rec(b"a", CFG, ROI)
        rec(b"a", CFG, ROI)
        assert seen == [b"a"] and S.Replayer(rec.calls, {})(b"a", CFG, ROI) == ("x", 1.0)


def _snap(rows, files):
    return {"matches": rows, "files": files}


class TestDifferences:

    def test_same_is_empty(self):
        s = _snap([{"source_docx": "a.docx", "id": "1"}], {"permit_crops/a.png": "h"})
        assert S.differences(s, s) == []

    def test_cell_difference_names_row_and_column(self):
        base = _snap([{"source_docx": "a.docx", "image_name": "image5.jpeg", "scan_tier": "2"}], {})
        act = _snap([{"source_docx": "a.docx", "image_name": "image5.jpeg", "scan_tier": "1"}], {})
        assert S.differences(base, act) == [
            "第 1 列(a.docx/image5.jpeg) scan_tier:快照 '2'、這次 '1'"]

    def test_row_count_and_files(self):
        base = _snap([{"id": "1"}, {"id": "2"}], {"x/a.png": "h1", "x/b.png": "h2"})
        act = _snap([{"id": "1"}], {"x/a.png": "zz", "y/c.png": "h3"})
        assert S.differences(base, act) == [
            "matches.csv 列數:快照 2、這次 1",
            "x/a.png 內容不同", "少寫了 x/b.png", "多寫了 y/c.png"]

    def test_excluded_columns_and_dirs_are_not_compared(self):
        base = _snap([{"id": "1", "low_conf": "p"}], {"low_conf_crops/a.png": "h"})
        act = _snap([{"id": "1"}], {})
        assert S.differences(base, act, excluded_columns=["low_conf"],
                             excluded_dirs=["low_conf_crops"]) == []
        assert S.differences(base, act) != []
