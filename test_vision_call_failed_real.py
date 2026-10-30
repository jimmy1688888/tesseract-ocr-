# -*- coding: utf-8 -*-
"""真實 docx 遇到 Vision 呼叫失敗時,L 欄寫什麼(#66、ADR-0015)。

認頁(契約頁、切結書頁)走真的 Tesseract,Vision 換成指定那一次失敗的假 reader ——
要的是「真實文件走到那一步時沒問到」,不必真的等 Google 掛掉。素材或 Tesseract 不在就 skip。
"""
import pytest

from conftest import FakeVision
import real_samples as rs
from employer_extract import extract_employer_from_docx
from employer_result import ExtractionStatus
from vision_reader import VisionCallFailed

# 契約頁讀到了,但沒有中文名 —— 切結書頁備援才會跑。
_NO_CHINESE_NAME = "Nama Majikan: WANG XIAO MING\nSELANJUTNYA DISEBUT PIHAK PERTAMA"


def _fail(image):
    raise VisionCallFailed("503")


def test_33703_contract_page_failure(real_ocr):
    result = extract_employer_from_docx(str(rs.sample("33703.docx")), FakeVision(document=_fail))

    assert result.status is ExtractionStatus.契約頁Vision呼叫失敗
    assert result.source.image == rs.checked_contract_page("33703.docx")
    assert result.疑慮標示 == "H~O:契約頁 Vision 呼叫失敗,未擷取"


def test_30766_pledge_page_failure_stops_at_the_first_page(real_ocr):
    """30766 的切結書有兩張候選頁;第一張沒問到就停,不打第二張。"""
    reads = []

    def read(image):
        reads.append(image)
        if len(reads) == 1:
            return _NO_CHINESE_NAME            # 契約頁
        raise VisionCallFailed("503")          # 切結書頁

    result = extract_employer_from_docx(str(rs.sample("30766.docx")), FakeVision(document=read))

    assert len(reads) == 2
    assert result.values.雇主名稱_中 == ""
    assert "H:未擷取到中文名；H:切結書頁 Vision 呼叫失敗" in result.疑慮標示


def test_33703_next_page_failure_is_stated_in_d(real_ocr, monkeypatch):
    """#67:反查救援讀真實 docx 的次頁(真 Tesseract 認契約頁)時沒問到 → D 欄註明。"""
    import pipeline
    from batch import Batch
    from sheet_row import SheetRow, SheetStatus

    vision = FakeVision(document=_fail)
    seed = SheetRow("33703.docx", "", SheetStatus.MANUAL_REVIEW, "large:全無命中")

    [row] = pipeline.rescue_manual_review_via_agency_phone(
        Batch.from_file(rs.sample("33703.docx")), [seed], object(), vision)   # 名冊在,才走得到次頁

    assert len(vision.document_reads) == 1                       # 真的走到次頁那一次
    assert (row.permit, row.status) == ("", SheetStatus.MANUAL_REVIEW)
    assert row.reason == "large:全無命中；反查救援未完成:次頁 Vision 呼叫失敗"
