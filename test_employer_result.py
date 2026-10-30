# -*- coding: utf-8 -*-
"""雇主擷取結果是一個有形狀的值:擷取狀況、欄位值、疑慮、來源(#45)。

舊做法是一個扁平 dict,混著輸出欄、內部疑慮、流程中介(`_image`、`_note`…);
擷取拋例外的那份 docx 甚至不在結果裡。資料全部捏造。
"""
from conftest import FakeVision
import employer_extract as ee
from employer_result import (DOUBT_KEY, EMPLOYER_COLUMNS, VALUE_KEYS, Doubt, EmployerExtraction,
                             EmployerValues, ExtractionStatus)
from sheet_row import SheetRow, SheetStatus

_TEXT = "\n".join(["甲方名稱: 王小明", "Nama Majikan: WANG XIAO MING",
                   "SELANJUTNYA DISEBUT PIHAK PERTAMA"])


class TestStatusFromDocx:
    """extract_employer_from_docx 的每條路都回一個擷取結果,狀況講明走了哪條。"""

    def test_no_images(self, monkeypatch):
        monkeypatch.setattr(ee, "_scored_pages", lambda p: [])
        r = ee.extract_employer_from_docx("x.docx", FakeVision(), roi=None)
        assert r.status is ExtractionStatus.docx無內嵌圖
        assert r.values == EmployerValues() and r.doubts == ()

    def test_contract_page_not_found_keeps_the_candidate_page(self, monkeypatch):
        monkeypatch.setattr(ee, "_scored_pages", lambda p: [(0, "image2.jpeg", b"x")])
        monkeypatch.setattr(ee, "rescue_name_from_pledge", lambda p, vision: ("", ""))
        r = ee.extract_employer_from_docx("x.docx", FakeVision(), roi=None)
        assert r.status is ExtractionStatus.找不到契約頁
        assert r.source.image == "image2.jpeg"
        assert r.values == EmployerValues()

    def test_success_carries_the_source(self, monkeypatch):
        monkeypatch.setattr(ee, "_scored_pages", lambda p: [(99, "image2.jpeg", b"x")])
        r = ee.extract_employer_from_docx("x.docx", FakeVision(document=lambda b: _TEXT),
                                          roi=None, deink=False)
        assert r.status is ExtractionStatus.擷取成功
        assert (r.source.image, r.source.text) == ("image2.jpeg", _TEXT)
        assert r.values.雇主名稱_中 == "王小明"

    def test_anchor_failure_is_its_own_status(self):
        r = ee.extract_employer_fields("勞動契約 監護工")
        assert r.status is ExtractionStatus.雇主區塊未錨定
        assert r.values == EmployerValues()


class TestExtractionFailureIsAResult:
    """單份拋例外不再從結果消失:一樣有一個擷取結果,狀況是擷取失敗。"""

    def test_failed_docx_is_in_the_result(self, tmp_path, monkeypatch, offline_identity):
        import pipeline
        from batch import Batch

        def boom(*a, **k):
            raise RuntimeError("壞掉的 docx")

        monkeypatch.setattr("employer_extract.extract_employer_from_docx", boom)
        got = pipeline.collect_employer_fields(
            Batch.from_file(tmp_path / "99997.docx", output_dir=tmp_path))
        r = got["99997.docx"]
        assert r.status is ExtractionStatus.擷取失敗
        row = pipeline._row_to_sheet_values(
            SheetRow("99997.docx", "", SheetStatus.MANUAL_REVIEW, ""), got, None)
        assert row[7:11] + row[12:15] == [""] * 7         # H~O 照舊留空
        assert row[11] == "H~O:雇主擷取失敗(例外),未擷取"   # L 寫原因(#46)


class TestSheetsRowPhone:
    """寫 Sheets 的那一列:O 欄是電話顯示形,擷取結果的值仍是標準形(#49)。"""

    def test_o_column_written_in_display_form(self, offline_identity):
        import pipeline
        got = {"99996.docx": EmployerExtraction(ExtractionStatus.擷取成功,
                                                EmployerValues(電話="0920875667"))}
        row = pipeline._row_to_sheet_values(
            SheetRow("99996.docx", "", SheetStatus.MANUAL_REVIEW, ""), got, None)
        assert row[14] == "0920-875-667"                  # O 欄
        assert got["99996.docx"].values.電話 == "0920875667"


class TestColumns:

    def test_h_to_o_in_order(self):
        assert [c for c, _k in EMPLOYER_COLUMNS] == list("HIJKLMNO")

    def test_columns_cover_exactly_the_values_plus_l(self):
        """欄位值改名時,欄位對照表一定要跟著改 —— 兩份清單在這裡交叉檢查。"""
        assert {k for _c, k in EMPLOYER_COLUMNS} == {*VALUE_KEYS, DOUBT_KEY}

    def test_doubt_on_unknown_field_fails_at_construction(self):
        import pytest
        with pytest.raises(ValueError):
            Doubt("地址_中標準", "打錯字的欄位")

    def test_cell_gives_values_and_the_composed_l_column(self):
        r = EmployerExtraction(ExtractionStatus.擷取成功, EmployerValues(地址_中="台北市"),
                               (Doubt("地址_中_標準", "中文地址未擷取到"),))
        assert r.cell("地址_中") == "台北市"
        assert r.cell("疑慮標示") == "J:中文地址未擷取到"

    def test_o_column_is_display_form_but_value_stays_standard_form(self):
        """O 欄寫電話顯示形;擷取結果的值仍是電話標準形(比對、快照都用它)(#49)。"""
        r = EmployerExtraction(ExtractionStatus.擷取成功, EmployerValues(電話="037985657"))
        assert r.cell("電話") == "037-985657"
        assert r.values.電話 == "037985657"

    def test_o_column_empty_when_no_phone(self):
        assert EmployerExtraction(ExtractionStatus.擷取成功).cell("電話") == ""
