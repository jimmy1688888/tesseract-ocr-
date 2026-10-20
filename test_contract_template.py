# -*- coding: utf-8 -*-
"""契約樣板:整份由文件判一次(#36,ADR-0010)。

判定順序:中文標題「勞動契約 ○○」→ 印尼文標籤單字 → SEKTOR INFORMAL → 認不出。
依 36 份 Vision 全文實測:標題 36/36 讀得到;工廠類的 Nama Perusahaan 5 份全被拆行,
所以標籤只認單字;只有 FORMAL 時機構類與工廠類分不開,不推定。
素材全部捏造(不貼個資)。
"""
import pytest

import real_samples as rs
from contract_template import Template, contract_template
from employer_extract import extract_employer_fields


def _t(*lines):
    return contract_template("\n".join(lines))


class TestTitleFirst:
    @pytest.mark.parametrize("title, want", [
        ("勞動契約 監護工", Template.家庭類),
        ("勞動契約 家庭幫傭", Template.家庭類),
        ("業勞動契約 監護工", Template.家庭類),          # 標題前黏了別欄的字(33704)
        ("勞動契約 養護機構/醫院類", Template.機構類),
        ("勞動契約 醫院類", Template.機構類),            # 「養護機構/」被章蓋掉
        ("勞動契約 工廠類", Template.工廠類),
    ])
    def test_title_names_the_template(self, title, want):
        assert _t("PERJANJIAN KERJA ANTARA", title, "MAJIKAN DENGAN PEKERJA") == want

    def test_title_wins_over_contradicting_label(self):
        """矛盾只可能是 OCR 讀錯,整行印刷大字的標題最可靠。"""
        assert _t("勞動契約 工廠類", "Nama Majikan:", "SEKTOR INFORMAL") == Template.工廠類


class TestLabelWhenTitleLost:
    @pytest.mark.parametrize("lines, want", [
        (["Nama", "Perusahaan :"], Template.工廠類),            # 被 Vision 拆行
        (["Nama Panti Jompo/Rumah Sakit :"], Template.機構類),
        (["Nama Majikan:"], Template.家庭類),
    ])
    def test_single_word_label(self, lines, want):
        assert _t("PERJANJIAN KERJA ANTARA", *lines, "SEKTOR", "FORMAL") == want

    def test_title_word_majikan_dengan_is_not_the_label(self):
        """「MAJIKAN DENGAN PEKERJA」是每一類都有的標題字,不是雇主標籤。"""
        assert _t("MAJIKAN DENGAN PEKERJA", "SEKTOR", "FORMAL") == Template.認不出

    def test_specific_label_beats_majikan(self):
        assert _t("Nama Majikan:", "Perusahaan :") == Template.工廠類


class TestSektorLast:
    def test_informal_is_family(self):
        assert _t("SEKTOR", "INFORMAL", "MAJIKAN DENGAN") == Template.家庭類

    def test_formal_alone_is_not_guessed(self):
        """機構類與工廠類都是 FORMAL;只看到 FORMAL 不推定。"""
        assert _t("SEKTOR", "FORMAL", "MAJIKAN DENGAN") == Template.認不出

    def test_nothing_at_all(self):
        assert _t("甲方名稱: 王小明") == Template.認不出
        assert contract_template("") == Template.認不出


class TestOrganisation:
    def test_institution_and_factory_are_organisations(self):
        assert Template.機構類.is_organisation and Template.工廠類.is_organisation
        assert not Template.家庭類.is_organisation and not Template.認不出.is_organisation

    def test_value_is_the_chinese_name(self):
        """核對值存的是中文類別名。"""
        assert Template.工廠類 == "工廠類"


def _factory(*, title="勞動契約 工廠類", name_en="DA MING METAL CORPORATION (WANG, DA-MING)"):
    lines = [title] if title else []
    return extract_employer_fields("\n".join(lines + [
        "MAJIKAN DENGAN PEKERJA",
        "雇主名稱(以下簡稱為甲方): 大明金屬股份有限公司(王大明)",
        "Nama",
        "Perusahaan :",
        name_en,
        "地址:",
        "高雄市左營區華夏路100號3樓",
        "Alamat : 3F., No.100, Huaxia Rd., Zuoying Dist., Kaohsiung City, Taiwan R.O.C.",
        "Nomor Telepon :",
        "07-1234567",
        "SELANJUTNYA DISEBUT PIHAK PERTAMA",
    ]))


class TestExtractionFollowsTemplate:
    """擷取看樣板,不從英文名逐行猜(ADR-0010)。"""

    def test_template_is_recorded_once(self):
        assert _factory()["契約樣板"] == Template.工廠類

    def test_factory_skips_count_check_even_without_co_ltd(self):
        """30767:英文名是「…CORPORATION」,舊的 Co/Ltd 字樣認不得 → 假疑慮。"""
        f = _factory()
        assert "音節" not in f["疑慮標示"]

    def test_company_name_with_taiwan_is_not_the_english_address(self):
        """公司名含 Taiwan(EN_ADDR_KW 認得)也不會被當成英文地址。"""
        f = _factory(name_en="TAIWAN DA MING METAL CO., LTD. (WANG, DA-MING)")
        assert f["雇主名稱_英"] == "TAIWAN DA MING METAL CO., LTD. (WANG, DA-MING)"
        assert f["地址_英"].startswith("3F., No.100")

    def test_family_english_address_without_digits_is_still_an_address(self):
        """家庭類的英文行一律不是機構名:門牌被章蓋掉、沒有數字的地址行照樣是地址。"""
        f = extract_employer_fields("\n".join([
            "勞動契約 監護工", "甲方名稱 (以下簡稱為甲方): 王小明",
            "Nama Majikan: WANG XIAO MING", "地址:", "高雄市左營區華夏路100號3樓",
            "Alamat : Huaxia Rd., Zuoying Dist., Kaohsiung City, Taiwan",
            "SELANJUTNYA DISEBUT PIHAK PERTAMA"]))
        assert f["地址_英"] == "Huaxia Rd., Zuoying Dist., Kaohsiung City, Taiwan"

    def test_unrecognised_template_uses_family_rules_and_says_so(self):
        """認不出 → 照家庭類規則(公司名照人名數音節 → 看得見的假疑慮),並講明。"""
        lines = [
            "甲方名稱 (以下簡稱為甲方): 大明金屬股份有限公司(王大明)",
            "DA MING METAL CORPORATION (WANG, DA-MING)",
            "地址:",
            "高雄市左營區華夏路100號3樓",
            "SELANJUTNYA DISEBUT PIHAK PERTAMA",
        ]
        f = extract_employer_fields("\n".join(lines))
        assert f["契約樣板"] == Template.認不出
        assert f["疑慮標示"].startswith("H:契約樣板未辨識,以家庭類規則擷取")
        assert "音節" in f["疑慮標示"]

    def test_recognised_template_has_no_template_doubt(self):
        assert "契約樣板" not in _factory()["疑慮標示"]
        family = extract_employer_fields("\n".join([
            "勞動契約 監護工", "甲方名稱 (以下簡稱為甲方): 王小明",
            "Nama Majikan: WANG XIAO MING", "SELANJUTNYA DISEBUT PIHAK PERTAMA"]))
        assert family["樣板_疑慮"] == "" and "契約樣板" not in family["疑慮標示"]

    def test_failed_anchor_carries_unrecognised_template_key(self):
        """區塊錨定失敗的早退路徑,鍵與成功路徑一致。"""
        f = extract_employer_fields("勞動契約 工廠類")
        assert f["_note"] and f["契約樣板"] == Template.認不出 and f["樣板_疑慮"] == ""


@pytest.mark.parametrize("stem", rs.vision_stems())
def test_real_sample_matches_checked_template(stem):
    """有樣板核對值的素材,離線重放 Vision 全文比對;沒有就 skip。"""
    want = rs.checked_template(stem)
    if want is None:
        pytest.skip(f"{stem} 沒有契約樣板核對值")
    assert contract_template(rs.employer_text(stem)) == want
