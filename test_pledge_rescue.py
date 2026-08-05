# -*- coding: utf-8 -*-
"""雇主中文名的切結書頁備援(ADR-0006)。

契約頁的中文名欄常被紅章蓋死(32510 的「姜信安」壓著兩枚章),而切結書頁另有一個
「中華民國雇主 / Majikan R.O.C」欄位,是同一份文件裡的第二個來源。

這條管道有兩個設計決定,各由一組實測逼出來,本組測試把它們釘住:

  取值用 Vision,不用本機 Tesseract
      同一格、同一個 chi_tra 語言包,換個前處理就讀出三個不同的字:
      32574 的「鍾滄榮」被讀成 鍾濃榮(紅通道)、鍾澹榮(去紅章),Vision 讀對。

  認頁用 Surat Pernyataan,不用 Majikan R.O.C
      32538/32554 的那一頁,Tesseract 在 35%/55%/70% 任何裁切範圍都讀不出
      Majikan R.O.C。認頁只能靠 Tesseract 讀得穩的印刷體大標題。
"""
import zipfile

import pytest

import employer_extract as ee
from employer_extract import _pledge_name

# 32572 實況:中英分行
_TWO_LINE = ("perihal No. IV ini. 中華民國雇主:張鈺珠 "
             "Majikan R.O.C: ZHANG YU ZHU (Stample atau tanda tangan) "
             "負責人或代表人簽章:張鈺珠")
# 32510 實況:中英擠同一行、斜線分隔,兩個標籤之間隔了 14 個字元
_SLASHED = ("Pekerja Migran Indonesia. 中華民國雇主/Majikan R.O.C: 姜信安/JIANG SIN AN "
            "負責人或代表人簽章/ Tanda tangan penanggung jawab atau wali 信安")


class TestPledgeNameExtraction:

    def test_two_line_layout(self):
        assert _pledge_name(_TWO_LINE) == "張鈺珠"

    def test_slashed_bilingual_layout(self):
        """標籤後隔 14 個字元。最初寫死「標籤後 8 字內」就是因此抓空,
        而當時誤判成「Vision 讀不到」——它讀到了。"""
        assert _pledge_name(_SLASHED) == "姜信安"

    def test_cjk_spacing_is_collapsed(self):
        """OCR 常在漢字之間插空白,值取出後要收斂(『張 鈺 珠』→『張鈺珠』)。"""
        assert _pledge_name("中 華 民 國 雇 主 ﹕ 張 鈺 珠 ( 簽 章 ﹚") == "張鈺珠"

    def test_romanised_only_contract_yields_empty(self):
        """32503:那一格本身就只填羅馬拼音,沒有中文可救。"""
        assert _pledge_name("中華民國雇主/Majikan R.O.C: FU BIN ANI FU") == ""

    def test_missing_field_yields_empty(self):
        """32538/32554 的切結書是另一版表格,整頁沒有這個欄位。"""
        assert _pledge_name("一份依據雇主聘僱外國人許可及管理辦法規定由雇主保存") == ""

    def test_boilerplate_is_rejected(self):
        """欄位空白而樣板字頂上來時不得當成名字(同 _is_name_like 的守門)。"""
        assert _pledge_name("中華民國雇主:勞動契約 家庭幫傭") == ""

    def test_signature_line_is_not_used_as_source(self):
        """簽章行也印著名字,但刻意不用它:32510 那行只讀到「信安」少了姓,
        且公司型雇主的負責人與雇主根本不是同一個人。"""
        assert _pledge_name("負責人或代表人簽章/ Tanda tangan ... 信安") == ""


def _make_docx(path, names):
    with zipfile.ZipFile(path, "w") as z:
        for n in names:
            z.writestr(f"word/media/{n}", n.encode())
    return str(path)


@pytest.fixture(autouse=True)
def _clear_cache():
    ee._page_score_cache.clear()
    yield
    ee._page_score_cache.clear()


@pytest.fixture
def pledge_front(monkeypatch):
    """把指定圖檔標成切結書第一頁,其餘 0 分。"""
    def _use(front: str):
        monkeypatch.setattr(ee, "_page_marks",
                            lambda b, **kw: (0, b.decode() == front))
    return _use


class TestPledgeNamePages:
    """候選頁 = 切結書第一頁之**後**最多兩頁。"""

    def test_returns_the_two_pages_after_the_front(self, tmp_path, pledge_front):
        pledge_front("image4.jpeg")
        p = _make_docx(tmp_path / "a.docx",
                       [f"image{n}.jpeg" for n in range(1, 8)])
        assert [n for n, _ in ee.pledge_name_pages(p)] == ["image5.jpeg",
                                                           "image6.jpeg"]

    def test_no_front_page_means_no_candidates(self, tmp_path, pledge_front):
        pledge_front("nothing.jpeg")
        p = _make_docx(tmp_path / "b.docx", ["image1.jpeg", "image2.jpeg"])
        assert ee.pledge_name_pages(p) == []

    def test_front_page_is_last_means_no_candidates(self, tmp_path, pledge_front):
        pledge_front("image2.jpeg")
        p = _make_docx(tmp_path / "c.docx", ["image1.jpeg", "image2.jpeg"])
        assert ee.pledge_name_pages(p) == []

    def test_permit_page_is_not_used_to_locate_the_field(self, tmp_path,
                                                         pledge_front):
        """雇主欄與許可證不一定同頁:32538/32554 的許可證在 image6、雇主欄在
        image5。曾經 4/4 成立的「同一頁」是巧合,不能拿來認頁。"""
        pledge_front("image4.jpeg")
        p = _make_docx(tmp_path / "d.docx",
                       [f"image{n}.jpeg" for n in range(1, 8)])
        assert [n for n, _ in ee.pledge_name_pages(p)][0] == "image5.jpeg"


class TestRescueFromPledge:

    def test_second_candidate_page_is_tried(self, tmp_path, pledge_front,
                                            monkeypatch):
        pledge_front("image4.jpeg")
        p = _make_docx(tmp_path / "e.docx",
                       [f"image{n}.jpeg" for n in range(1, 8)])
        monkeypatch.setattr(ee, "vision_full_text",
                            lambda b, client=None: _SLASHED
                            if b == b"image6.jpeg" else "無關文字")
        assert ee.rescue_name_from_pledge(p) == ("姜信安", "image6.jpeg")

    def test_stops_at_the_first_hit(self, tmp_path, pledge_front, monkeypatch):
        pledge_front("image4.jpeg")
        p = _make_docx(tmp_path / "f.docx",
                       [f"image{n}.jpeg" for n in range(1, 8)])
        seen: list[bytes] = []

        def _vision(b, client=None):
            seen.append(b)
            return _TWO_LINE

        monkeypatch.setattr(ee, "vision_full_text", _vision)
        ee.rescue_name_from_pledge(p)
        assert seen == [b"image5.jpeg"]          # 命中就停,不白花第二次

    def test_vision_failure_does_not_propagate(self, tmp_path, pledge_front,
                                               monkeypatch):
        """憑證/額度/網路問題不得讓整批掛掉——救援失敗只是少一格值。"""
        pledge_front("image4.jpeg")
        p = _make_docx(tmp_path / "g.docx", ["image4.jpeg", "image5.jpeg"])

        def _boom(b, client=None):
            raise RuntimeError("403 quota")

        monkeypatch.setattr(ee, "vision_full_text", _boom)
        assert ee.rescue_name_from_pledge(p) == ("", "")


class TestApplyRescue:
    """填值與疑慮措辭。"""

    def _fields(self, cn="", en="JIANG SIN AN"):
        f = dict(ee._EMPTY_FIELDS)
        f["雇主名稱_中"], f["雇主名稱_英"] = cn, en
        f["名稱_疑慮"] = ee._name_doubt(cn, en)
        f["疑慮標示"] = ee._compose_doubt(f)
        return f

    def test_rescued_name_is_written_and_flagged(self, monkeypatch):
        monkeypatch.setattr(ee, "rescue_name_from_pledge",
                            lambda p, client=None: ("姜信安", "image5.jpeg"))
        f = self._fields()
        assert f["疑慮標示"] == "H:未擷取到中文名"
        ee._apply_pledge_rescue(f, "x.docx")
        assert f["雇主名稱_中"] == "姜信安"
        assert "H:中文名取自切結書頁(image5.jpeg)" in f["疑慮標示"]
        assert "未擷取到" not in f["疑慮標示"]      # 已經擷取到了,那句話變成假的

    def test_count_check_still_runs_on_the_rescued_name(self, monkeypatch):
        """救回來的名字仍要過字數/音節數檢查——換來源不等於免檢。"""
        monkeypatch.setattr(ee, "rescue_name_from_pledge",
                            lambda p, client=None: ("信安", "image5.jpeg"))
        f = self._fields()
        ee._apply_pledge_rescue(f, "x.docx")
        assert "取自切結書頁" in f["疑慮標示"]
        assert "2 字與英譯 3 音節不符" in f["疑慮標示"]

    def test_failed_rescue_changes_nothing(self, monkeypatch):
        """救不回來時 H 欄維持空、L 欄維持「未擷取到」。

        兩種空值成因(紅章壓死 vs 契約只填拼音)從文字上仍分不出來,ADR-0004
        那條「不猜原因」不因為多了一條管道而改變——32503 正是救不回的那一半。
        """
        monkeypatch.setattr(ee, "rescue_name_from_pledge",
                            lambda p, client=None: ("", ""))
        f = self._fields()
        ee._apply_pledge_rescue(f, "x.docx")
        assert f["雇主名稱_中"] == ""
        assert f["疑慮標示"] == "H:未擷取到中文名"

    def test_english_name_is_never_overwritten(self, monkeypatch):
        """只救中文名。切結書頁的英文反而讀壞過(32503 讀成 FU BIN ANI FU),
        而英文名本來就是可靠的那一欄,覆蓋它是負向交換。"""
        monkeypatch.setattr(ee, "rescue_name_from_pledge",
                            lambda p, client=None: ("姜信安", "image5.jpeg"))
        f = self._fields(en="JIANG SIN AN")
        ee._apply_pledge_rescue(f, "x.docx")
        assert f["雇主名稱_英"] == "JIANG SIN AN"

    def test_rescue_only_fires_when_chinese_name_is_missing(self, monkeypatch,
                                                            tmp_path):
        """契約頁已有中文名就不該多花一次 Vision。"""
        calls: list[str] = []
        monkeypatch.setattr(ee, "rescue_name_from_pledge",
                            lambda p, client=None: calls.append(p) or ("", ""))
        monkeypatch.setattr(ee, "_scored_pages", lambda p: [])
        ee.extract_employer_from_docx(str(tmp_path / "h.docx"), roi=None)
        assert calls == []                       # 無內嵌圖就提早返回,更不會救援
