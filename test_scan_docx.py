# -*- coding: utf-8 -*-
"""一份 docx 的圖 → 它在 matches.csv 的列(permit_reading.scan_docx,#57)。

寫列的規則只在這個純函式:讀不到圖的佔位列、原始張數判 small/large、large 的分層
與全無命中佔位列、small 只寫命中列/全無命中每列標 small_no_hit、要存哪幾張原圖。
讀圖函式由參數傳入,不跑 OCR、不碰硬碟,也不必寫 CSV 再讀回。
"""
import pytest

import permit_reading as R
from matches_csv import ResultStatus, ScanResult


def _img(n: int) -> tuple[str, bytes]:
    return (f"image{n}.jpeg", f"bytes{n}".encode())


def _imgs(*ns):
    return [_img(n) for n in ns]


class Reader:
    """假讀圖函式:依圖檔名查表回判讀,記下被讀過的圖。查不到 = 沒命中。"""

    def __init__(self, docx_class, hits=None):
        self.docx_class = docx_class
        self.hits = hits or {}
        self.seen: list[str] = []

    def __call__(self, name, data):
        self.seen.append(name)
        r = ScanResult(source_docx="a.docx", image_name=name, docx_class=self.docx_class)
        if name in self.hits:
            for k, v in self.hits[name].items():
                setattr(r, k, v)
            return R.Reading(r, [])
        return R.Reading(r, []) if self.docx_class == "small" else None


VOTE = {"id": "1234", "id_conf": 70.0, "id_from_vote": True}


def _scan(images, large=None, small=None, **kw):
    return R.scan_docx("a.docx", images, large or Reader("large"), small or Reader("small"), **kw)


def _rows(scan):
    return [(r.result.image_name, r.result.status, r.result.scan_tier) for r in scan.rows]


ALL_CASES = [
    pytest.param(None, id="打不開"),
    pytest.param([], id="沒有圖"),
    pytest.param(_imgs(1, 2), id="small 無命中"),
    pytest.param(_imgs(1, 2, 3, 4, 5, 6, 7), id="large 無命中"),
]


@pytest.mark.parametrize("images", ALL_CASES)
def test_every_docx_gets_at_least_one_row(images):
    assert len(_scan(images).rows) >= 1


class TestUnscannable:

    @pytest.mark.parametrize("images, status, note", [
        (None, ResultStatus.UNREADABLE, "docx 無法開啟"),
        ([], ResultStatus.NO_IMAGES, "docx 沒有可讀的內嵌圖"),
    ])
    def test_one_placeholder_row(self, images, status, note):
        large, small = Reader("large"), Reader("small")
        scan = _scan(images, large, small)

        [row] = [r.result for r in scan.rows]
        assert (row.source_docx, row.image_name, row.docx_class) == ("a.docx", "", "")
        assert (row.status, row.note, row.manual_review) == (status, note, True)
        assert (scan.docx_class, scan.review_images, scan.scanned) == ("", frozenset(), 0)
        assert large.seen == small.seen == []


class TestClassByOriginalCount:
    """small/large 用原始張數,在 --image 過濾之前算(ADR-0005)。"""

    def test_three_images_is_small(self):
        assert _scan(_imgs(1, 2, 3)).docx_class == "small"

    def test_four_images_is_large(self):
        assert _scan(_imgs(1, 2, 3, 4)).docx_class == "large"

    def test_image_filter_does_not_turn_large_into_small(self):
        large, small = Reader("large"), Reader("small")
        scan = _scan(_imgs(1, 2, 3, 4, 5, 6, 7), large, small, image_filter="image5.jpeg")
        assert scan.docx_class == "large"
        assert (large.seen, small.seen) == (["image5.jpeg"], [])

    def test_image_filter_miss_raises(self):
        """對不到要由 main 在掃描前擋下;走到這裡是不該發生的事,不能安靜略過。"""
        with pytest.raises(ValueError, match=r"image9\.jpeg.*image1\.jpeg"):
            _scan(_imgs(1, 2, 3, 4), image_filter="image9.jpeg")


class TestLarge:

    def test_hits_are_the_rows(self):
        scan = _scan(_imgs(1, 2, 3, 4, 5, 6, 7), large=Reader("large", {"image5.jpeg": VOTE}))
        assert _rows(scan) == [("image5.jpeg", ResultStatus.OK, 1)]
        assert (scan.tier, scan.review_images, scan.scanned) == (
            R.TierOutcome.第一層命中, frozenset(), 3)

    def test_no_hit_after_expanding_is_marked_second_tier(self):
        scan = _scan(_imgs(1, 2, 3, 4, 5, 6, 7))
        assert _rows(scan) == [("image1.jpeg", ResultStatus.LARGE_NO_HIT, 2)]
        [row] = [r.result for r in scan.rows]
        assert (row.note, row.manual_review, row.docx_class) == ("large:全無命中", True, "large")
        assert scan.tier is R.TierOutcome.擴掃
        assert scan.review_images == {2, 5, 6}

    def test_no_hit_with_nothing_to_expand_stays_first_tier(self):
        scan = _scan(_imgs(1, 2, 3, 4, 5, 6, 7), full_scan=True)
        assert _rows(scan) == [("image1.jpeg", ResultStatus.LARGE_NO_HIT, 1)]
        assert scan.tier is R.TierOutcome.無可擴

    def test_placeholder_names_the_filtered_image(self):
        scan = _scan(_imgs(1, 2, 3, 4, 5, 6, 7), image_filter="image6.jpeg")
        assert _rows(scan) == [("image6.jpeg", ResultStatus.LARGE_NO_HIT, 1)]


class TestSmall:

    def test_only_hit_rows_are_written(self):
        small = Reader("small", {"image2.jpeg": {"mol": "1234", "mol_conf": 60.0}})
        scan = _scan(_imgs(1, 2, 3), small=small)
        assert _rows(scan) == [("image2.jpeg", ResultStatus.OK, 1)]
        assert (scan.review_images, scan.tier, scan.scanned) == (frozenset(), None, 3)

    def test_all_no_hit_writes_every_row_as_small_no_hit(self):
        scan = _scan(_imgs(1, 2, 3))
        assert _rows(scan) == [(f"image{i}.jpeg", ResultStatus.SMALL_NO_HIT, 1) for i in (1, 2, 3)]
        assert {r.result.note for r in scan.rows} == {"small:無命中"}
        assert scan.review_images == {2, 3}

    def test_small_is_not_tiered(self):
        small = Reader("small")
        _scan(_imgs(1), small=small)
        assert small.seen == ["image1.jpeg"]
