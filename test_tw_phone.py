# -*- coding: utf-8 -*-
"""tw_phone:從一行抓台灣電話、轉電話標準形(CONTEXT.md;ADR-0011)。

抓出來的就是標準形 —— 純數字、國內前導 0。同一支號碼不論 OCR 寫成
連字號、空白、括號區碼或 +886,在系統裡只有一種樣子。
"""
import pytest

from tw_phone import PHONE_MIN_LEN, display_form, phones_in, standard_form


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


class TestDisplayForm:
    """電話顯示形(#49、ADR-0011 追加):依數位部國內有效號碼指配表加連字號,只用在 O 欄。
    手機 4-3-3;市話只在區碼後加一個連字號;不符區碼表 → 退回標準形。"""

    @pytest.mark.parametrize("std,expect", [
        ("0227276999", "02-27276999"),     # 臺北(10 碼)
        ("035310852", "03-5310852"),       # 新竹
        ("037985657", "037-985657"),       # 苗栗:037 最長前綴,不切成 03-7985657
        ("0423263526", "04-23263526"),     # 臺中 10 碼
        ("048345561", "04-8345561"),       # 彰化 9 碼
        ("0492345678", "049-2345678"),     # 南投
        ("055575505", "05-5575505"),       # 雲林
        ("069219899", "06-9219899"),       # 澎湖
        ("073437621", "07-3437621"),       # 高雄
        ("087654321", "08-7654321"),       # 屏東
        ("082312345", "082-312345"),       # 金門
        ("082661234", "0826-61234"),       # 烏坵:0826 最長前綴,不切成 082-661234
        ("083621234", "0836-21234"),       # 馬祖
        ("089312345", "089-312345"),       # 臺東
    ])
    def test_landline(self, std, expect):
        assert display_form(std) == expect

    @pytest.mark.parametrize("std,expect", [
        ("0920875667", "0920-875-667"),
        ("0912345678", "0912-345-678"),
        ("0981234567", "0981-234-567"),
        ("0851234567", "0851-234-567"),    # 085、086 也是手機
        ("0861234567", "0861-234-567"),
    ])
    def test_mobile_4_3_3(self, std, expect):
        assert display_form(std) == expect

    @pytest.mark.parametrize("std", [
        "0800123456",      # 免付費:080 不在表上(屏東首碼只有 4、7、8)
        "080012345",       # 9 碼的 080:長度合屏東,首碼 0 不合
        "02272769",        # 臺北少碼
        "04234567890",     # 臺中多碼
        "042345678",       # 臺中首碼 2 卻只有 9 碼(彰化才是 9 碼)
        "0483455610",      # 彰化首碼 8 卻有 10 碼
        "037185657",       # 苗栗首碼不含 1
        "091234567",       # 手機少一碼
        "",                # 空字串
    ])
    def test_not_in_table_falls_back_to_standard_form(self, std):
        assert display_form(std) == std

    def test_accepts_any_writing_by_way_of_standard_form(self):
        """呼叫端給的若不是標準形,也先轉標準形再查表 —— 退回時寫的也是標準形。"""
        assert display_form("03-7985657") == "037-985657"
        assert display_form("+886 920 875 667") == "0920-875-667"
        assert display_form("02-1234") == "021234"

    def test_display_form_round_trips_to_the_same_standard_form(self):
        for std in ("0227276999", "037985657", "0920875667", "082661234"):
            assert standard_form(display_form(std)) == std
