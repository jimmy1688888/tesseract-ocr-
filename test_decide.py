# -*- coding: utf-8 -*-
"""decide_result 行為測試。

decide_result 是 pipeline 的決策核心。它根據 mol/id 兩個 OCR 結果與信心分數,
決定:
  - final_value     最終要寫入 Sheets 的值
  - vision_review   是否需要送 Google Vision 二次確認
  - note            給人看的決策說明

這支測試「鎖住」目前的行為。本次重構(dict → ScanResult dataclass)後的
所有測試名稱、行為斷言、邊界值都跟重構前一致;差別僅在:
  - 輸入從 dict 變成 ScanResult 物件
  - 斷言從字串比對("Y"/"") 變成 bool (True/False)
  - final_conf 為 0 時的內部表示從 "" 變成 0.0(CSV 序列化仍是 "")

測試組織:
  TestBothMolAndId        — Branch A:mol 與 id 都有值
  TestOnlyMol             — Branch B:只有 mol
  TestOnlyId              — Branch C:只有 id(含 multi-vote 情境)
  TestCrossMatchOverlay   — cross_match 覆蓋層
  TestPermitPartialOverlay — PERMIT_PARTIAL status 覆蓋層
  TestEdgeCases           — 邊界情境(空輸入、CSV 序列化)
  test_*_threshold_*      — 參數化的門檻邊界測試
"""

import pytest

from pipeline import (
    decide_result,
    _empty_result,
    ScanResult,
    ResultStatus,
    CONF_KEY_IN,
    CONF_VOTE_MIN,
)


# ═══════════════════════════════════════════════════════════════════════════
# 測試輔助
# ═══════════════════════════════════════════════════════════════════════════

def make_result(
    *,
    mol: str = "",
    id: str = "",
    mol_conf: float = 0.0,
    id_conf: float = 0.0,
    cross_match: bool = False,
    docx_class: str = "large",   # decide_result 預設用於 large
    status: ResultStatus = ResultStatus.OK,
    id_from_vote: bool = False,
) -> ScanResult:
    """建立 decide_result 的輸入 ScanResult。

    在 _empty_result 之上以關鍵字參數覆寫常用欄位。型別契約:
      - mol_conf/id_conf 是 float(過去測試傳 int 也行,因 Python 自動 upcast)
      - cross_match 是 bool(過去是 "✓"/"" 字串)
      - status 是 ResultStatus enum(過去是 .value 字串)
    """
    r = _empty_result("test.docx", "img.jpeg", docx_class)
    r.mol = mol
    r.id = id
    r.mol_conf = mol_conf
    r.id_conf = id_conf
    r.cross_match = cross_match
    r.status = status
    r.id_from_vote = id_from_vote
    return r


# ═══════════════════════════════════════════════════════════════════════════
# Branch A:mol 與 id 都有值
# ═══════════════════════════════════════════════════════════════════════════

class TestBothMolAndId:
    """mol 跟 id 都抓到的決策邏輯。

    四個內部分支:
      A1:mol_conf >= id_conf  → 用 mol(note 視等號決定文字)
      A2:mol_conf <  id_conf  → 用 id
      A3:final_conf <= 門檻   → 標 vision_review(疊加,不覆蓋 final_value)
      A4:mol_conf > id_conf 且 mol != id → 衝突,強制 vision_review
    """

    def test_mol_higher_conf_same_value(self):
        """mol 信心高、mol == id → 用 mol,note=mol勝(信心高),不送 Vision"""
        r = make_result(mol="1234", id="1234", mol_conf=80, id_conf=60)
        decide_result(r)
        assert r.final_value == "1234"
        assert r.final_conf == 80.0
        assert r.vision_review is False
        assert r.note == "mol勝(信心高)"

    def test_mol_higher_conf_different_value_triggers_conflict(self):
        """mol 信心高、mol != id → 強制送 Vision,note=值衝突。

        這是 A4 分支:雖然 A1 已經選了 mol 為 final_value,
        但因為兩者不同且 mol 信心高,視為「Tesseract 對 mol 太有信心了,可疑」,
        覆蓋 note 並強制 vision_review。
        """
        r = make_result(mol="1234", id="5678", mol_conf=80, id_conf=60)
        decide_result(r)
        assert r.final_value == "1234"       # 仍取 mol 作 candidate
        assert r.vision_review is True       # 但要 Vision 確認
        assert "值衝突" in r.note
        assert "mol≠permit" in r.note

    def test_id_higher_conf_same_value(self):
        """id 信心高、mol == id → 用 id,note=permit勝(信心高)"""
        r = make_result(mol="1234", id="1234", mol_conf=60, id_conf=80)
        decide_result(r)
        assert r.final_value == "1234"
        assert r.final_conf == 80.0
        assert r.vision_review is False
        assert r.note == "permit勝(信心高)"

    def test_id_higher_conf_different_value(self):
        """id 信心高、mol != id → 用 id。

        值得注意:此情境沒有 A4 衝突分支保護(A4 條件是 mol_conf > id_conf)。
        若 id 是錯的而 mol 是對的,會被靜默選用 id。這是現有設計的潛在弱點,
        但本測試僅鎖住現有行為。
        """
        r = make_result(mol="1234", id="5678", mol_conf=60, id_conf=80)
        decide_result(r)
        assert r.final_value == "5678"
        assert r.final_conf == 80.0
        # 80 > 55 不觸發 A3;mol_conf > id_conf 為 False 不觸發 A4
        assert r.vision_review is False

    def test_equal_conf_same_value_uses_mol_equals_note(self):
        """信心相等、mol == id → 用 mol,note=mol==permit"""
        r = make_result(mol="1234", id="1234", mol_conf=70, id_conf=70)
        decide_result(r)
        assert r.final_value == "1234"
        assert r.note == "mol==permit"
        assert r.vision_review is False

    def test_equal_conf_different_value_no_conflict_flag(self):
        """信心相等、mol != id → 用 mol,但不觸發衝突分支。

        這是 A4 邏輯的盲點:條件是 mol_conf > id_conf(嚴格大於),所以信心相等時
        即使值不同也不送 Vision。本測試僅鎖住現有行為。
        """
        r = make_result(mol="1234", id="5678", mol_conf=70, id_conf=70)
        decide_result(r)
        assert r.final_value == "1234"
        assert r.note == "mol==permit"   # 因 mol_conf == id_conf
        assert r.vision_review is False  # 現況:信心相等時不觸發衝突

    def test_both_present_low_conf_triggers_vision(self):
        """兩個都有值、贏家信心 <= 門檻 → A3 觸發 vision_review"""
        r = make_result(mol="1234", id="1234", mol_conf=50, id_conf=40)
        decide_result(r)
        assert r.final_value == "1234"
        assert r.final_conf == 50.0
        assert r.vision_review is True


# ═══════════════════════════════════════════════════════════════════════════
# Branch B:只有 mol
# ═══════════════════════════════════════════════════════════════════════════

class TestOnlyMol:
    """只抓到 mol(permit 沒抓到)的決策邏輯。

    觸發 vision_review 的條件 (B1):
      docx_class == "large" AND mol_conf <= CONF_KEY_IN
    換言之:small docx 即使 mol 信心低,在 decide_result 內也不會被標記
    (small docx 走 aggregate_small_docx 路徑做整批決策,不經 decide_result)。
    """

    def test_mol_only_large_high_conf(self):
        """large + 只有 mol + 高信心 → 直接 key-in"""
        r = make_result(mol="1234", mol_conf=80, docx_class="large")
        decide_result(r)
        assert r.final_value == "1234"
        assert r.final_conf == 80.0
        assert r.vision_review is False

    def test_mol_only_large_low_conf(self):
        """large + 只有 mol + 低信心 → 送 Vision"""
        r = make_result(mol="1234", mol_conf=40, docx_class="large")
        decide_result(r)
        assert r.final_value == "1234"
        assert r.vision_review is True
        assert r.note == "僅mol，信心低"

    def test_mol_only_small_low_conf_not_flagged(self):
        """small + 只有 mol + 低信心 → 不送 Vision(因為 B1 的 large 限制)

        鎖住現有行為,但提醒這在實務上不會發生(small docx 不會呼叫 decide_result)。
        """
        r = make_result(mol="1234", mol_conf=40, docx_class="small")
        decide_result(r)
        assert r.vision_review is False


# ═══════════════════════════════════════════════════════════════════════════
# Branch C:只有 id (permit)
# ═══════════════════════════════════════════════════════════════════════════

class TestOnlyId:
    """只抓到 id 的決策邏輯。

    關鍵設計:id_from_vote=True 時門檻從 CONF_KEY_IN(55) 放寬至 CONF_VOTE_MIN(45),
    因為多數票本身就是一層額外保證(多次 OCR 取同樣的值)。
    """

    def test_id_only_high_conf(self):
        """只有 id + 高信心 → 不送 Vision"""
        r = make_result(id="5678", id_conf=80)
        decide_result(r)
        assert r.final_value == "5678"
        assert r.final_conf == 80.0
        assert r.vision_review is False

    def test_id_only_low_conf(self):
        """只有 id + 低信心 → 送 Vision"""
        r = make_result(id="5678", id_conf=40)
        decide_result(r)
        assert r.vision_review is True
        assert r.note == "僅permit，信心低"

    def test_id_from_vote_passes_relaxed_threshold(self):
        """vote 來源 + 信心介於 CONF_VOTE_MIN 與 CONF_KEY_IN 之間 → 不送

        50 介於 45 (vote 門檻) 與 55 (一般門檻) 之間:
          - 一般情境:50 <= 55 → 送 Vision
          - vote 情境:50 > 45 → 不送
        """
        r = make_result(id="5678", id_conf=50, id_from_vote=True)
        decide_result(r)
        assert r.vision_review is False

    def test_id_from_vote_below_vote_threshold(self):
        """vote 來源 + 信心 <= CONF_VOTE_MIN → 仍送 Vision,note 註明 vote 來源"""
        r = make_result(id="5678", id_conf=30, id_from_vote=True)
        decide_result(r)
        assert r.vision_review is True
        assert r.note == "permit多數票，信心低"

    def test_id_non_vote_below_main_threshold(self):
        """非 vote 來源 + 信心低 → 用嚴格門檻"""
        r = make_result(id="5678", id_conf=30, id_from_vote=False)
        decide_result(r)
        assert r.vision_review is True
        assert r.note == "僅permit，信心低"


# ═══════════════════════════════════════════════════════════════════════════
# 覆蓋層(overlay):cross_match
# ═══════════════════════════════════════════════════════════════════════════

class TestCrossMatchOverlay:
    """cross_match=True 在 Branch A/B/C 處理完之後再做一次低信心檢查。

    語意:即使 mol 跟 permit 對上(cross_match),若整體信心仍低,
    還是不放心,要 Vision 確認。

    注意:過去這個欄位是字串 "✓"/"";重構後是 bool True/False。
    decide_result 的邏輯不變(都是 truthy 檢查)。
    """

    def test_cross_match_high_conf_passes(self):
        """cross_match + 高信心 → 不送 Vision"""
        r = make_result(mol="1234", id="1234", mol_conf=80, id_conf=80,
                        cross_match=True)
        decide_result(r)
        assert r.vision_review is False

    def test_cross_match_low_conf_appends_note(self):
        """cross_match + final_conf 在門檻 → 送 Vision,note 附加'多數決信心低'

        此情境 Branch A 的 A3 也會觸發 vision_review,然後 overlay 再附加 note。
        """
        r = make_result(mol="1234", id="1234",
                        mol_conf=CONF_KEY_IN, id_conf=CONF_KEY_IN,
                        cross_match=True)
        decide_result(r)
        assert r.vision_review is True
        assert "多數決信心低" in r.note


# ═══════════════════════════════════════════════════════════════════════════
# 覆蓋層(overlay):PERMIT_PARTIAL status
# ═══════════════════════════════════════════════════════════════════════════

class TestPermitPartialOverlay:
    """status == PERMIT_PARTIAL 永遠觸發 vision_review。

    這個 status 是上一輪重構從 _permit_partial_hit 隱藏 flag 取代而來,
    語意:permit 投票有部分命中但無多數,候選值不可信。
    """

    def test_permit_partial_with_high_conf_still_triggers_vision(self):
        """PERMIT_PARTIAL 即使信心高也送 Vision(因為投票無多數)"""
        r = make_result(id="5678", id_conf=99,
                        status=ResultStatus.PERMIT_PARTIAL)
        decide_result(r)
        assert r.vision_review is True
        assert "permit部分命中無多數" in r.note

    def test_permit_partial_appends_to_existing_note(self):
        """已有 note 時,partial 訊息附加在後面不覆蓋"""
        r = make_result(mol="1234", id="1234", mol_conf=80, id_conf=80,
                        status=ResultStatus.PERMIT_PARTIAL)
        decide_result(r)
        # 同時有 mol==permit 與 partial 訊息
        assert "mol==permit" in r.note
        assert "permit部分命中無多數" in r.note


# ═══════════════════════════════════════════════════════════════════════════
# 邊界情境
# ═══════════════════════════════════════════════════════════════════════════

class TestEdgeCases:

    def test_no_values_at_all(self):
        """mol/id 都沒值 → final_value 空,vision_review 不觸發"""
        r = make_result()
        decide_result(r)
        assert r.final_value == ""
        assert r.vision_review is False

    def test_default_zero_conf_triggers_vision_when_both_present(self):
        """conf 用預設值 0.0(過去測試會傳 "" 觸發 _to_f) → 0 <= 55 觸發 A3。

        過去的測試是 `mol_conf="", id_conf=""` 鎖住「空字串會被 _to_f 視為 0」的行為。
        現在 _to_f 不存在,conf 直接是 float。語意完全保留(0 <= 55 仍觸發 A3)。
        """
        r = make_result(mol="1234", id="1234")   # conf 用預設 0.0
        decide_result(r)
        assert r.final_value == "1234"
        assert r.vision_review is True   # 0 <= 55 觸發 A3

    def test_csv_roundtrip_converts_string_conf_to_float(self):
        """從 CSV 讀回的 conf 是字串('67.3') → from_csv_row 轉成 float。

        過去 _to_f 散在 decide_result / process_large_vs / aggregate_small_docx;
        重構後 str → float 的轉換集中在 ScanResult.from_csv_row,decide_result 永遠
        只看到 float。此測試驗證集中化的型別契約。
        """
        # 模擬一筆 CSV 列
        csv_row = {
            "source_docx": "test.docx", "image_name": "img.jpeg",
            "docx_class": "large",
            "mol": "1234", "mol_layer": "1", "mol_conf": "67.3",
            "id": "1234", "id_layer": "1", "id_conf": "50.5",
            "cross_match": "", "final_value": "", "final_conf": "",
            "vision_review": "", "note": "", "manual_review": "",
            "low_conf": "", "hit_config": "", "hit_roi": "",
            "mol_crop": "", "permit_crop": "",
            "status": "OK",
        }
        r = ScanResult.from_csv_row(csv_row)
        # 型別與值都正確
        assert r.mol_conf == 67.3
        assert isinstance(r.mol_conf, float)
        assert r.id_conf == 50.5
        # decide_result 直接消費,不需要再翻譯
        decide_result(r)
        assert r.final_value == "1234"
        assert r.final_conf == 67.3
        assert r.vision_review is False   # 67.3 > 55

    def test_final_conf_zero_serializes_to_empty_in_csv(self):
        """final_conf == 0 時內部是 0.0,但寫到 CSV 仍序列化為 ""。

        重構前:final_conf 內部就是 ""(混用 str/float)
        重構後:final_conf 內部一律 float,CSV 序列化邊界(to_csv_row)才轉 ""。
        對 CSV 使用者(包括 Excel 觀察員)行為完全一致。
        """
        r = make_result()   # 全空
        decide_result(r)
        # 內部:float
        assert r.final_conf == 0.0
        assert isinstance(r.final_conf, float)
        # CSV 序列化:空字串(行為保留)
        assert r.to_csv_row()["final_conf"] == ""

    def test_decide_returns_same_object_in_place(self):
        """decide_result 就地修改並回傳同一個 ScanResult(非 copy)"""
        r = make_result(mol="1234", id="1234", mol_conf=80, id_conf=80)
        returned = decide_result(r)
        assert returned is r


# ═══════════════════════════════════════════════════════════════════════════
# 參數化:門檻邊界
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("conf,expected_vision_review", [
    (CONF_KEY_IN - 1, True),     # 54 → <= 55,送
    (CONF_KEY_IN,     True),     # 55 → <= 55,送(<= 邊界)
    (CONF_KEY_IN + 1, False),    # 56 → > 55,不送
    (CONF_KEY_IN + 10, False),
])
def test_keyin_threshold_is_inclusive(conf, expected_vision_review):
    """A3 的門檻條件是 <=,所以邊界值(55)會被視為低信心而送 Vision"""
    r = make_result(mol="1234", id="1234", mol_conf=conf, id_conf=conf,
                    docx_class="large")
    decide_result(r)
    assert r.vision_review is expected_vision_review


@pytest.mark.parametrize("conf,from_vote,expected_vision_review", [
    # 非 vote → 用嚴格門檻 CONF_KEY_IN(55)
    (CONF_KEY_IN - 1, False, True),
    (CONF_KEY_IN,     False, True),
    (CONF_KEY_IN + 1, False, False),
    # vote → 放寬門檻 CONF_VOTE_MIN(45)
    (CONF_VOTE_MIN - 1, True, True),
    (CONF_VOTE_MIN,     True, True),
    (CONF_VOTE_MIN + 1, True, False),
    (CONF_KEY_IN - 1,   True, False),   # 54 對 vote 來說已經是高信心
])
def test_id_only_threshold_depends_on_vote_source(conf, from_vote, expected_vision_review):
    """純 permit 情境,門檻會根據是否來自多數票切換"""
    r = make_result(id="5678", id_conf=conf, id_from_vote=from_vote)
    decide_result(r)
    assert r.vision_review is expected_vision_review
