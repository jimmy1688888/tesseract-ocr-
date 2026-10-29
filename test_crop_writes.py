# -*- coding: utf-8 -*-
"""裁切圖:掃描決定要寫什麼,run_scan 寫出(#18)。

以前六個 mkdir + save 交織在 OCR 迴圈裡,要跑到多數票與交叉比對就得先寫檔。
現在掃描回傳 Reading(result, crops),不碰硬碟;寫出的檔名與路徑與以前相同:

    <輸出目錄>/mol_crops/<docx>_<圖>.png
    <輸出目錄>/permit_crops/<docx>_<圖>.png

掃描交出的列已帶這個相對路徑(posix,相對 matches.csv 所在資料夾);run_scan 照著
寫出,不回填(#61)。matches.csv 裡因此看不到輸出目錄在哪。

低信心截圖(low_conf_crops/)#58 起不再寫出:沒人看,門檻也和送審決策不一致。
"""
import csv
from io import BytesIO

import pytest
from PIL import Image

import docx_images
import permit_reading as R
from matches_csv import ScanResult
import pipeline as P
from batch import Batch

ROI_NAME = {v: k for k, v in R.ROI_REGIONS.items()}


class FakeOcr(dict):
    """注入的假 OCR:依 (ROI 名稱, 設定名稱) 查表回 (文字, 信心);查不到就是什麼都沒讀到。"""

    def __call__(self, image_bytes, cfg, roi):
        return self.get((ROI_NAME[roi], cfg["name"]), ("", 0.0))


@pytest.fixture
def ocr():
    return FakeOcr()


def _every_config(table, roi, text, conf, configs=None):
    for cfg in configs or (R.SCAN_CONFIGS + R.FALLBACK_CONFIGS):
        table[(roi, cfg["name"])] = (text, conf)


class TestScanWritesNothing:

    def test_large_low_confidence_permit(self, ocr, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _every_config(ocr, "permit_upper", "No. Izin: 1234", 40.0)

        result, crops = R.scan_image_large("32503.docx", "image5.jpeg", b"not an image", ocr=ocr)

        assert crops == [R.Crop("permit_crops/32503_image5.png", R.ROI_REGIONS["permit_upper"])]
        assert (result.permit_crop, result.mol_crop) == ("permit_crops/32503_image5.png", "")
        assert list(tmp_path.iterdir()) == []

    def test_large_mol_vote_and_permit(self, ocr):
        _every_config(ocr, "mol", "MOL 1234", 70.0, R.PERMIT_VOTE_CONFIGS)
        _every_config(ocr, "permit_lower", "No. Izin 1234", 30.0)

        result, crops = R.scan_image_large("a.docx", "image2.jpeg", b"", ocr=ocr)

        assert [c.path for c in crops] == ["mol_crops/a_image2.png", "permit_crops/a_image2.png"]
        assert (result.mol_crop, result.permit_crop) == ("mol_crops/a_image2.png",
                                                         "permit_crops/a_image2.png")

    def test_small_mol_vote(self, ocr, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _every_config(ocr, "mol", "MOL 1234", 70.0, R.PERMIT_VOTE_CONFIGS)

        result, crops = R.scan_image_mol_only("a.docx", "image2.png", b"", ocr=ocr)

        assert result.mol_from_vote and result.mol == "1234"
        assert crops == [R.Crop("mol_crops/a_image2.png", R.ROI_REGIONS["mol"])]
        assert result.mol_crop == "mol_crops/a_image2.png"
        assert list(tmp_path.iterdir()) == []

    def test_small_single_read_low_confidence(self, ocr):
        ocr[("mol", R.FALLBACK_CONFIGS[0]["name"])] = ("MOL 1234", 40.4)

        result, crops = R.scan_image_mol_only("a.docx", "image2.png", b"", ocr=ocr)

        assert (result.mol, result.mol_from_vote) == ("1234", False)
        assert [c.path for c in crops] == [result.mol_crop] == ["mol_crops/a_image2.png"]

    def test_small_nothing_read(self, ocr):
        result, crops = R.scan_image_mol_only("a.docx", "image2.png", b"", ocr=ocr)
        assert crops == []
        assert result.mol_crop == ""
        assert result.note == "mol 無值"     # 掃描當下的事實;要不要人工是送審決策的事


def _png(w=100, h=50) -> bytes:
    buf = BytesIO()
    Image.new("RGB", (w, h), (200, 10, 10)).save(buf, format="PNG")
    return buf.getvalue()


class TestRunScanWrites:

    def test_crops_land_where_the_row_says(self, monkeypatch, tmp_path):
        roi = R.ROI_REGIONS["permit_upper"]
        monkeypatch.setattr(docx_images, "read_images",
                            lambda p: [(f"image{n}.png", _png()) for n in range(1, 8)])

        def large(docx, name, data, roi_filter=""):
            if name != "image5.png":
                return None
            r = ScanResult(source_docx=docx, image_name=name, docx_class="large",
                           id="1234", id_conf=70.0, id_from_vote=True,
                           permit_crop="permit_crops/32503_image5.png")
            return R.Reading(r, [R.Crop("permit_crops/32503_image5.png", roi)])
        out = tmp_path / "out"

        P.run_scan(Batch((tmp_path / "32503.docx",), source=tmp_path, output_dir=out),
                   read_large=large)

        [row] = csv.DictReader(open(out / "matches.csv", encoding="utf-8-sig"))
        assert row["permit_crop"] == "permit_crops/32503_image5.png"   # 不帶輸出目錄
        assert row["mol_crop"] == ""
        written = Image.open(out / "permit_crops" / "32503_image5.png")
        assert written.size == (60, 25)     # 100x50 的 (0.40, 0.05, 1.00, 0.55)
