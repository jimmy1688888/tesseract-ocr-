# -*- coding: utf-8 -*-
"""雇主疑慮是一條條「(哪一格, 事實)」,L 欄只在一個地方組成字串(#44)。

舊做法各種疑慮各佔一個字串鍵(名稱、地址、縣市、門牌、樣板),字串裡自己帶欄位
字母;組 L 欄的知識住兩處 —— 切結書頁備援用不了 `_compose_doubt`,只好自己再
手寫一次 join。資料全部捏造。
"""
import employer_extract as ee
from employer_extract import EmployerAddress, extract_employer_fields
from employer_result import VALUE_KEYS, WHOLE, Doubt, compose_doubts


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


class TestAddressFactsGetFieldNames:
    """地址庫給的事實不冠欄名;雇主擷取冠上:郵遞區號留空的成因冠「郵遞區號」,
    縣市中英不符冠「地址_中」(#29、#78 Q2 表)。"""

    @staticmethod
    def _doubts(cn, en=""):
        return ee._check_address(EmployerAddress(cn, en, "")).doubts

    def test_zip_blank_reason_is_prefixed_zip(self):
        assert self._doubts("XX@@市北區磐石路29號7樓") == (Doubt("郵遞區號", "縣市未比對到官方資料庫"),)
        assert self._doubts("新竹市OOO29號7樓") == (Doubt("郵遞區號", "行政區未判定"),)
        assert self._doubts("台桃市中壢區中山路8號") == (
            Doubt("郵遞區號", "縣市僅模糊比對命中,郵遞區號一併未採用"),)

    def test_city_mismatch_is_prefixed_address(self):
        assert self._doubts("臺中市西區民權路88號5樓", "No.88, Zuoying Dist., Kaohsiung City") == (
            Doubt("地址_中", "縣市中英不符(臺中市／Kaohsiung City)"),)

    def test_empty_address_is_a_fact_of_its_own(self):
        """空輸入不呼叫地址庫:「中文地址未擷取到」,不是「縣市未比對到」。"""
        r = ee._check_address(EmployerAddress("", "No. 5, Taoyuan City", ""))
        assert (r.zip, r.doubts) == ("", (Doubt("地址_中", "中文地址未擷取到"),))

    def test_matched_address(self):
        r = ee._check_address(EmployerAddress("新竹市北區磐石路29號7樓", "", ""))
        assert (r.zip, r.doubts) == ("300", ())

    def test_english_hint_feeds_the_anchor(self):
        """讀不到英文地址時,黏了紅章殘影的 en_hint 照樣餵英文行錨定。"""
        r = ee._check_address(EmployerAddress(
            "台桃市中壢區中山路8號", "", "司賜No. 8, Zhongshan Rd., Zhongli Dist., Taoyuan City"))
        assert (r.zip, r.doubts) == ("320", ())


class TestStandardAddressNoLongerProduced:

    def test_standard_address_is_always_empty(self):
        """路名層拿掉後標準中文地址算不出來,值永遠空;欄位由 #30 拿掉(#78)。"""
        f = extract_employer_fields("\n".join([
            "甲方名稱: 王小明", "Nama Majikan: WANG XIAO MING", "地址:",
            "新竹市北區磐石路29號7樓", "SELANJUTNYA DISEBUT PIHAK PERTAMA"]))
        assert (f.values.地址_中, f.values.地址_中_標準, f.values.郵遞區號) == (
            "新竹市北區磐石路29號7樓", "", "300")


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

    def test_name_then_address_then_house_number_then_zip(self):
        """L 欄的順序:樣板 → 名稱 → 地址_中(縣市中英不符)→ 門牌 → 郵遞區號(#78 Q2 表)。"""
        f = extract_employer_fields("\n".join([
            "甲方名稱: 王明", "Nama Majikan: WANG XIAO MING", "地址:",
            "新竹市OOO天馬行空路29號7樓",
            "Alamat: No. 30, Tianma Rd., North Dist., Taichung City",
            "SELANJUTNYA DISEBUT PIHAK PERTAMA"]))
        assert f.疑慮標示 == ("雇主名稱_中:中文 2 字與英譯 3 音節不符；"
                             "地址_中:縣市中英不符(新竹市／Taichung City)；"
                             "地址_中:門牌號中英不符(29／30)；"
                             "郵遞區號:行政區未判定")


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
        assert f.values.郵遞區號 == "320"

