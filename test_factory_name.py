# -*- coding: utf-8 -*-
"""工廠類英文雇主名:優先取含公司字樣的行(#37,ADR-0010)。

工廠類的 Nama Perusahaan 被 Vision 拆成好幾行,「第一個像英文名的行」先中了別的
字串 —— 30770 的英文名抓成一個編號,真正的公司英文名排在後面。知道是工廠類之後,
英文名優先取含公司字樣(CO./LTD/CORP/INDUSTR…/ENTERPRISE)、不含數字的行;
找不到才退回現行規則。素材全部捏造,形態照 30770。
"""
import pytest

from employer_extract import extract_employer_fields

_COMPANY_EN = "DA MING ENTERPRISE CO.,LTD. (WANG DA MING)."


def _fields(title, name_lines):
    return extract_employer_fields("\n".join([
        title,
        "MAJIKAN DENGAN PEKERJA",
        "雇主名稱(以下簡稱為甲方): 大明實業有限公司(王大明)",
        "Perusahaan:",
        "ABC-1234",                      # 拆行標籤後先出現的編號(30770 的形態)
        "SEKTOR",
        "FORMAL",
        "Nama",
        *name_lines,
        "地址:",
        "高雄市左營區華夏路100號3樓",
        "Alamat :",
        "NO.100, HUAXIA RD., ZUOYING DIST., KAOHSIUNG CITY, TAIWAN",
        "SELANJUTNYA DISEBUT PIHAK PERTAMA",
    ]))


def test_factory_prefers_the_line_with_company_words():
    f = _fields("勞動契約 工廠類", [_COMPANY_EN])
    assert f["雇主名稱_英"] == _COMPANY_EN


@pytest.mark.parametrize("en", [               # 每例只含一種字樣
    "DA MING CO. (WANG DA MING)",
    "DA MING LTD. (WANG DA MING)",
    "DA MING CORP. (WANG DA MING)",
    "DA MING CORPORATION (WANG DA MING)",
    "DA MING LIMITED (WANG DA MING)",
    "DA MING INC. (WANG DA MING)",
    "DA MING COMPANY (WANG DA MING)",
    "DA MING ENTERPRISE (WANG DA MING)",
    "DA MING METAL INDUSTRIAL (WANG DA MING)",
    "DA MING INDUSTRY (WANG DA MING)",
])
def test_each_company_word_counts(en):
    assert _fields("勞動契約 工廠類", [en])["雇主名稱_英"] == en


def test_factory_without_company_words_falls_back_to_current_rule():
    f = _fields("勞動契約 工廠類", ["DA MING (WANG DA MING)"])
    assert f["雇主名稱_英"] == "ABC-1234"


def test_company_line_with_digits_is_not_taken():
    """含數字的是地址或編號,不是公司名(地址一定有門牌號)。"""
    f = _fields("勞動契約 工廠類", ["NO.5, INDUSTRIAL RD., CO. (WANG DA MING)"])
    assert f["雇主名稱_英"] == "ABC-1234"


def test_family_template_unaffected():
    f = _fields("勞動契約 監護工", [_COMPANY_EN])
    assert f["雇主名稱_英"] == "ABC-1234"
