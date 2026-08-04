# -*- coding: utf-8 -*-
"""疑慮標示:中文地址標準化留空時,P 欄要說明為什麼(ADR-0003)。

雇主欄位是整條 pipeline 唯一沒有權威名冊可交叉驗證的部分,而 C 欄的
keyed-in/manual_review 完全由許可證判讀決定——雇主資料只是搭便車。本組測試
釘住補上的那道說明:標準欄留空時 P 欄講出成因,讓人核對時知道該看哪一格。

刻意不做英譯比對:契約英譯沒有單一正確寫法(Panshih/Panshi 都出現得到),
分不出「拼法不同」與「讀錯」。代價是 32448 那類「中文讀對、英文讀錯」抓不到,
這是已知且接受的缺口。
"""
import json

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
        assert "縣市未比對到" in f["地址_疑慮"]

    def test_district_not_matched(self):
        """區配不到、路名又跨多個區(反推不啟動)→ 區留空並說明為何無從判定。

        素材刻意用中正路:新竹市的北區與東區都有,所以路名反推不了區。
        用單一區才有的路(磐石路)會被反推救回來,那是另一組測的行為。
        """
        f = _std("新竹市OOO中正路29號7樓")
        assert f["地址_中_標準"] == ""
        assert "行政區未判定" in f["地址_疑慮"]
        assert "多個行政區" in f["地址_疑慮"]

    def test_road_not_in_db_states_fact_only(self):
        """只說「沒在清單中」這個事實,不推斷原因、不叫人去補資料庫。

        舊措辭寫「可補進 custom_roads.json」,而 32538 實例證明那個建議會出錯:
        區被誤配時,一條好端端存在於別區的路也會被說成不在資料庫,照著補會
        重複加入並掛錯區。沒配到是確定的,為什麼沒配到不是。
        """
        f = _std("新竹市北區天馬行空路29號7樓")
        assert f["地址_中_標準"] == ""
        assert "路名未在" in f["地址_疑慮"]
        assert "custom_roads" not in f["地址_疑慮"]

    def test_no_reason_offers_an_action(self):
        """整組理由都不得夾帶行動建議——它自己可能是錯的。"""
        for cn in ("XX@@市北區磐石路29號7樓", "新竹市OOO中正路29號7樓",
                   "新竹市北區天馬行空路29號7樓", ""):
            d = _std(cn)["地址_疑慮"]
            assert "可補" not in d and ".json" not in d, cn

    def test_rural_address_flagged_as_normal(self):
        """鄉村型無路名地址也標,但要寫明是正常的,否則人會白跑一趟查證。"""
        f = _std("宜蘭縣三星鄉大隱村12鄰5號")
        assert f["地址_中_標準"] == ""
        assert "鄉村型" in f["地址_疑慮"]

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


class TestCityNameDoesNotFeedDistrictMatch:
    """縣市名不得自己餵進行政區比對(32538 教訓)。

    行政區比對的第二階段會去尾字:「桃園區」去掉「區」剩「桃園」,而「桃園市…」
    裡就有這兩字——於是一個完全沒寫區的地址被安上桃園區。後果是連鎖的:
    錯的區 → 只在該區的路名清單裡找 → 找不到 → 郵遞區號填成 330(實為 333)、
    且把好端端在龜山區的「文化七路」報成路名不在資料庫。

    修法是配區前先剝掉縣市名,與同一支函式配路名時的既有做法對稱。
    """

    NO_DISTRICT = "桃園市高山里文化七路206巷100弄13號3樓"   # 只有里,沒有區
    CORRECT = "桃園市龜山區高山里文化七路206巷100弄13號3樓"

    def test_matches_the_explicit_district_version(self):
        """沒寫區的版本要與寫了正確區的版本得到同一個答案。"""
        a, b = normalize_address(self.NO_DISTRICT), normalize_address(self.CORRECT)
        assert (a["district"], a["road"], a["zip"]) == \
               (b["district"], b["road"], b["zip"]) == ("龜山區", "文化七路", "333")

    def test_zip_is_not_the_city_named_district(self):
        """330 是桃園區的郵遞區號——不能再從縣市名借一個區出來。"""
        assert normalize_address(self.NO_DISTRICT)["zip"] != "330"

    def test_no_doubt_raised(self):
        assert _std(self.NO_DISTRICT)["地址_疑慮"] == ""

    def test_same_named_county_seat_unaffected(self):
        """縣治與縣同名的合法碰撞不能被誤傷——那正是去尾字階段存在的理由。"""
        for cn, dist in (("宜蘭縣宜蘭市津梅路142巷2號", "宜蘭市"),
                         ("彰化縣和美鎮德南街100號", "和美鎮"),
                         ("桃園市桃園區文化街5號", "桃園區")):
            assert normalize_address(cn)["district"] == dist, cn


class TestDistrictInferredFromUniqueRoad:
    """區配不到時,以「該縣市內唯一擁有此路名的區」反推——限路名精確命中。

    性質同英文行錨定:拿一條與「區」的 OCR 無關的獨立證據把區補回來。
    全台 90.8% 的路名在其縣市內只屬於一個行政區,訊號夠強;但通用路名
    (中山路、中正路)不唯一,一律不猜。
    """

    def test_unique_road_pins_the_district(self):
        r = normalize_address("新竹市OOO磐石路29號7樓")   # 磐石路只有北區有
        assert r["district"] == "北區" and r["zip"] == "300"

    def test_common_road_does_not_guess(self):
        """中正路在新竹市的北區與東區都有 → 不猜,區留空、郵遞留空。"""
        r = normalize_address("新竹市OOO中正路29號7樓")
        assert r["district"] == "" and r["zip"] == ""

    def test_unmatched_road_does_not_guess(self):
        r = normalize_address("桃園市新屋區清華路50巷101弄75號")
        assert r["road"] == ""          # 清華路確實不在庫裡(只有清華一街/二街)

    def test_fuzzy_road_does_not_infer(self):
        """模糊命中的路名不反推區:兩層推測疊加,錯了會是確信的錯值。"""
        r = normalize_address("成園市格梅區梅翠路二段716巷7號")
        assert r["district"] == "" and r["zip"] == ""


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
        assert f["郵遞區號"] == "" and "縣市未比對到" in f["地址_疑慮"]

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
        row = self._row("b.docx", {"疑慮標示": "J:路名未在該行政區的路名清單中"})
        assert row[self.L] == "J:路名未在該行政區的路名清單中"

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
        row = self._row("d.docx", {"疑慮標示": "J:縣市未比對到官方資料庫"})
        assert "縣市" not in row[3]

    def test_unknown_docx_gives_empty_doubt_not_crash(self):
        import pipeline
        row = pipeline._row_to_sheet_values(
            {"source_docx": "never-seen.docx"}, pipeline.SheetStatus.MANUAL_REVIEW)
        assert len(row) == 15 and row[self.L] == ""


class TestVisionTextIsPersisted:
    """Vision 全文要真的落地——它是唯一花錢買來的東西,丟了就只能憑猜測改規則。

    這條路徑先前差點靜默壞掉:pipeline.py 沒有 import json,而沒有測試走到
    write_employer_texts,整份存檔會在跑真實資料時才 NameError。
    """

    def test_writes_json_and_returns_count(self, tmp_path, monkeypatch):
        import pipeline
        target = tmp_path / "employer_texts.json"
        monkeypatch.setattr(pipeline, "EMPLOYER_TEXT_PATH", target)
        n = pipeline.write_employer_texts({
            "32538.docx": {"image": "image2.jpeg", "text": "甲方名稱: 王小明\n地址:"},
        })
        assert n == 1
        data = json.loads(target.read_text(encoding="utf-8"))
        assert data["32538.docx"]["text"].startswith("甲方名稱")
        assert data["32538.docx"]["image"] == "image2.jpeg"

    def test_empty_input_writes_nothing(self, tmp_path, monkeypatch):
        import pipeline
        target = tmp_path / "employer_texts.json"
        monkeypatch.setattr(pipeline, "EMPLOYER_TEXT_PATH", target)
        assert pipeline.write_employer_texts({}) == 0
        assert not target.exists()

    def test_write_failure_does_not_raise(self, tmp_path, monkeypatch):
        """輔助產物寫不出來,不該拖垮已經跑完的主流程。"""
        import pipeline
        monkeypatch.setattr(pipeline, "EMPLOYER_TEXT_PATH", tmp_path / "x" / "y")
        monkeypatch.setattr(pipeline.Path, "mkdir",
                            lambda *a, **k: (_ for _ in ()).throw(OSError("nope")))
        assert pipeline.write_employer_texts({"a.docx": {"text": "x"}}) == 0

    def test_text_is_moved_out_of_the_row_cache(self, tmp_path, monkeypatch):
        """全文另存後不留在快取:快取組每一列時都會查,夾一大塊文字只是負擔。"""
        import pipeline
        monkeypatch.setattr(pipeline, "EMPLOYER_TEXT_PATH",
                            tmp_path / "employer_texts.json")
        monkeypatch.setattr(pipeline, "get_vision_client", lambda: None)
        monkeypatch.setattr(
            "employer_extract.extract_employer_from_docx",
            lambda *a, **k: {"雇主名稱_中": "王小明", "疑慮標示": "",
                             "_note": "", "_image": "image2.jpeg", "_crop": "",
                             "_text": "甲方名稱: 王小明"})
        pipeline.collect_employer_fields([tmp_path / "99999.docx"])
        try:
            cached = pipeline._EMPLOYER_FIELDS_BY_DOCX["99999.docx"]
            assert "_text" not in cached
            assert cached["雇主名稱_中"] == "王小明"
            saved = json.loads(
                (tmp_path / "employer_texts.json").read_text(encoding="utf-8"))
            assert saved["99999.docx"]["text"] == "甲方名稱: 王小明"
        finally:
            pipeline._EMPLOYER_FIELDS_BY_DOCX.pop("99999.docx", None)


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


class TestEnglishRoadFallback:
    """英文路名備援:中文路名被讀壞、英文行倖存時的最後一道救援。

    這條路徑寫好之後從未真的跑起來過——_EN_ROAD_TOKEN 寫死「Rd 後面必須有句點」,
    而契約常印成「Meishi Rd,」(無句點),findall 直接回空,備援靜默放棄。
    另外 token 的字元類別不含逗號,會從上一個逗號一路吃進「Ln. 716.」這類前綴,
    把相似度稀釋到 0.727 而過不了 0.9 的門檻。
    """

    CN_BROKEN = "成園市格梅區梅翠路二段716巷7號"        # 梅獅路被讀成梅翠路
    EN_OK = ("No. 62, Ln. 716. Sec. 2. Meishi Rd, Yangmei Dist. "
             "Taoyuan City 326014, Taiwan")

    def test_recovers_road_from_english_line(self):
        r = normalize_address(self.CN_BROKEN, en_text=self.EN_OK)
        assert r["road"] == "梅獅路２段" and r["zip"] == "326"

    def test_chinese_fuzzy_alone_still_refuses(self):
        """中文那邊仍該拒配——主幹防護擋住梅翠→梅獅(0.667 < 0.75)是刻意的。
        救回來的功勞屬於英文行,不是把中文的門檻放寬。"""
        assert normalize_address("桃園市楊梅區梅翠路二段716巷7號")["road"] == ""

    def test_genuinely_missing_road_still_not_matched(self):
        """32540:新屋區真的沒有清華路。最像的是「新文路/Xinwen Rd.」0.857,
        必須擋掉——配成新文路會是確信的錯值,比留空糟。"""
        r = normalize_address(
            "桃園市新屋區清華路50巷101弄75號",
            en_text="No. 75, Aly. 101, Ln. 50, Qingwen Rd., Xinwu Dist., "
                    "Taoyuan City , Taiwan (R.O.C.)")
        assert r["road"] == ""

    def test_road_suffix_without_period_is_accepted(self):
        from address_db import _EN_ROAD_TOKEN
        assert _EN_ROAD_TOKEN.findall("Sec. 2. Meishi Rd, Yangmei")

    def test_east_is_not_mistaken_for_st(self):
        """後綴前的詞界不可省:少了它「East,」會被拆成 Ea + st 誤判成 St. 結尾。"""
        from address_db import _EN_ROAD_TOKEN
        assert not _EN_ROAD_TOKEN.findall("Somewhere East, ")
