# -*- coding: utf-8 -*-
"""真實素材 corpus(real_samples)本身的行為(#31)。

素材、核對值都放在 samples/ 底下(不進版控),測試一律經由 real_samples 取用。
這裡用 tmp 目錄假造一份 samples/,不碰真的素材。
"""
import json

import pytest

import real_samples as rs


@pytest.fixture
def corpus(tmp_path, monkeypatch):
    """把 corpus 指到 tmp 底下一份空的 samples/。"""
    monkeypatch.setattr(rs, "ROOT", tmp_path / "samples")
    return tmp_path / "samples"


def _no_skip(fn, *args):
    """素材在場時必須拿得到 —— skip 在這裡就是 bug,不能讓它變成安靜的略過。"""
    try:
        return fn(*args)
    except pytest.skip.Exception as e:
        pytest.fail(f"素材明明在,卻 skip 了:{e}")


def _write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


class TestCheckedValues:

    def test_contract_page(self, corpus):
        _write_json(corpus / "checked_values.json",
                    {"33701": {"契約頁": "image2.png"}})
        assert rs.checked_contract_page("33701.docx") == "image2.png"

    def test_no_checked_value_is_none_not_a_guess(self, corpus):
        _write_json(corpus / "checked_values.json",
                    {"33701": {"契約頁": "image2.png"}})
        assert rs.checked_contract_page("33799.docx") is None

    def test_employer_fields_may_be_partial(self, corpus):
        """核對值允許只填部分欄位:人確認過哪一欄就填哪一欄。"""
        _write_json(corpus / "checked_values.json",
                    {"33703": {"雇主欄位": {"電話": "06-0000000"}}})
        assert rs.checked_employer_fields("33703") == {"電話": "06-0000000"}

    def test_no_file_means_no_checked_values(self, corpus):
        assert rs.checked_contract_page("33701.docx") is None
        assert rs.checked_employer_fields("33701") == {}

    def test_record_contract_page_keeps_other_values(self, corpus):
        _write_json(corpus / "checked_values.json",
                    {"33703": {"雇主欄位": {"電話": "06-0000000"}}})
        rs.record_checked_contract_page("33703.docx", "image2.png")
        rs.record_checked_contract_page("33711.docx", "image3.jpeg")
        assert rs.checked_contract_page("33703.docx") == "image2.png"
        assert rs.checked_employer_fields("33703") == {"電話": "06-0000000"}
        assert rs.checked_contract_page("33711.docx") == "image3.jpeg"


class TestVisionTexts:

    def test_employer_text_is_the_roi_the_pipeline_sends(self, corpus):
        _write_json(corpus / "vision" / "33701.json",
                    {"image": "image2.png", "roi": "0.35",
                     "texts": {"0.35": "甲方 王小明", "1.0": "整頁"}})
        assert _no_skip(rs.employer_text, "33701") == "甲方 王小明"

    def test_stems_with_vision_text(self, corpus):
        for stem in ("33702", "33701"):
            _write_json(corpus / "vision" / f"{stem}.json",
                        {"image": "image2.png", "roi": "0.35", "texts": {"0.35": ""}})
        assert rs.vision_stems() == ["33701", "33702"]

    def test_no_vision_folder_means_no_stems(self, corpus):
        assert rs.vision_stems() == []

    def test_saved_text_reads_back_as_employer_text(self, corpus):
        rs.save_vision_text("33711", "image2.jpeg", "0.35", "甲方 王小明")
        assert _no_skip(rs.employer_text, "33711") == "甲方 王小明"
        assert rs.vision_sample("33711")["image"] == "image2.jpeg"

    def test_saved_text_is_never_overwritten(self, corpus):
        """每份素材的全文只打一次;第二次存檔一定是流程錯了,不能蓋掉花錢買來的那份。"""
        rs.save_vision_text("33711", "image2.jpeg", "0.35", "第一次")
        with pytest.raises(FileExistsError):
            rs.save_vision_text("33711", "image3.jpeg", "0.35", "第二次")
        assert rs.employer_text("33711") == "第一次"

    def test_missing_text_skips(self, corpus):
        with pytest.raises(pytest.skip.Exception):
            rs.employer_text("33701")


class TestLegacyFiles:

    def test_old_matches_csv_lives_in_samples(self, corpus):
        p = corpus / "legacy" / "matches.21份.backup.csv"
        p.parent.mkdir(parents=True)
        p.write_text("source_docx\n", encoding="utf-8")
        assert _no_skip(rs.old_matches_csv) == p

    def test_old_matches_csv_missing_skips(self, corpus):
        with pytest.raises(pytest.skip.Exception):
            rs.old_matches_csv()


class TestDocx:

    def test_word_lock_files_are_not_samples(self, corpus):
        (corpus / "docs").mkdir(parents=True)
        for name in ("33701.docx", "~$33701.docx"):
            (corpus / "docs" / name).write_bytes(b"")
        assert [p.name for p in rs.docx_samples()] == ["33701.docx"]

    def test_no_docs_folder_means_no_samples(self, corpus):
        assert rs.docx_samples() == []
