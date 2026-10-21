# -*- coding: utf-8 -*-
"""許可證取號認得 Vision 實際讀出的寫法(#42)。

30768/30770 的截圖寫 `No ijin:0567`,Vision 讀成 `No jin:0567`(少 i),取號要求
`i[zjl1]in` 取不出 → 判「Vision 無值」進人工審查;同一張圖的中文行 `許可證號:0567`
Vision 讀得很乾淨,但沒有 pattern 認得中文標籤。冒號字元類從第一個 commit 起
就只有半形 `:`(重複)加 `﹕`,沒有全形 `：`。

Tesseract 掃描與 Vision 取號共用同一份 pattern(#41),這裡一起釘住。資料全部捏造。
"""
import pytest

from permit_patterns import (RE_MOL_LIST, RE_PERMIT_ID_LIST, RE_PERMIT_ID_LIST_LOWER,
                             find_permits, permit_in_text)

PERMIT_LISTS = pytest.mark.parametrize(
    "patterns", [RE_PERMIT_ID_LIST, RE_PERMIT_ID_LIST_LOWER], ids=["upper", "lower"])


def _permit_and_layer(text, patterns):
    id_, layer, _, _ = find_permits(text, patterns, find_mol=False)
    return id_, layer


class TestNoAnchorWithoutI:
    """第 1 條放寬:`No` 後的 i 可有可無,但一定要有 `No` 錨點。"""

    @PERMIT_LISTS
    @pytest.mark.parametrize("text", ["No jin:1234", "No zin: 1234", "No. jin:1234",
                                      "NO lin : 1234"])
    def test_no_followed_by_jin(self, patterns, text):
        assert _permit_and_layer(text, patterns) == ("1234", 1)

    @PERMIT_LISTS
    def test_existing_spelling_still_layer_1(self, patterns):
        assert _permit_and_layer("No. Izin: 1234", patterns) == ("1234", 1)

    @PERMIT_LISTS
    def test_without_no_anchor_jin_does_not_match(self, patterns):
        """第 3 條(無 No 錨點)沒有放寬:光是 `jin:1234` 不配。"""
        assert _permit_and_layer("jin:1234", patterns) == ("", 0)
        assert _permit_and_layer("ajin:1234", patterns) == ("", 0)

    @PERMIT_LISTS
    def test_layer_3_still_needs_the_i(self, patterns):
        assert _permit_and_layer("izin:1234", patterns) == ("1234", 3)


class TestChineseLabel:
    """第 5 條:中文標籤 許可證號／許可證号(排最後,不改既有 id_layer)。"""

    @PERMIT_LISTS
    @pytest.mark.parametrize("text, value", [
        ("許可證號:1234", "1234"),
        ("許可證号:1234", "1234"),
        ("許可證號：1234", "1234"),
        ("許可證號 1234", "1234"),
        ("許可證號1234", "1234"),
        ("許可證號:1234-2", "1234-2"),
    ])
    def test_chinese_label(self, patterns, text, value):
        assert _permit_and_layer(text, patterns) == (value, 5)

    @PERMIT_LISTS
    def test_chinese_label_is_last(self, patterns):
        """同一段文字既有 `No jin` 又有中文標籤 → 取第 1 條(既有 id_layer 不動)。"""
        assert _permit_and_layer("許可證號:1234\nNo jin:1234", patterns) == ("1234", 1)

    @PERMIT_LISTS
    def test_five_digits_do_not_match(self, patterns):
        assert _permit_and_layer("許可證號:12345", patterns) == ("", 0)

    def test_list_has_five_layers(self):
        assert len(RE_PERMIT_ID_LIST) == len(RE_PERMIT_ID_LIST_LOWER) == 5


COLONS = [":", "：", "﹕", "∶"]


class TestColons:
    """冒號字元類:半形、全形、小型、比例,permit 與 mol 一起。"""

    @PERMIT_LISTS
    @pytest.mark.parametrize("colon", COLONS)
    @pytest.mark.parametrize("text, layer", [
        ("No. Izin{c} 1234", 1),
        ("Nizin{c} 1234", 2),
        ("izin{c} 1234", 3),
    ])
    def test_permit_colons(self, patterns, colon, text, layer):
        assert _permit_and_layer(text.format(c=colon), patterns) == ("1234", layer)

    @pytest.mark.parametrize("colon", COLONS)
    @pytest.mark.parametrize("text, layer", [
        ("Agency's MOL License Number{c} 5678", 1),
        ("Agenci's MOL License Number{c} 5678", 2),
        ("Number{c} 5678", 3),
    ])
    def test_mol_colons(self, colon, text, layer):
        _, _, mol, mol_layer = find_permits(text.format(c=colon), find_id=False)
        assert (mol, mol_layer) == ("5678", layer)

    def test_no_duplicate_chars_in_colon_classes(self):
        import re
        for p in RE_PERMIT_ID_LIST + RE_PERMIT_ID_LIST_LOWER + RE_MOL_LIST:
            for cls in re.findall(r"\[([^\]]*)\]", p.pattern):
                if ":" in cls:
                    assert len(cls) == len(set(cls)), f"{p.pattern}: [{cls}] 有重複字元"


class TestVisionReadings:
    """Vision 讀到的真實樣子(捏造號碼):兩行都在時取得出號碼。"""

    def test_lower_roi_reads_no_jin(self):
        assert permit_in_text("許可證號:1234\nNo jin:1234\n", "permit_lower") == "1234"

    def test_chinese_line_alone_is_enough(self):
        assert permit_in_text("surat\n許可證號:1234\nTanggal:2026", "permit_lower") == "1234"
