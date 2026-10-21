# -*- coding: utf-8 -*-
"""雇主擷取結果是一個有形狀的值:擷取狀況、欄位值、疑慮、來源(#45)。

舊做法是一個扁平 dict,混著輸出欄、內部疑慮、流程中介(`_image`、`_note`…);
擷取拋例外的那份 docx 甚至不在結果裡。資料全部捏造。
"""
import employer_extract as ee
from employer_result import (EMPLOYER_COLUMNS, Doubt, EmployerExtraction, EmployerValues,
                             ExtractionStatus)

_TEXT = "\n".join(["甲方名稱: 王小明", "Nama Majikan: WANG XIAO MING",
                   "SELANJUTNYA DISEBUT PIHAK PERTAMA"])


class TestStatusFromDocx:
    """extract_employer_from_docx 的每條路都回一個擷取結果,狀況講明走了哪條。"""

    def test_no_images(self, monkeypatch):
        monkeypatch.setattr(ee, "_scored_pages", lambda p: [])
        r = ee.extract_employer_from_docx("x.docx", roi=None)
        assert r.status is ExtractionStatus.docx無內嵌圖
        assert r.values == EmployerValues() and r.doubts == ()

    def test_contract_page_not_found_keeps_the_candidate_page(self, monkeypatch):
        monkeypatch.setattr(ee, "_scored_pages", lambda p: [(0, "image2.jpeg", b"x")])
        monkeypatch.setattr(ee, "rescue_name_from_pledge", lambda p, client=None: ("", ""))
        r = ee.extract_employer_from_docx("x.docx", roi=None)
        assert r.status is ExtractionStatus.找不到契約頁
        assert r.source.image == "image2.jpeg"
        assert r.values == EmployerValues()

    def test_success_carries_the_source(self, monkeypatch):
        monkeypatch.setattr(ee, "_scored_pages", lambda p: [(99, "image2.jpeg", b"x")])
        monkeypatch.setattr(ee, "vision_full_text", lambda b, client=None: _TEXT)
        r = ee.extract_employer_from_docx("x.docx", roi=None, deink=False)
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
        row = pipeline._row_to_sheet_values({"source_docx": "99997.docx"},
                                            pipeline.SheetStatus.MANUAL_REVIEW, got)
        assert row[7:11] + row[12:15] == [""] * 7         # H~O 照舊留空
        assert row[11] == "H~O:雇主擷取失敗(例外),未擷取"   # L 寫原因(#46)


class TestColumns:

    def test_h_to_o_in_order(self):
        assert [c for c, _k in EMPLOYER_COLUMNS] == list("HIJKLMNO")

    def test_cell_gives_values_and_the_composed_l_column(self):
        r = EmployerExtraction(ExtractionStatus.擷取成功, EmployerValues(電話="02-1234"),
                               (Doubt("地址_中_標準", "中文地址未擷取到"),))
        assert r.cell("電話") == "02-1234"
        assert r.cell("疑慮標示") == "J:中文地址未擷取到"
