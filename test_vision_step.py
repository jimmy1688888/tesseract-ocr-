# -*- coding: utf-8 -*-
"""送 Vision 那一步:每一筆都要有去處,不能安靜消失(#39)。

該送 Vision 的 docx 若截圖檔不在,舊做法 `continue` —— 不進 key-in、不進人工審查,
整份 docx 從 Sheets 消失,只在 log 留一行。改成進人工審查:B 欄填 Tesseract 候選值,
D 欄只陳述事實,比照 Vision 沒讀到值(VISION_NO_HIT)的既有做法。
不改走反查救援:手上已有候選值,缺的是第二個證據。
資料全部捏造,Vision 以假的讀取函式代替(不打 API)。
"""
from dataclasses import replace as _replace

import pytest

from submission_decision import Decision, Destination
from conftest import FakeVision
from vision_reader import VisionCallFailed
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
    vision = FakeVision(sparse=lambda b: "No. Izin: 1234")
    [check] = run_vision_items([_item(path)], vision)
    assert vision.sparse_reads == []                     # 沒圖就不打 Vision
    assert (check.decision.source_docx, check.value, check.keyin) == ("a.docx", "1234", False)
    assert (check.finding, check.verified) == ("截圖檔不在,未送 Vision", False)


def test_existing_crop_still_goes_through_vision(tmp_path):
    crop = tmp_path / "crop.png"
    crop.write_bytes(b"x")
    [check] = run_vision_items(
        [_item(str(crop))], FakeVision(sparse=lambda b: "No. Izin: 1234"))
    assert (check.value, check.keyin, check.verified) == ("1234", True, True)


class TestVisionCallFailed:
    """Vision 呼叫失敗只影響那一份(ADR-0015、#65):進人工審查、D 欄說是沒問到,
    其他份照常驗證;以前 503 重試用盡會整批中止,API 回報錯誤則被判成「沒取到號」。"""

    def _crop(self, tmp_path, name):
        crop = tmp_path / f"{name}.png"
        crop.write_bytes(name.encode())
        return str(crop)

    def test_failed_one_goes_to_manual_review_with_the_candidate(self, tmp_path):
        def read(image):
            raise VisionCallFailed("503")
        [check] = run_vision_items([_item(self._crop(tmp_path, "a"))],
                                   FakeVision(sparse=read))
        assert (check.value, check.keyin) == ("1234", False)
        assert (check.finding, check.verified) == ("Vision 呼叫失敗,未驗證", False)

    def test_others_are_still_verified(self, tmp_path):
        def read(image):
            if image == b"bad":
                raise VisionCallFailed("503")
            return "No. Izin: 1234"
        items = [_item(self._crop(tmp_path, "bad")),
                 _replace(_item(self._crop(tmp_path, "ok")), source_docx="b.docx")]
        checks = run_vision_items(items, FakeVision(sparse=read))
        assert [(c.decision.source_docx, c.keyin) for c in checks] == [("a.docx", False),
                                                                     ("b.docx", True)]

    def test_identity_refused_still_aborts(self, tmp_path):
        from google.api_core.exceptions import PermissionDenied

        def read(image):
            raise PermissionDenied("403")
        with pytest.raises(PermissionDenied):
            run_vision_items([_item(self._crop(tmp_path, "a"))], FakeVision(sparse=read))
