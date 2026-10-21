# -*- coding: utf-8 -*-
"""機構類(養護機構/醫院)契約的雇主欄位(#28,33703 教訓)。

#27 讓 33703 選對了契約頁,但雇主欄位照家庭類的假設在抽:
  - 機構全名很長,Vision 斷成兩行「…長照機構(負責人姓」+「名)」→ 只取第一行,
    括號補齊再補出一個假的「)」
  - 機構英文名含「County/City」→ 被當成地址行,英文名只剩下一行的「(負責人拼音)」,
    英文地址反而拿到機構英文名
  - 機構不是人,「中文字數 vs 英譯音節數」的檢查必然不符 → 假疑慮
電話與地址的位置雖比家庭類低,現行 EMPLOYER_ROI(上緣 35%)的 Vision 全文仍讀得到
電話(Nomor Telepon 那行被切半、讀壞,但 PHONE 不吃它),ROI 不用改。

以下用捏造的機構資料重現 33703 的 Vision 序列化形態(不貼個資)。
"""
import pytest

from employer_extract import extract_employer_fields
from employer_result import ExtractionStatus
from real_samples import checked_employer_fields, employer_text

# 33703 在現行 ROI 下的 Vision 序列化順序:中文名斷兩行;Nama 標籤之後先插進
# 「地址:」與中文地址,才輪到機構英文名(兩行,第二行是負責人拼音)
_INSTITUTION = [
    "台仲中文/英名稱:",
    "SOME AGENCY INC",
    "SEKTOR",
    "FORMAL",
    "勞動契約 養護機構/醫院類",
    "PERJANJIAN KERJA ANTARA",
    "MAJIKAN DENGAN",
    "PERAWAT DI PANTI JOMPO/RUMAH SAKIT",
    "(NURSING HOME)",
    "養護機構/醫院名稱 (以下簡稱甲方):",
    "安心長照社團法人附設新北市私立安心住宿長照機構(王",
    "大明)",
    "Nama Panti Jompo/Rumah Sakit :",
    "地址:",
    "新北市板橋區文化路一段100號(3樓)",
    "New Taipei City Private(An Xin: )Long-term Care",
    "(WANG DA MING)",
    "Alamat : 3F., No.100, Sec. 1, Wenhua Rd., Banqiao Dist., New Taipei City 220, Taiwan (R.O.C.)",
    "電話:",
    "02-29681234",
]

_CN_NAME = "安心長照社團法人附設新北市私立安心住宿長照機構(王大明)"
_EN_NAME = "New Taipei City Private(An Xin: )Long-term Care (WANG DA MING)"
_EN_ADDR = ("3F., No.100, Sec. 1, Wenhua Rd., Banqiao Dist., "
            "New Taipei City 220, Taiwan (R.O.C.)")


def _fields(lines):
    return extract_employer_fields("\n".join(lines))


def _replace(lines, old, new):
    assert old in lines
    return [new if l == old else l for l in lines]



def _name_facts(f):
    """中文名那一格的疑慮事實,串成一句方便比對。"""
    return "；".join(d.fact for d in f.doubts if d.field == "雇主名稱_中")

class TestInstitutionContract:

    def test_wrapped_chinese_name_joined(self):
        """斷成兩行的機構全名要接回來,不是「…(王)」。"""
        assert _fields(_INSTITUTION).values.雇主名稱_中 == _CN_NAME

    def test_english_name_with_county_is_a_name(self):
        """含 City 的機構英文名是名稱,連同下一行的負責人拼音;冒號不切。"""
        assert _fields(_INSTITUTION).values.雇主名稱_英 == _EN_NAME

    def test_english_address_is_the_alamat_line(self):
        assert _fields(_INSTITUTION).values.地址_英 == _EN_ADDR

    def test_chinese_address_and_phone(self):
        f = _fields(_INSTITUTION)
        assert f.values.地址_中 == "新北市板橋區文化路一段100號(3樓)"
        assert f.values.電話 == "02-29681234"

    def test_no_syllable_count_doubt_for_institution(self):
        """機構名的中文字數與英譯音節數本來就對不上,不是疑慮。"""
        assert _name_facts(_fields(_INSTITUTION)) == ""

    def test_title_line_is_not_english_name(self):
        """標題「(NURSING HOME)」落進區塊時不能頂上來當英文名。"""
        lines = _INSTITUTION[:10] + ["(NURSING HOME)"] + _INSTITUTION[10:]
        assert _fields(lines).values.雇主名稱_英 == _EN_NAME

    def test_institution_label_alone_anchors(self):
        """「以下簡稱甲方」被毀、「醫院名稱」完好時,照樣錨在甲方標籤、中文名照抽。"""
        lines = _replace(_INSTITUTION, "養護機構/醫院名稱 (以下簡稱甲方):",
                         "養護機構/醫院名稱 (以下統為用方):")
        assert _fields(lines).values.雇主名稱_中 == _CN_NAME

    def test_eaten_bracket_closed_on_its_own_line(self):
        """OCR 吃掉的右括號屬於機構名那一行,補在那行尾,不是整串尾端的「…))」。"""
        lines = _replace(_INSTITUTION, "New Taipei City Private(An Xin: )Long-term Care",
                         "New Taipei City Private(An Xin::JLong-term Care")
        assert (_fields(lines).values.雇主名稱_英
                == "New Taipei City Private(An Xin::JLong-term Care) (WANG DA MING)")

    def test_label_without_colon_is_not_a_name(self):
        """標籤行沒讀到冒號時,剝掉「以下簡稱甲方」後剩下的標籤字不能當名字。"""
        lines = _replace(_INSTITUTION, "養護機構/醫院名稱 (以下簡稱甲方):",
                         "養護機構/醫院名稱 (以下簡稱甲方)")
        assert _fields(lines).values.雇主名稱_中 == _CN_NAME


class TestDamagedInstitutionLabel:
    """中文標籤被紅章毀掉 → 退「Nama Panti Jompo」,但從標籤那行起算、不往回退。

    家庭類退 Nama Majikan 時往回一行是因為中文姓名固定在它的前一行;機構類的
    中文名斷成兩行在它之上,往回一行只會拿到「大明)」—— 括號補齊後變成看起來
    正常的「(大明)」,是看不見的錯值。中文名寧可留空(L 欄會標「未擷取到」)。
    """

    # 「醫院名稱」「以下簡稱甲方」兩個中文標籤都不成立
    LINES = _replace(_INSTITUTION, "養護機構/醫院名稱 (以下簡稱甲方):",
                     "養護機木/醫阮名構 (以下統為用方):")

    def test_other_fields_recovered(self):
        f = _fields(self.LINES)
        assert f.status is ExtractionStatus.擷取成功
        assert f.values.雇主名稱_英 == _EN_NAME
        assert f.values.地址_英 == _EN_ADDR
        assert f.values.電話 == "02-29681234"

    def test_chinese_name_left_blank_not_guessed(self):
        f = _fields(self.LINES)
        assert f.values.雇主名稱_中 == ""
        assert "未擷取到中文名" in _name_facts(f)


class TestUnjoinedWrapIsFlagged:
    """斷行沒接上(後半截不在緊鄰的下一行)時,補齊的括號要在 L 欄講出來。

    Vision 逐欄序列化,後半截可能被別欄的行隔開;補齊後的「…機構(王)」看起來
    完全正常,機構名又不做字數檢查 —— 不標就是看不見的錯值。
    """

    LINES = [l for l in _INSTITUTION if l != "大明)"]
    LINES = LINES[:LINES.index("Nama Panti Jompo/Rumah Sakit :") + 1] + ["大明)"] \
        + LINES[LINES.index("Nama Panti Jompo/Rumah Sakit :") + 1:]

    def test_doubt_says_bracket_was_added(self):
        f = _fields(self.LINES)
        assert f.values.雇主名稱_中.endswith("(王)")
        assert "括號不成對" in _name_facts(f)
        assert "括號不成對" in f.疑慮標示

    def test_joined_wrap_is_not_flagged(self):
        assert "括號" not in _name_facts(_fields(_INSTITUTION))


class TestFamilyAddressWithInstitutionWord:
    """家庭類地址行含 Welfare/Hospital 之類的字,仍是地址、不是機構英文名。"""

    LINES = [
        "甲方名稱 (以下簡稱為甲方): 王小明",
        "Nama Majikan: WANG XIAO MING",
        "地址:",
        "台中市北區福利路5號",
        "Alamat : No. 5, Welfare Rd., North Dist., Taichung City, Taiwan",
        "電話:",
        "0912345678",
        "SELANJUTNYA DISEBUT PIHAK PERTAMA",
    ]

    def test_address_line_stays_address(self):
        f = _fields(self.LINES)
        assert f.values.地址_英 == "No. 5, Welfare Rd., North Dist., Taichung City, Taiwan"
        assert f.values.雇主名稱_英 == "WANG XIAO MING"


class TestFamilyContractNameNotJoined:
    """家庭類的中文名後面接的是別的欄位,不能被接進名字裡。"""

    def test_name_without_open_bracket_untouched(self):
        lines = [
            "甲方名稱 (以下簡稱為甲方): 王小明",
            "陳)",
            "Nama Majikan: WANG XIAO MING",
            "SELANJUTNYA DISEBUT PIHAK PERTAMA",
        ]
        assert _fields(lines).values.雇主名稱_中 == "王小明"


def test_33703_replayed_from_saved_vision_text():
    """33703 的真實 Vision 全文(現行 ROI)離線重放,比對核對值;素材不在就 skip。

    核對值只收人看圖可確認的欄;疑慮是程式的陳述,交給快照守(test_snapshots)。
    """
    f = extract_employer_fields(employer_text("33703"))
    checked = checked_employer_fields("33703")
    if not checked:
        pytest.skip("33703 沒有雇主欄位核對值")
    for k, v in checked.items():
        assert getattr(f.values, k) == v, k
