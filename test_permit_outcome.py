# -*- coding: utf-8 -*-
"""許可證判讀結果(CONTEXT.md):一批送審決策走完去向後的 Sheets A~D,逐字釘住(#68)。

3+K+AC+AB 這一輪全是純重構 —— D 欄一個字都不能變。送審快照只守送審決策那一段理由,
守不到 Vision 驗證與反查救援接上去的片段,所以這裡把每條路徑的**整列**寫死。
期望值是當時(2026-10-30)正式程式的輸出,重構時一格都不改;改了就是行為變更。

Vision、名冊、次頁台仲區塊全用替身,資料全部捏造。
"""
import pytest

from batch import Batch
from conftest import ROOT, FakeVision, project_sources
from matches_csv import Roi
from sheet_row import SheetRow, SheetStatus
from submission_decision import Decision, Destination
from test_agency_pick import Roster, _block
from vision_reader import VisionCallFailed

KEYED, REVIEW = SheetStatus.KEYED_IN, SheetStatus.MANUAL_REVIEW
NOT_DONE = "；反查救援未完成:次頁 Vision 呼叫失敗"
VISION_WHY = "small:mol非多數票(單一判讀 conf=60.0),需人工複核"
NO_HIT_WHY = "large:全無命中"
LOW_CONF_WHY = ("[單一判讀低信心 conf=40.0<50,略過 ROI Vision 改走次頁電話反查] "
                "small:mol非多數票(單一判讀 conf=40.0),需人工複核")
NO_IMAGES_WHY = "docx 無可讀的內嵌圖(word/media 底下沒有 png/jpg/bmp/tif/gif)"
UNREADABLE_WHY = "docx 無法開啟(不是有效的 docx 檔)"


def settle(monkeypatch, decisions, batch, vision, roster, next_page):
    """一批送審決策 → 依寫入順序的 A~D 列。次頁台仲區塊在外部邊界換掉。"""
    import employer_extract
    import permit_outcome

    monkeypatch.setattr(employer_extract, "agency_block_from_next_page", next_page)
    return permit_outcome.settle(decisions, batch, vision, roster).rows


# ── 各去向的送審決策 ────────────────────────────────────────────────────────

def _keyin(docx, value):
    return Decision(docx, Destination.直接KEYIN, value, 80.0, "image5.jpeg", "",
                    Roi.PERMIT_LOWER, "mol多數票_高信心 avg_conf=80.0")


def _to_vision(docx, crop, candidate="1234"):
    return Decision(docx, Destination.送VISION, candidate, 60.0, "image5.jpeg", crop,
                    Roi.PERMIT_LOWER, VISION_WHY)


def _rescue(docx, seed="", reason=NO_HIT_WHY):
    return Decision(docx, Destination.反查救援, seed, 40.0 if seed else 0.0, "", "", None, reason)


def _direct_review(docx, reason):
    return Decision(docx, Destination.直接人工審查, "", 0.0, "", "", None, reason)


# Vision 讀到什麼:依截圖內容(檔名)決定
VISION_TEXT = {
    "confirmed": "No. Izin: 1234",
    "one_off":   "No. Izin: 1284",
    "only":      "No. Izin: 5678",
    "disagree":  "No. Izin: 9876",
    "no_hit":    "看不出號碼",
}
NEXT_PAGE = {
    "phone.docx":      _block(["0211111111"]),
    "prefix.docx":     _block(["022222222"]),
    "name.docx":       _block(name="丙仲介"),
    "address.docx":    _block(address="丁地址"),
    "ambiguous.docx":  _block(["0244444444"]),
    "nothing.docx":    _block(),
    "low_conf.docx":   _block(),
    "failed.docx":     VisionCallFailed("503"),
}
ROSTER = Roster(phone={"0211111111": ["1111"], "0244444444": ["4444", "5555"]},
                prefix={"022222222": ["2222"]},
                name={"丙仲介": ("3333", 0.9)},
                address={"丁地址": ("4444", 0.8)})


@pytest.fixture
def world(tmp_path):
    """一批:要開次頁的 docx 檔在、截圖檔在(vision_fail 的截圖內容讓 Vision 拋錯)。"""
    for name in NEXT_PAGE:
        (tmp_path / name).write_bytes(b"x")
    crops = {}
    for key in [*VISION_TEXT, "vision_fail"]:
        crops[key] = tmp_path / f"{key}.png"
        crops[key].write_bytes(key.encode())

    def sparse(image):
        if image == b"vision_fail":
            raise VisionCallFailed("Vision 呼叫失敗: 503")
        return VISION_TEXT[image.decode()]

    def next_page(path, vision):
        found = NEXT_PAGE[path.replace("\\", "/").rsplit("/", 1)[-1]]
        if isinstance(found, Exception):
            raise found
        return found

    decisions = [
        _direct_review("no_images.docx", NO_IMAGES_WHY),
        _rescue("phone.docx"),
        _to_vision("confirmed.docx", str(crops["confirmed"])),
        _keyin("keyin.docx", "1001"),
        _to_vision("one_off.docx", str(crops["one_off"])),
        _to_vision("only.docx", str(crops["only"]), candidate=""),
        _to_vision("disagree.docx", str(crops["disagree"])),
        _to_vision("no_hit.docx", str(crops["no_hit"])),
        _to_vision("missing_crop.docx", str(tmp_path / "nope.png")),
        _to_vision("vision_fail.docx", str(crops["vision_fail"])),
        _rescue("prefix.docx"),
        _rescue("name.docx"),
        _rescue("address.docx"),
        _rescue("ambiguous.docx", seed="9999", reason=LOW_CONF_WHY),
        _rescue("nothing.docx"),
        _rescue("low_conf.docx", seed="9999", reason=LOW_CONF_WHY),
        _rescue("failed.docx"),
        _direct_review("unreadable.docx", UNREADABLE_WHY),
    ]
    return decisions, Batch.from_dir(tmp_path), FakeVision(sparse=sparse), next_page


def _v(fragment):
    return f"[送Vision原因] {VISION_WHY} ／ {fragment}"


EXPECTED = [
    # 直接 key-in
    SheetRow("keyin.docx", "1001", KEYED, "mol多數票_高信心 avg_conf=80.0"),
    # Vision 確認
    SheetRow("confirmed.docx", "1234", KEYED,
             _v("[Vision結果] Tesseract 與 Vision 雙引擎一致:'1234'")),
    # Vision 未過(依送審決策順序)
    SheetRow("one_off.docx", "", REVIEW,
             _v("[Vision結果] Tesseract='1234'(conf=60.0) vs Vision='1284' 差 1 字元"
                "(疑似 OCR 視覺相近字混淆);不自動填值,需人工輸入")),
    SheetRow("only.docx", "5678", REVIEW, _v("[Vision結果] 僅 Vision 讀到 '5678',需人工審查")),
    SheetRow("disagree.docx", "9876", REVIEW,
             _v("[Vision結果] Tesseract='1234' 與 Vision='9876' 差異 4 字元,兩引擎不一致,需人工確認")),
    SheetRow("no_hit.docx", "1234", REVIEW, _v("[Vision結果] Vision 無正則命中,需人工審查")),
    SheetRow("missing_crop.docx", "1234", REVIEW, _v("截圖檔不在,未送 Vision")),
    SheetRow("vision_fail.docx", "1234", REVIEW, _v("Vision 呼叫失敗,未驗證")),
    # 反查救援
    SheetRow("phone.docx", "1111", REVIEW,
             f"{NO_HIT_WHY}；[電話反查救回] 次頁台仲電話 0211111111 → 許可證 1111／甲仲介(仍請人工核對)"),
    SheetRow("prefix.docx", "2222", REVIEW,
             f"{NO_HIT_WHY}；[電話前綴反查救回·電話OCR可能少碼] 次頁台仲電話 022222222 "
             "前綴唯一命中 → 許可證 2222／乙仲介(仍請人工核對)"),
    SheetRow("name.docx", "3333", REVIEW,
             f"{NO_HIT_WHY}；[名稱反查救回·電話無唯一命中] 次頁台仲名稱 → 許可證 3333／丙仲介"
             "(名冊相似度 0.9,仍請人工核對)"),
    SheetRow("address.docx", "4444", REVIEW,
             f"{NO_HIT_WHY}；[地址反查救回·電話無唯一命中] 次頁台仲地址 → 許可證 4444／丁仲介"
             "(名冊相似度 0.8,仍請人工核對)"),
    SheetRow("ambiguous.docx", "9999", REVIEW,
             f"{LOW_CONF_WHY}；[電話反查·需人工擇一] 台仲電話 0244444444 "
             "命中2 家:4444／丁仲介、5555／戊仲介"),
    SheetRow("nothing.docx", "", REVIEW, NO_HIT_WHY),
    SheetRow("low_conf.docx", "9999", REVIEW, LOW_CONF_WHY),
    SheetRow("failed.docx", "", REVIEW, NO_HIT_WHY + NOT_DONE),
    # 直接人工審查(讀不到圖)
    SheetRow("no_images.docx", "", REVIEW, NO_IMAGES_WHY),
    SheetRow("unreadable.docx", "", REVIEW, UNREADABLE_WHY),
]


def test_every_path_cell_by_cell_in_write_order(monkeypatch, world):
    decisions, batch, vision, next_page = world
    rows = settle(monkeypatch, decisions, batch, vision, ROSTER, next_page)
    assert [r.source_docx for r in rows] == [r.source_docx for r in EXPECTED]
    for got, want in zip(rows, EXPECTED):
        assert got == want


def test_roster_unavailable_skips_the_rescue_and_keeps_the_seeds(monkeypatch, world):
    """名冊降級:不讀次頁(不花 Vision)、種子列原樣。D 欄不註明 —— 要註明是 #74。"""
    decisions, batch, vision, _ = world
    rescues = [d for d in decisions if d.destination is Destination.反查救援]

    def must_not_read(path, vision):
        raise AssertionError("名冊不在,不該讀次頁")

    rows = settle(monkeypatch, rescues, batch, vision, None, must_not_read)
    assert rows == [SheetRow(d.source_docx, d.candidate_value, REVIEW, d.reason) for d in rescues]
    assert vision.document_reads == []


# ── D 欄只在一處組成(#72)────────────────────────────────────────────────────

REASON_MODULE = "permit_outcome/reason.py"


def _production_sources():
    return [p for p in project_sources()
            if not p.name.startswith(("test_", "conftest"))]


def _relative(p):
    return p.relative_to(ROOT).as_posix()


@pytest.mark.parametrize("piece", ["[送Vision原因]", "[Vision結果]", " ／ "])
def test_labels_and_separators_live_only_in_the_reason_module(piece):
    holders = [_relative(p) for p in _production_sources()
               if piece in p.read_text(encoding="utf-8")]
    assert holders == [REASON_MODULE]


def test_rescue_fragments_are_joined_only_in_the_reason_module():
    """反查救援的片段本身不帶「；」;接上去的那一下只在 reason。"""
    holders = [_relative(p) for p in _production_sources()
               if p.parent.name in ("permit_outcome", "permit_lookup")
               and "；" in p.read_text(encoding="utf-8")]
    assert holders == [REASON_MODULE]
