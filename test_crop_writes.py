# -*- coding: utf-8 -*-
"""裁切圖:掃描決定要寫什麼,run_scan 寫出(#18)。

以前六個 mkdir + save 交織在 OCR 迴圈裡,要跑到投票與交叉比對就得先寫檔。
現在掃描回傳 Reading(result, crops),不碰硬碟;寫出的檔名與路徑與以前相同:

    <輸出目錄>/mol_crops/<docx>_<圖>.png
    <輸出目錄>/permit_crops/<docx>_<圖>.png
    <輸出目錄>/low_conf_crops/<docx>_<圖>_<ROI>_conf<信心取整>.png   (信心 < CONF_KEY_IN)
"""
import csv
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

import pipeline as P
from batch import Batch

ROI_NAME = {v: k for k, v in P.ROI_REGIONS.items()}


@pytest.fixture
def ocr(monkeypatch):
    """假 OCR:依 (ROI, 設定名稱) 查表回 (文字, 信心);查不到就是什麼都沒讀到。"""
    table: dict[tuple[str, str], tuple[str, float]] = {}
    monkeypatch.setattr(P, "preprocess", lambda data, cfg: (ROI_NAME[cfg["roi"]], cfg["name"]))
    monkeypatch.setattr(P, "ocr_with_conf", lambda img, lang, tess: table.get(img, ("", 0.0)))
    return table


def _every_config(table, roi, text, conf, configs=None):
    for cfg in configs or (P.SCAN_CONFIGS + P.FALLBACK_CONFIGS):
        table[(roi, cfg["name"])] = (text, conf)


class TestScanWritesNothing:

    def test_large_low_confidence_permit(self, ocr, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _every_config(ocr, "permit_upper", "No. Izin: 1234", 40.0)

        result, crops = P.scan_image_large("32503.docx", "image5.jpeg", b"not an image")

        assert crops == [
            P.Crop("permit_crop", Path("permit_crops/32503_image5.png"),
                   P.ROI_REGIONS["permit_upper"]),
            P.Crop("low_conf", Path("low_conf_crops/32503_image5_permit_upper_conf40.png"),
                   P.ROI_REGIONS["permit_upper"]),
        ]
        assert (result.permit_crop, result.low_conf) == ("", "")
        assert list(tmp_path.iterdir()) == []

    def test_large_high_confidence_writes_no_low_conf_crop(self, ocr):
        _every_config(ocr, "permit_upper", "No. Izin: 1234", 80.0)
        _, crops = P.scan_image_large("a.docx", "image5.jpeg", b"")
        assert [c.field for c in crops] == ["permit_crop"]

    def test_large_mol_vote_and_permit(self, ocr):
        _every_config(ocr, "mol", "MOL 1234", 70.0, P.PERMIT_VOTE_CONFIGS)
        _every_config(ocr, "permit_lower", "No. Izin 1234", 30.0)

        _, crops = P.scan_image_large("a.docx", "image2.jpeg", b"")

        assert [(c.field, str(c.path)) for c in crops] == [
            ("mol_crop", str(Path("mol_crops/a_image2.png"))),
            ("permit_crop", str(Path("permit_crops/a_image2.png"))),
            ("low_conf", str(Path("low_conf_crops/a_image2_permit_lower_conf30.png"))),
        ]

    def test_large_only_one_low_conf_crop(self, ocr):
        """兩個 ROI 都低信心:只留第一個(以前靠 result.low_conf 已有值擋掉第二張)。"""
        not_voting = [c for c in P.SCAN_CONFIGS if c not in P.PERMIT_VOTE_CONFIGS]
        _every_config(ocr, "mol", "MOL 1234", 40.0, not_voting)   # 單一判讀,不成多數票
        _every_config(ocr, "permit_upper", "No. Izin: 1234", 30.0)

        _, crops = P.scan_image_large("a.docx", "image2.jpeg", b"")

        assert [c.path.name for c in crops if c.field == "low_conf"] == [
            "a_image2_mol_conf40.png"]

    def test_small_mol_vote(self, ocr, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _every_config(ocr, "mol", "MOL 1234", 70.0, P.PERMIT_VOTE_CONFIGS)

        result, crops = P.scan_image_mol_only("a.docx", "image2.png", b"")

        assert result.mol_from_vote and result.mol == "1234"
        assert crops == [P.Crop("mol_crop", Path("mol_crops/a_image2.png"),
                                P.ROI_REGIONS["mol"])]
        assert list(tmp_path.iterdir()) == []

    def test_small_single_read_low_confidence(self, ocr):
        ocr[("mol", P.FALLBACK_CONFIGS[0]["name"])] = ("MOL 1234", 40.4)

        result, crops = P.scan_image_mol_only("a.docx", "image2.png", b"")

        assert (result.mol, result.mol_from_vote) == ("1234", False)
        assert [str(c.path) for c in crops] == [
            str(Path("mol_crops/a_image2.png")),
            str(Path("low_conf_crops/a_image2_mol_conf40.png")),
        ]

    def test_small_nothing_read(self, ocr):
        result, crops = P.scan_image_mol_only("a.docx", "image2.png", b"")
        assert crops == []
        assert result.manual_review


def _png(w=100, h=50) -> bytes:
    buf = BytesIO()
    Image.new("RGB", (w, h), (200, 10, 10)).save(buf, format="PNG")
    return buf.getvalue()


class TestRunScanWrites:

    def test_crops_land_at_the_same_paths_and_fill_the_csv(self, monkeypatch, tmp_path):
        roi = P.ROI_REGIONS["permit_upper"]
        monkeypatch.setattr(P.docx_images, "read_images",
                            lambda p: [(f"image{n}.png", _png()) for n in range(1, 8)])

        def large(docx, name, data, roi_filter=""):
            if name != "image5.png":
                return None
            r = P.ScanResult(source_docx=docx, image_name=name, docx_class="large",
                             id="1234", id_conf=70.0, id_from_vote=True)
            return P.Reading(r, [
                P.Crop("permit_crop", Path("permit_crops/32503_image5.png"), roi),
                P.Crop("low_conf", Path("low_conf_crops/32503_image5_permit_upper_conf40.png"), roi),
            ])
        monkeypatch.setattr(P, "scan_image_large", large)
        out = tmp_path / "out"

        P.run_scan(Batch((tmp_path / "32503.docx",), source=tmp_path, output_dir=out))

        [row] = csv.DictReader(open(out / "matches.csv", encoding="utf-8-sig"))
        assert row["permit_crop"] == str(out / "permit_crops" / "32503_image5.png")
        assert row["low_conf"] == str(out / "low_conf_crops" / "32503_image5_permit_upper_conf40.png")
        assert row["mol_crop"] == ""
        written = Image.open(out / "permit_crops" / "32503_image5.png")
        assert written.size == (60, 25)     # 100x50 的 (0.40, 0.05, 1.00, 0.55)
