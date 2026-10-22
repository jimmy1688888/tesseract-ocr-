# -*- coding: utf-8 -*-
"""+886 國際格式電話的抽取與反查正規化(問題一:32324 台仲電話以 +886 顯示)。

契約次頁台仲電話有時印成國際格式 +886(國碼後省略國內前導 0),
名冊存的是國內格式 02-2727-6999。兩端都轉電話標準形(tw_phone,ADR-0011)才能反查命中:
  - 抽取端 tw_phone.phones_in:認 +886 格式並補回前導 0
  - 反查端 tw_phone.standard_form:886 前綴還原為 0(即使抽取端漏補也救得回)
"""
from tw_phone import phones_in, standard_form


class TestPhonesInIntl:
    """phones_in:+886 國際格式 → 國內「0…」;國內原格式不受影響。"""

    def test_spaced_landline(self):
        assert "0227276999" in phones_in("電話: +886 2 2727 6999")

    def test_hyphen_landline(self):
        assert "0227276999" in phones_in("+886-2-2727-6999")

    def test_no_separator_landline(self):
        assert "0227276999" in phones_in("TEL +886227276999")

    def test_mobile(self):
        assert "0912345678" in phones_in("+886 912 345 678")

    def test_redundant_leading_zero(self):
        """+886 後又多寫國內前導 0(+886-02-…)也要還原成單一 0。"""
        assert "0227276999" in phones_in("+886-02-2727-6999")

    def test_domestic_paren_still_works(self):
        """既有括號區碼格式不受新增邏輯影響。"""
        assert "047775863" in phones_in("(04)7775-863")

    def test_no_886_false_positive(self):
        """純數字串(如統編)不應被 886 邏輯誤抓。"""
        assert phones_in("統一編號 88612345") == []


class TestPhonesInDomesticSeparators:
    """phones_in:國內號號段間可夾連字號/空白(涵蓋雙連字號 02-2727-6999),抓出來即標準形。"""

    def test_double_hyphen_landline(self):
        assert phones_in("電話: 02-2727-6999") == ["0227276999"]

    def test_double_hyphen_mobile(self):
        assert phones_in("0912-345-678") == ["0912345678"]

    def test_double_hyphen_central_landline(self):
        assert phones_in("04-7775-8631") == ["0477758631"]

    def test_space_separated(self):
        assert phones_in("02 2727 6999") == ["0227276999"]

    def test_single_hyphen_still_works(self):
        assert phones_in("04-77758631") == ["0477758631"]

    def test_no_separator_still_works(self):
        assert phones_in("0227276999") == ["0227276999"]

    def test_reject_tax_id(self):
        """統編(8碼、不 0 開頭)不應被當電話。"""
        assert phones_in("統一編號 12345678") == []

    def test_reject_loan_amount(self):
        """放款編號(0075-0010,8碼)不應被當電話。"""
        assert phones_in("放款金額 0075-0010") == []

    def test_reject_long_account(self):
        """過長帳號不應被當電話。"""
        assert phones_in("帳號 0123-4567-8901-2345") == []


class TestStandardFormIntl:
    """standard_form:去分隔 + 886 前綴還原為國內前導 0。"""

    def test_domestic_unchanged(self):
        assert standard_form("02-2727-6999") == "0227276999"

    def test_intl_spaced(self):
        assert standard_form("+886 2 2727 6999") == "0227276999"

    def test_intl_digits_only(self):
        assert standard_form("886227276999") == "0227276999"

    def test_intl_redundant_zero(self):
        assert standard_form("+886-02-2727-6999") == "0227276999"

    def test_mobile_domestic_and_intl_agree(self):
        assert standard_form("0912-345-678") == standard_form("+886 912 345 678") == "0912345678"


def test_intl_extraction_and_lookup_consistent():
    """端到端一致性:國際格式抽取後,反查正規化結果 == 名冊國內號正規化結果。"""
    intl = phones_in("電話 +886 2 2727 6999")[0]
    assert standard_form(intl) == standard_form("02-2727-6999")
