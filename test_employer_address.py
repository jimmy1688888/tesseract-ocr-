# -*- coding: utf-8 -*-
"""雇主中文地址:地址標籤優先 + 縣市中英不符疑慮(#34,ADR-0003 追加 2026-10-19)。

33704 是兩欄版面,Vision 逐欄序列化把仲介框的下半段(含仲介的中文地址)夾進
雇主區塊。中文地址取「區塊內第一個像地址的行」,於是先中仲介那行;門牌檢查要求
中英都有門牌才比,中文那行的門牌被切到下一行就沉默 —— J、K、N 欄寫進仲介資料,
L 欄一個字都沒有。

兩條互補,各自都要有效:
  挑對   雇主區塊內「地址」標籤後幾行內的中文地址優先,找不到退回原規則
  安全網 標準化後的中文縣市 vs 英文地址的官方縣市名,兩邊都確定且不同才標

素材全部捏造(不貼個資),版面形態照 33704。
"""
import pytest

import real_samples as rs
from employer_extract import extract_employer_fields
from tw_phone import standard_form

# 雇主在高雄,仲介在臺中;仲介地址被逐欄序列化夾在甲方標籤與雇主地址標籤之間,
# 門牌還被切到下一行(33704 的形態)。
_HEAD = [
    "甲方名稱 (以下簡稱為甲方): 王小明",
    "Nama Majikan:",
    "WANG XIAO MING",
    "勞動契約 監護工",
    "PERJANJIAN KERJA ANTARA",
    "臺中市西區民權路",               # ← 仲介的中文地址
    "88號5樓",
    "MAJIKAN DENGAN",
]
_EN_KAOHSIUNG = "3F., No.100, Huaxia Rd., Zuoying Dist., Kaohsiung City, Taiwan R.O.C."
_TAIL = [
    "電話:",
    "0912345678",
    "0912345678",
    "Nomor Telepon :",
    "SELANJUTNYA DISEBUT PIHAK PERTAMA",
]


def _fields(lines):
    return extract_employer_fields("\n".join(lines))


def _block(addr_lines):
    return _fields(_HEAD + addr_lines + _TAIL)


class TestAddressLabelFirst:
    """挑對:雇主區塊內地址標籤後的中文地址,勝過區塊內先出現的仲介地址。"""

    def test_label_next_line_wins_over_earlier_agency_address(self):
        f = _block(["地址:", "高雄市左營區華夏路100號3樓", "Alamat:", _EN_KAOHSIUNG])
        assert f.values.地址_中 == "高雄市左營區華夏路100號3樓"
        assert f.values.地址_中_標準 == "高雄市左營區華夏路100號3樓"
        assert f.values.郵遞區號 == "813"
        assert "縣市中英不符" not in f.疑慮標示

    def test_address_on_the_label_line_itself(self):
        f = _block(["地址:高雄市左營區華夏路100號3樓", "Alamat:", _EN_KAOHSIUNG])
        assert f.values.地址_中 == "高雄市左營區華夏路100號3樓"

    def test_address_a_few_lines_after_the_label(self):
        """標籤與地址之間夾了別欄的字(逐欄序列化),幾行內仍算標籤的值。"""
        f = _block(["地址:", "Perawat Orang Sakit (Care Giver)",
                    "高雄市左營區華夏路100號3樓", "Alamat:", _EN_KAOHSIUNG])
        assert f.values.地址_中 == "高雄市左營區華夏路100號3樓"

    def test_word_address_mid_line_is_not_a_label(self):
        """行中提到「地址」的不是標籤,不能讓它後面的仲介地址搶先。"""
        f = _fields(["甲方名稱 (以下簡稱為甲方): 王小明",
                     "通訊地址同下",
                     "臺中市西區民權路88號5樓",
                     "地址:", "高雄市左營區華夏路100號3樓", "Alamat:", _EN_KAOHSIUNG,
                     "SELANJUTNYA DISEBUT PIHAK PERTAMA"])
        assert f.values.地址_中 == "高雄市左營區華夏路100號3樓"

    def test_label_with_no_address_nearby_falls_back(self):
        """標籤與地址被拆開(32510、32518、33701 的形態)→ 退回「第一個像地址的行」。

        這裡沒有夾仲介地址,退回的第一行就是雇主的。
        """
        lines = [
            "甲方名稱 (以下簡稱為甲方): 王小明",
            "地址:",
            "Alamat:",
            "電話:",
            "Nomor Telepon :",
            "Nama Majikan: WANG XIAO MING",
            "高雄市左營區華夏路100號3樓",
            _EN_KAOHSIUNG,
            "0912345678",
            "SELANJUTNYA DISEBUT PIHAK PERTAMA",
        ]
        assert _fields(lines).values.地址_中 == "高雄市左營區華夏路100號3樓"

    def test_agency_label_outside_the_block_is_ignored(self):
        """仲介框也有地址標籤;只看雇主區塊內的標籤。"""
        lines = [
            "台灣仲介公司: 某某人力仲介有限公司",
            "地址:",
            "臺中市西區民權路88號5樓",
            "甲方名稱 (以下簡稱為甲方): 王小明",
            "Nama Majikan: WANG XIAO MING",
            "高雄市左營區華夏路100號3樓",
            _EN_KAOHSIUNG,
            "SELANJUTNYA DISEBUT PIHAK PERTAMA",
        ]
        assert _fields(lines).values.地址_中 == "高雄市左營區華夏路100號3樓"


class TestCityMismatchDoubt:
    """安全網:中文縣市(標準化後)與英文官方縣市名兩邊都確定且不同 → 掛疑慮。"""

    def test_label_missing_agency_address_is_flagged(self):
        """挑對沒救到(標籤被毀)、只剩安全網時,33704 型的資料要被標出來。"""
        f = _block(["高雄市左營區華夏路100號3樓", "Alamat:", _EN_KAOHSIUNG])
        assert f.values.地址_中.startswith("臺中市")          # 前提:確實挑到仲介那行
        assert "J:縣市中英不符(臺中市／Kaohsiung City)" in f.疑慮標示

    def test_same_city_not_flagged(self):
        f = _block(["地址:", "高雄市左營區華夏路100號3樓", "Alamat:", _EN_KAOHSIUNG])
        assert "縣市中英不符" not in f.疑慮標示

    def test_chinese_city_unmatched_not_flagged_twice(self):
        """中文縣市比不到 → 已有「縣市未比對到官方資料庫」那條,不重複標縣市中英不符。"""
        lines = ["甲方名稱 (以下簡稱為甲方): 王小明",
                 "地址:", "XX@@市北區某某路29號7樓",
                 "Alamat:", "7F., No.29, Mouxx Rd., North Dist., Xxyyzz, Taiwan",
                 "SELANJUTNYA DISEBUT PIHAK PERTAMA"]
        f = _fields(lines)
        assert "縣市未比對到" in f.疑慮標示
        assert "縣市中英不符" not in f.疑慮標示

    def test_english_matches_no_official_city_not_flagged(self):
        """英文行讀壞、比不到任何官方縣市 → 不標(寧可漏報,不用模糊比對製造誤報)。"""
        f = _block(["臺中市西區民權路88號5樓", "Alamat:",
                    "3F., No.100, Huaxia Rd., Zuoying Dist., Kaohslung Clty, Taiwan R.O.C."])
        assert "縣市中英不符" not in f.疑慮標示

    @pytest.mark.parametrize("cn, en", [
        ("臺北市大安區復興南路一段10號", "No.10, Sec. 1, Fuxing S. Rd., Da'an Dist., Taipei City"),
        ("新北市板橋區文化路一段10號", "No.10, Sec. 1, Wenhua Rd., Banqiao Dist., New Taipei City"),
    ])
    def test_taipei_and_new_taipei_not_confused(self, cn, en):
        """取最長命中:New Taipei City 不會被當成 Taipei City,反之亦然。"""
        f = _block(["地址:", cn, "Alamat:", en])
        assert "縣市中英不符" not in f.疑慮標示

    def test_taipei_vs_new_taipei_is_flagged(self):
        f = _block(["地址:", "臺北市大安區復興南路一段10號", "Alamat:",
                    "No.10, Sec. 1, Wenhua Rd., Banqiao Dist., New Taipei City"])
        assert "J:縣市中英不符(臺北市／New Taipei City)" in f.疑慮標示

    def test_fuzzy_chinese_city_uses_standardized_name(self):
        """OCR 把縣市讀歪(桃園市 → 成園市)時比的是標準化後的縣市,不誤報。"""
        f = _block(["地址:", "成園市中壢區中正路100號", "Alamat:",
                    "No.100, Zhongzheng Rd., Zhongli Dist., Taoyuan City"])
        assert f.values.地址_中 == "成園市中壢區中正路100號"     # 前提:OCR 原文確實讀歪
        assert "縣市中英不符" not in f.疑慮標示

    def test_wording_states_facts_only(self):
        f = _block(["高雄市左營區華夏路100號3樓", "Alamat:", _EN_KAOHSIUNG])
        d = [p for p in f.疑慮標示.split("；") if "縣市中英不符" in p][0]
        assert "可能" not in d and "請" not in d and "應" not in d


@pytest.mark.parametrize("stem", rs.vision_stems())
def test_replayed_vision_text_matches_checked_values(stem):
    """每份有雇主欄位核對值的素材,離線重放 Vision 全文後逐欄比對;沒核對值就 skip。

    33704 的地址核對值由使用者對著契約圖確認後記入(#34)。
    """
    checked = rs.checked_employer_fields(stem)
    if not checked:
        pytest.skip(f"{stem} 沒有雇主欄位核對值")
    f = extract_employer_fields(rs.employer_text(stem))
    for k, v in checked.items():
        if k == "電話":              # 核對值照人看到的寫(06-9219899),O 欄是電話標準形
            v = standard_form(v)
        assert getattr(f.values, k) == v, k
