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

import page_identification as pi
from page_identification import PageIdentifier
from real_samples import REAL_DOCX, checked_contract_page, needs_samples, sample

# 資料表頁:去紅章後才讀得到的欄位標籤(FORM ×2)
# 上緣 OCR 是 ind+eng,讀不出中日韓字,所以假文字裡也不放中文。
_FORM_TEXT = ("Nama Majikan: FANGYUAN TOWNSHIP FARMERS' ASSOCIATION "
              "Nama Pemberi Kerja: Fangyuan Township Farmers' Association "
              "Alamat: No. 195, Fangyuan Section "
              "Nomor Telepon: 04-8984111")
# 次頁:版面乾淨,原圖就讀得到,但只有樣板 prose(PROSE ×1)
_PROSE_TEXT = ("Perjanjian Kerja dibuat dalam rangkap 2 (dua) asli, "
               "1 (satu) untuk PIHAK PERTAMA, SELANJUTNYA")


def _fake_ocr(monkeypatch, table: dict[bytes, str]):
    """假 OCR:deink 在 bytes 前綴 b'D:',上緣讀字查表回該影像「該階段」讀得到的文字。"""
    monkeypatch.setattr(pi, "deink_red_stamp", lambda b: b"D:" + b)
    return lambda b: table.get(b, "")


def _make_docx(path, images: dict[str, bytes]) -> str:
    """做一份只含 word/media/* 的假 docx(docx_images.read_images 只讀這些項)。"""
    with zipfile.ZipFile(path, "w") as z:
        for name, data in images.items():
            z.writestr(f"word/media/{name}", data)
    return str(path)


class TestContractScore:
    """契約頁分數:欄位標籤(FORM)×2 + 樣板(PROSE)×1,讀的是去紅章後的上緣。"""

    def test_stamped_form_page_scores_only_after_deink(self, monkeypatch):
        """紅章蓋住時原圖 0 分,去紅章後靠 FORM 標籤拿高分(32426 的 image2)。"""
        top = _fake_ocr(monkeypatch, {b"D:img2": _FORM_TEXT})   # 原圖(b'img2')讀不到
        assert pi.page_marks(b"img2", top).contract_score == 8   # FORM 4 個 ×2

    def test_prose_page_scores_low(self, monkeypatch):
        """次頁版面乾淨、原圖就讀得到,但只有 prose,權重 1。"""
        top = _fake_ocr(monkeypatch, {b"img3": _PROSE_TEXT, b"D:img3": _PROSE_TEXT})
        assert pi.page_marks(b"img3", top).contract_score == 3   # PROSE 3 個 ×1


class TestStampedFormPageIsPicked:

    def test_stamped_form_page_beats_clean_prose_page(self, tmp_path, monkeypatch):
        """32426 的實況:image2 被紅章蓋住、image3 乾淨。去紅章後仍須選中 image2。"""
        top = _fake_ocr(monkeypatch, {
            b"img2": "",                    # 原圖:紅章蓋住,讀不到
            b"D:img2": _FORM_TEXT,          # 去紅章:欄位標籤現形
            b"img3": _PROSE_TEXT,
            b"D:img3": _PROSE_TEXT,
        })
        p = _make_docx(tmp_path / "32426.docx", {"image2.jpeg": b"img2",
                                                 "image3.jpeg": b"img3"})
        contract = PageIdentifier(top).identify(p).contract_page()
        assert (contract.page.name, contract.found) == ("image2.jpeg", True)


# ── 機構類契約樣板(#27)───────────────────────────────────────────────────

class TestInstitutionTemplateLabels:
    """養護機構/醫院類契約的雇主欄位標籤是「Nama Panti Jompo/Rumah Sakit」,
    不是家庭類的「Nama Majikan」。認不得它,資料表就少一個 FORM 標籤,輸給次頁(#27)。"""

    def test_institution_name_label_counts_once(self):
        """一個標籤算一次 —— 拆成兩個分支會讓同一個標籤拿兩份 FORM 加權。"""
        assert len(pi._FORM_LABELS.findall("Nama Panti Jompo/Rumah Sakit : ")) == 1
        assert len(pi._FORM_LABELS.findall("Nama Panti Jompo : ")) == 1

    def test_hospital_in_prose_is_not_a_label(self):
        """條文裡的「Rumah Sakit」(醫院,住院/醫療費用條款常見)不得變成欄位標籤。"""
        assert not pi._FORM_LABELS.findall(
            "biaya perawatan di Rumah Sakit ditanggung oleh PIHAK PERTAMA")


@pytest.mark.usefixtures("real_ocr")
class TestRealContractPagePicks:
    """真實素材上「選哪張當契約頁」(真的跑 Tesseract)。"""

    @staticmethod
    def _pick(path):
        contract = PageIdentifier().identify(path).contract_page()
        assert contract is not None, f"{path.name}:沒有任何一頁被認成契約頁"
        return contract.page.name

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
        checked = checked_contract_page(docx.name)
        if checked is None:
            pytest.skip(f"{docx.name} 沒有契約頁核對值:先由人看圖確認"
                        "(python snapshots.py record 會列出來請你確認)")
        assert self._pick(docx) == checked
