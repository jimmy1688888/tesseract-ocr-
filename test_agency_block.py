# -*- coding: utf-8 -*-
"""台仲區塊定界(問題二:32345 台仲在右側,不能靠幾何裁半)。

核心是 _is_agency_label_line:區分「真欄位標籤」與內文 prose 誤配——
契約樣板段落會同時出現「印尼仲介公司…台灣仲介公司」「agency Taiwan…agency Indonesia」,
若取第一個標記會錨到 prose(32324 教訓),故以「短行+非句號結尾」濾除。
測試字串取自 32324 / 32345 次頁實際 OCR 結果。
"""
from employer_extract import (_address_in_block, _is_agency_label_line,
                              _name_in_block, _phones_in_block,
                              _TW_AGENCY_LABEL, _TW_AGENCY_LABEL_LOOSE,
                              _ID_AGENCY_LABEL)


class TestIsAgencyLabelLine:
    """真欄位標籤(短、非句號)才算;prose 長句誤配要被濾掉。"""

    # ---- 真台仲欄位標籤:應為 True ----
    def test_real_tw_label_with_paren(self):
        assert _is_agency_label_line("台灣仲介公司(Agency Taiwan):", _TW_AGENCY_LABEL)

    def test_real_tw_label_agensi(self):
        assert _is_agency_label_line("台灣仲介 Agensi Taiwan:", _TW_AGENCY_LABEL)

    def test_real_tw_label_trailing_space_before_colon(self):
        # 32324 左半 OCR 實際樣態:冒號前有空白
        assert _is_agency_label_line("台灣仲介公司(Agency Taiwan) :", _TW_AGENCY_LABEL)

    # ---- prose 誤配:應為 False ----
    def test_prose_chinese_sentence_rejected(self):
        line = "「關備查外,另一份由印尼仲介公司存查,另一份由台灣仲介公司存查。"
        assert not _is_agency_label_line(line, _TW_AGENCY_LABEL)

    def test_prose_indonesian_sentence_rejected(self):
        line = "oleh agency Taiwan dan 1 (satu) berkas disimpan oleh agency Indonesia."
        assert not _is_agency_label_line(line, _TW_AGENCY_LABEL)

    def test_prose_matches_id_label_but_rejected(self):
        # 同一句 prose 也會誤配印尼標記,同樣要被濾掉
        line = "「關備查外,另一份由印尼仲介公司存查,另一份由台灣仲介公司存查。"
        assert not _is_agency_label_line(line, _ID_AGENCY_LABEL)

    # ---- 真印尼欄位標籤:應為 True(作為區塊下界) ----
    def test_real_id_label(self):
        assert _is_agency_label_line("印尼仲介 P3MI:", _ID_AGENCY_LABEL)

    # ---- 非標記行 ----
    def test_plain_address_line_not_label(self):
        assert not _is_agency_label_line("台中市北區台灣大道2段", _TW_AGENCY_LABEL)


# 32426 次頁實際 OCR:紅章把『台灣仲介 Agensi Taiwan:』蓋成『tensi Taiwan:』,
# 嚴格標籤對不上就整份放棄,連同頁上完好的『電話:04-23263526』一起丟掉
# (該電話在名冊精確命中許可證 2340 鼎力國際開發有限公司)。
_MANGLED_LABEL = "tensi Taiwan:"
# 同一頁其他含 Taiwan 的行——放寬後這些都不能被誤當標籤
_DECOYS_SAME_PAGE = [
    "DEFLTING 260.SEX TAIWAN BLVD NORTH",          # 短且不以句號結尾,只能靠 pattern 擋
    "DISE CHUNG CITY, TAIWAN(RO.C.)",
    "ke Instansi Pemerintah Taiwan yang berwenang untuk diverifikasi.",
    "台中市北區台灣大道2段360號23樓之1",
]


class TestLooseAgencyLabel:
    """標籤被紅章毀掉時的後備錨:錨得到才找值,錨不到仍不猜(交人工)。"""

    def test_strict_label_misses_mangled(self):
        # 前提:嚴格式確實對不上,後備才有存在意義
        assert not _is_agency_label_line(_MANGLED_LABEL, _TW_AGENCY_LABEL)

    def test_loose_label_anchors_mangled(self):
        assert _is_agency_label_line(_MANGLED_LABEL, _TW_AGENCY_LABEL_LOOSE)

    def test_loose_label_still_matches_intact_label(self):
        # 放寬不能破壞正常樣態
        assert _is_agency_label_line("台灣仲介 Agensi Taiwan:", _TW_AGENCY_LABEL_LOOSE)
        assert _is_agency_label_line("台灣仲介公司(Agency Taiwan):",
                                     _TW_AGENCY_LABEL_LOOSE)

    def test_same_page_taiwan_lines_not_anchored(self):
        for line in _DECOYS_SAME_PAGE:
            assert not _is_agency_label_line(line, _TW_AGENCY_LABEL_LOOSE), line

    def test_bare_taiwan_colon_not_anchored(self):
        """Agensi/Agency 殘骸全失 → 不視為標籤,維持「不猜」交人工。"""
        assert not _is_agency_label_line("Taiwan:", _TW_AGENCY_LABEL_LOOSE)
        assert not _is_agency_label_line("in Taiwan :", _TW_AGENCY_LABEL_LOOSE)


class TestValuesFromMangledBlock:
    """錨到之後,值仍要抽得出來——32426 次頁自 `tensi Taiwan:` 起的實際 OCR 行。"""

    BLOCK = [
        "tensi Taiwan:",
        "ma",
        "DIN GHAERNATIONAL DEVELOPMENT",
        "C. 鼎力及院發有限公司)",
        "DEFLTING 260.SEX TAIWAN BLVD NORTH",
        "DISE CHUNG CITY, TAIWAN(RO.C.)",
        "台中市北區台灣大道2段360號23樓之1",
        "電話:04-23263526,傳真:04-23263528",
        "適用章",
        "印尼駐台北經濟貿易辦事處 Kantor Dagang dan Ekonomi Indonesia di Taipei",
    ]

    def test_phone_extracted_and_fax_excluded(self):
        # 傳真 04-23263528 必須被切掉,只留電話(名冊反查靠這支)
        assert _phones_in_block(self.BLOCK) == ["0423263526"]

    def test_same_phone_written_two_ways_deduped(self):
        """去重以電話標準形為鍵(#48):同一支號碼寫法不同,只留一支候選。"""
        assert _phones_in_block(["電話:04-23263526", "TEL: 0423263526",
                                 "(04)2326-3526"]) == ["0423263526"]

    def test_address_extracted(self):
        assert _address_in_block(self.BLOCK) == "台中市北區台灣大道2段360號23樓之1"

    def test_name_is_ocr_damaged_but_not_a_label(self):
        # 名稱被章毀成「鼎力及院發…」(正解為鼎力國際開發有限公司):抽得到但不可靠,
        # 故電話反查排在名稱之前——這裡只釘住「不會把標籤行當成名稱」。
        assert "Taiwan" not in _name_in_block(self.BLOCK)


class TestRescueReasonUsesStandardForm:
    """D 欄反查理由裡的台仲電話來自 OCR,跟著寫成電話標準形(#48)。
    替身只放在外部邊界:次頁 Vision 讀出的行、Vision client;名冊用注入的假名冊。"""

    def test_reason_shows_standard_form(self, monkeypatch, tmp_path):
        import employer_extract
        import google_clients
        import pipeline
        from batch import Batch
        from permit_lookup import PermitLookup
        from sheet_row import SheetRow, SheetStatus

        roster = PermitLookup([{"許可證": "2340", "機構名稱": "測試仲介", "機構地址": "地址",
                                "電話": "(02)2727-6999#12", "廢止許可日期": "", "終止營業日期": ""}])
        monkeypatch.setattr(pipeline, "_permit_lookup", lambda: roster)
        monkeypatch.setattr(google_clients, "vision_client", lambda: object())
        monkeypatch.setattr(employer_extract, "_agency_block_lines",
                            lambda path, client=None: ["電話:02-2727-6999,傳真:02-2727-6000"])
        docx = tmp_path / "32417.docx"
        docx.write_bytes(b"x")
        seed = [SheetRow("32417.docx", "", SheetStatus.MANUAL_REVIEW, "全無命中")]

        [row] = pipeline.rescue_manual_review_via_agency_phone(Batch.from_file(docx), seed)
        assert row.permit == "2340"
        assert "台仲電話 0227276999 " in row.reason
        assert "02-2727-6999" not in row.reason
