# -*- coding: utf-8 -*-
"""雇主中文名的抽取與疑慮標示(21 份真實契約的教訓)。

中文名的可靠度遠低於英文名:紅章蓋的是中文那一欄,羅馬拼音在另一欄反而倖存。
實測 21 份契約,可比對的 13 筆裡有 7 筆中文字數與英譯音節數對不上。

四類確定的抽取錯誤(本組全部釘住):
  樣板字   契約標題「勞動契約 家庭幫傭/監護工」在標籤行沒抓到名字時頂上來
  標籤殘骸 「甲方名稱(以下」被咬斷,剝掉標籤後剩「以下」變成值
  分隔符   OCR 把全形冒號讀成分號,「; 林瑞峰」整串進欄位
  窗口     Vision 逐欄序列化,中文名未必與標籤相鄰(32508 的「呂偉」在第 9 行)
"""
import re

from employer_extract import (extract_employer_fields, _name_doubt,
                              _house_number_doubt)

# 32508 實況節錄:標籤行沒有名字,契約標題從另一欄串進來,真名遠在後面
_LINES_TITLE_INTERFERES = [
    "甲方名稱 (以下商稱為甲方):",
    "Nama Majikan: LU SHI WEI",
    "勞動契約 家庭幫傭",
    "SEKTOR",
    "INFORMAL",
    "PERJANJIAN KERJA",
    "ANTARA MAJIKAN DENGAN",
    "PENATA LAKSANA RUMAH TANGGA",
    "(DOMESTIC HELPER)",
    "呂偉",
    "地址: 台中市北屯區松竹路二段178巷35號4樓之1",
    "SELANJUTNYA DISEBUT PIHAK PERTAMA",
]


def _f(lines):
    return extract_employer_fields("\n".join(lines))


class TestChineseBoilerplateRejected:
    """契約標題不得成為雇主名稱。

    BOILERPLATE 從頭到尾只有印尼文與英文,且只用在英文名那條路徑——中文名完全
    沒有樣板過濾,於是「勞動契約 家庭幫傭」三個條件(有中日韓字、非中式地址、
    非英式地址)全過,直接當成了雇主名。
    """

    def test_title_not_taken_as_name(self):
        assert _f(_LINES_TITLE_INTERFERES)["雇主名稱_中"] == "呂偉"

    def test_title_alone_yields_empty_not_title(self):
        """整個區塊只有標題沒有名字時,要留空——不是拿標題充數(32510 實況)。"""
        lines = ["平方名稱 {以下間稱為甲方):", "Nama Majikan", "JIANG SIN AN",
                 "勞動契約 監護工", "SEKTOR", "SELANJUTNYA DISEBUT PIHAK PERTAMA"]
        assert _f(lines)["雇主名稱_中"] == ""

    def test_care_worker_titles_covered(self):
        for title in ("勞動契約 監護工", "勞動契約 家庭幫傭", "家庭看護工"):
            lines = ["甲方名稱 (以下簡稱為甲方):", title,
                     "SELANJUTNYA DISEBUT PIHAK PERTAMA"]
            assert _f(lines)["雇主名稱_中"] == "", title


class TestLabelDebrisRejected:
    """OCR 咬斷標籤後的殘骸不得變成值(32527:「甲方名稱(以下」→「以下」)。"""

    def test_truncated_label_yields_empty(self):
        lines = ["甲方名稱(以下", "Nama Majikan", "地址:",
                 "SELANJUTNYA DISEBUT PIHAK PERTAMA"]
        f = _f(lines)
        assert f["雇主名稱_中"] == ""
        assert "以下" not in f["雇主名稱_中"]


class TestSeparatorMisread:
    """全形冒號被 OCR 讀成分號時,標籤仍要切乾淨(32521:「; 林瑞峰」)。"""

    def test_semicolon_acts_as_colon(self):
        lines = ["甲方名稱 (以下簡稱為甲方); 林瑞峰",
                 "SELANJUTNYA DISEBUT PIHAK PERTAMA"]
        assert _f(lines)["雇主名稱_中"] == "林瑞峰"


class TestWindowCoversWholeBlock:
    """窗口涵蓋整個雇主區塊,而不是前 N 行。

    舊碼掃 seg[:9],而 32508 的「呂偉」剛好排在索引 9——差一格,於是抓不到。
    Vision 對兩欄版面逐欄序列化,中文名與它的標籤本來就未必相鄰。
    """

    def test_name_far_from_its_label_is_found(self):
        assert _f(_LINES_TITLE_INTERFERES)["雇主名稱_中"] == "呂偉"

    def test_damaged_address_line_not_taken_as_name(self):
        """窗口放寬的代價:縣市被毀而 CN_ADDR 抓不到的地址殘行會有機會頂上來。
        靠「含阿拉伯數字」擋掉——門牌號一定有數字,人名與公司名都不會有。"""
        lines = ["甲方名稱 (以下簡稱為甲方):", "Nama Majikan", "CHEN",
                 "新竹騒麒西鎮大同里8鄰水坑2之8號",
                 "SELANJUTNYA DISEBUT PIHAK PERTAMA"]
        assert _f(lines)["雇主名稱_中"] == ""

    def test_company_employer_still_accepted(self):
        """雇主可以是公司(CONTEXT.md),不能用「2~4 個純中文字」當條件。"""
        lines = ["甲方名稱 (以下簡稱為甲方): 台灣某某科技股份有限公司",
                 "SELANJUTNYA DISEBUT PIHAK PERTAMA"]
        assert _f(lines)["雇主名稱_中"] == "台灣某某科技股份有限公司"


class TestNameDoubt:
    """中文字數與英譯音節數不符即標示。是結構性計數,不是音譯比對。"""

    def test_truncated_name_flagged(self):
        """最危險的一類:截斷後看起來完全像個正常名字,人核對時會滑過去。"""
        assert "不符" in _name_doubt("明芳", "SONG, MING-FANG")     # 少了姓「宋」
        assert "不符" in _name_doubt("彭武", "PENG WU KE")
        assert "不符" in _name_doubt("邱鈺", "CIOU, YU-PU")

    def test_noise_and_duplication_flagged(self):
        assert "不符" in _name_doubt("陳紅足 正", "CHEN HONG ZU")
        assert "不符" in _name_doubt("呂佳紋 紋", "LU, CHIA-WEN")

    def test_matching_counts_pass(self):
        assert _name_doubt("陳國輝", "CHEN KUO HUI") == ""
        assert _name_doubt("林瑞峰", "LIN RUI FENG") == ""

    def test_hyphen_and_comma_count_as_separators(self):
        assert _name_doubt("宋明芳", "SONG, MING-FANG") == ""

    def test_known_blind_spot_same_count_wrong_chars(self):
        """同字數的錯字抓不到——要抓得真的比對音譯,已知且接受的缺口。

        釘住它是為了讓這個缺口有名有姓:哪天有人以為數量檢查萬無一失,
        這條測試會告訴他 32519/32549 這兩筆從頭到尾就沒被擋下來。
        """
        assert _name_doubt("盖果杰", "WENG, BING-JIE") == ""    # 姓完全不同
        assert _name_doubt("洪清廷", "HUNG YAN TING") == ""     # 中間字不同

    def test_company_names_skip_the_count_check(self):
        """公司英文名(XX Co., Ltd.)音節數與中文字數無對應關係,不能拿來比。"""
        assert _name_doubt("台灣某某科技股份有限公司", "TAIWAN XX TECH CO., LTD.") == ""

    def test_missing_english_name_skips_the_check(self):
        assert _name_doubt("王小明", "") == ""


class TestEmptyNameStatesFactOnly:
    """中文名為空一律只寫「未擷取到」,不猜原因。

    32510 的「姜信安」被紅章壓住讀不出來,32503 的契約本身就只填羅馬拼音
    (那一格寫的就是 FU BIN)——兩者在 OCR 文字上一模一樣,分不出來。
    寫成「契約未載中文名」會誤導人「不必去翻契約」,而那正好是錯的那一半。
    """

    def test_reason_is_neutral(self):
        d = _name_doubt("", "FU BIN")
        assert "未擷取到" in d
        assert "契約" not in d and "紅章" not in d and "未載" not in d

    def test_flagged_even_when_english_name_exists(self):
        assert _name_doubt("", "JIANG SIN AN") != ""


class TestComposedDoubtColumn:
    """L 欄合併名稱與地址的疑慮,每條冠上欄名指明是哪一格。"""

    def test_both_doubts_appear_with_prefixes(self):
        lines = ["平方名稱 {以下間稱為甲方):", "Nama Majikan", "JIANG SIN AN",
                 "勞動契約 監護工", "地址:", "成園市格梅區梅翠路二段716巷7號",
                 "SELANJUTNYA DISEBUT PIHAK PERTAMA"]
        d = _f(lines)["疑慮標示"]
        assert d.startswith("H:")
        assert "J:" in d

    def test_clean_row_is_empty(self):
        lines = ["甲方名稱 (以下簡稱為甲方): 李宜蓁",
                 "Nama Majikan: LI YI JHEN",
                 "地址:", "新竹市北區磐石路29號7樓",
                 "SELANJUTNYA DISEBUT PIHAK PERTAMA"]
        assert _f(lines)["疑慮標示"] == ""

    def test_every_entry_names_its_column(self):
        lines = ["甲方名稱 (以下簡稱為甲方): 明芳", "Nama Majikan: SONG, MING-FANG",
                 "SELANJUTNYA DISEBUT PIHAK PERTAMA"]
        for part in _f(lines)["疑慮標示"].split("；"):
            assert re.match(r"^[A-Z]:", part), part


class TestHouseNumberCrossCheck:
    """中文與英文的門牌號不一致即標示。

    這道檢查是「英文路名備援」修好之後的必要配套:修好前 32510 因路名配不到而
    J 欄留空並掛疑慮(人看得見);修好後路名救回來了,J 欄卻會填進中文那個讀壞的
    門牌號(圖上是 62 號,OCR 讀成 7 號)而不帶任何標示——等於把一個看得見的失敗
    換成看不見的錯值。

    門牌號是阿拉伯數字,沒有拼音變體問題,這正是英文地址整體不做判定卻仍能拿
    門牌號來對的原因。同名稱的字數/音節數檢查,是結構性比對而非語意判斷。
    """
    def test_mismatch_flagged(self):
        d = _house_number_doubt("成園市格梅區梅翠路二段716巷7號",
                    "No. 62, Ln. 716. Sec. 2. Meishi Rd, Yangmei Dist.")
        assert "門牌號中英不符" in d and "7／62" in d

    def test_agreement_passes(self):
        assert _house_number_doubt("桃園市新屋區清華路50巷101弄75號",
                       "No. 75, Aly. 101, Ln. 50, Qingwen Rd.") == ""

    def test_zhi_form_equals_hyphen_form(self):
        """「8之15之2號」與「No.8-15-2」是同一件事的兩種寫法,不得誤報。

        比對前不統一的話,32509 與 32523 會被當成不符——實測那兩筆其實一致,
        是粗糙的 regex 只抓到最後一段數字才看起來不同。
        """
        assert _house_number_doubt("台中市太平區鵬儀路214巷6弄8之15之2號",
                       "No.8-15-2, Alley 6, Lane 214, Pengyi Rd.") == ""
        assert _house_number_doubt("臺北市信義區虎林街164巷13之1號2樓",
                       "2F., No.13-1, Ln. 164, Hulin St.") == ""

    def test_missing_either_side_skips(self):
        assert _house_number_doubt("", "No. 62, Ln. 716") == ""
        assert _house_number_doubt("桃園市新屋區清華路75號", "") == ""
