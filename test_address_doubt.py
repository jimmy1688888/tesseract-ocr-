# -*- coding: utf-8 -*-
"""疑慮標示:中文地址標準化留空時,P 欄要說明為什麼(ADR-0003)。

雇主欄位是整條 pipeline 唯一沒有權威名冊可交叉驗證的部分,而 C 欄的
keyed-in/manual_review 完全由許可證判讀決定——雇主資料只是搭便車。本組測試
釘住補上的那道說明:標準欄留空時 P 欄講出成因,讓人核對時知道該看哪一格。

刻意不做英譯比對:契約英譯沒有單一正確寫法(Panshih/Panshi 都出現得到),
分不出「拼法不同」與「讀錯」。代價是 32448 那類「中文讀對、英文讀錯」抓不到,
這是已知且接受的缺口。
"""
from address_db import normalize_address
from employer_extract import _standardize_address


def _std(cn, en=""):
    f = {"地址_英": en}
    _standardize_address(f, cn)
    return f


class TestDoubtReasons:
    """五種成因各自要講對——人看到不同的話,要做的事完全不同。"""

    def test_city_not_matched(self):
        f = _std("XX@@市北區磐石路29號7樓")
        assert f["地址_中_標準"] == ""
        assert "縣市比不到" in f["地址_疑慮"]

    def test_district_not_matched(self):
        f = _std("新竹市OOO磐石路29號7樓")
        assert f["地址_中_標準"] == ""
        assert "行政區比不到" in f["地址_疑慮"]

    def test_road_not_in_db_is_actionable(self):
        """路名有樣式卻配不到 → 可補 custom_roads.json,話要講到這一步。"""
        f = _std("新竹市北區天馬行空路29號7樓")
        assert f["地址_中_標準"] == ""
        assert "custom_roads.json" in f["地址_疑慮"]

    def test_rural_address_flagged_as_normal(self):
        """鄉村型無路名地址也標,但要寫明是正常的,否則人會白跑一趟查證。"""
        f = _std("宜蘭縣三星鄉大隱村12鄰5號")
        assert f["地址_中_標準"] == ""
        assert "鄉村型" in f["地址_疑慮"]
        assert "custom_roads" not in f["地址_疑慮"]   # 別叫人去補一條不存在的路

    def test_no_chinese_address_at_all(self):
        assert "未擷取到" in _std("")["地址_疑慮"]

    def test_clean_address_has_no_doubt(self):
        f = _std("新竹市北區磐石路29號7樓")
        assert f["地址_中_標準"] == "新竹市北區磐石路29號7樓"
        assert f["地址_疑慮"] == ""


class TestFuzzyCityWithholdsZip:
    """縣市只靠模糊比對湊上、路名又沒配到 → 郵遞區號與英譯不採用。

    收緊前:郵遞區號只要 matched 就填,不受 J 欄那道
    `district and road` 守衛保護。「中山區」「中正區」「中山路」全台通用,
    模糊配到錯誤縣市時底下照樣配得到,於是 J 欄留空(看得見)而 N 欄填了
    個確信的錯值(看不見)——正是人工核對最抓不到的那類錯誤。
    """

    WRONG = "台桃市中壢區中山路8號"    # 桃園市中壢區,縣市被咬

    def test_fuzzy_city_really_lands_on_wrong_city(self):
        """前提檢查:這個輸入確實會模糊配到別的縣市,否則本組測試沒有意義。"""
        r = normalize_address(self.WRONG)
        assert r["matched"] and r["city_tier"] == 3
        assert r["city"] != "桃園市" and r["road"] == ""

    def test_zip_withheld(self):
        assert _std(self.WRONG)["郵遞區號"] == ""

    def test_english_ocr_never_backfilled(self):
        """英文 OCR 空就是空——官方英譯不得從任何路徑補進 M 欄。"""
        f = _std(self.WRONG, en="")
        assert f["地址_英"] == ""
        assert "地址_英_標準" not in f

    def test_reason_names_the_withheld_columns(self):
        d = _std(self.WRONG)["地址_疑慮"]
        assert "模糊" in d and "郵遞區號" in d


class TestFuzzyCityRescueSurvives:
    """模糊命中本身不是錯誤——別為了防錯把正確的救援一起擋掉。

    「宜藺縣」是印章咬掉一字的「宜蘭縣」,模糊比對把它救了回來,而且區、路、
    郵遞全對。判準是「路名配得到就是佐證」,不是「模糊一律不信」。
    """

    RESCUED = "宜藺縣羅東鎮中正路3號"

    def test_still_fuzzy_tier(self):
        assert normalize_address(self.RESCUED)["city_tier"] == 3

    def test_standard_address_still_filled(self):
        assert _std(self.RESCUED)["地址_中_標準"] == "宜蘭縣羅東鎮中正路3號"

    def test_zip_still_filled(self):
        assert _std(self.RESCUED)["郵遞區號"] == "265"

    def test_no_doubt_raised(self):
        assert _std(self.RESCUED)["地址_疑慮"] == ""


class TestEnglishAnchorCountsAsPrecise:
    """英文行錨定救回的縣市要算「精確」,否則收緊規則會誤傷既有的救援路徑。

    印章多蓋在中文行首(縣市被毀),而英文地址的 County/City 慣例在行尾而倖存,
    address_db 因此以英文行反查縣市/區為錨。_match_city_area_en 只做「官方英文名
    精確含」不做模糊,所以這條路徑的縣市與完整名命中同級。

    危險在於:若沒把它歸類為精確,「縣市非精確就不填郵遞區號」會把這些本來救得
    回來的地址一併打掉,而且理由還寫成「縣市僅模糊比對命中」——縣市明明是確定的。
    有路名時路名會當佐證而掩蓋這個問題,故本組刻意用路名也配不到的地址。
    """

    CN_DEAD = "◎◎◎左營區天馬行空路1號"      # 縣市全毀 + 路名不在庫
    EN_ALIVE = "No.1, Tianma Rd., Zuoying Dist., Kaohsiung City, Taiwan R.O.C."

    def test_anchor_recovers_city(self):
        r = normalize_address(self.CN_DEAD, en_text=self.EN_ALIVE)
        assert r["city"] == "高雄市" and r["district"] == "左營區"

    def test_anchored_city_is_tier_one(self):
        assert normalize_address(self.CN_DEAD, en_text=self.EN_ALIVE)["city_tier"] == 1

    def test_zip_survives_without_road_evidence(self):
        """路名配不到時,郵遞區號全靠這個層級判斷撐著。"""
        f = _std(self.CN_DEAD, en=self.EN_ALIVE)
        assert f["郵遞區號"] == "813"

    def test_doubt_blames_the_road_not_the_city(self):
        f = _std(self.CN_DEAD, en=self.EN_ALIVE)
        assert "路名" in f["地址_疑慮"]
        assert "縣市" not in f["地址_疑慮"]

    def test_without_english_line_it_really_does_fail(self):
        """前提檢查:沒有英文行時這個地址確實救不回來,否則上面幾條沒有意義。"""
        f = _std(self.CN_DEAD, en="")
        assert f["郵遞區號"] == "" and "縣市比不到" in f["地址_疑慮"]

    def test_stamped_city_also_recovered(self):
        """印章壓字型(「中共雄巿」)也走同一條路徑。"""
        f = _std("中共雄巿左營區華夏路1728號13樓",
                 en="13F., No.1728, Huaxia Rd., Zuoying Dist., Kaohsiung City")
        assert f["地址_中_標準"] == "高雄市左營區華夏路1728號13樓"
        assert f["郵遞區號"] == "813" and f["地址_疑慮"] == ""


class TestCityTierExposed:
    """city_tier 得真的出現在回傳值裡——收緊規則整個掛在它身上。"""

    def test_exact_city_name(self):
        assert normalize_address("新竹市北區磐石路29號7樓")["city_tier"] == 1

    def test_no_match_is_zero(self):
        assert normalize_address("XX@@市北區磐石路29號")["city_tier"] == 0

    def test_empty_input_is_zero(self):
        assert normalize_address("")["city_tier"] == 0


class TestSheetWiring:
    """疑慮標示佔 L 欄(原英文標準地址),整列維持 15 欄。

    英文標準地址取消輸出:契約英譯沒有單一正確寫法,官方譯名並不比契約上的更權威,
    兩欄並列只是讓人多比對一次卻分不出對錯。英文一律以 OCR 原文(M 欄)為準。
    """

    L, M = 11, 12       # A=0 … L=11 M=12(H~O 對應 index 7~14)

    def _row(self, docx, fields):
        import pipeline
        pipeline._EMPLOYER_FIELDS_BY_DOCX[docx] = fields
        try:
            return pipeline._row_to_sheet_values(
                {"source_docx": docx, "final_value": "2340"},
                pipeline.SheetStatus.KEYED_IN)
        finally:
            pipeline._EMPLOYER_FIELDS_BY_DOCX.pop(docx, None)

    def test_row_is_fifteen_columns(self):
        assert len(self._row("a.docx", {})) == 15

    def test_doubt_lands_in_column_l(self):
        row = self._row("b.docx", {"地址_疑慮": "路名不在資料庫"})
        assert row[self.L] == "路名不在資料庫"

    def test_english_column_is_ocr_verbatim(self):
        """M 欄只放 OCR 原文,官方英譯不得從任何路徑出現在列上。"""
        row = self._row("e.docx", {
            "地址_英": "7F., No.29, Panshih Rd., North Dist., Hsinchu City 300068",
        })
        assert row[self.M] == "7F., No.29, Panshih Rd., North Dist., Hsinchu City 300068"
        assert "Panshi Rd." not in row[self.L]

    def test_english_ocr_empty_stays_empty(self):
        """讀不到就空——不拿官方譯名充數,否則 M 欄的語意會出現例外。"""
        assert self._row("f.docx", {"地址_中_標準": "宜蘭縣宜蘭市津梅路142巷2號"})[self.M] == ""

    def test_clean_row_has_empty_doubt(self):
        row = self._row("c.docx", {"地址_中_標準": "新竹市北區磐石路29號7樓"})
        assert row[self.L] == ""

    def test_doubt_does_not_leak_into_reason_column(self):
        """D 欄專講許可證判讀,不能被雇主的疑慮汙染(一個欄位不承載兩種語意)。"""
        row = self._row("d.docx", {"地址_疑慮": "縣市比不到"})
        assert "縣市" not in row[3]

    def test_unknown_docx_gives_empty_doubt_not_crash(self):
        import pipeline
        row = pipeline._row_to_sheet_values(
            {"source_docx": "never-seen.docx"}, pipeline.SheetStatus.MANUAL_REVIEW)
        assert len(row) == 15 and row[self.L] == ""


class TestRealDocsUnaffected:
    """7 份真實契約的地址一份都不能因為這次收緊而掉欄位。"""

    REAL = [
        ("高雄市左營區華夏路1728號13樓", "813"),
        ("新竹市北區磐石路29號7樓", "300"),
        ("花蓮縣新城鄉樹林街162號", "971"),
        ("雲林縣四湖鄉中山東路38巷1弄12號", "654"),
        ("高雄市楠梓區德賢路555巷12號20樓", "811"),
        ("宜蘭縣宜蘭市津梅路142巷2號", "260"),
        ("新竹市北區延平路一段401巷1號", "300"),
    ]

    def test_all_seven_keep_zip_and_standard_address(self):
        for cn, zipcode in self.REAL:
            f = _std(cn)
            assert f["郵遞區號"] == zipcode, cn
            assert f["地址_中_標準"], cn
            assert f["地址_疑慮"] == "", cn
