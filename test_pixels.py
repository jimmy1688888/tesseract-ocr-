# -*- coding: utf-8 -*-
"""pixels:像素前處理(轉正、相對比例裁切、通道/放大/對比/去雜、去紅章)的行為測試。

三個呼叫端(契約頁送 Vision、次頁送 Vision、許可證 Tesseract 掃描)共用同一個
preprocess;彼此的差異只在設定值,這裡把那幾組設定之間的關係釘住。
"""
import ast
from io import BytesIO
from pathlib import Path

import numpy as np
from PIL import Image

import pixels
from pixels import (CONTRACT_PAGE_DEINK, NEXT_PAGE_DEINK, crop, crop_png, deink_red_stamp,
                    open_upright, preprocess, to_png)

WHITE, BLACK = (255, 255, 255), (0, 0, 0)
STAMP     = (220, 40, 50)     # 白底上的紅印章:R 高、G/B 低
DARK_RED  = (100, 20, 20)     # 壓在黑字上的暗紅:R 不夠亮,不該被抹掉


def _png(a: np.ndarray, exif_orientation: int | None = None) -> bytes:
    img = Image.fromarray(a.astype(np.uint8))
    buf = BytesIO()
    if exif_orientation is None:
        img.save(buf, format="PNG")
    else:
        exif = Image.Exif()
        exif[0x0112] = exif_orientation
        img.save(buf, format="JPEG", exif=exif.tobytes(), quality=100)
    return buf.getvalue()


def _page() -> np.ndarray:
    """40x40 白紙:上排左黑字、右紅章;中排左暗紅筆畫、右灰;底下 10 列留白。"""
    a = np.full((40, 40, 3), 255, dtype=np.uint8)
    a[:20, :20] = BLACK
    a[:20, 20:] = STAMP
    a[20:30, :20] = DARK_RED
    a[20:30, 20:] = (128, 128, 128)
    return a


def _pixels(png: bytes) -> np.ndarray:
    return np.asarray(Image.open(BytesIO(png)))


class TestOpenUpright:
    def test_applies_exif_rotation(self):
        """手機拍的掃描檔靠 EXIF 記方向;不轉正,相對比例的 ROI 會裁到別處。"""
        img = open_upright(_png(np.zeros((30, 20, 3)), exif_orientation=6))
        assert img.size == (30, 20)

    def test_is_rgb(self):
        gray = Image.new("L", (5, 5))
        buf = BytesIO()
        gray.save(buf, format="PNG")
        assert open_upright(buf.getvalue()).mode == "RGB"


class TestCrop:
    def test_relative_fractions(self):
        img = Image.new("RGB", (100, 200))
        assert crop(img, (0.0, 0.0, 1.0, 0.35)).size == (100, 70)

    def test_truncates_to_whole_pixels(self):
        img = Image.new("RGB", (10, 10))
        assert crop(img, (0.05, 0.04, 0.40, 0.25)).size == (4, 2)

    def test_crop_png_crops_the_upright_image(self):
        """先轉正再裁:裁的是人看到的上緣,不是檔案存放時的上緣。"""
        a = np.zeros((20, 10, 3), dtype=np.uint8)
        png = crop_png(_png(a, exif_orientation=6), (0, 0, 1, 0.5))
        assert _pixels(png).shape[:2] == (5, 20)


class TestPreprocess:
    def test_red_channel_turns_stamp_light_and_keeps_text_dark(self):
        out = np.asarray(preprocess(_png(_page()), {"channel": "R", "contrast": (0, 100)}))
        assert out[0, 0] == 0                 # 黑字
        assert out[0, -1] > 200               # 紅章在紅通道下接近白

    def test_gray_channel_keeps_stamp_dark(self):
        out = np.asarray(preprocess(_png(_page()), {"channel": "gray", "contrast": (0, 100)}))
        assert out[0, -1] < 128

    def test_scale(self):
        assert preprocess(_png(_page()), {"channel": "R", "scale": 2}).size == (80, 80)

    def test_roi_is_cut_after_rotation(self):
        img = preprocess(_png(np.zeros((20, 10, 3)), exif_orientation=6),
                         {"channel": "R", "roi": (0, 0, 1, 0.5)})
        assert img.size == (20, 5)

    def test_no_whitening_unless_asked(self):
        """次頁與許可證掃描不抹白:只靠紅通道讓章變淡。"""
        out = np.asarray(preprocess(_png(_page()), {"channel": "R", "contrast": (0, 100)}))
        assert out[0, -1] < 255


class TestDeink:
    def test_stamp_on_white_is_whitened(self):
        assert (_pixels(deink_red_stamp(_png(_page())))[:20, 20:] == 255).all()

    def test_dark_red_on_text_is_kept(self):
        """壓在黑字上的暗紅像素 R 偏低,抹掉會連筆畫一起抹掉。"""
        assert (_pixels(deink_red_stamp(_png(_page())))[20:30, :20] < 255).all()

    def test_black_text_stays_black(self):
        assert (_pixels(deink_red_stamp(_png(_page())))[:20, :20] == 0).all()

    def test_is_preprocess_with_the_contract_page_settings(self):
        """一套去紅章:契約頁那一份就是 preprocess 加上抹白,不是另一份實作。"""
        b = _png(_page())
        assert (_pixels(deink_red_stamp(b))
                == np.asarray(preprocess(b, CONTRACT_PAGE_DEINK))).all()


class TestSettings:
    """三個呼叫端的差異只在設定值;這裡釘住設定之間的關係。"""

    def test_only_the_contract_page_whitens(self):
        assert "whiten_red" in CONTRACT_PAGE_DEINK
        assert "whiten_red" not in NEXT_PAGE_DEINK

    def test_next_page_is_the_first_permit_scan_setting(self):
        """次頁用的就是許可證掃描第一組(紅通道_2x_中值3),只是不帶 Tesseract 的 psm。"""
        import pipeline
        first = pipeline.SCAN_CONFIGS[0]
        assert first["name"] == "紅通道_2x_中值3"
        assert {k: v for k, v in first.items() if k not in ("name", "psm")} == NEXT_PAGE_DEINK

    def test_to_png_roundtrip(self):
        img = Image.new("L", (3, 4), 7)
        assert _pixels(to_png(img)).shape == (4, 3)


def test_pixels_is_a_leaf_module():
    """只依賴標準函式庫、PIL、numpy:pipeline 與 employer_extract 都 import 它,
    它若反過來 import 任一側,延遲 import 的約定就又回來了。"""
    tree = ast.parse(Path(pixels.__file__).read_text(encoding="utf-8"))
    roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            roots.add((node.module or "").split(".")[0])
    assert roots <= {"__future__", "io", "numpy", "PIL"}
