# -*- coding: utf-8 -*-
"""真的打 Google Vision,確認 GoogleVisionReader 兩種讀法接對了(#64)。

快照重放的是存下來的全文,根本不經過 adapter:feature 接反、bytes 包錯,其他測試照樣全綠。
這裡各打一次真的 Vision,比對**取出來的值**等於核對值 —— 不比全文,Google 的模型會更新,
多一個空白或換行不該算錯。

花錢、要憑證,所以平常不跑:pytest -m vision。跑之前先 check_vision(真的送一張小圖),
Vision 沒開或拿不到憑證就整個 skip,不重試。
"""
from pathlib import Path

import pytest

import google_clients
import identity
import real_samples as rs
from vision_reader import GoogleVisionReader

pytestmark = pytest.mark.vision


@pytest.fixture(autouse=True)
def real_vision():
    """拿掉測試護欄、換回真身分;Vision 不可用就 skip。"""
    identity.reset()
    google_clients.reset()
    from gcp_identity.checks import check_vision
    r = check_vision(identity.current())
    if not r.ok or r.skipped:
        pytest.skip(f"Vision 不可用(先開 Vision 再跑):{r.detail or r.fix}")


def test_read_sparse_keys_in_the_checked_permit_of_30768():
    import pipeline
    from vision_verification import run_vision_items

    vision_decisions = pipeline.build_vision_queue(rs.permit_scan_csv()).vision
    items = [d for d in vision_decisions if Path(d.source_docx).stem == "30768"]
    if not items or not Path(items[0].img_path).exists():
        pytest.skip("30768 送 Vision 的截圖不在最新的某日掃描")

    auto, review = run_vision_items(items, GoogleVisionReader())

    assert review == []
    assert [r.permit for r in auto] == [rs.checked_permit("30768")]


def test_read_document_reads_the_checked_employer_name_of_33703(real_ocr):
    from employer_extract import extract_employer_from_docx

    expected = rs.checked_employer_fields("33703").get("雇主名稱_中")
    if not expected:
        pytest.skip("33703 沒有雇主名稱核對值")

    result = extract_employer_from_docx(str(rs.sample("33703.docx")), GoogleVisionReader())

    assert result.values.雇主名稱_中 == expected


def test_broken_image_is_a_vision_call_failure_not_an_empty_reading():
    """#65:Google 拒收的圖(截斷的 PNG)是「沒問到」—— raise VisionCallFailed,
    不是空字串(那會被判成沒取到號),也不是讓整批中止的例外。"""
    from vision_reader import VisionCallFailed

    crop = next(rs.ROOT.glob("permit_scan/*/permit_crops/30768_*.png"), None)
    if crop is None:
        pytest.skip("沒有 30768 的許可證截圖")
    truncated = crop.read_bytes()[:64]

    with pytest.raises(VisionCallFailed):
        GoogleVisionReader().read_sparse(truncated)
