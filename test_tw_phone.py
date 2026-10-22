# -*- coding: utf-8 -*-
"""tw_phone:從一行抓台灣電話、轉電話標準形(CONTEXT.md;ADR-0011)。

抓出來的就是標準形 —— 純數字、國內前導 0。同一支號碼不論 OCR 寫成
連字號、空白、括號區碼或 +886,在系統裡只有一種樣子。
"""
import pytest

from tw_phone import PHONE_MIN_LEN, phones_in, standard_form


class TestStandardForm:

    @pytest.mark.parametrize("raw,expect", [
        ("0920-875-667", "0920875667"),        # 連字號
        ("02 2727 6999", "0227276999"),        # 空白
        ("(04)7775-863", "047775863"),         # 括號區碼
        ("+886 2 2727 6999", "0227276999"),    # 國際格式
        ("886227276999", "0227276999"),
        ("+886-02-2727-6999", "0227276999"),   # 國碼後又多寫前導 0
        ("0919889969", "0919889969"),          # 已是標準形
        ("", ""),
        (None, ""),
    ])
    def test_standard_form(self, raw, expect):
        assert standard_form(raw) == expect


class TestPhonesIn:
    """抓出來的就是標準形。"""

    @pytest.mark.parametrize("line,expect", [
        ("電話:0920-875-667", ["0920875667"]),
        ("電話: 02 2727 6999", ["0227276999"]),
        ("電話:(04)7775-863", ["047775863"]),
        ("電話:(02) 2371-7608", ["0223717608"]),
        ("電話: +886 2 2727 6999", ["0227276999"]),
        ("+886 912 345 678", ["0912345678"]),
        ("TEL +886227276999", ["0227276999"]),
        ("Nomor Telepon:0920875667", ["0920875667"]),
    ])
    def test_phone_in_standard_form(self, line, expect):
        assert phones_in(line) == expect

    def test_same_number_matched_twice_in_one_line_counts_once(self):
        """+886-02-… 同一段字被國內與國際兩條規則都抓到 —— 是同一次出現,不是兩次。"""
        assert phones_in("+886-02-2727-6999") == ["0227276999"]

    def test_two_numbers_in_one_line_both_kept_in_order(self):
        assert phones_in("0920-875-667 / 03-7985657") == ["0920875667", "037985657"]

    @pytest.mark.parametrize("line", [
        "統一編號 12345678",            # 統編 8 碼
        "統一編號 88612345",            # 886 開頭的統編不當國際格式
        "放款金額 0075-0010",           # 8 碼
        "帳號 0123-4567-8901-2345",     # 過長
        "",
    ])
    def test_not_a_phone(self, line):
        assert phones_in(line) == []


def test_min_len_is_shorter_than_any_extracted_phone():
    """抓得到的台灣號 9~10 碼;PHONE_MIN_LEN 是名冊殘段與前綴反查的門檻,須低於它。"""
    assert PHONE_MIN_LEN == 8
    assert all(len(p) > PHONE_MIN_LEN for p in phones_in("02-2727-6999 037-985657"))
