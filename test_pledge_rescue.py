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
import pytest

from conftest import FakeVision, pages_of
import employer_extract as ee
from contract_template import Template
from employer_extract import _pledge_name
from employer_result import EmployerExtraction, EmployerValues, ExtractionStatus, Source
from vision_reader import VisionCallFailed

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


def _pledge_pages(front: str = "image4.jpeg", count: int = 7):
    """image1~imageN 的認頁結果,front 那張是切結書第一頁;bytes 就是檔名。"""
    return pages_of(*[(0, f"image{n}.jpeg", f"image{n}.jpeg".encode(), f"image{n}.jpeg" == front)
                      for n in range(1, count + 1)])


def _rescuing(pledge_text: str = _SLASHED) -> FakeVision:
    """切結書頁(image5)的 Vision 全文;其他頁讀到無關文字。"""
    return FakeVision(document=lambda b: pledge_text if b == b"image5.jpeg" else "無關文字")


def _failing_vision() -> FakeVision:
    def _failed(image):
        raise VisionCallFailed("503")
    return FakeVision(document=_failed)


class TestPledgeNamePages:
    """候選頁 = 切結書第一頁之**後**最多兩頁。"""

    def test_permit_page_is_not_used_to_locate_the_field(self):
        """雇主欄與許可證不一定同頁:32538/32554 的許可證在 image6、雇主欄在
        image5。曾經 4/4 成立的「同一頁」是巧合,不能拿來認頁。"""
        assert _pledge_pages().pledge_name_pages()[0].name == "image5.jpeg"


class TestRescueFromPledge:

    def test_second_candidate_page_is_tried(self):
        vision = FakeVision(document=lambda b: _SLASHED if b == b"image6.jpeg" else "無關文字")
        assert ee.rescue_name_from_pledge(_pledge_pages(), vision) == ("姜信安", "image6.jpeg")

    def test_stops_at_the_first_hit(self):
        vision = FakeVision(document=lambda b: _TWO_LINE)
        ee.rescue_name_from_pledge(_pledge_pages(), vision)
        assert vision.document_reads == [b"image5.jpeg"]          # 命中就停,不白花第二次

    def test_vision_call_failure_stops_without_trying_the_next_page(self):
        """Vision 呼叫失敗多半是服務那一端,換一張再打多半一樣;交給呼叫端寫疑慮(#66)。"""

        def _boom(b):
            raise VisionCallFailed("503")

        vision = FakeVision(document=_boom)
        with pytest.raises(VisionCallFailed):
            ee.rescue_name_from_pledge(_pledge_pages(), vision)
        assert vision.document_reads == [b"image5.jpeg"]


class TestApplyRescue:
    """填值與疑慮措辭。"""

    def _fields(self, cn="", en="JIANG SIN AN", template=Template.家庭類):
        return EmployerExtraction(
            ExtractionStatus.擷取成功, EmployerValues(雇主名稱_中=cn, 雇主名稱_英=en),
            ee._doubts("雇主名稱_中", ee._name_doubt(cn, en)), Source(template))

    def test_rescued_name_is_written_and_flagged(self):
        f = self._fields()
        assert f.疑慮標示 == "雇主名稱_中:未擷取到中文名"
        f = ee._apply_pledge_rescue(f, _pledge_pages(), _rescuing())
        assert f.values.雇主名稱_中 == "姜信安"
        assert "雇主名稱_中:中文名取自切結書頁(image5.jpeg)" in f.疑慮標示
        assert "未擷取到" not in f.疑慮標示      # 已經擷取到了,那句話變成假的

    def test_count_check_still_runs_on_the_rescued_name(self):
        """救回來的名字仍要過字數/音節數檢查——換來源不等於免檢。"""
        f = self._fields()
        f = ee._apply_pledge_rescue(f, _pledge_pages(),
                                    _rescuing("中華民國雇主:信安 Majikan R.O.C: JIANG SIN AN"))
        assert "取自切結書頁" in f.疑慮標示
        assert "2 字與英譯 3 音節不符" in f.疑慮標示

    def test_rescued_company_name_skips_count_check_on_factory_template(self):
        """工廠類樣板救回的是公司名:跟契約頁一樣看樣板跳過檢查(ADR-0010)。"""
        f = self._fields(en="DA MING METAL CORPORATION (WANG, DA-MING)",
                         template=Template.工廠類)
        f = ee._apply_pledge_rescue(f, _pledge_pages(), _rescuing(
            "中華民國雇主:大明金屬股份有限公司 Majikan R.O.C: DA MING METAL"))
        assert "取自切結書頁" in f.疑慮標示
        assert "音節" not in f.疑慮標示

    def test_vision_call_failure_is_stated_beside_the_missing_name(self):
        """沒問到 ≠ 救不回:H 欄維持空,L 欄另加一條,兩條並列(#66、ADR-0015)。"""
        f = ee._apply_pledge_rescue(self._fields(), _pledge_pages(), _failing_vision())
        assert f.values.雇主名稱_中 == ""
        assert f.status is ExtractionStatus.擷取成功
        assert f.疑慮標示 == "雇主名稱_中:未擷取到中文名；雇主名稱_中:切結書頁 Vision 呼叫失敗"

    def test_failed_rescue_changes_nothing(self):
        """救不回來時 H 欄維持空、L 欄維持「未擷取到」。

        兩種空值成因(紅章壓死 vs 契約只填拼音)從文字上仍分不出來,ADR-0004
        那條「不猜原因」不因為多了一條管道而改變——32503 正是救不回的那一半。
        """
        f = self._fields()
        f = ee._apply_pledge_rescue(f, _pledge_pages(), _rescuing("無關文字"))
        assert f.values.雇主名稱_中 == ""
        assert f.疑慮標示 == "雇主名稱_中:未擷取到中文名"

    def test_english_name_is_never_overwritten(self):
        """只救中文名。切結書頁的英文反而讀壞過(32503 讀成 FU BIN ANI FU),
        而英文名本來就是可靠的那一欄,覆蓋它是負向交換。"""
        f = self._fields(en="JIANG SIN AN")
        f = ee._apply_pledge_rescue(f, _pledge_pages(), _rescuing())
        assert f.values.雇主名稱_英 == "JIANG SIN AN"

    def test_no_images_does_not_rescue(self):
        """無內嵌圖就提早返回,更不會救援(FakeVision 沒給讀整頁,被呼叫就會紅)。"""
        ee.extract_employer_from_docx(pages_of(), FakeVision(), roi=None)


class TestPledgeVisionFailureShape:
    """#66 review:沒問到只加自己那一條;非 Vision 的例外照舊不拖垮契約頁的值。"""

    def test_not_extracted_status_gets_only_the_failure_line(self):
        """找不到契約頁時本來就沒有「未擷取到中文名」,不該被補上。"""
        f = ee._apply_pledge_rescue(EmployerExtraction(ExtractionStatus.找不到契約頁),
                                    _pledge_pages(), _failing_vision())
        assert f.疑慮標示 == "雇主資料:找不到契約頁,未擷取；雇主名稱_中:切結書頁 Vision 呼叫失敗"

    def test_other_errors_still_give_up_quietly(self):

        def _boom(b):
            raise RuntimeError("讀圖壞了")

        assert ee.rescue_name_from_pledge(_pledge_pages(), FakeVision(document=_boom)) == ("", "")
