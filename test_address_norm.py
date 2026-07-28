# -*- coding: utf-8 -*-
"""異體折疊 address_db.fold_variants(原 _norm,因 permit_lookup 也要用而升為公開)。

台灣同一個地址有多種合法寫法,官方庫、勞動部名冊、契約 OCR 三方各用一套:
  - 縣市:官方地址庫與名冊機構地址慣用正體「臺」,契約印的多是「台」
  - 段號:名冊自身就國字/阿拉伯各半(實測 308 / 313 筆),契約慣用「一段」
  - 全形:Vision OCR 偶爾吐全形數字
不折疊則子字串精確比對必漏接、相似度比對憑空被扣分。見 docs/adr/0001。

fold_variants 是**比對用**中間字串,不可拿來輸出——TestNotForOutput 釘住這件事。
"""
import address_db
from address_db import fold_variants


class TestVariantChars:
    """異體字:臺/台、鍾/鐘 收斂成同一種。"""

    def test_tai_formal_to_common(self):
        assert fold_variants("臺北市") == "台北市"

    def test_tai_common_unchanged(self):
        assert fold_variants("台北市") == "台北市"

    def test_two_writings_agree(self):
        """兩種寫法折疊後必須相等——這是整個折疊存在的理由。"""
        assert fold_variants("臺中市") == fold_variants("台中市")

    def test_zhong_variant(self):
        """官方庫「鍾山新村」vs OCR/慣用「鐘山新村」。"""
        assert fold_variants("鍾山新村") == fold_variants("鐘山新村")


class TestSegmentNumber:
    """段號:國字一律轉阿拉伯,對上官方庫寫法。"""

    def test_cn_numeral(self):
        assert fold_variants("中山北路一段") == "中山北路1段"

    def test_arabic_unchanged(self):
        assert fold_variants("中山北路1段") == "中山北路1段"

    def test_two_writings_agree(self):
        assert fold_variants("辛亥路七段") == fold_variants("辛亥路7段")

    def test_ten(self):
        assert fold_variants("十段") == "10段"

    def test_teens(self):
        assert fold_variants("十二段") == "12段"

    def test_multiple_of_ten(self):
        assert fold_variants("三十段") == "30段"

    def test_not_a_segment_untouched(self):
        """「德南路201巷」的 201 不接『段』,不可被當段號處理。"""
        assert fold_variants("德南路201巷") == "德南路201巷"


class TestWidthAndSpace:
    """NFKC 全形→半形,以及去空白。"""

    def test_fullwidth_digits(self):
        assert fold_variants("１２３號") == "123號"

    def test_fullwidth_latin(self):
        assert fold_variants("ＡＢＣ") == "ABC"

    def test_strips_spaces(self):
        """Vision 逐詞序列化會在中文與數字交界插入斷詞空格(「名光街 38 號」)。"""
        assert fold_variants("名光街 38 號") == "名光街38號"

    def test_strips_newlines(self):
        assert fold_variants("台北市\n中山區") == "台北市中山區"


class TestEdges:

    def test_empty(self):
        assert fold_variants("") == ""

    def test_none_safe(self):
        assert fold_variants(None) == ""

    def test_idempotent(self):
        """折疊兩次要與折疊一次相同——permit_lookup 會在既已折疊的字串上再比對。"""
        once = fold_variants("臺北市中山北路一段１２號")
        assert fold_variants(once) == once

    def test_combined(self):
        assert fold_variants("臺北市 中山北路 一段 １２ 號") == "台北市中山北路1段12號"


class TestNotForOutput:
    """fold_variants 是比對用,normalize_address 才是輸出用——兩者不可混淆。

    README 明訂 J 欄標準地址須照官方庫原樣輸出(保留正體「臺」,此為使用者拍板)。
    若哪天有人把 fold_variants 拿去填 J 欄,下面這個測試會先失敗。
    """

    def test_normalize_address_keeps_formal_tai(self):
        r = address_db.normalize_address("台北市文山區辛亥路7段69巷15號3樓")
        if not r["matched"]:                      # 缺 data/AllData.json 時安靜跳過
            return
        assert "臺" in r["address_cn"], "標準地址輸出必須保留官方正體「臺」"

    def test_fold_variants_lowers_formal_tai(self):
        """對照組:折疊會把「臺」變「台」,所以它的輸出不是標準地址。"""
        assert "臺" not in fold_variants("臺北市")
