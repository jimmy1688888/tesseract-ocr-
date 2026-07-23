# -*- coding: utf-8 -*-
"""+886 國際格式電話的抽取與反查正規化(問題一:32324 台仲電話以 +886 顯示)。

契約次頁台仲電話有時印成國際格式 +886(國碼後省略國內前導 0),
名冊存的是國內格式 02-2727-6999。兩端須把 +886 還原成國內「0…」才能反查命中:
  - 抽取端 employer_extract._phones_in:認 +886 格式並補回前導 0
  - 反查端 permit_lookup._norm_phone:886 前綴還原為 0(即使抽取端漏補也救得回)
"""
from employer_extract import _phones_in
from permit_lookup import _norm_phone


class TestPhonesInIntl:
    """_phones_in:+886 國際格式 → 國內「0…」;國內原格式不受影響。"""

    def test_spaced_landline(self):
        assert "0227276999" in _phones_in("電話: +886 2 2727 6999")

    def test_hyphen_landline(self):
        assert "0227276999" in _phones_in("+886-2-2727-6999")

    def test_no_separator_landline(self):
        assert "0227276999" in _phones_in("TEL +886227276999")

    def test_mobile(self):
        assert "0912345678" in _phones_in("+886 912 345 678")

    def test_redundant_leading_zero(self):
        """+886 後又多寫國內前導 0(+886-02-…)也要還原成單一 0。"""
        assert "0227276999" in _phones_in("+886-02-2727-6999")

    def test_domestic_paren_still_works(self):
        """既有括號區碼格式不受新增邏輯影響。"""
        assert "04-7775863" in _phones_in("(04)7775-863")

    def test_no_886_false_positive(self):
        """純數字串(如統編)不應被 886 邏輯誤抓。"""
        assert _phones_in("統一編號 88612345") == []


def _digits(phones):
    """把電話候選去分隔,便於不管輸出帶不帶連字號都能比對。"""
    return {"".join(c for c in p if c.isdigit()) for p in phones}


class TestPhonesInDomesticSeparators:
    """_phones_in:國內號號段間可夾連字號/空白(涵蓋雙連字號 02-2727-6999)。"""

    def test_double_hyphen_landline(self):
        assert "0227276999" in _digits(_phones_in("電話: 02-2727-6999"))

    def test_double_hyphen_mobile(self):
        assert "0912345678" in _digits(_phones_in("0912-345-678"))

    def test_double_hyphen_central_landline(self):
        assert "0477758631" in _digits(_phones_in("04-7775-8631"))

    def test_space_separated(self):
        assert "0227276999" in _digits(_phones_in("02 2727 6999"))

    def test_single_hyphen_still_works(self):
        assert "0477758631" in _digits(_phones_in("04-77758631"))

    def test_no_separator_still_works(self):
        assert "0227276999" in _digits(_phones_in("0227276999"))

    def test_reject_tax_id(self):
        """統編(8碼、不 0 開頭)不應被當電話。"""
        assert _phones_in("統一編號 12345678") == []

    def test_reject_loan_amount(self):
        """放款編號(0075-0010,8碼)不應被當電話。"""
        assert _phones_in("放款金額 0075-0010") == []

    def test_reject_long_account(self):
        """過長帳號不應被當電話。"""
        assert _phones_in("帳號 0123-4567-8901-2345") == []


class TestNormPhoneIntl:
    """_norm_phone:去分隔 + 886 前綴還原為國內前導 0。"""

    def test_domestic_unchanged(self):
        assert _norm_phone("02-2727-6999") == "0227276999"

    def test_intl_spaced(self):
        assert _norm_phone("+886 2 2727 6999") == "0227276999"

    def test_intl_digits_only(self):
        assert _norm_phone("886227276999") == "0227276999"

    def test_intl_redundant_zero(self):
        assert _norm_phone("+886-02-2727-6999") == "0227276999"

    def test_mobile_domestic_and_intl_agree(self):
        assert _norm_phone("0912-345-678") == _norm_phone("+886 912 345 678") == "0912345678"


def test_intl_extraction_and_lookup_consistent():
    """端到端一致性:國際格式抽取後,反查正規化結果 == 名冊國內號正規化結果。"""
    intl = _phones_in("電話 +886 2 2727 6999")[0]
    assert _norm_phone(intl) == _norm_phone("02-2727-6999")
