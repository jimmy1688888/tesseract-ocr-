# -*- coding: utf-8 -*-
"""台仲區塊(CONTEXT.md「台仲區塊」):次頁 Vision 全文 → 台仲的電話／名稱／地址。

錨定規則一律從全文進(agency_block_in),不碰錨定的私有函式 —— 錨定怎麼重寫都不用改測試:
  ① 以「台灣仲介」欄位標籤錨定起點(短、非句號;條文裡的 prose 不算)
  ② 標籤被紅章毀到只剩殘骸時靠殘骸錨定;嚴格標籤優先
  ③ 下界:下一個仲介標籤(印尼或台灣)或印尼電話標籤
  ④ 台印兩欄並排(標籤相鄰、圈不到資料)時放寬到乙方,沒有乙方就到頁尾
  ⑤ 都錨不到 → 沒有台仲區塊(不猜)
測試字串取自 32324 / 32345 / 32426 次頁實際 OCR,其餘捏造。
"""
import io

from PIL import Image

from agency_block import AgencyBlock, agency_block_from_next_page, agency_block_in
from conftest import FakeIdentifier, FakeVision, pages_of

TW_LABEL = "台灣仲介公司(Agency Taiwan):"
ID_LABEL = "印尼仲介公司(Agency Indonesia):"
TW_PHONE = "電話:02-2727-6999"


def _text(*lines: str) -> str:
    return "\n".join(lines)


class TestAnchor:
    """① 起點:真欄位標籤才算。"""

    def test_block_after_tw_label(self):
        block = agency_block_in(_text(
            "PERJANJIAN PENEMPATAN", TW_LABEL, "鼎力國際開發有限公司",
            "台中市北區台灣大道2段360號23樓之1", TW_PHONE))
        assert block == AgencyBlock(("0227276999",), "鼎力國際開發有限公司",
                                    "台中市北區台灣大道2段360號23樓之1")

    def test_label_spellings(self):
        """32324 / 32345 的三種實際寫法都錨得到。"""
        for label in ["台灣仲介公司(Agency Taiwan):", "台灣仲介 Agensi Taiwan:",
                      "台灣仲介公司 (Agency Taiwan) :"]:
            assert agency_block_in(_text(label, TW_PHONE)).phones == ("0227276999",), label

    def test_prose_mentioning_tw_agency_is_not_a_label(self):
        """條文長句「…由台灣仲介公司存查。」不是標籤:錨到它會把它後面的雇主電話當成台仲的。"""
        block = agency_block_in(_text(
            "本契約一式三份,一份由勞工收執,一份由雇主收執,另一份由台灣仲介公司存查。",
            "電話:03-1111-1111",
            "oleh agency Taiwan dan agency Indonesia sesuai dengan peraturan yang berlaku.",
            "電話:03-2222-2222",
            TW_LABEL, TW_PHONE))
        assert block.phones == ("0227276999",)

    def test_long_line_mentioning_tw_agency_is_not_a_label(self):
        """Vision 把條文從句中斷行時沒有句號,靠長度擋:標籤是短的欄名。"""
        block = agency_block_in(_text(
            "本契約一式三份,一份由勞工收執,一份由雇主收執,另一份由台灣仲介公司存查,如有爭議依中華民國",
            "電話:03-1111-1111",
            TW_LABEL, TW_PHONE))
        assert block.phones == ("0227276999",)


class TestMangledLabel:
    """② 32426:紅章把『台灣仲介 Agensi Taiwan:』蓋成『tensi Taiwan:』,頁上電話完好。"""

    # 同頁其他含 Taiwan 的行 —— 放寬後都不能被當成標籤
    DECOYS = [
        "DEFLTING 260.SEX TAIWAN BLVD NORTH",
        "DISE CHUNG CITY, TAIWAN(RO.C.)",
        "ke Instansi Pemerintah Taiwan yang berwenang untuk diverifikasi.",
        "Taiwan:",
        "in Taiwan :",
    ]
    # 32426 次頁自 `tensi Taiwan:` 起的實際 OCR 行
    BLOCK_32426 = [
        "tensi Taiwan:",
        "ma",
        "DIN GHAERNATIONAL DEVELOPMENT",
        "C. 鼎力及院發有限公司)",
        "DEFLTING 260.SEX TAIWAN BLVD NORTH",
        "DISE CHUNG CITY, TAIWAN(RO.C.)",
        "台中市北區台灣大道2段360號23樓之1",
        "電話:04-23263526,傳真:04-23263528",
        "適用章",
        "印尼駐台北經濟貿易辦事處 Kantor Dagang dan Ekonomi Indonesia di Taipei",
    ]

    def test_32426_values(self):
        block = agency_block_in(_text("PERJANJIAN PENEMPATAN", *self.BLOCK_32426))
        assert block.phones == ("0423263526",)                    # 傳真切掉
        assert block.address == "台中市北區台灣大道2段360號23樓之1"
        # 名稱被章毀成「鼎力及院發…」:抽得到但不可靠(所以反查電話排在名稱前);
        # 這裡只釘住不會把標籤行當成名稱
        assert "Taiwan" not in block.name

    def test_decoys_alone_are_not_anchored(self):
        """殘骸錨要 Agensi/Agency 的殘骸加冒號;只有 Taiwan 的行錨不到 → 不猜。"""
        assert agency_block_in(_text(*self.DECOYS, TW_PHONE)) == AgencyBlock()

    def test_strict_label_wins_over_remnant(self):
        """嚴格標籤在就用它,殘骸式只在嚴格式完全找不到時才用。"""
        block = agency_block_in(_text(
            "tensi Taiwan:", "電話:03-1111-1111", "Agensi Indonesia:",
            TW_LABEL, TW_PHONE))
        assert block.phones == ("0227276999",)


class TestLowerBound:
    """③ 下界:台仲區塊不吃進後面那一欄的電話。"""

    def test_stops_at_indonesian_agency_label(self):
        block = agency_block_in(_text(
            TW_LABEL, "測試人力仲介有限公司", TW_PHONE, ID_LABEL, "電話:03-3333-3333"))
        assert block.phones == ("0227276999",)

    def test_stops_at_another_tw_label(self):
        block = agency_block_in(_text(
            TW_LABEL, "測試人力仲介有限公司", TW_PHONE, "台灣仲介 Agensi Taiwan:",
            "電話:03-3333-3333"))
        assert block.phones == ("0227276999",)

    def test_stops_at_indonesian_phone_label(self):
        """印尼標籤糊掉沒對上時,印尼電話標籤 Telp 仍收得住。"""
        block = agency_block_in(_text(
            TW_LABEL, "測試人力仲介有限公司", TW_PHONE, "Telp:", "03-3333-3333"))
        assert block.phones == ("0227276999",)


class TestSideBySide:
    """④ 台印兩欄並排:台仲標籤緊接印尼標籤,資料排在其後並與印尼交錯。"""

    # 印尼那一欄刻意放會被誤認成台仲的行:中文機構名、0 開頭的印尼市話
    INTERLEAVED = [
        TW_LABEL, "P3MI (Agensi Indonesia):",
        "PT MAJU JAYA 人力仲介有限公司 (P3MI)", "測試人力仲介有限公司",
        "台中市北區台灣大道2段360號", "JL. PEJANGGIK NO 17 MATARAM 0370-631234",
        TW_PHONE, "Telp: +62 21 5550000",
    ]

    def test_widens_to_party_b_and_drops_indonesian_lines(self):
        block = agency_block_in(_text(
            *self.INTERLEAVED, "乙方(PIHAK KEDUA):", "電話:0912-345-678"))
        assert block == AgencyBlock(("0227276999",), "測試人力仲介有限公司",
                                    "台中市北區台灣大道2段360號")

    def test_widens_to_page_end_without_party_b(self):
        block = agency_block_in(_text(*self.INTERLEAVED, "電話:0912-345-678"))
        assert block.phones == ("0227276999", "0912345678")


class TestValues:
    """從錨到的區塊取值。"""

    def test_same_phone_written_two_ways_deduped(self):
        """去重以電話標準形為鍵(#48):同一支號碼寫法不同,只留一支。"""
        block = agency_block_in(_text(
            TW_LABEL, "電話:04-23263526", "TEL: 0423263526", "(04)2326-3526"))
        assert block.phones == ("0423263526",)

    def test_name_after_label_colon(self):
        block = agency_block_in(_text("台灣仲介公司:鼎力國際開發有限公司", TW_PHONE))
        assert block.name == "鼎力國際開發有限公司"

    def test_name_stripped_of_english_residue(self):
        """英文名 CO.,LTD 斷行黏進中文名。"""
        block = agency_block_in(_text(TW_LABEL, "LTD(裕倉國際開發有限公司", TW_PHONE))
        assert block.name == "裕倉國際開發有限公司"

    def test_labelled_address(self):
        for line in ["地址:台北市松山區復興北路191號5樓", "Alamat:台北市松山區復興北路191號5樓",
                     "地址/Alamat:台北市松山區復興北路191號5樓"]:
            block = agency_block_in(_text(TW_LABEL, line))
            assert block.address == "台北市松山區復興北路191號5樓", line

    def test_english_only_address_is_empty(self):
        """台仲地址只有英文時取不到(改靠名稱、電話反查)。"""
        block = agency_block_in(_text(TW_LABEL, "5F, NO 191, FUXING N. RD., TAIPEI CITY"))
        assert block.address == ""


def _png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (20, 20), "white").save(buf, "PNG")
    return buf.getvalue()


class TestFromNextPage:
    """邊界:認頁結果的次頁 → 整頁送 Vision 一次 → 全文取台仲區塊。"""

    def test_reads_the_page_after_the_contract_page(self):
        contract, next_page = _png(), _png()
        vision = FakeVision(document=lambda image: _text(TW_LABEL, TW_PHONE))
        pages = pages_of((6, "image2.png", contract), (0, "image3.png", next_page))
        assert agency_block_from_next_page(pages, vision).phones == ("0227276999",)
        assert len(vision.document_reads) == 1

    def test_no_contract_page_no_vision(self):
        """契約頁身分不明就沒有次頁,不花 Vision(CONTEXT「契約頁 / 次頁」)。"""
        pages = pages_of((0, "image2.png", _png()), (0, "image3.png", _png()))
        assert agency_block_from_next_page(pages, FakeVision()) == AgencyBlock()

    def test_contract_page_is_last_no_vision(self):
        pages = pages_of((0, "image1.png", _png()), (6, "image2.png", _png()))
        assert agency_block_from_next_page(pages, FakeVision()) == AgencyBlock()


class TestRescueReasonUsesStandardForm:
    """D 欄反查理由裡的台仲電話來自 OCR,跟著寫成電話標準形(#48)。
    替身只放在外部邊界:次頁 Vision 讀出的全文;名冊用注入的假名冊。"""

    def test_reason_shows_standard_form(self, tmp_path):
        from batch import Batch
        from permit_outcome.rescue import rescue_from_next_page
        from permit_lookup import PermitLookup
        from sheet_row import SheetRow, SheetStatus

        roster = PermitLookup([{"許可證": "2340", "機構名稱": "測試仲介", "機構地址": "地址",
                                "電話": "(02)2727-6999#12", "廢止許可日期": "", "終止營業日期": ""}])
        vision = FakeVision(document=lambda image: _text(
            TW_LABEL, "電話:02-2727-6999,傳真:02-2727-6000"))
        docx = tmp_path / "32417.docx"
        docx.write_bytes(b"x")
        seed = [SheetRow("32417.docx", "", SheetStatus.MANUAL_REVIEW, "全無命中")]
        identifier = FakeIdentifier((6, "image2.png", _png()), (0, "image3.png", _png()))

        [row] = rescue_from_next_page(Batch.from_file(docx), seed, roster, vision, identifier)
        assert row.permit == "2340"
        assert "台仲電話 0227276999 " in row.reason
        assert "02-2727-6999" not in row.reason
