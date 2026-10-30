# -*- coding: utf-8 -*-
"""沒擷取到雇主資料時,L 欄寫明原因(#46)。

舊做法四種「整份沒擷取」H~O 全空、L 也空,原因只在日誌 —— 在 Sheets 上跟「契約上
真的沒填」分不出來。雇主區塊未錨定時切結書頁救回中文名 → H 有值、L 只寫「取自
切結書頁」,看不出整個區塊其實沒錨到;日誌卻印「H~O 留空」。資料全部捏造。
"""
import logging

import pytest

from conftest import FakeVision
import employer_extract as ee
from contract_template import Template
from employer_result import EmployerExtraction, ExtractionStatus

_NO_ANCHOR = "勞動契約 監護工\n仲介公司 某某人力"


def _rescue(name="姜信安", page="image5.jpeg"):
    return lambda p, vision: (name, page)


class TestReasonInLColumn:

    @pytest.mark.parametrize("status, l_column", [
        (ExtractionStatus.雇主區塊未錨定, "H~O:契約頁雇主區塊未錨定,未擷取"),
        (ExtractionStatus.找不到契約頁, "H~O:找不到契約頁,未擷取"),
        (ExtractionStatus.docx無內嵌圖, "H~O:docx 無內嵌圖,未擷取"),
        (ExtractionStatus.擷取失敗, "H~O:雇主擷取失敗(例外),未擷取"),
    ])
    def test_each_status_states_why(self, status, l_column):
        assert EmployerExtraction(status).疑慮標示 == l_column

    def test_success_has_no_reason(self):
        assert EmployerExtraction(ExtractionStatus.擷取成功).疑慮標示 == ""

    def test_exception_detail_is_not_in_the_l_column(self):
        r = EmployerExtraction(ExtractionStatus.擷取失敗, detail="RuntimeError('壞掉')")
        assert "RuntimeError" not in r.疑慮標示


class TestRescueAlongsideTheReason:

    def test_anchor_failure_then_rescue(self, monkeypatch):
        monkeypatch.setattr(ee, "_scored_pages", lambda p: [(99, "image2.jpeg", b"x")])
        monkeypatch.setattr(ee, "rescue_name_from_pledge", _rescue())
        r = ee.extract_employer_from_docx("x.docx", FakeVision(document=lambda b: _NO_ANCHOR),
                                          roi=None, deink=False)
        assert r.status is ExtractionStatus.雇主區塊未錨定
        assert r.values.雇主名稱_中 == "姜信安"
        assert r.疑慮標示 == "H~O:契約頁雇主區塊未錨定,未擷取；H:中文名取自切結書頁(image5.jpeg)"

    def test_contract_page_not_found_then_rescue(self, monkeypatch):
        monkeypatch.setattr(ee, "_scored_pages", lambda p: [(0, "image2.jpeg", b"x")])
        monkeypatch.setattr(ee, "rescue_name_from_pledge", _rescue())
        r = ee.extract_employer_from_docx("x.docx", FakeVision(), roi=None)
        assert r.status is ExtractionStatus.找不到契約頁
        assert r.values.雇主名稱_中 == "姜信安"
        assert r.疑慮標示 == "H~O:找不到契約頁,未擷取；H:中文名取自切結書頁(image5.jpeg)"

    def test_failed_rescue_leaves_only_the_reason(self, monkeypatch):
        monkeypatch.setattr(ee, "_scored_pages", lambda p: [(0, "image2.jpeg", b"x")])
        monkeypatch.setattr(ee, "rescue_name_from_pledge", _rescue("", ""))
        r = ee.extract_employer_from_docx("x.docx", FakeVision(), roi=None)
        assert r.values.雇主名稱_中 == ""
        assert r.疑慮標示 == "H~O:找不到契約頁,未擷取"

    def test_no_images_does_not_rescue(self, monkeypatch):
        calls = []
        monkeypatch.setattr(ee, "_scored_pages", lambda p: [])
        monkeypatch.setattr(ee, "rescue_name_from_pledge",
                            lambda p, vision: calls.append(p) or ("姜信安", "image5.jpeg"))
        r = ee.extract_employer_from_docx("x.docx", FakeVision(), roi=None)
        assert calls == [] and r.疑慮標示 == "H~O:docx 無內嵌圖,未擷取"

    def test_extraction_failure_does_not_rescue(self, tmp_path, monkeypatch, offline_identity):
        import pipeline
        from batch import Batch
        calls = []
        monkeypatch.setattr(ee, "rescue_name_from_pledge",
                            lambda p, vision: calls.append(p) or ("姜信安", "image5.jpeg"))
        monkeypatch.setattr("employer_extract.extract_employer_from_docx",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("壞掉")))
        got = pipeline.collect_employer_fields(
            Batch.from_file(tmp_path / "99996.docx", output_dir=tmp_path))
        assert calls == []
        assert got["99996.docx"].疑慮標示 == "H~O:雇主擷取失敗(例外),未擷取"


class TestTemplateDoubtIsAboutTheWholeDocx:

    def test_unrecognised_template_prefix(self):
        r = ee.extract_employer_fields("\n".join([
            "甲方名稱: 王小明", "SELANJUTNYA DISEBUT PIHAK PERTAMA"]))
        assert r.source.template is Template.認不出
        assert r.疑慮標示.startswith("H~O:契約樣板未辨識,以家庭類規則擷取")


class TestLogMatchesSheets:

    def _collect(self, tmp_path, monkeypatch, result):
        import pipeline
        from batch import Batch
        monkeypatch.setattr("employer_extract.extract_employer_from_docx", lambda *a, **k: result)
        pipeline.collect_employer_fields(Batch.from_file(tmp_path / "99995.docx",
                                                         output_dir=tmp_path))

    def test_rescued_row_does_not_claim_h_to_o_empty(self, tmp_path, monkeypatch, caplog,
                                                     offline_identity):
        from dataclasses import replace
        from employer_result import EmployerValues
        r = replace(EmployerExtraction(ExtractionStatus.雇主區塊未錨定),
                    values=EmployerValues(雇主名稱_中="姜信安"))
        with caplog.at_level(logging.INFO):
            self._collect(tmp_path, monkeypatch, r)
        assert "H~O 留空" not in caplog.text
        assert "雇主區塊未錨定" in caplog.text and "姜信安" in caplog.text

    def test_empty_row_says_h_to_o_empty(self, tmp_path, monkeypatch, caplog, offline_identity):
        with caplog.at_level(logging.INFO):
            self._collect(tmp_path, monkeypatch, EmployerExtraction(ExtractionStatus.找不到契約頁))
        assert "找不到契約頁" in caplog.text and "H~O 留空" in caplog.text
