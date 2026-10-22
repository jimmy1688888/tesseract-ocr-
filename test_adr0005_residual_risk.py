# -*- coding: utf-8 -*-
"""ADR-0005「已知殘餘風險(明示接受)」:分層之後,規則 A 的 mol ∩ permit 交集
只由已經掃過的圖組成(#20)。

ADR 的例子:mol@image2 = 1234,image4 也印著 1234,image5 多數票讀成 1284。

    全掃  permit 值 = {1234, 1284} → 交集 {1234} → cross_match,採 1234
    分層  permit 值 = {1284}       → 交集空 → 衝突路徑,permit 優先,採 1284

這是分層唯一可能讓**最終值不同**的路徑;ADR 接受它,理由是發生時 D 欄寫
「mol≠permit 衝突」並送 Vision,不是黑箱。要消除它得把「mol 與 permit 多數票
對不上」也列為擴掃條件 —— 討論過,決定不做。這組測試釘住的就是 ADR 寫下的
這段分析:哪天有人改了擴掃條件或規則 A,這裡會先告訴他。

走的是真的分層掃描(read_large_docx)與真的送審決策(decide),只有讀圖是假的。
"""
import permit_reading as R
from permit_reading import ScanResult
from submission_decision import Destination, decide

IMAGES = [(f"image{n}.jpeg", b"") for n in range(1, 8)]


def _mol(name, value, conf=70.0):
    return ScanResult("a.docx", name, "large", mol=value, mol_conf=conf,
                      mol_from_vote=True, hit_roi="mol", mol_crop="m.png")


def _permit_vote(name, value, conf=70.0):
    return ScanResult("a.docx", name, "large", id=value, id_conf=conf,
                      id_from_vote=True, hit_roi="permit_lower", permit_crop="p.png")


def _partial(name, value, conf=40.0):
    return ScanResult("a.docx", name, "large", id=value, id_conf=conf,
                      status=R.ResultStatus.PERMIT_PARTIAL, hit_roi="permit_lower",
                      permit_crop="p.png")


def _decide(hits: dict[str, ScanResult], full_scan=False):
    """這份 docx 每張圖讀到什麼(hits;其餘全無命中)→ (送審決策, 掃過的圖)。"""
    seen = []

    def read(name, data):
        seen.append(name)
        return R.Reading(hits[name], []) if name in hits else None

    scan = R.read_large_docx("a.docx", IMAGES, read, full_scan=full_scan)
    return decide([r.result for r in scan.readings]), seen


class TestIntersectionOnlyFromFirstTier:
    """ADR-0005 明示接受的風險:第一層就有 permit 多數票,不擴掃,交集少了 image4。"""

    HITS = {
        "image2.jpeg": _mol("image2.jpeg", "1234"),
        "image4.jpeg": _permit_vote("image4.jpeg", "1234", conf=65.0),
        "image5.jpeg": _permit_vote("image5.jpeg", "1284"),
    }

    def test_tiered_takes_the_conflict_path(self):
        d, seen = _decide(self.HITS)

        assert "image4.jpeg" not in seen            # image5 的多數票停止了擴掃
        assert d.destination is Destination.送VISION
        assert (d.candidate_value, d.image_name) == ("1284", "image5.jpeg")
        assert d.reason.startswith("mol≠permit衝突")   # D 欄看得出來,不是黑箱

    def test_full_scan_cross_matches_on_image4(self):
        d, seen = _decide(self.HITS, full_scan=True)

        assert "image4.jpeg" in seen
        assert d.destination is Destination.直接KEYIN
        assert d.candidate_value == "1234"
        assert d.reason.startswith("cross_match(file)_高信心")

    def test_this_is_the_only_difference(self):
        """同一份輸入,分層與全掃只差在最終值與去向 —— ADR 說的「最終值不同」。"""
        tiered, _ = _decide(self.HITS)
        full, _ = _decide(self.HITS, full_scan=True)
        assert tiered.candidate_value != full.candidate_value


class TestIntersectionIncludesSecondTier:
    """第一層沒有可信的 permit 多數票 → 擴掃,交集納入第二層的圖,與全掃同值。"""

    HITS = {
        "image2.jpeg": _mol("image2.jpeg", "1234"),
        "image4.jpeg": _permit_vote("image4.jpeg", "1234", conf=65.0),
        "image5.jpeg": _partial("image5.jpeg", "1284"),     # 湊不出多數,不停止擴掃
    }

    def test_tiered_expands_and_cross_matches(self):
        d, seen = _decide(self.HITS)

        assert "image4.jpeg" in seen
        assert d.destination is Destination.直接KEYIN
        assert d.candidate_value == "1234"
        assert d.reason.startswith("cross_match(file)_高信心")

    def test_same_as_full_scan(self):
        tiered, _ = _decide(self.HITS)
        full, _ = _decide(self.HITS, full_scan=True)
        assert (tiered.destination, tiered.candidate_value, tiered.image_name) == (
            full.destination, full.candidate_value, full.image_name)
