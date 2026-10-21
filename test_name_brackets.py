# -*- coding: utf-8 -*-
"""中文雇主名結尾的成對括號不是標籤殘骸(#35)。

工廠類、機構類的雇主名稱寫成「名稱(負責人)」。取中文名時清標籤殘留符號那一步
連名字結尾的右括號一起剝掉,接著括號補齊又把它補回來 —— 於是判成「補出來的」,
掛上 `H:中文名括號不成對,已補齊`,一條假疑慮(工廠類素材 5 份中 4 份)。
家庭類的人名沒有括號,所以一直沒浮現。

修法只剝標籤殘骸:空括號、不成對的那一邊;成對的括號屬於名字。
素材全部捏造(不貼個資)。
"""
import pytest

from employer_extract import extract_employer_fields

_TAIL = ["Nama", "Perusahaan :", "SELANJUTNYA DISEBUT PIHAK PERTAMA"]


def _name(first_line):
    f = extract_employer_fields("\n".join(["勞動契約 工廠類", first_line] + _TAIL))
    return f["雇主名稱_中"], "；".join(d.fact for d in f["疑慮"] if d.field == "雇主名稱_中")


@pytest.mark.parametrize("line, name", [
    ("雇主名稱(以下簡稱為甲方): 大明金屬股份有限公司(王大明)", "大明金屬股份有限公司(王大明)"),
    ("雇主名稱（以下簡稱為甲方）：大明金屬股份有限公司（王大明）", "大明金屬股份有限公司（王大明）"),
])
def test_balanced_trailing_bracket_is_kept_and_not_flagged(line, name):
    got, doubt = _name(line)
    assert got == name
    assert "括號" not in doubt


def test_really_missing_bracket_still_balanced_and_flagged():
    got, doubt = _name("雇主名稱(以下簡稱為甲方): 大明金屬股份有限公司(王大明")
    assert got == "大明金屬股份有限公司(王大明)"
    assert "括號不成對" in doubt


@pytest.mark.parametrize("line", [
    "甲方名稱 (以下簡稱為甲方) 王小明",      # 標籤剝掉後剩空括號
    "甲方名稱(以下簡稱為甲方 王小明",         # 標籤的右括號被 OCR 吃掉,剩左半
    "甲方名稱 以下簡稱為甲方) 王小明",        # 剩右半
    "甲方名稱 (以下簡稱為甲方: 王小明)",      # 標籤的右括號落在值的尾端
])
def test_label_residue_still_removed(line):
    got, doubt = _name(line)
    assert got == "王小明"
    assert doubt == "" or "括號" not in doubt


@pytest.mark.parametrize("line", [
    "甲方名稱(以下簡稱為甲方): (王小明)",
    "甲方名稱(以下簡稱為甲方): （王小明）",
])
def test_value_wrapped_in_one_pair_is_unwrapped(line):
    """整個值被一對括號包住 → 那對括號是版面的,不是名字的(與舊做法一致)。"""
    got, doubt = _name(line)
    assert got == "王小明"
    assert "括號" not in doubt


def test_brackets_at_both_ends_but_not_one_pair_are_kept():
    """頭尾都是括號、卻是兩對(「(股)…(負責人)」)→ 不是被包住,一個都不剝。"""
    got, doubt = _name("雇主名稱(以下簡稱為甲方): (股)大明金屬公司(王大明)")
    assert got == "(股)大明金屬公司(王大明)"
    assert "括號" not in doubt
