# -*- coding: utf-8 -*-
"""許可證判讀:image bytes → 一次判讀,不碰硬碟、不碰網路、不改全域(#19)。

OCR 由參數注入,所以這裡全部不跑 Tesseract、不需要真的影像,也不 monkeypatch
任何模組屬性。以前 scan_image_large(146 行)與分層掃描的控制流零測試,只能
在 run_scan 外面把四個模組屬性換掉才碰得到。
"""
import permit_reading as R
from matches_csv import ResultStatus, Roi, ScanResult
from permit_reading import PERMIT_VOTE_CONFIGS, ROI_REGIONS
from submission_decision import CONF_VOTE_MIN

ROI_NAME = {v: k for k, v in ROI_REGIONS.items()}
FIRST_HIT = R.SCAN_CONFIGS + R.FALLBACK_CONFIGS


class FakeOcr(dict):
    """(ROI 名稱, 設定名稱) → (文字, 信心);查不到就是什麼都沒讀到。記下讀過哪些 ROI。"""

    def __init__(self):
        super().__init__()
        self.read_rois: set[str] = set()

    def __call__(self, image_bytes, cfg, roi):
        self.read_rois.add(ROI_NAME[roi])
        return self.get((ROI_NAME[roi], cfg["name"]), ("", 0.0))

    def every(self, roi, text, conf, configs=FIRST_HIT):
        for cfg in configs:
            self[(roi, cfg["name"])] = (text, conf)
        return self


def _large(ocr, roi_filter=""):
    return R.scan_image_large("a.docx", "image5.jpeg", b"", roi_filter=roi_filter, ocr=ocr)


class TestScanImageLarge:

    def test_nothing_read_is_none(self):
        assert _large(FakeOcr()) is None

    def test_permit_vote_without_mol(self):
        """mol 沒讀到:permit 上下兩半各跑多數票那 6 組設定,多數票取代單一判讀。"""
        ocr = FakeOcr().every("permit_upper", "No. Izin: 1234", 70.0)
        ocr[("permit_lower", PERMIT_VOTE_CONFIGS[0]["name"])] = ("No. Izin 1234", 40.0)

        r, _ = _large(ocr)

        assert (r.id, r.id_from_vote, r.id_layer) == ("1234", True, 0)
        assert r.id_conf == round((70.0 * 6 + 40.0) / 7, 1)
        assert not r.cross_match

    def test_mol_and_permit_agree_is_cross_match(self):
        ocr = (FakeOcr().every("mol", "MOL 1234", 70.0, PERMIT_VOTE_CONFIGS)
               .every("permit_upper", "No. Izin: 1234", 60.0))

        r, _ = _large(ocr)

        assert r.cross_match
        assert (r.mol, r.mol_from_vote, r.id, r.id_from_vote) == ("1234", True, "1234", False)

    def test_mol_and_permit_disagree(self):
        """兩個都讀到就不跑 permit 多數票:衝突留給送審決策(mol≠permit 衝突)。"""
        ocr = (FakeOcr().every("mol", "MOL 1234", 70.0, PERMIT_VOTE_CONFIGS)
               .every("permit_upper", "No. Izin: 1284", 60.0))

        r, _ = _large(ocr)

        assert not r.cross_match
        assert (r.mol, r.id) == ("1234", "1284")

    def test_no_majority_is_permit_partial_with_the_most_confident_read(self):
        ocr = FakeOcr()
        for i, cfg in enumerate(FIRST_HIT):
            ocr[("permit_upper", cfg["name"])] = (f"No. Izin: 12{i:02d}", 40.0 + i)

        r, _ = _large(ocr)

        assert r.status is ResultStatus.PERMIT_PARTIAL
        # 候選值是多數票那 6 組設定裡信心最高的(不是 first-hit 讀到的第一個)
        i = max(FIRST_HIT.index(c) for c in PERMIT_VOTE_CONFIGS)
        assert i != 0
        assert (r.id, r.id_conf) == (f"12{i:02d}", 40.0 + i)
        assert not r.id_from_vote

    def test_mol_vote_alone(self):
        r, crops = _large(FakeOcr().every("mol", "MOL 1234", 70.0, PERMIT_VOTE_CONFIGS))
        assert (r.mol, r.mol_conf, r.mol_from_vote, r.id) == ("1234", 70.0, True, "")
        assert [c.path for c in crops] == [r.mol_crop] and r.permit_crop == ""

    def test_roi_filter_reads_only_that_roi_for_the_first_hit(self):
        ocr = FakeOcr().every("mol", "MOL 1234", 70.0, PERMIT_VOTE_CONFIGS)
        _large(ocr, roi_filter="mol")
        # permit 只在多數票那一步才讀(mol 已有值、permit 沒有)
        assert ocr.read_rois == {"mol", "permit_upper", "permit_lower"}
        ocr = FakeOcr().every("permit_upper", "No. Izin: 1234", 70.0)
        r, _ = _large(ocr, roi_filter="permit_upper")
        assert "mol" not in ocr.read_rois
        assert r.id == "1234"

    def test_roi_filter_mol_still_cross_checks_against_the_permit_vote(self):
        """--roi mol 時 permit 不跑 first-hit,但缺欄的多數票照跑,與 mol 相同就是吻合。"""
        ocr = (FakeOcr().every("mol", "MOL 1234", 70.0, PERMIT_VOTE_CONFIGS)
               .every("permit_upper", "No. Izin: 1234", 60.0))

        r, _ = _large(ocr, roi_filter="mol")

        assert r.cross_match


NON_VOTE = [c for c in FIRST_HIT if c not in PERMIT_VOTE_CONFIGS]


class TestFirstHitPerField:
    """first-hit 每欄只收第一個命中:之後的 ROI、之後的設定都不再改那一欄。

    兩欄都讀到時不跑 permit 多數票,所以這裡讀到的就是最後寫進列的值。
    """

    @staticmethod
    def _mol_vote():
        return FakeOcr().every("mol", "MOL 1234", 70.0, PERMIT_VOTE_CONFIGS)

    def test_upper_hit_skips_the_lower_half(self):
        ocr = (self._mol_vote().every("permit_upper", "No. Izin: 1234", 60.0)
               .every("permit_lower", "No. Izin 5678", 90.0))

        r, crops = _large(ocr)

        assert r.id == "1234"
        assert "permit_lower" not in ocr.read_rois
        assert [c.path for c in crops] == [r.mol_crop, r.permit_crop]
        assert ROI_NAME[crops[1].roi] == "permit_upper"

    def test_hit_config_and_roi_are_the_first_field_hit(self):
        ocr = FakeOcr()
        ocr[("mol", NON_VOTE[0]["name"])] = ("MOL 1234", 55.0)
        ocr[("permit_upper", NON_VOTE[1]["name"])] = ("No. Izin: 1284", 65.0)

        r, _ = _large(ocr)

        assert (r.hit_config, r.hit_roi) == (NON_VOTE[0]["name"], Roi.MOL)
        assert (r.mol, r.mol_conf, r.mol_from_vote) == ("1234", 55.0, False)
        assert (r.id, r.id_conf, r.id_from_vote) == ("1284", 65.0, False)

    def test_first_config_to_hit_sets_the_confidence(self):
        ocr = self._mol_vote()
        first, *later = FIRST_HIT
        ocr[("permit_upper", first["name"])] = ("No. Izin: 1234", 50.0)
        for cfg in later:
            ocr[("permit_upper", cfg["name"])] = ("No. Izin: 9999", 90.0)

        r, _ = _large(ocr)

        assert (r.id, r.id_conf) == ("1234", 50.0)


class TestScanImageMolOnly:

    def test_vote_beats_first_hit(self):
        ocr = FakeOcr().every("mol", "MOL 1234", 60.0)
        r, _ = R.scan_image_mol_only("a.docx", "image2.png", b"", ocr=ocr)
        assert (r.mol, r.mol_from_vote, r.hit_config) == ("1234", True, "")

    def test_first_hit_records_the_config(self):
        ocr = FakeOcr()
        cfg = R.FALLBACK_CONFIGS[1]
        ocr[("mol", cfg["name"])] = ("MOL 1234", 80.0)
        r, _ = R.scan_image_mol_only("a.docx", "image2.png", b"", ocr=ocr)
        assert (r.mol, r.mol_from_vote, r.hit_config, r.mol_conf) == (
            "1234", False, cfg["name"], 80.0)
        assert ocr.read_rois == {"mol"}


# ─── 擴掃決策(ADR-0005)─────────────────────────────────────────────────

def _img(n):
    return (f"image{n}.jpeg", f"bytes{n}".encode())


def _reader(hits: dict[str, ScanResult]):
    """假的讀圖函式:有列的圖回 Reading,其餘全無命中。記下讀過的圖。"""
    seen: list[str] = []

    def read(name, data):
        seen.append(name)
        r = hits.get(name)
        return R.Reading(r, []) if r else None
    return read, seen


def _vote(name, value="1234", conf=70.0):
    return ScanResult("a.docx", name, "large", id=value, id_conf=conf, id_from_vote=True)


class TestReadLargeDocx:
    IMAGES = [_img(n) for n in range(1, 8)]

    def test_vote_in_first_tier_stops(self):
        read, seen = _reader({"image5.jpeg": _vote("image5.jpeg")})
        scan = R.read_large_docx("a.docx", self.IMAGES, read)
        assert seen == ["image2.jpeg", "image5.jpeg", "image6.jpeg"]
        assert (scan.tier, scan.scanned) == (R.TierOutcome.第一層命中, 3)
        assert [r.result.scan_tier for r in scan.readings] == [1]

    def test_no_vote_expands_and_marks_the_second_tier(self):
        read, seen = _reader({"image4.jpeg": _vote("image4.jpeg")})
        scan = R.read_large_docx("a.docx", self.IMAGES, read)
        assert seen == ["image2.jpeg", "image5.jpeg", "image6.jpeg",
                        "image1.jpeg", "image3.jpeg", "image4.jpeg", "image7.jpeg"]
        assert (scan.tier, scan.scanned) == (R.TierOutcome.擴掃, 7)
        assert [(r.result.image_name, r.result.scan_tier) for r in scan.readings] == [
            ("image4.jpeg", 2)]

    def test_low_confidence_vote_does_not_stop(self):
        read, _ = _reader({"image5.jpeg": _vote("image5.jpeg", conf=CONF_VOTE_MIN)})
        assert R.read_large_docx("a.docx", self.IMAGES, read).tier is R.TierOutcome.擴掃

    def test_nothing_left_to_expand(self):
        read, seen = _reader({})
        scan = R.read_large_docx("a.docx", [_img(2), _img(5)], read)
        assert (scan.tier, scan.scanned) == (R.TierOutcome.無可擴, 2)

    def test_full_scan_is_one_tier(self):
        read, seen = _reader({"image5.jpeg": _vote("image5.jpeg")})
        scan = R.read_large_docx("a.docx", self.IMAGES, read, full_scan=True)
        assert len(seen) == 7
        assert scan.tier is R.TierOutcome.第一層命中
        assert {r.result.scan_tier for r in scan.readings} == {1}

    def test_readings_are_not_judged_here(self):
        """掃描只留讀值;要不要送 Vision 在送審決策判(ADR-0013)。"""
        read, _ = _reader({"image5.jpeg": _vote("image5.jpeg")})
        [reading] = R.read_large_docx("a.docx", self.IMAGES, read).readings
        assert (reading.result.id, reading.result.id_conf) == ("1234", 70.0)


def _source_trees():
    import ast
    from conftest import module_sources
    return [ast.parse(p.read_text(encoding="utf-8")) for p in module_sources(R)]


def test_permit_reading_does_not_import_pipeline():
    """判讀不依賴流程模組:pipeline 2,000 行、Google 套件、身分物件都不該被拉進來。"""
    import ast
    imported = set()
    for tree in _source_trees():
        imported |= {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import)
                     for a in n.names}
        imported |= {n.module.split(".")[0] for n in ast.walk(tree)
                     if isinstance(n, ast.ImportFrom) and n.module}
    assert "pipeline" not in imported
    assert not {m for m in imported if m.startswith("google")}


def test_package_init_only_forwards_its_own_submodules():
    """__init__ 只轉發 permit_reading 自己子模組的名字,不轉手別人的(#59、ADR-0014)。"""
    import ast
    from pathlib import Path
    tree = ast.parse(Path(R.__file__).read_text(encoding="utf-8"))
    imports = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
    assert imports and all(isinstance(n, ast.ImportFrom) and n.level == 1 for n in imports)


def test_only_the_vote_predicate_comes_from_submission_decision():
    """許可證判讀不碰送審門檻:只為停止擴掃用多數票謂詞(ADR-0005、#58)。"""
    import ast
    names, plain = set(), set()
    for tree in _source_trees():
        names |= {a.name for n in ast.walk(tree)
                  if isinstance(n, ast.ImportFrom) and n.module == "submission_decision"
                  for a in n.names}
        plain |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert names == {"is_permit_vote"} and "submission_decision" not in plain
