# -*- coding: utf-8 -*-
"""雇主疑慮是一條條「(哪一格, 事實)」,L 欄只在一個地方組成字串(#44)。

舊做法各種疑慮各佔一個字串鍵(名稱、地址、縣市、門牌、樣板),字串裡自己帶欄位
字母;組 L 欄的知識住兩處 —— 切結書頁備援用不了 `_compose_doubt`,只好自己再
手寫一次 join。資料全部捏造。
"""
import employer_extract as ee
from employer_extract import extract_employer_fields
from employer_result import (VALUE_KEYS, Doubt, EmployerExtraction, EmployerValues,
                             ExtractionStatus, compose_doubts)


class TestComposeDoubts:

    def test_each_doubt_gets_its_column_letter(self):
        doubts = (Doubt("雇主名稱_中", "未擷取到中文名"),
                  Doubt("地址_中_標準", "中文地址未擷取到"))
        assert compose_doubts(doubts) == "H:未擷取到中文名；J:中文地址未擷取到"

    def test_no_doubts_is_empty(self):
        assert compose_doubts(()) == ""

    def test_order_is_kept(self):
        doubts = (Doubt("地址_中_標準", "乙"), Doubt("雇主名稱_中", "甲"))
        assert compose_doubts(doubts) == "J:乙；H:甲"


class TestStandardizeAddressReturnsAValue:

    def test_returns_value_without_touching_anything(self):
        r = ee._standardize_address("")
        assert (r.standard, r.zip) == ("", "")
        assert r.doubts == (Doubt("地址_中_標準", "中文地址未擷取到"),)

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
        assert f.疑慮標示 == ("H:中文 2 字與英譯 3 音節不符；"
                             "J:路名未在該行政區的路名清單中；"
                             "J:縣市中英不符(新竹市／Taichung City)；"
                             "J:門牌號中英不符(29／30)")


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

    def test_rescue_replaces_the_missing_name_doubt_in_place(self, monkeypatch):
        monkeypatch.setattr(ee, "rescue_name_from_pledge",
                            lambda p, client=None: ("姜信安", "image5.jpeg"))
        doubts = (Doubt("雇主名稱_中", "未擷取到中文名"),
                  Doubt("地址_中_標準", "中文地址未擷取到"))
        f = EmployerExtraction(ExtractionStatus.擷取成功,
                               EmployerValues(雇主名稱_英="JIANG SIN AN"), doubts)
        f = ee._apply_pledge_rescue(f, "x.docx")
        assert f.doubts == (Doubt("雇主名稱_中", "中文名取自切結書頁(image5.jpeg)"),
                            Doubt("地址_中_標準", "中文地址未擷取到"))
        assert f.疑慮標示 == "H:中文名取自切結書頁(image5.jpeg)；J:中文地址未擷取到"
