# -*- coding: utf-8 -*-
"""郵遞區號判定(CONTEXT.md):用縣市區表從中文地址行判出縣市與區,回填郵遞區號(#78)。

寧空勿錯:縣市精確命中、區也命中才填;縣市非精確命中一律不填。留空時講最上游卡住的
那一關(三條最多一條),因為空白的郵遞區號看起來與「契約沒寫」一樣。事實不冠欄名
——地址庫不知道雇主欄位,冠欄名是雇主擷取的事。

測試直接用 repo 裡的真表(data/tw_districts.json),不注入:表進了版控,換機器不會轉紅。
"""
import pytest

import address_db
from address_db import determine_zip

CITY_UNMATCHED = "縣市未比對到官方資料庫"
CITY_FUZZY = "縣市僅模糊比對命中,郵遞區號一併未採用"
DISTRICT_UNDETERMINED = "行政區未判定"


class TestZipFilled:

    def test_exact_city_and_district(self):
        z = determine_zip("新竹市北區磐石路29號7樓")
        assert (z.zip, z.city, z.zip_blank_reason, z.city_mismatch) == ("300", "新竹市", "", "")

    def test_city_keeps_official_formal_tai(self):
        """判出的縣市照表上的官方寫法(正體「臺」),不是比對用的折疊字串。"""
        assert determine_zip("台北市文山區辛亥路7段69巷15號3樓").city == "臺北市"

    def test_city_without_suffix_is_still_exact(self):
        """缺「市/縣」尾字(去尾字命中)仍算精確。"""
        assert determine_zip("新竹北區磐石路29號").zip == "300"

    @pytest.mark.parametrize("cn, zipcode", [
        ("高雄市左營區華夏路1728號13樓", "813"),
        ("新竹市北區磐石路29號7樓", "300"),
        ("花蓮縣新城鄉樹林街162號", "971"),
        ("雲林縣四湖鄉中山東路38巷1弄12號", "654"),
        ("高雄市楠梓區德賢路555巷12號20樓", "811"),
        ("宜蘭縣宜蘭市津梅路142巷2號", "260"),
        ("新竹市北區延平路一段401巷1號", "300"),
    ])
    def test_real_contract_addresses_keep_their_zip(self, cn, zipcode):
        """7 份真實契約的地址:路名層拿掉後郵遞區號一份都不能掉。"""
        z = determine_zip(cn)
        assert (z.zip, z.zip_blank_reason) == (zipcode, "")

    def test_longest_district_wins(self):
        """「安南區」含子字串「南區」:取最長命中,不配到別的區。"""
        assert determine_zip("臺南市安南區海佃路一段100號").zip == "709"

    @pytest.mark.parametrize("cn, zipcode", [
        ("宜蘭縣宜蘭市津梅路142巷2號", "260"),
        ("彰化縣和美鎮德南街100號", "508"),
        ("桃園市桃園區文化街5號", "330"),
    ])
    def test_same_named_county_seat(self, cn, zipcode):
        """縣治與縣同名(宜蘭縣/宜蘭市)的合法碰撞:剝掉的是縣市名,區名還在。"""
        assert determine_zip(cn).zip == zipcode


class TestZipBlankReason:
    """留空的三種成因,講最上游卡住的那一關。"""

    def test_city_not_matched(self):
        z = determine_zip("XX@@市北區磐石路29號7樓")
        assert (z.zip, z.city, z.zip_blank_reason) == ("", "", CITY_UNMATCHED)

    def test_fuzzy_city_never_gives_a_zip(self):
        """縣市只靠模糊比對湊上 → 郵遞區號一律不填。「中壢區」這類名字在錯的縣市
        底下照樣配得到,會產出確信的錯值;路名佐證那條路已經沒了(#78)。"""
        z = determine_zip("台桃市中壢區中山路8號")
        assert (z.zip, z.zip_blank_reason) == ("", CITY_FUZZY)

    def test_fuzzy_city_even_when_correct(self):
        """「宜藺縣」多半就是宜蘭縣 —— 但沒有其他佐證可以翻案,照樣不填。"""
        z = determine_zip("宜藺縣羅東鎮中正路3號")
        assert (z.zip, z.zip_blank_reason) == ("", CITY_FUZZY)

    def test_district_not_determined(self):
        z = determine_zip("新竹市OOO中正路29號7樓")
        assert (z.zip, z.city, z.zip_blank_reason) == ("", "新竹市", DISTRICT_UNDETERMINED)

    def test_city_name_does_not_lend_a_district(self):
        """縣市名不得自己餵進行政區比對(32538):「桃園區」去尾剩「桃園」,而「桃園市」
        裡就有這兩字 —— 沒寫區的地址不能被安上桃園區(郵遞 330)。"""
        z = determine_zip("桃園市高山里文化七路206巷100弄13號3樓")
        assert (z.zip, z.zip_blank_reason) == ("", DISTRICT_UNDETERMINED)

    def test_single_char_district_core_does_not_count(self):
        """去尾字的區名核心至少兩字:「北大路」的「北」不是北區、「東門街」的「東」不是東區。"""
        for cn in ("新竹市OOO北大路29號", "新竹市OOO東門街10號"):
            assert determine_zip(cn).zip_blank_reason == DISTRICT_UNDETERMINED, cn

    def test_reasons_offer_no_action(self):
        """理由只陳述事實,不夾帶行動建議 —— 它自己可能是錯的(ADR-0003)。"""
        for cn in ("XX@@市北區磐石路29號7樓", "台桃市中壢區中山路8號", "新竹市OOO中正路29號7樓"):
            reason = determine_zip(cn).zip_blank_reason
            assert reason and "可補" not in reason and ".json" not in reason, cn


class TestEnglishLineAnchor:
    """紅章常蓋毀中文行首的縣市,英文行尾的縣市倖存:以英文行反查縣市/區為錨。
    英文行只做官方英文名精確包含,所以錨出來的縣市算精確。"""

    CN_DEAD = "◎◎◎左營區天馬行空路1號"
    EN_ALIVE = "No.1, Tianma Rd., Zuoying Dist., Kaohsiung City, Taiwan R.O.C."

    def test_anchor_recovers_city_and_zip(self):
        z = determine_zip(self.CN_DEAD, en_text=self.EN_ALIVE)
        assert (z.zip, z.city, z.zip_blank_reason) == ("813", "高雄市", "")

    def test_without_english_line_it_really_does_fail(self):
        """前提檢查:沒有英文行時這個地址確實救不回來。"""
        assert determine_zip(self.CN_DEAD).zip_blank_reason == CITY_UNMATCHED

    def test_stamped_city_recovered(self):
        z = determine_zip("中共雄巿左營區華夏路1728號13樓",
                          en_text="13F., No.1728, Huaxia Rd., Zuoying Dist., Kaohsiung City")
        assert (z.zip, z.zip_blank_reason) == ("813", "")

    def test_fuzzy_chinese_city_replaced_by_english(self):
        """中文縣市只是模糊命中、英文行精確 → 改以英文行的縣市為準,郵遞區號照填。"""
        z = determine_zip("台桃市中壢區中山路8號",
                          en_text="No. 8, Zhongshan Rd., Zhongli Dist., Taoyuan City")
        assert (z.zip, z.city, z.zip_blank_reason) == ("320", "桃園市", "")

    def test_english_district_fills_missing_chinese_district(self):
        """中文縣市可信但比不到區,英文錨同縣市且有區 → 補上區。"""
        z = determine_zip("新竹市OOO中正路29號7樓",
                          en_text="7F., No.29, Zhongzheng Rd., North Dist., Hsinchu City")
        assert (z.zip, z.zip_blank_reason) == ("300", "")

    def test_english_line_of_another_city_does_not_override_exact_chinese(self):
        z = determine_zip("新竹市北區磐石路29號7樓",
                          en_text="No. 8, Zhongshan Rd., Zhongli Dist., Taoyuan City")
        assert (z.zip, z.city) == ("300", "新竹市")


class TestCityMismatch:
    """判出的中文縣市 ≠ 英文行精確包含的官方英文縣市名 → 縣市中英不符。
    專抓「挑到別人的地址」(33704)。英文側配不到任何官方縣市就不標;不比到區。"""

    def test_different_cities_flagged(self):
        z = determine_zip("臺中市西區民權路88號5樓",
                          en_text="3F., No.100, Huaxia Rd., Zuoying Dist., Kaohsiung City")
        assert z.city_mismatch == "縣市中英不符(臺中市／Kaohsiung City)"

    def test_mismatch_does_not_withhold_the_zip(self):
        z = determine_zip("臺中市西區民權路88號5樓",
                          en_text="3F., No.100, Huaxia Rd., Zuoying Dist., Kaohsiung City")
        assert (z.zip, z.zip_blank_reason) == ("403", "")

    def test_same_city_not_flagged(self):
        z = determine_zip("高雄市左營區華夏路100號3樓",
                          en_text="3F., No.100, Huaxia Rd., Zuoying Dist., Kaohsiung City")
        assert z.city_mismatch == ""

    def test_english_matches_no_official_city_not_flagged(self):
        z = determine_zip("臺中市西區民權路88號5樓",
                          en_text="3F., No.100, Huaxia Rd., Kaohslung Clty, Taiwan R.O.C.")
        assert z.city_mismatch == ""

    def test_chinese_city_unmatched_not_flagged(self):
        """中文縣市比不到另有成因,不重複標。"""
        z = determine_zip("XX@@市北區某某路29號7樓", en_text="No.29, Xxyyzz, Taiwan")
        assert z.city_mismatch == ""

    def test_taipei_vs_new_taipei(self):
        """取最長命中:New Taipei City 不會被當成 Taipei City。"""
        z = determine_zip("臺北市大安區復興南路一段10號",
                          en_text="No.10, Sec. 1, Wenhua Rd., Banqiao Dist., New Taipei City")
        assert z.city_mismatch == "縣市中英不符(臺北市／New Taipei City)"
        assert determine_zip("新北市板橋區文化路一段10號",
                             en_text="No.10, Banqiao Dist., New Taipei City").city_mismatch == ""


class TestTableUnreadable:
    """縣市區表在 repo 裡,讀不到就是程式或安裝壞了:拋出,不安靜留空。"""

    def test_missing_table_raises(self, missing_district_table):
        with pytest.raises(OSError):
            determine_zip("新竹市北區磐石路29號7樓")

    def test_import_reads_nothing(self, monkeypatch):
        """ADR-0001:import 零 I/O —— permit_lookup 只要異體折疊,不能被表拖累。"""
        import importlib
        from pathlib import Path

        def refuse(*_a, **_k):
            raise AssertionError("import address_db 時讀了檔")
        monkeypatch.setattr(Path, "read_text", refuse)
        monkeypatch.setattr(Path, "read_bytes", refuse)
        importlib.reload(address_db)
        assert address_db.fold_variants("臺北市") == "台北市"


class TestPublicSurface:
    """地址庫對外只有異體折疊與判定郵遞區號(#78)。"""

    def test_only_two_public_functions(self):
        public = {n for n, v in vars(address_db).items()
                  if callable(v) and not n.startswith("_")
                  and getattr(v, "__module__", "") == "address_db"}
        assert public == {"fold_variants", "determine_zip", "ZipDetermination"}
