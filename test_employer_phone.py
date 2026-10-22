# -*- coding: utf-8 -*-
"""雇主電話:從雇主區塊(seg)挑出 O 欄那一支(#8)。

電話這一刀只吃雇主區塊的行、吐一個電話標準形字串,直接餵 seg 測試,不必繞過錨定。
三個實測教訓各一組:
  32098        區塊內的雇主電話讀壞時寧可留空,不退回區塊外(仲介電話會勝出)
  32262        農業契約的地號長得像電話,標籤行上的號碼優先
  32447/32417  同行「電話:…,傳真:…」先切掉傳真,否則傳真靠標籤行優先勝出
"""
import pytest

from employer_extract import locate_employer_block, pick_employer_phone


class TestNoFallbackOutsideBlock:
    """32098:雇主電話被格式/紅章毀掉時,全文統計的 fallback 會把仲介電話誤當雇主電話。
    電話只看雇主區塊 —— 函式只收 seg,區塊外的號碼根本進不來。"""

    def test_32098_damaged_employer_phone_stays_empty(self):
        seg = ["甲方名稱 (以下簡稱為甲方): 王小明",
               "電話:09376",                  # 讀壞:位數不足
               "Nomor Telepon:"]
        assert pick_employer_phone(seg) == ""

    def test_32098_intact_agency_phone_above_block_never_wins(self):
        """仲介框的電話完好、雇主電話讀壞:定位區塊再挑電話 → 留空,不是仲介電話。"""
        lines = ["台中中文/英名稱: 苙與人力仲介有限公司",
                 "電話:03-5310852",
                 "電話:03-5310852",
                 "甲方名稱 (以下簡稱為甲方): 王小明",
                 "電話:09376",
                 "SELANJUTNYA DISEBUT PIHAK PERTAMA"]
        block = locate_employer_block(lines)
        assert pick_employer_phone(lines[block.start:block.end]) == ""

    def test_empty_block_has_no_phone(self):
        assert pick_employer_phone([]) == ""


class TestLabelledLineWins:
    """32262:農業契約地址含地號,被電話規則誤抓,且中英地址各一次 → 出現兩次。
    電話/Nomor Telepon 標籤行上的號碼優先;標籤行取不到才退回全區塊。"""

    SEG_32262 = [
        "甲方名稱 (以下簡稱為甲方): 林大山",
        "地址:雲林縣四湖鄉國中段 0075-00100 地號",
        "Alamat: Guozhong Sec. 0075-00100, Sihu Township, Yunlin County",
        "Nomor Telepon: 05-5880133",
    ]

    def test_32262_land_number_not_taken_as_phone(self):
        assert pick_employer_phone(self.SEG_32262) == "055880133"

    def test_falls_back_to_whole_block_without_labelled_line(self):
        """標籤與號碼被逐欄序列化拆開時(32417 的「Nomor Telepon:」+ 下一行號碼)。"""
        seg = ["電話:", "Nomor Telepon:", "0932237611", "0932237611"]
        assert pick_employer_phone(seg) == "0932237611"


class TestFaxCutBeforeCounting:
    """32447/32417:同一行寫成「電話:03-5310852,傳真:03-5277128」,整行是電話標籤行。
    不切傳真後段,傳真號會與電話一起進統計,還靠標籤行優先勝出。"""

    def test_32417_damaged_phone_does_not_fall_back_to_fax(self):
        """電話讀壞、傳真完好 → 切掉後段只剩讀壞的 093766 → 留空,不拿傳真充數。"""
        assert pick_employer_phone(["電話:093766)傳真:07-2692232", "Nomor Telepon:"]) == ""

    def test_32447_phone_before_fax_still_taken(self):
        assert pick_employer_phone(["電話:03-5310852,傳真:03-5277128"]) == "035310852"

    def test_fax_keyword_case_insensitive(self):
        assert pick_employer_phone(["TEL: 03-5310852 FAX: 03-5277128"]) == "035310852"


class TestSamePhoneCountedByStandardForm:
    """出現次數多者優先(電話+Nomor Telepon 各一),同號判斷用電話標準形(#48、ADR-0011)。"""

    def test_hyphenated_and_plain_count_as_two(self):
        """排在前面的 03-7985657 只出現一次:用原字串計數時三支各一次,會挑到它。"""
        seg = ["電話:03-7985657", "電話:0920-875-667", "Nomor Telepon:0920875667"]
        assert pick_employer_phone(seg) == "0920875667"

    def test_tie_goes_to_first_seen(self):
        assert pick_employer_phone(["電話:03-7985657", "電話:0920-875-667"]) == "037985657"

    @pytest.mark.parametrize("line,expect", [
        ("電話:0920-875-667", "0920875667"),          # 連字號
        ("電話:0920 875 667", "0920875667"),          # 空白
        ("電話:(03)7985-657", "037985657"),           # 括號區碼
        ("電話:+886 920 875 667", "0920875667"),      # 國際格式
    ])
    def test_standard_form(self, line, expect):
        assert pick_employer_phone([line]) == expect
