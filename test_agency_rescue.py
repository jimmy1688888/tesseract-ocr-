# -*- coding: utf-8 -*-
"""仲介反查名冊的名稱／地址通道:跨異體寫法仍要命中(permit_lookup.best_by_*)。

全無命中件走「次頁台仲區塊 → 反查名冊 → 回推許可證」的救援路徑。電話兩級
反查都落空時,靠名稱／地址相似度墊底。名冊機構地址用官方體例的「臺」與
阿拉伯段號,而契約 OCR 印的是「台」與國字段——不折疊異體,正確的那家會被
白白扣掉相似度,在紅章咬字時就掉到門檻以下。實測 OCR 損壞 20% 時,折疊
多救回 25/300 件而誤配僅 +1(見 docs/adr/0002)。

PermitLookup(records) 接受注入的名冊清單,故本檔完全不碰 data/agency_roster.json,
也不連網——三筆手寫假名冊即可測完整反查行為。
"""
import pytest

from permit_lookup import ADDR_MATCH_MIN, NAME_MATCH_MIN, PermitLookup


def _rec(permit, name, addr, phone, revoked="", closed=""):
    return {
        "許可證": permit, "機構名稱": name, "機構地址": addr, "電話": phone,
        "廢止許可日期": revoked, "終止營業日期": closed,
    }


# 名冊側一律官方體例:正體「臺」+ 阿拉伯段號
ROSTER = [
    _rec("1001", "臺灣鴻運國際人力仲介有限公司",
         "臺北市中山區中山北路1段10號3樓", "02-2523-1001"),
    _rec("1002", "南方之星人力資源管理顧問有限公司",
         "高雄市前鎮區成功二路25號", "07-3336-2002"),
    _rec("1003", "臺中永信跨國人力仲介股份有限公司",
         "臺中市西屯區臺灣大道3段99號12樓之1", "04-2251-3003"),
]


@pytest.fixture
def lookup():
    return PermitLookup(ROSTER)


class TestAddressAcrossVariants:
    """契約 OCR 寫法(台 + 國字段)要能命中名冊寫法(臺 + 阿拉伯段)。"""

    def test_formal_tai_vs_common_tai(self, lookup):
        hit = lookup.best_by_address("台北市中山區中山北路1段10號3樓")
        assert hit is not None and hit["許可證"] == "1001"

    def test_cn_segment_vs_arabic_segment(self, lookup):
        hit = lookup.best_by_address("臺北市中山區中山北路一段10號3樓")
        assert hit is not None and hit["許可證"] == "1001"

    def test_both_variants_at_once(self, lookup):
        """契約最常見的樣子:台 + 國字段 同時出現。"""
        hit = lookup.best_by_address("台北市中山區中山北路一段10號3樓")
        assert hit is not None and hit["許可證"] == "1001"
        assert hit["_ratio"] == 1.0, "折疊後應為完全相同,不該只是勉強過門檻"

    def test_tai_inside_road_name(self, lookup):
        """「臺灣大道」的臺在路名裡,同樣要折疊。"""
        hit = lookup.best_by_address("台中市西屯區台灣大道三段99號12樓之1")
        assert hit is not None and hit["許可證"] == "1003"
        assert hit["_ratio"] == 1.0

    def test_fullwidth_digits(self, lookup):
        hit = lookup.best_by_address("台北市中山區中山北路一段１０號３樓")
        assert hit is not None and hit["許可證"] == "1001"

    def test_punctuation_and_spaces_ignored(self, lookup):
        hit = lookup.best_by_address("台北市 中山區 中山北路一段 10 號 3 樓")
        assert hit is not None and hit["許可證"] == "1001"


class TestNameAcrossVariants:
    """名稱通道同樣走折疊(效益小,但與地址共用一套規則)。"""

    def test_formal_tai_vs_common_tai(self, lookup):
        hit = lookup.best_by_name("台灣鴻運國際人力仲介有限公司")
        assert hit is not None and hit["許可證"] == "1001"
        assert hit["_ratio"] == 1.0

    def test_name_with_punctuation_noise(self, lookup):
        """_clean_agency_name 之後仍可能殘留符號,正規化要吸收。"""
        hit = lookup.best_by_name("台灣鴻運國際人力仲介(有限公司)")
        assert hit is not None and hit["許可證"] == "1001"


class TestStillConservative:
    """折疊不得讓反查變得敢亂猜——寧可回空,不填錯。"""

    def test_unrelated_address_returns_none(self, lookup):
        assert lookup.best_by_address("宜蘭縣羅東鎮公正路88號") is None

    def test_unrelated_name_returns_none(self, lookup):
        assert lookup.best_by_name("大東亞漁業機械股份有限公司") is None

    def test_too_short_returns_none(self, lookup):
        """過短的 query 易誤命中,一律不比。"""
        assert lookup.best_by_address("台北") is None

    def test_empty_returns_none(self, lookup):
        assert lookup.best_by_address("") is None
        assert lookup.best_by_name("") is None

    def test_thresholds_unchanged(self):
        """折疊會系統性推高相似度,但門檻刻意不動(誤配對門檻不敏感,見 ADR-0002)。"""
        assert ADDR_MATCH_MIN == 0.72
        assert NAME_MATCH_MIN == 0.78

    def test_threshold_is_honoured(self, lookup):
        """把門檻拉到 1.0 時,只有完全相同才可命中。"""
        assert lookup.best_by_address(
            "台北市中山區中山北路一段10號4樓", min_ratio=1.0) is None


class TestInactiveExcluded:
    """已廢止/終止營業者不進反查索引。"""

    def test_revoked_not_matched(self):
        roster = [_rec("2001", "已廢止人力仲介有限公司",
                       "臺南市東區前鋒路100號", "06-2001-2001",
                       revoked="1100101")]
        lk = PermitLookup(roster)
        assert lk.best_by_address("台南市東區前鋒路100號") is None
        assert lk.best_by_name("已廢止人力仲介有限公司") is None
