# -*- coding: utf-8 -*-
"""台仲區塊定界(問題二:32345 台仲在右側,不能靠幾何裁半)。

核心是 _is_agency_label_line:區分「真欄位標籤」與內文 prose 誤配——
契約樣板段落會同時出現「印尼仲介公司…台灣仲介公司」「agency Taiwan…agency Indonesia」,
若取第一個標記會錨到 prose(32324 教訓),故以「短行+非句號結尾」濾除。
測試字串取自 32324 / 32345 次頁實際 OCR 結果。
"""
from employer_extract import (_is_agency_label_line,
                              _TW_AGENCY_LABEL, _ID_AGENCY_LABEL)


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
