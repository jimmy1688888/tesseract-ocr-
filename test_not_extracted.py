# -*- coding: utf-8 -*-
"""沒擷取到雇主資料時,L 欄寫明原因(#46)。

舊做法四種「整份沒擷取」H~O 全空、L 也空,原因只在日誌 —— 在 Sheets 上跟「契約上
真的沒填」分不出來。雇主區塊未錨定時切結書頁救回中文名 → H 有值、L 只寫「取自
切結書頁」,看不出整個區塊其實沒錨到;日誌卻印「H~O 留空」。資料全部捏造。
"""
import io
import logging

import pytest
from PIL import Image

from conftest import FakeIdentifier, FakeVision, pages_of
import employer_extract as ee
from contract_template import Template
from employer_result import EmployerExtraction, EmployerValues, ExtractionStatus
from vision_reader import VisionCallFailed

_NO_ANCHOR = "勞動契約 監護工\n仲介公司 某某人力"


# 切結書頁:image4 是切結書第一頁,雇主姓名在 image5(ADR-0006)。
_PLEDGE = ((0, "image4.jpeg", b"image4.jpeg", True), (0, "image5.jpeg", b"image5.jpeg"))
_PLEDGE_TEXT = "中華民國雇主/Majikan R.O.C: 姜信安/JIANG SIN AN"


def _contract(score: int, image: bytes = b"x"):
    """契約頁 image2(score 分),後面接切結書頁。"""
    return pages_of((score, "image2.jpeg", image), *_PLEDGE)


def _vision(contract_text: str = "", pledge_text: str = _PLEDGE_TEXT) -> FakeVision:
    """契約頁讀到 contract_text、切結書頁讀到 pledge_text。"""
    return FakeVision(document=lambda b: pledge_text if b == b"image5.jpeg" else contract_text)


class TestReasonInLColumn:

    @pytest.mark.parametrize("status, l_column", [
        (ExtractionStatus.雇主區塊未錨定, "H~O:契約頁雇主區塊未錨定,未擷取"),
        (ExtractionStatus.找不到契約頁, "H~O:找不到契約頁,未擷取"),
        (ExtractionStatus.docx無內嵌圖, "H~O:docx 無內嵌圖,未擷取"),
        (ExtractionStatus.契約頁Vision呼叫失敗, "H~O:契約頁 Vision 呼叫失敗,未擷取"),
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
        r = ee.extract_employer_from_docx(_contract(99), _vision(_NO_ANCHOR),
                                          roi=None, deink=False)
        assert r.status is ExtractionStatus.雇主區塊未錨定
        assert r.values.雇主名稱_中 == "姜信安"
        assert r.疑慮標示 == "H~O:契約頁雇主區塊未錨定,未擷取；H:中文名取自切結書頁(image5.jpeg)"

    def test_contract_page_not_found_then_rescue(self, monkeypatch):
        r = ee.extract_employer_from_docx(_contract(0), _vision(), roi=None)
        assert r.status is ExtractionStatus.找不到契約頁
        assert r.values.雇主名稱_中 == "姜信安"
        assert r.疑慮標示 == "H~O:找不到契約頁,未擷取；H:中文名取自切結書頁(image5.jpeg)"

    def test_failed_rescue_leaves_only_the_reason(self, monkeypatch):
        r = ee.extract_employer_from_docx(_contract(0), _vision(pledge_text="無關文字"), roi=None)
        assert r.values.雇主名稱_中 == ""
        assert r.疑慮標示 == "H~O:找不到契約頁,未擷取"

    def test_no_images_does_not_rescue(self):
        r = ee.extract_employer_from_docx(pages_of(), FakeVision(), roi=None)   # 讀整頁就會紅
        assert r.疑慮標示 == "H~O:docx 無內嵌圖,未擷取"

    def test_extraction_failure_does_not_rescue(self, tmp_path, monkeypatch):
        import pipeline
        from batch import Batch
        monkeypatch.setattr("employer_extract.extract_employer_from_docx",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("壞掉")))
        vision = FakeVision()                   # 沒給讀整頁:救援一跑就紅
        got = pipeline.collect_employer_fields(
            Batch.from_file(tmp_path / "99996.docx", output_dir=tmp_path), vision,
            identifier=FakeIdentifier(*_PLEDGE))
        assert got["99996.docx"].疑慮標示 == "H~O:雇主擷取失敗(例外),未擷取"


class TestContractPageVisionCallFailed:
    """契約頁沒問到:自己一種擷取狀況,不和程式例外混在「擷取失敗」(#66、ADR-0015)。"""

    @staticmethod
    def _failing(image):
        raise VisionCallFailed("503")

    def test_has_its_own_status_and_keeps_the_page(self):
        r = ee.extract_employer_from_docx(_contract(99), FakeVision(document=self._failing),
                                          roi=None, deink=False)
        assert r.status is ExtractionStatus.契約頁Vision呼叫失敗
        assert r.values == EmployerValues()
        assert r.source.image == "image2.jpeg"        # 截圖照樣留著,人工看得到
        assert r.疑慮標示 == "H~O:契約頁 Vision 呼叫失敗,未擷取"

    def test_pledge_rescue_does_not_run(self):
        vision = FakeVision(document=self._failing)
        ee.extract_employer_from_docx(_contract(99), vision, roi=None, deink=False)
        assert vision.document_reads == [b"x"]          # 只有契約頁,沒去讀切結書頁

    def test_one_failure_does_not_touch_the_others(self, tmp_path):
        import pipeline
        from batch import Batch
        (tmp_path / "bad.docx").write_bytes(b"")
        (tmp_path / "ok.docx").write_bytes(b"")
        page = io.BytesIO()
        Image.new("RGB", (40, 40), "white").save(page, format="PNG")
        text = "\n".join(["甲方名稱: 王小明", "Nama Majikan: WANG XIAO MING",
                          "SELANJUTNYA DISEBUT PIHAK PERTAMA"])
        reads = []

        def read(image):                        # 一批依檔名排序:bad.docx 先讀
            reads.append(image)
            if len(reads) == 1:
                raise VisionCallFailed("503")
            return text
        got = pipeline.collect_employer_fields(
            Batch.from_dir(tmp_path, output_dir=tmp_path / "out"), FakeVision(document=read),
            identifier=FakeIdentifier((99, "image2.jpeg", page.getvalue())))
        assert got["bad.docx"].status is ExtractionStatus.契約頁Vision呼叫失敗
        assert got["ok.docx"].values.雇主名稱_中 == "王小明"

    def test_identity_refused_aborts_the_run(self, tmp_path, monkeypatch):
        """403/401 換下一份也一樣:不吞成擷取失敗,往上拋讓整批中止。"""
        import pipeline
        from batch import Batch
        from google.api_core.exceptions import PermissionDenied

        def refused(*a, **k):
            raise PermissionDenied("403")
        monkeypatch.setattr("employer_extract.extract_employer_from_docx", refused)
        with pytest.raises(PermissionDenied):
            pipeline.collect_employer_fields(
                Batch.from_file(tmp_path / "99994.docx", output_dir=tmp_path), FakeVision(),
                identifier=FakeIdentifier())


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
                                                         output_dir=tmp_path),
                                         FakeVision(), identifier=FakeIdentifier())

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
