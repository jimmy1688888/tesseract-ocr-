# -*- coding: utf-8 -*-
"""認頁與認頁結果(CONTEXT「認頁 / 認頁結果」,#77)。

認頁結果是資料:契約頁、次頁、切結書頁都從它推出,不經 docx、不跑 Tesseract。
認頁來源(PageIdentifier)一批共用:同一份 docx 只評一次分,圖的 bytes 每次重讀。
"""
import zipfile

import page_identification as pi
from page_identification import IdentifiedPage, PageIdentification, PageIdentifier

M = pi.MIN_CONTRACT_SCORE


def _pages(*scores, pledge_front: str = "") -> PageIdentification:
    """image1、image2… 依序給分;bytes 就是檔名,讀過哪張一看就知道。"""
    return PageIdentification("x.docx", tuple(
        IdentifiedPage(f"image{i}.png", f"image{i}.png".encode(), s, f"image{i}.png" == pledge_front)
        for i, s in enumerate(scores, start=1)))


def _names(pages) -> list[str]:
    return [p.name for p in pages]


class TestContractPage:
    """最高分頁;未達門檻仍回候選(找到=False);全零分退 image2;同分取圖序在前。"""

    def test_highest_score_is_found(self):
        c = _pages(1, 0, M + 2).contract_page()
        assert (c.page.name, c.page.contract_score, c.found) == ("image3.png", M + 2, True)

    def test_below_threshold_keeps_the_best_as_candidate(self):
        c = _pages(M - 1, 0).contract_page()
        assert (c.page.name, c.found) == ("image1.png", False)

    def test_all_zero_falls_back_to_image2(self):
        c = _pages(0, 0, 0).contract_page()
        assert (c.page.name, c.found) == ("image2.png", False)

    def test_all_zero_without_image2_keeps_the_first(self):
        pages = PageIdentification("x.docx", (IdentifiedPage("image1.png", b"1", 0, False),
                                              IdentifiedPage("image3.png", b"3", 0, False)))
        assert pages.contract_page().page.name == "image1.png"

    def test_tie_goes_to_the_earlier_image(self):
        assert _pages(M, M).contract_page().page.name == "image1.png"

    def test_no_images(self):
        assert _pages().contract_page() is None


class TestNextPage:
    """次頁 = 找到的契約頁的下一張;契約頁找不到(不猜)或已是最後一張 → 沒有。"""

    def test_page_after_the_contract_page(self):
        assert _pages(0, M, 1).next_page().name == "image3.png"

    def test_contract_page_not_found_means_no_next_page(self):
        assert _pages(0, M - 1, 0).next_page() is None

    def test_contract_page_is_last_means_no_next_page(self):
        assert _pages(0, M).next_page() is None

    def test_no_images(self):
        assert _pages().next_page() is None


class TestPledgeNamePages:
    """切結書第一頁之**後**最多 2 頁(雇主姓名那頁,ADR-0006)。"""

    def test_two_pages_after_the_front(self):
        pages = _pages(0, 0, 0, 0, 0, 0, 0, pledge_front="image4.png")
        assert _names(pages.pledge_name_pages()) == ["image5.png", "image6.png"]

    def test_no_front_page_means_none(self):
        assert _pages(0, 0).pledge_name_pages() == ()

    def test_front_page_is_last_means_none(self):
        assert _pages(0, 0, pledge_front="image2.png").pledge_name_pages() == ()


# ── 認頁來源:真的開 docx(最小的 zip),上緣讀字換成計數的假讀字器 ──────────

def _make_docx(path, images: dict[str, bytes]) -> str:
    with zipfile.ZipFile(path, "w") as z:
        for name, data in images.items():
            z.writestr(f"word/media/{name}", data)
    return str(path)


class _CountingTopText:
    """假的上緣讀字:查表回文字,記下每張圖被讀幾次。"""

    def __init__(self, table: dict[bytes, str] | None = None):
        self.table = table or {}
        self.reads: dict[bytes, int] = {}

    def __call__(self, image: bytes) -> str:
        self.reads[image] = self.reads.get(image, 0) + 1
        return self.table.get(image, "")


class TestPageIdentifier:

    def test_pages_in_image_order_with_their_marks(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pi, "deink_red_stamp", lambda b: b)
        top = _CountingTopText({b"i2": "Nama Majikan Alamat", b"i10": "Surat Pernyataan"})
        p = _make_docx(tmp_path / "a.docx", {"image10.png": b"i10", "image2.png": b"i2"})
        got = PageIdentifier(top).identify(p)
        assert got.pages == (IdentifiedPage("image2.png", b"i2", 4, False),
                             IdentifiedPage("image10.png", b"i10", 0, True))

    def test_same_docx_is_scored_once_per_batch(self, tmp_path, monkeypatch):
        """反查救援之後的雇主擷取不重評:兩次 identify 合計只讀一輪上緣。"""
        monkeypatch.setattr(pi, "deink_red_stamp", lambda b: b)
        top = _CountingTopText()
        p = _make_docx(tmp_path / "b.docx", {"image1.png": b"i1", "image2.png": b"i2"})
        identifier = PageIdentifier(top)
        identifier.identify(p)
        identifier.identify(p)
        assert top.reads == {b"i1": 1, b"i2": 1}

    def test_scores_are_kept_but_image_bytes_are_reread(self, tmp_path, monkeypatch):
        """只記分數:第二次拿到的是檔案現在的 bytes,分數沿用第一次的。"""
        monkeypatch.setattr(pi, "deink_red_stamp", lambda b: b)
        top = _CountingTopText({b"old": "Nama Majikan"})
        p = tmp_path / "c.docx"
        identifier = PageIdentifier(top)
        _make_docx(p, {"image1.png": b"old"})
        identifier.identify(str(p))
        _make_docx(p, {"image1.png": b"new"})
        assert identifier.identify(str(p)).pages == (IdentifiedPage("image1.png", b"new", 2, False),)

    def test_a_new_identifier_scores_again(self, tmp_path, monkeypatch):
        """壽命是一批:換一個來源就重評,沒有跨批的全域快取。"""
        monkeypatch.setattr(pi, "deink_red_stamp", lambda b: b)
        top = _CountingTopText()
        p = _make_docx(tmp_path / "d.docx", {"image1.png": b"i1"})
        PageIdentifier(top).identify(p)
        PageIdentifier(top).identify(p)
        assert top.reads == {b"i1": 2}

    def test_empty_docx(self, tmp_path):
        top = _CountingTopText()
        p = _make_docx(tmp_path / "e.docx", {})
        assert PageIdentifier(top).identify(p).pages == () and top.reads == {}


class TestOneIdentificationAcrossStages:
    """反查救援讀次頁、雇主擷取讀契約頁:同一個認頁來源,一份 docx 只評一次分。"""

    def test_rescue_then_employer_scores_once(self, tmp_path, monkeypatch):
        import employer_extract
        import pipeline
        from batch import Batch
        from conftest import FakeVision
        from permit_outcome.rescue import rescue_from_next_page
        from sheet_row import SheetRow, SheetStatus

        monkeypatch.setattr(pi, "deink_red_stamp", lambda b: b)
        monkeypatch.setattr(employer_extract, "agency_block_from_next_page",   # 次頁的 Vision 那端
                            lambda pages, vision: {"phones": [], "name": "", "address": ""})
        top = _CountingTopText()
        docx = _make_docx(tmp_path / "a.docx", {"image1.png": b"i1", "image2.png": b"i2"})
        batch = Batch.from_file(docx, output_dir=tmp_path / "out")
        identifier = PageIdentifier(top)
        seed = [SheetRow("a.docx", "", SheetStatus.MANUAL_REVIEW, "large:全無命中")]

        rescue_from_next_page(batch, seed, object(), FakeVision(), identifier)
        assert top.reads == {b"i1": 1, b"i2": 1}           # 救援用的是傳進來的這一個
        pipeline.collect_employer_fields(batch, FakeVision(), identifier=identifier)
        assert top.reads == {b"i1": 1, b"i2": 1}           # 雇主擷取沿用,不重評


class TestMarksReadTheDeinkedImage:
    """評分前先去紅章(32426/32427:紅章蓋住資料表,原圖 0 分輸給次頁);去紅章失敗退原圖。"""

    def test_top_text_reads_the_deinked_image(self, monkeypatch):
        monkeypatch.setattr(pi, "deink_red_stamp", lambda b: b"D:" + b)
        top = _CountingTopText()
        pi.page_marks(b"RAW", top)
        assert top.reads == {b"D:RAW": 1}

    def test_deink_failure_falls_back_to_raw(self, monkeypatch):
        def _boom(b):
            raise ValueError("cannot decode")
        monkeypatch.setattr(pi, "deink_red_stamp", _boom)
        top = _CountingTopText({b"RAW": "PERJANJIAN KERJA"})
        assert pi.page_marks(b"RAW", top) == (1, False)
