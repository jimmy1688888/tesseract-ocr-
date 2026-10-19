# -*- coding: utf-8 -*-
"""契約頁挑選:評分前必須先去紅章(32426 / 32427 教訓)。

那兩份的 image2 才是雇主資料表,但整片被大紅章蓋住,原圖直送 Tesseract 一個
FORM 標籤都讀不到 → 0 分,敗給只有 prose 的次頁 image3(2 分)。選錯頁的後果是
雙重的:雇主欄位抽到合約條文,而且 image3 是最後一張 → 次頁台仲反查判定「無次頁」
直接放棄,連 Vision 都沒送。去紅章後 image2 由 0 → 6/7 分,穩定勝出。

這裡不跑真 OCR(結果會隨 Tesseract 版本浮動),改用假的 OCR 函式重現
「原圖讀不到、去紅章後讀得到」這個情境,釘住流程順序與加權結果。
"""
import zipfile

import pytest

import employer_extract as ee
from real_samples import REAL_DOCX, checked_contract_page, needs_samples, sample

# 資料表頁:去紅章後才讀得到的欄位標籤(FORM ×2)
_FORM_TEXT = ("雇主名稱:彰化縣芳苑鄉農會(林木村) "
              "Nama Pemberi Kerja: Fangyuan Township Farmers' Association "
              "Alamat: No. 195, Fangyuan Section "
              "電話號碼 Nomor Telepon: 04-8984111")
# 次頁:版面乾淨,原圖就讀得到,但只有樣板 prose(PROSE ×1)
_PROSE_TEXT = ("Perjanjian Kerja dibuat dalam rangkap 2 (dua) asli, "
               "1 (satu) untuk PIHAK PERTAMA — 勞動契約")


def _fake_ocr(monkeypatch, table: dict[bytes, str]) -> None:
    """假 OCR:deink 在 bytes 前綴 b'D:',查表回該影像「該階段」讀得到的文字。"""
    monkeypatch.setattr(ee, "deink_red_stamp", lambda b, **kw: b"D:" + b)
    monkeypatch.setattr(ee, "_tesseract_top_text",
                        lambda b, **kw: table.get(b, ""))


class TestScoreContractPage:

    def test_scores_the_deinked_image_not_the_raw(self, monkeypatch):
        """掃描對象必須是 deink_red_stamp 的輸出。"""
        seen: list[bytes] = []

        def _spy(b, **kw):
            seen.append(b)
            return ""

        monkeypatch.setattr(ee, "deink_red_stamp", lambda b, **kw: b"DEINKED")
        monkeypatch.setattr(ee, "_tesseract_top_text", _spy)
        ee.score_contract_page(b"RAW")
        assert seen == [b"DEINKED"]

    def test_stamped_form_page_scores_only_after_deink(self, monkeypatch):
        """紅章蓋住時原圖 0 分,去紅章後靠 FORM 標籤拿高分(32426 的 image2)。"""
        _fake_ocr(monkeypatch, {b"D:img2": _FORM_TEXT})   # 原圖(b'img2')讀不到
        assert ee.score_contract_page(b"img2") == 8       # FORM 4 個 ×2

    def test_prose_page_scores_low(self, monkeypatch):
        """次頁版面乾淨、原圖就讀得到,但只有 prose,權重 1。"""
        _fake_ocr(monkeypatch, {b"img3": _PROSE_TEXT, b"D:img3": _PROSE_TEXT})
        assert ee.score_contract_page(b"img3") == 3       # PROSE 3 個 ×1

    def test_deink_failure_falls_back_to_raw(self, monkeypatch):
        """前處理爆掉不該讓評分中斷,退回原圖繼續掃。"""
        def _boom(b, **kw):
            raise ValueError("cannot decode")

        monkeypatch.setattr(ee, "deink_red_stamp", _boom)
        monkeypatch.setattr(ee, "_tesseract_top_text",
                            lambda b, **kw: _PROSE_TEXT if b == b"img3" else "")
        assert ee.score_contract_page(b"img3") == 3


class TestFindContractImage:

    def test_stamped_form_page_beats_clean_prose_page(self, monkeypatch):
        """32426 的實況:image2 被紅章蓋住、image3 乾淨。去紅章後仍須選中 image2。"""
        _fake_ocr(monkeypatch, {
            b"img2": "",                    # 原圖:紅章蓋住,讀不到
            b"D:img2": _FORM_TEXT,          # 去紅章:欄位標籤現形
            b"img3": _PROSE_TEXT,
            b"D:img3": _PROSE_TEXT,
        })
        images = [("image3.jpeg", b"img3"), ("image2.jpeg", b"img2")]
        assert ee.find_contract_image(images)[0] == "image2.jpeg"

    def test_all_pages_below_min_score_returns_none(self, monkeypatch):
        """全都達不到門檻 → None(維持原本「不猜」行為)。"""
        _fake_ocr(monkeypatch, {})
        assert ee.find_contract_image([("image1.jpeg", b"img1")]) is None


# ── 評分快取:同一份 docx 在一次 pipeline 會被兩個入口各評一次,共用之 ──────

@pytest.fixture(autouse=True)
def _clear_cache():
    ee._page_score_cache.clear()
    yield
    ee._page_score_cache.clear()


def _make_docx(path, images: dict[str, bytes]):
    """做一份只含 word/media/* 的假 docx(docx_images.read_images 只讀這些項)。"""
    with zipfile.ZipFile(path, "w") as z:
        for name, data in images.items():
            z.writestr(f"word/media/{name}", data)
    return str(path)


@pytest.fixture
def counting_scorer(monkeypatch):
    """把 _page_marks 換成計次器,回傳 {圖檔名: 被實際掃描次數}。

    攔的是 _page_marks 而不是 score_contract_page:契約頁分數與「是否切結書
    第一頁」共用同一次上緣掃描(ADR-0006),快取的是那一次的結果,所以計次要計
    在那個接縫上。回 (0, False):0 分讓兩個入口都提早返回、不碰 Vision;
    False 表示沒有切結書頁,救援也不會啟動。
    """
    calls: dict[bytes, int] = {}

    def _marks(b, **kw):
        calls[b] = calls.get(b, 0) + 1
        return 0, False

    monkeypatch.setattr(ee, "_page_marks", _marks)
    return calls


class TestScoreCache:

    def test_same_docx_scored_once_across_calls(self, tmp_path, counting_scorer):
        p = _make_docx(tmp_path / "a.docx", {"image1.jpeg": b"i1",
                                             "image2.jpeg": b"i2"})
        ee._scored_pages(p)
        ee._scored_pages(p)
        assert counting_scorer == {b"i1": 1, b"i2": 1}

    def test_two_entry_points_share_the_score(self, tmp_path, counting_scorer):
        """雇主擷取與次頁台仲反查各跑一次,合計仍只評一輪(本次修正的目的)。"""
        p = _make_docx(tmp_path / "b.docx", {"image1.jpeg": b"i1",
                                             "image2.jpeg": b"i2"})
        ee.extract_employer_from_docx(p, roi=None)   # roi=None:略過裁切,假 bytes 不必是真圖
        ee._agency_block_lines(p)
        assert counting_scorer == {b"i1": 1, b"i2": 1}

    def test_cached_scores_survive_but_results_agree(self, tmp_path,
                                                     counting_scorer):
        """快取回來的分數要對得上圖檔,順序仍為自然排序。"""
        p = _make_docx(tmp_path / "c.docx", {"image10.jpeg": b"i10",
                                             "image2.jpeg": b"i2"})
        first = ee._scored_pages(p)
        assert [n for _s, n, _b in first] == ["image2.jpeg", "image10.jpeg"]
        assert ee._scored_pages(p) == first

    def test_changed_file_invalidates_cache(self, tmp_path, counting_scorer):
        """檔案被換掉(大小/時間改變)→ 重新評分,不吃到舊 docx 的結果。"""
        p = tmp_path / "d.docx"
        _make_docx(p, {"image1.jpeg": b"i1"})
        ee._scored_pages(str(p))
        _make_docx(p, {"image1.jpeg": b"CHANGED-CONTENT"})
        ee._scored_pages(str(p))
        assert counting_scorer == {b"i1": 1, b"CHANGED-CONTENT": 1}

    def test_empty_docx_returns_empty(self, tmp_path, counting_scorer):
        p = _make_docx(tmp_path / "e.docx", {})
        assert ee._scored_pages(p) == []
        assert counting_scorer == {}


# ── 機構類契約樣板(#27)───────────────────────────────────────────────────

class TestInstitutionTemplateLabels:
    """養護機構/醫院類契約的雇主欄位標籤是「Nama Panti Jompo/Rumah Sakit」,
    不是家庭類的「Nama Majikan」。認不得它,資料表就少一個 FORM 標籤,輸給次頁(#27)。"""

    def test_institution_name_label_counts_once(self):
        """一個標籤算一次 —— 拆成兩個分支會讓同一個標籤拿兩份 FORM 加權。"""
        assert len(ee._FORM_LABELS.findall("Nama Panti Jompo/Rumah Sakit : ")) == 1
        assert len(ee._FORM_LABELS.findall("Nama Panti Jompo : ")) == 1

    def test_hospital_in_prose_is_not_a_label(self):
        """條文裡的「Rumah Sakit」(醫院,住院/醫療費用條款常見)不得變成欄位標籤。"""
        assert not ee._FORM_LABELS.findall(
            "biaya perawatan di Rumah Sakit ditanggung oleh PIHAK PERTAMA")


@pytest.mark.usefixtures("real_ocr")
class TestRealContractPagePicks:
    """真實素材上「選哪張當契約頁」(真的跑 Tesseract)。"""

    @staticmethod
    def _pick(path):
        best = ee._best_scored_page(ee._scored_pages(str(path)))
        assert best is not None, f"{path.name}:沒有任何一頁被認成契約頁"
        return best[1]

    def test_33703_institution_contract_picks_image2(self):
        """33703 是機構類契約;使用者看圖確認雇主資料表是 image2。

        修正前 image2 只讀到 1 個 FORM 標籤(4 分),輸給次頁 image3(5 分) ——
        雇主欄 H~O 會從次頁讀出錯的資料,台仲反查也會掃錯頁,都不報錯。
        修正後是 6 比 5,只贏 1 分,所以這支刻意**常跑**(約 10 秒,不標 real):
        它是這份素材的守門員,改評分規則時最先倒的會是它。
        """
        assert self._pick(sample("33703.docx")) == "image2.png"

    @pytest.mark.real
    @needs_samples
    @pytest.mark.parametrize("docx", REAL_DOCX, ids=lambda p: p.name)
    def test_every_sample_picks_its_contract_page(self, docx):
        """每份素材選中的契約頁與它的契約頁核對值相同。改評分規則時跑這組。

        核對值在 samples/checked_values.json(跟著素材走,不進版控)。
        """
        expected = checked_contract_page(docx.name)
        if expected is None:
            pytest.skip(f"{docx.name} 沒有契約頁核對值:先由人看圖確認"
                        "(python snapshots.py record 會列出來請你確認)")
        assert self._pick(docx) == expected
