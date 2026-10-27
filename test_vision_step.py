# -*- coding: utf-8 -*-
"""送 Vision 那一步:每一筆都要有去處,不能安靜消失(#39)。

該送 Vision 的 docx 若截圖檔不在,舊做法 `continue` —— 不進 key-in、不進人工審查,
整份 docx 從 Sheets 消失,只在 log 留一行。改成進人工審查:B 欄填 Tesseract 候選值,
D 欄只陳述事實,比照 Vision 沒讀到值(FORMAT_INVALID)的既有做法。
不改走反查救援:手上已有候選值,缺的是第二個證據。
資料全部捏造,Vision 以假的讀取函式代替(不打 API)。
"""
import pytest

from sheet_row import SheetStatus
from submission_decision import Decision, Destination
from vision_verification import run_vision_items


def _item(img_path, **kw):
    kw.setdefault("candidate_value", "1234")
    kw.setdefault("candidate_conf", 60.0)
    return Decision(source_docx="a.docx", destination=Destination.送VISION,
                    image_name="image5.jpeg", img_path=img_path,
                    reason="permit部分命中無多數", hit_roi="permit_lower", **kw)


@pytest.mark.parametrize("img_path", ["", "MISSING"])
def test_missing_crop_goes_to_manual_review_not_nowhere(tmp_path, img_path):
    path = str(tmp_path / "nope.png") if img_path == "MISSING" else ""
    calls = []
    auto, review = run_vision_items(
        [_item(path)],
        read_text=lambda p: calls.append(p) or "No. Izin: 1234")
    assert calls == []                                   # 沒圖就不打 Vision
    assert auto == []
    assert len(review) == 1
    row = review[0]
    assert (row.source_docx, row.permit, row.status) == ("a.docx", "1234", SheetStatus.MANUAL_REVIEW)
    assert "截圖檔不在,未送 Vision" in row.reason
    assert "permit部分命中無多數" in row.reason           # 送 Vision 的原因照樣留著


def test_existing_crop_still_goes_through_vision(tmp_path):
    crop = tmp_path / "crop.png"
    crop.write_bytes(b"x")
    auto, review = run_vision_items(
        [_item(str(crop))], read_text=lambda p: "No. Izin: 1234")
    assert [(r.permit, r.status) for r in auto] == [("1234", SheetStatus.KEYED_IN)]
    assert review == []
