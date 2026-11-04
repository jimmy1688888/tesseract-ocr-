# -*- coding: utf-8 -*-
"""雇主疑慮是一條條「(哪一格, 事實)」,L 欄只在一個地方組成字串(#44)。

舊做法各種疑慮各佔一個字串鍵(名稱、地址、縣市、門牌、樣板),字串裡自己帶欄位
字母;組 L 欄的知識住兩處 —— 切結書頁備援用不了 `_compose_doubt`,只好自己再
手寫一次 join。資料全部捏造。
"""
from conftest import FakeVision, pages_of
import employer_extract as ee
from employer_extract import extract_employer_fields
from employer_result import (VALUE_KEYS, WHOLE, Doubt, EmployerExtraction, EmployerValues,
                             ExtractionStatus, compose_doubts)


class TestComposeDoubts:

    def test_each_doubt_gets_its_field_name(self):
        """冠欄位名稱、不冠欄位字母:Sheets 移動欄位(#30)時字母會安靜地指到別格(#29)。"""
        doubts = (Doubt("雇主名稱_中", "未擷取到中文名"),
                  Doubt("地址_中", "中文地址未擷取到"))
        assert compose_doubts(doubts) == "雇主名稱_中:未擷取到中文名；地址_中:中文地址未擷取到"

    def test_whole_document_doubt_is_prefixed_employer_data(self):
        """講整份的疑慮冠「雇主資料」,不冠 H~O —— H~O 也是欄位字母。"""
        assert compose_doubts((Doubt(WHOLE, "甲"),)) == "雇主資料:甲"

    def test_zip_doubt_is_prefixed_zip(self):
        assert compose_doubts((Doubt("郵遞區號", "行政區未判定"),)) == "郵遞區號:行政區未判定"

    def test_no_doubts_is_empty(self):
        assert compose_doubts(()) == ""

    def test_order_is_kept(self):
        doubts = (Doubt("地址_中", "乙"), Doubt("雇主名稱_中", "甲"))
        assert compose_doubts(doubts) == "地址_中:乙；雇主名稱_中:甲"


class TestZipBlankReasonsArePrefixedZip:
    """郵遞區號留空的三種成因冠「郵遞區號」;其餘地址疑慮冠「地址_中」(#29)。"""

    def _fields(self, cn, en=""):
        return [(d.field, d.fact) for d in ee._standardize_address(cn, en).doubts]

    def test_city_not_matched(self):
        assert self._fields("XX@@市北區磐石路29號7樓") == [("郵遞區號", "縣市未比對到官方資料庫")]

    def test_city_only_fuzzy(self):
        fields = self._fields("台桃市中壢區天馬行空路8號")
        assert ("郵遞區號", "縣市僅模糊比對命中,郵遞區號一併未採用") in fields

    def test_district_not_determined(self):
        assert ("郵遞區號", "行政區未判定") in self._fields("新竹市OOO29號7樓")

    def test_district_ambiguous_by_road_is_address(self):
        assert ("地址_中", "行政區未判定(該路名在多個行政區皆有)") in self._fields(
            "新竹市OOO中正路29號7樓")

    def test_road_not_in_list_is_address(self):
        assert self._fields("新竹市北區天馬行空路29號7樓") == [
            ("地址_中", "路名未在該行政區的路名清單中")]

    def test_no_letter_prefix_left(self):
        for cn in ("XX@@市北區磐石路29號7樓", "新竹市OOO29號7樓", "新竹市北區天馬行空路29號7樓", ""):
            for d in ee._standardize_address(cn).doubts:
                assert d.field in ("地址_中", "郵遞區號"), (cn, d)


class TestStandardizeAddressReturnsAValue:

    def test_returns_value_without_touching_anything(self):
        r = ee._standardize_address("")
        assert (r.standard, r.zip) == ("", "")
        assert r.doubts == (Doubt("地址_中", "中文地址未擷取到"),)

    def test_matched_address(self):
        r = ee._standardize_address("新竹市北區磐石路29號7樓")
        assert r.standard and r.zip and r.doubts == ()


class TestExtractedFieldsCarryTheList:

    def test_l_column_is_composed_from_the_list(self):
        f = extract_employer_fields("甲方:\nNama Majikan: WANG XIAO MING\nPIHAK KEDUA")
        assert f.疑慮標示 == compose_doubts(f.doubts)
        assert Doubt("雇主名稱_中", "未擷取到中文名") in f.doubts

    def test_values_and_doubts_are_separate(self):
        f = extract_employer_fields("甲方: 王小明\nNama Majikan: WANG XIAO MING\nPIHAK KEDUA")
        assert set(VALUE_KEYS) == {"雇主名稱_中", "雇主名稱_英", "地址_中", "地址_中_標準",
                                   "地址_英", "郵遞區號", "電話"}
        assert all(isinstance(d, Doubt) for d in f.doubts)


class TestOrderOfTheLColumn:

    def test_name_then_address_then_city_then_house_number(self):
        """L 欄的順序:樣板 → 名稱 → 地址 → 縣市 → 門牌(與改成清單之前逐字相同)。"""
        f = extract_employer_fields("\n".join([
            "甲方名稱: 王明", "Nama Majikan: WANG XIAO MING", "地址:",
            "新竹市北區天馬行空路29號7樓",
            "Alamat: No. 30, Tianma Rd., North Dist., Taichung City",
            "SELANJUTNYA DISEBUT PIHAK PERTAMA"]))
        assert f.疑慮標示 == ("雇主名稱_中:中文 2 字與英譯 3 音節不符；"
                             "地址_中:路名未在該行政區的路名清單中；"
                             "地址_中:縣市中英不符(新竹市／Taichung City)；"
                             "地址_中:門牌號中英不符(29／30)")


class TestEnglishHintAnchorsTheAddress:

    def test_english_line_with_stamp_residue_still_anchors(self):
        """英文地址行頭黏了紅章殘影字(含中文,所以不算英文地址),仍拿它當錨:
        縣市被咬的「台桃市」靠行尾的 Taoyuan City 救回。"""
        f = extract_employer_fields("\n".join([
            "甲方名稱: 王小明", "Nama Majikan: WANG XIAO MING",
            "地址:台桃市中壢區中山路8號",
            "司賜No. 8, Zhongshan Rd., Zhongli Dist., Taoyuan City",
            "SELANJUTNYA DISEBUT PIHAK PERTAMA"]))
        assert f.values.地址_英 == ""
        assert (f.values.地址_中_標準, f.values.郵遞區號) == ("桃園市中壢區中山路8號", "320")


class TestPledgeRescueAddsToTheList:

    def test_rescue_replaces_the_missing_name_doubt_in_place(self):
        pages = pages_of((0, "image4.jpeg", b"front", True), (0, "image5.jpeg", b"name"))
        vision = FakeVision(document=lambda b: "中華民國雇主/Majikan R.O.C: 姜信安/JIANG SIN AN")
        doubts = (Doubt("雇主名稱_中", "未擷取到中文名"),
                  Doubt("地址_中", "中文地址未擷取到"))
        f = EmployerExtraction(ExtractionStatus.擷取成功,
                               EmployerValues(雇主名稱_英="JIANG SIN AN"), doubts)
        f = ee._apply_pledge_rescue(f, pages, vision)
        assert f.doubts == (Doubt("雇主名稱_中", "中文名取自切結書頁(image5.jpeg)"),
                            Doubt("地址_中", "中文地址未擷取到"))
        assert f.疑慮標示 == "雇主名稱_中:中文名取自切結書頁(image5.jpeg)；地址_中:中文地址未擷取到"
