# -*- coding: utf-8 -*-
"""疑慮標示的管線接線:L 欄、疑慮報表、Vision 全文存檔(ADR-0003)。

雇主欄位是整條 pipeline 唯一沒有權威名冊可交叉驗證的部分,而 C 欄的
keyed-in/manual_review 完全由許可證判讀決定——雇主資料只是搭便車。疑慮標示讓人
核對時知道該看哪一格。郵遞區號判定本身(縣市區表)見 test_zip_determination.py。
"""
from conftest import FakeIdentifier, FakeVision
import json
from dataclasses import replace
from pathlib import Path

from employer_result import (Doubt, EmployerExtraction, EmployerValues, ExtractionStatus,
                             Source)


def _result(doubts=(), **values):
    """一份擷取成功的雇主擷取結果(捏造)。"""
    return EmployerExtraction(ExtractionStatus.擷取成功, EmployerValues(**values), tuple(doubts))


class TestSheetWiring:
    """疑慮標示佔 L 欄(原英文標準地址),整列維持 15 欄。

    英文標準地址取消輸出:契約英譯沒有單一正確寫法,官方譯名並不比契約上的更權威,
    兩欄並列只是讓人多比對一次卻分不出對錯。英文一律以 OCR 原文(M 欄)為準。
    """

    L, M = 11, 12       # A=0 … L=11 M=12(H~O 對應 index 7~14)

    def _row(self, docx, result):
        import pipeline
        from sheet_row import SheetRow, SheetStatus
        return pipeline._row_to_sheet_values(
            SheetRow(docx, "2340", SheetStatus.KEYED_IN, ""), {docx: result}, None)

    def test_row_is_fifteen_columns(self):
        assert len(self._row("a.docx", _result())) == 15

    def test_doubt_lands_in_column_l(self):
        row = self._row("b.docx", _result([Doubt("地址_中", "路名未在該行政區的路名清單中")]))
        assert row[self.L] == "地址_中:路名未在該行政區的路名清單中"

    def test_english_column_is_ocr_verbatim(self):
        """M 欄只放 OCR 原文,官方英譯不得從任何路徑出現在列上。"""
        row = self._row("e.docx", _result(
            地址_英="7F., No.29, Panshih Rd., North Dist., Hsinchu City 300068"))
        assert row[self.M] == "7F., No.29, Panshih Rd., North Dist., Hsinchu City 300068"
        assert "Panshi Rd." not in row[self.L]

    def test_english_ocr_empty_stays_empty(self):
        """讀不到就空——不拿官方譯名充數,否則 M 欄的語意會出現例外。"""
        assert self._row("f.docx", _result(地址_中_標準="宜蘭縣宜蘭市津梅路142巷2號"))[self.M] == ""

    def test_clean_row_has_empty_doubt(self):
        row = self._row("c.docx", _result(地址_中_標準="新竹市北區磐石路29號7樓"))
        assert row[self.L] == ""

    def test_doubt_does_not_leak_into_reason_column(self):
        """D 欄專講許可證判讀,不能被雇主的疑慮汙染(一個欄位不承載兩種語意)。"""
        row = self._row("d.docx", _result([Doubt("郵遞區號", "縣市未比對到官方資料庫")]))
        assert "縣市" not in row[3]

    def test_unknown_docx_gives_empty_doubt_not_crash(self):
        import pipeline
        from sheet_row import SheetRow, SheetStatus
        row = pipeline._row_to_sheet_values(
            SheetRow("never-seen.docx", "", SheetStatus.MANUAL_REVIEW, ""), {}, None)
        assert len(row) == 15 and row[self.L] == ""


class TestVisionTextIsPersisted:
    """Vision 全文要真的落地——它是唯一花錢買來的東西,丟了就只能憑猜測改規則。

    這條路徑先前差點靜默壞掉:pipeline.py 沒有 import json,而沒有測試走到
    write_employer_texts,整份存檔會在跑真實資料時才 NameError。
    """

    @staticmethod
    def _batch(out):
        from batch import Batch
        return Batch((), source=out, output_dir=out)

    def test_writes_json_and_returns_count(self, tmp_path):
        import pipeline
        target = tmp_path / "employer_texts.json"
        n = pipeline.write_employer_texts(self._batch(tmp_path), {
            "32538.docx": {"image": "image2.jpeg", "text": "甲方名稱: 王小明\n地址:"},
        })
        assert n == 1
        data = json.loads(target.read_text(encoding="utf-8"))
        assert data["32538.docx"]["text"].startswith("甲方名稱")
        assert data["32538.docx"]["image"] == "image2.jpeg"

    def test_empty_input_writes_nothing(self, tmp_path):
        import pipeline
        target = tmp_path / "employer_texts.json"
        assert pipeline.write_employer_texts(self._batch(tmp_path), {}) == 0
        assert not target.exists()

    def test_write_failure_does_not_raise(self, tmp_path, monkeypatch):
        """輔助產物寫不出來,不該拖垮已經跑完的主流程。"""
        import pipeline
        monkeypatch.setattr(Path, "mkdir",
                            lambda *a, **k: (_ for _ in ()).throw(OSError("nope")))
        assert pipeline.write_employer_texts(
            self._batch(tmp_path / "x"), {"a.docx": {"text": "x"}}) == 0

    def test_text_is_moved_out_of_the_returned_fields(
        self, tmp_path, monkeypatch, offline_identity
    ):
        """全文另存後不留在回傳的欄位裡:組每一列時都會查,夾一大塊文字只是負擔。"""
        import pipeline
        from batch import Batch
        monkeypatch.setattr(
            "employer_extract.extract_employer_from_docx",
            lambda *a, **k: replace(_result(雇主名稱_中="王小明"),
                                    source=Source(image="image2.jpeg", text="甲方名稱: 王小明")))
        employer = pipeline.collect_employer_fields(
            Batch.from_file(tmp_path / "99999.docx", output_dir=tmp_path), FakeVision(),
            identifier=FakeIdentifier())
        result = employer["99999.docx"]
        assert result.source.text == ""
        assert result.values.雇主名稱_中 == "王小明"
        saved = json.loads(
            (tmp_path / "employer_texts.json").read_text(encoding="utf-8"))
        assert saved["99999.docx"]["text"] == "甲方名稱: 王小明"


class TestEmployerOutputFollowsBatch:
    """疑慮報表與雇主截圖寫在這一批的輸出目錄底下,不是寫死的 scan_results/(#24)。"""

    def test_report_lands_in_output_dir(self, tmp_path, monkeypatch, offline_identity):
        import pipeline
        from batch import Batch
        monkeypatch.chdir(tmp_path)              # 預設的 ./scan_results 會落在這裡
        crop_dirs = []

        def fake_extract(path, vision, crop_dir=""):
            crop_dirs.append(Path(crop_dir))
            return _result([Doubt("郵遞區號", "與地址不符")], 雇主名稱_中="王小明")

        monkeypatch.setattr("employer_extract.extract_employer_from_docx", fake_extract)
        out = tmp_path / "另一處"
        b = Batch.from_file(tmp_path / "99998.docx", output_dir=out)
        pipeline.write_doubt_report(b, pipeline.collect_employer_fields(b, FakeVision(),
                                                                        identifier=FakeIdentifier()))
        report = (out / "address_doubts.csv").read_text(encoding="utf-8-sig")
        assert "99998.docx" in report
        assert crop_dirs == [out / "employer_crops"]   # 雇主截圖也跟著走
        assert not (tmp_path / "scan_results").exists()


class TestDoubtReportOrder:
    """疑慮報表依檔名排序 —— 人工數誤報率時要能跟上一次的報表逐列對照。"""

    def test_rows_sorted_by_docx_name(self, tmp_path):
        import pipeline
        from batch import Batch
        doubt = _result([Doubt("郵遞區號", "與地址不符")])
        employer_by_docx = {"32574.docx": doubt, "32503.docx": doubt, "32538.docx": doubt}
        b = Batch((), source=tmp_path, output_dir=tmp_path)
        assert pipeline.write_doubt_report(b, employer_by_docx) == 3
        lines = (tmp_path / "address_doubts.csv").read_text(encoding="utf-8-sig").splitlines()
        assert [ln.split(",")[0] for ln in lines[1:]] == [
            "32503.docx", "32538.docx", "32574.docx"]


class TestDoubtReportOnlyListsDoubts:

    def test_docx_without_doubt_is_not_listed(self, tmp_path):
        import pipeline
        from batch import Batch
        employer_by_docx = {"32574.docx": _result([Doubt("郵遞區號", "與地址不符")]),
                            "32503.docx": _result(雇主名稱_中="王小明")}
        b = Batch((), source=tmp_path, output_dir=tmp_path)
        assert pipeline.write_doubt_report(b, employer_by_docx) == 1
        report = (tmp_path / "address_doubts.csv").read_text(encoding="utf-8-sig")
        assert "32574.docx" in report and "32503.docx" not in report


class TestTwoBatchesDoNotLeak:
    """同一個 process 連跑兩批,第二批的疑慮報表不帶到第一批的結果(#25)。

    雇主欄位以前寄放在 process 全域的 dict,寫入方與讀取方從簽名上看不出來,
    而且只增不減 —— 第二批的報表會把第一批的疑慮也列進去。CLI 一次只跑一批所以
    平常看不到,但測試、或任何在同一個 process 裡跑兩批的呼叫端都會撞到。
    """

    def test_second_report_lists_only_second_batch(
        self, tmp_path, monkeypatch, offline_identity
    ):
        import pipeline
        from batch import Batch
        monkeypatch.setattr(
            "employer_extract.extract_employer_from_docx",
            lambda pages, vision, **k: _result([Doubt("郵遞區號", "與地址不符")], 雇主名稱_中="王小明"))

        def run(name, out):
            b = Batch.from_file(tmp_path / name, output_dir=out)
            pipeline.write_doubt_report(b, pipeline.collect_employer_fields(b, FakeVision(),
                                                                            identifier=FakeIdentifier()))
            return (out / "address_doubts.csv").read_text(encoding="utf-8-sig")

        assert "11111.docx" in run("11111.docx", tmp_path / "第一批")
        second = run("22222.docx", tmp_path / "第二批")
        assert "22222.docx" in second
        assert "11111.docx" not in second


