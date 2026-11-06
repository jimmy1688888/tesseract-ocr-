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
import real_samples as rs
from employer_extract import PledgeRescue, _pledge_name, extract_employer_from_docx
from employer_result import ExtractionStatus
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


_PAGE_COUNT = 7                         # image1~image7
_CONTRACT_PAGE, _PLEDGE_FRONT = 2, 4
_CONTRACT_FOUND, _CONTRACT_NOT_FOUND = 10, 1


def _docx(contract_score: int = _CONTRACT_FOUND):
    """一份 docx 的認頁結果:image2 是契約頁(分數決定找不找得到),image4 是切結書
    第一頁;bytes 就是檔名。"""
    return pages_of(*[(contract_score if n == _CONTRACT_PAGE else 0, f"image{n}.jpeg",
                       f"image{n}.jpeg".encode(), n == _PLEDGE_FRONT)
                      for n in range(1, _PAGE_COUNT + 1)])


def _failing_vision() -> FakeVision:
    def _failed(image):
        raise VisionCallFailed("503")
    return FakeVision(document=_failed)


class TestPledgeNamePages:
    """候選頁 = 切結書第一頁之**後**最多兩頁。"""

    def test_permit_page_is_not_used_to_locate_the_field(self):
        """雇主欄與許可證不一定同頁:32538/32554 的許可證在 image6、雇主欄在
        image5。曾經 4/4 成立的「同一頁」是巧合,不能拿來認頁。"""
        assert _docx().pledge_name_pages()[0].name == "image5.jpeg"


class TestRescueFromPledge:

    def test_second_candidate_page_is_tried(self):
        vision = FakeVision(document=lambda b: _SLASHED if b == b"image6.jpeg" else "無關文字")
        assert ee.rescue_name_from_pledge(_docx(), vision) == PledgeRescue("姜信安", "image6.jpeg")

    def test_stops_at_the_first_hit(self):
        vision = FakeVision(document=lambda b: _TWO_LINE)
        ee.rescue_name_from_pledge(_docx(), vision)
        assert vision.document_reads == [b"image5.jpeg"]          # 命中就停,不白花第二次

    @pytest.mark.parametrize("stem, name", [
        ("33334", "建興安泰工業股份有限公司"),               # 工廠類;「(簽」「章)」被斷成兩行
        ("34226", "高陳蘭家庭農場"),                         # 農業類
        ("34227", "保證責任屏東縣允陽青果生產合作社"),        # 農業類;(簽章) 緊貼名稱
    ])
    def test_real_pledge_pages_give_the_name_without_the_signature_note(self, stem, name):
        """真實切結書頁:雇主欄只印「名稱 (簽章)」,負責人另在簽章欄 —— 救回的是
        不含負責人的名稱(CONTEXT「切結書頁備援」)。"""
        vision = FakeVision(document=lambda b: rs.pledge_text(stem))
        assert ee.rescue_name_from_pledge(_docx(), vision) == PledgeRescue(name, "image5.jpeg")

    def test_vision_call_failure_stops_without_trying_the_next_page(self):
        """Vision 呼叫失敗多半是服務那一端,換一張再打多半一樣;記下來交給組裝寫疑慮(#66)。"""
        vision = _failing_vision()
        assert ee.rescue_name_from_pledge(_docx(), vision) == PledgeRescue(vision_failed=True)
        assert vision.document_reads == [b"image5.jpeg"]


# 契約頁讀得到、錨得到,但中文名那格是空的 —— 切結書頁備援才會跑。
# 地址也空著,好看出名稱的幾條疑慮排在地址之前、位置不因救援而動。
_NO_CHINESE_NAME = ("勞動契約 監護工\n甲方名稱:\nNama Majikan: JIANG SIN AN\n地址:\n"
                    "SELANJUTNYA DISEBUT PIHAK PERTAMA")
_UNANCHORED = "勞動契約 監護工\n一些無關文字"

def _extract(contract_text: str, pledge, contract_score: int = _CONTRACT_FOUND):
    """契約頁讀到 contract_text;切結書頁讀到 pledge(是例外就拋出)。"""
    def read(image):
        if image in (b"image5.jpeg", b"image6.jpeg"):
            if isinstance(pledge, Exception):
                raise pledge
            return pledge
        return contract_text
    return extract_employer_from_docx(_docx(contract_score), FakeVision(document=read),
                                      deink=False, roi=None)


class TestRescueAtAssembly:
    """中文名來源在組裝時決定,L 欄依來源寫(#81)。每條路徑一支,值逐字比對。"""

    def test_name_on_the_contract_page_skips_the_pledge_page(self):
        vision = FakeVision(document=lambda b: _NO_CHINESE_NAME.replace("甲方名稱:", "甲方名稱:姜信安"))
        f = extract_employer_from_docx(_docx(), vision, deink=False, roi=None)
        assert f.values.雇主名稱_中 == "姜信安"
        assert vision.document_reads == [b"image2.jpeg"]

    def test_rescued_name_is_written_and_flagged_in_place(self):
        f = _extract(_NO_CHINESE_NAME, _SLASHED)
        assert f.values.雇主名稱_中 == "姜信安"
        assert f.疑慮標示 == "雇主名稱_中:中文名取自切結書頁(image5.jpeg)；地址_中:中文地址未擷取到"

    def test_count_check_still_runs_on_the_rescued_name(self):
        """救回來的名字仍要過字數/音節數檢查——換來源不等於免檢。"""
        f = _extract(_NO_CHINESE_NAME, "中華民國雇主:信安 Majikan R.O.C: JIANG SIN AN")
        assert f.疑慮標示 == ("雇主名稱_中:中文名取自切結書頁(image5.jpeg)；"
                            "雇主名稱_中:中文 2 字與英譯 3 音節不符；地址_中:中文地址未擷取到")

    def test_rescued_company_name_skips_count_check_on_factory_template(self):
        """工廠類樣板救回的是公司名:跟契約頁一樣看樣板跳過檢查(ADR-0010)。"""
        contract = _NO_CHINESE_NAME.replace("監護工", "工廠").replace(
            "Nama Majikan: JIANG SIN AN", "Nama Perusahaan: DA MING METAL CORPORATION (WANG, DA-MING)")
        f = _extract(contract, "中華民國雇主:大明金屬股份有限公司 Majikan R.O.C: DA MING METAL")
        assert f.values.雇主名稱_中 == "大明金屬股份有限公司"
        assert f.疑慮標示 == "雇主名稱_中:中文名取自切結書頁(image5.jpeg)；地址_中:中文地址未擷取到"

    def test_template_doubt_stays_ahead_of_the_rescued_name(self):
        f = _extract("甲方名稱:\n地址:\nSELANJUTNYA DISEBUT PIHAK PERTAMA", _SLASHED)
        assert f.疑慮標示 == ("雇主資料:契約樣板未辨識,以家庭類規則擷取；"
                            "雇主名稱_中:中文名取自切結書頁(image5.jpeg)；地址_中:中文地址未擷取到")

    def test_zip_doubt_stays_after_the_rescued_name(self):
        f = _extract(_NO_CHINESE_NAME.replace("地址:", "地址: 新竹市OOO29號7樓"), _SLASHED)
        assert f.疑慮標示 == "雇主名稱_中:中文名取自切結書頁(image5.jpeg)；郵遞區號:行政區未判定"

    def test_failed_rescue_changes_nothing(self):
        """救不回來時 H 欄維持空、L 欄維持「未擷取到」。

        兩種空值成因(紅章壓死 vs 契約只填拼音)從文字上仍分不出來,ADR-0004
        那條「不猜原因」不因為多了一條管道而改變——32503 正是救不回的那一半。
        """
        f = _extract(_NO_CHINESE_NAME, "無關文字")
        assert f.values.雇主名稱_中 == ""
        assert f.疑慮標示 == "雇主名稱_中:未擷取到中文名；地址_中:中文地址未擷取到"

    def test_vision_call_failure_is_stated_beside_the_missing_name(self):
        """沒問到 ≠ 救不回:H 欄維持空,L 欄另加一條,兩條並列(#66、ADR-0015)。"""
        f = _extract(_NO_CHINESE_NAME, VisionCallFailed("503"))
        assert f.values.雇主名稱_中 == ""
        assert f.status is ExtractionStatus.擷取成功
        assert f.疑慮標示 == ("雇主名稱_中:未擷取到中文名；雇主名稱_中:切結書頁 Vision 呼叫失敗；"
                            "地址_中:中文地址未擷取到")

    def test_english_name_is_never_overwritten(self):
        """只救中文名。切結書頁的英文反而讀壞過(32503 讀成 FU BIN ANI FU),
        而英文名本來就是可靠的那一欄,覆蓋它是負向交換。"""
        f = _extract(_NO_CHINESE_NAME, "中華民國雇主:姜信安 Majikan R.O.C: FU BIN ANI FU")
        assert f.values.雇主名稱_英 == "JIANG SIN AN"

    def test_no_images_does_not_rescue(self):
        """無內嵌圖就提早返回,更不會救援(FakeVision 沒給讀整頁,被呼叫就會紅)。"""
        ee.extract_employer_from_docx(pages_of(), FakeVision(), roi=None)


class TestRescueWhenNotExtracted:
    """雇主區塊未錨定、找不到契約頁:H~O 未擷取,本來就沒有「未擷取到中文名」,
    救援那條接在原因之後(CONTEXT「雇主擷取結果」)。"""

    def test_unanchored_block_rescued(self):
        f = _extract(_UNANCHORED, _SLASHED)
        assert f.status is ExtractionStatus.雇主區塊未錨定
        assert f.values.雇主名稱_中 == "姜信安"
        assert f.疑慮標示 == "雇主資料:契約頁雇主區塊未錨定,未擷取；雇主名稱_中:中文名取自切結書頁(image5.jpeg)"

    def test_unanchored_block_vision_failure_gets_only_the_failure_line(self):
        f = _extract(_UNANCHORED, VisionCallFailed("503"))
        assert f.疑慮標示 == "雇主資料:契約頁雇主區塊未錨定,未擷取；雇主名稱_中:切結書頁 Vision 呼叫失敗"

    def test_contract_page_not_found_rescued(self):
        f = _extract(_NO_CHINESE_NAME, _SLASHED, _CONTRACT_NOT_FOUND)
        assert f.status is ExtractionStatus.找不到契約頁
        assert f.values.雇主名稱_中 == "姜信安"
        assert f.疑慮標示 == "雇主資料:找不到契約頁,未擷取；雇主名稱_中:中文名取自切結書頁(image5.jpeg)"

    def test_contract_page_not_found_vision_failure_gets_only_the_failure_line(self):
        f = _extract(_NO_CHINESE_NAME, VisionCallFailed("503"), _CONTRACT_NOT_FOUND)
        assert f.疑慮標示 == "雇主資料:找不到契約頁,未擷取；雇主名稱_中:切結書頁 Vision 呼叫失敗"

    def test_contract_page_not_found_failed_rescue_adds_nothing(self):
        f = _extract(_NO_CHINESE_NAME, "無關文字", _CONTRACT_NOT_FOUND)
        assert f.疑慮標示 == "雇主資料:找不到契約頁,未擷取"


class TestOtherPledgeErrors:

    def test_other_errors_still_give_up_quietly(self):
        """非 Vision 的例外只是少一格值,不拖垮契約頁讀到的 H~O(ADR-0006)。"""

        def _boom(b):
            raise RuntimeError("讀圖壞了")

        assert ee.rescue_name_from_pledge(_docx(), FakeVision(document=_boom)) == PledgeRescue()
