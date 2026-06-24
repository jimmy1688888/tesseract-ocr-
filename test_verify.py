# -*- coding: utf-8 -*-
"""verify_vision_result 與輔助函數的行為測試。

這層是 Vision 結果的最後一道閘門:對每個 Vision OCR 回傳值,做三層檢查
(格式 → 雙引擎比對 → 已知清單),決定可否自動 key-in。

設計原則:**只有雙引擎(Tesseract + Vision)完全一致才自動 key-in**。
這份測試以可執行規格的形式鎖住這個保守策略。任何放寬(例如「在清單內就放行」)
都應該先改測試、看清楚變動範圍,再改 production code。

測試組織:
  TestConfirmed             — 雙引擎一致(唯一自動 key-in 的情境)
  TestLikelyOcrConfusion    — 差 1 字元(視覺相近字混淆,仍需審查)
  TestVisionOnly            — Tesseract 沒讀到、僅 Vision 有值(仍需審查)
  TestDisagreement          — 差 2 字元以上(兩引擎不同意)
  TestFormatInvalid         — Vision 無值或格式不符
  TestKnownListEffect       — 已知清單對決策的影響(僅影響 rationale)
  TestEditDistance          — _edit_distance 工具函數
  TestLoadKnownPermits      — load_known_permits_from_log 行為
"""

import csv
import tempfile
from pathlib import Path

import pytest

from pipeline import (
    verify_vision_result,
    VerifiedResult,
    VerificationLevel,
    _edit_distance,
    _is_valid_permit_format,
    load_known_permits_from_log,
)


# ═══════════════════════════════════════════════════════════════════════════
# Confirmed:雙引擎一致(唯一自動 key-in)
# ═══════════════════════════════════════════════════════════════════════════

class TestConfirmed:
    """雙引擎完全一致 → 唯一自動 key-in 的情境。"""

    def test_both_engines_agree_keyins_directly(self):
        """Tesseract 與 Vision 讀到完全相同的值 → should_keyin=True"""
        v = verify_vision_result(
            vision_value="1234",
            tesseract_candidate="1234",
            known_permits=set(),
        )
        assert v.level == VerificationLevel.CONFIRMED
        assert v.should_keyin is True
        assert v.final_value == "1234"
        assert "雙引擎一致" in v.rationale

    def test_confirmed_with_known_list_still_keyins(self):
        """雙引擎一致 + 在已知清單內 → 仍自動 key-in,rationale 註明在清單"""
        v = verify_vision_result(
            vision_value="1234",
            tesseract_candidate="1234",
            known_permits={"1234", "5678"},
        )
        assert v.should_keyin is True
        assert v.in_known_list is True
        assert "已知清單" in v.rationale

    def test_confirmed_without_known_list_still_keyins(self):
        """雙引擎一致 + 不在已知清單 → 仍自動 key-in(雙引擎一致已足夠)"""
        v = verify_vision_result(
            vision_value="9999",
            tesseract_candidate="9999",
            known_permits={"1234", "5678"},
        )
        assert v.should_keyin is True
        assert v.in_known_list is False

    def test_confirmed_with_none_known_permits(self):
        """known_permits=None(未提供清單) → 雙引擎一致仍可 key-in"""
        v = verify_vision_result(
            vision_value="1234",
            tesseract_candidate="1234",
            known_permits=None,
        )
        assert v.should_keyin is True
        assert v.in_known_list is False


# ═══════════════════════════════════════════════════════════════════════════
# LikelyOcrConfusion:差 1 字元(疑似視覺相近字)
# ═══════════════════════════════════════════════════════════════════════════

class TestLikelyOcrConfusion:
    """差 1 字元的情境。常見於 0/O、1/l、5/S 等視覺相近字混淆。

    **重要設計決定**:即使 Vision 候選值在已知清單內,差 1 字元仍標人工審查。
    理由:在 4 位數的小空間裡,「差 1 字元也剛好在清單內」並不罕見,
    若以此為自動採用條件,會把 OCR 錯誤合理化為「歷史出現過的合法值」。
    """

    def test_zero_vs_O_confusion(self):
        """1234 vs 12O4(0 與 O 混淆) → LIKELY_OCR_CONFUSION,不 keyin"""
        v = verify_vision_result(
            vision_value="1234",
            tesseract_candidate="12O4",  # 假設 Tesseract 把 0 看成 O
            known_permits=set(),
        )
        # 注意:_is_valid_permit_format 只看 vision_value,Tesseract 候選可帶字母
        assert v.level == VerificationLevel.LIKELY_OCR_CONFUSION
        assert v.should_keyin is False

    def test_one_char_substitution(self):
        """1234 vs 1334 → 差 1 字元"""
        v = verify_vision_result(
            vision_value="1234",
            tesseract_candidate="1334",
            known_permits=set(),
        )
        assert v.level == VerificationLevel.LIKELY_OCR_CONFUSION
        assert v.should_keyin is False
        assert "差 1 字元" in v.rationale

    def test_one_char_diff_in_known_list_still_not_keyin(self):
        """**改點 1**:差 1 字元 + 在已知清單 → 仍標 review(不自動 key-in)。

        此測試明確鎖住保守策略:不因「Vision 值剛好在歷史清單裡」就放行,
        因為這個訊號太弱(4 位數的解空間小,巧合機率不低)。
        """
        v = verify_vision_result(
            vision_value="1234",
            tesseract_candidate="1334",
            known_permits={"1234", "5678"},   # 1234 在清單內
        )
        assert v.level == VerificationLevel.LIKELY_OCR_CONFUSION
        assert v.should_keyin is False        # ← 重點:仍是 False
        assert v.in_known_list is True
        # rationale 應該包含「在已知清單內」的提示,給審查者參考
        assert "已知清單" in v.rationale

    def test_one_char_diff_not_in_known_list(self):
        """差 1 字元 + 不在已知清單 → 標 review"""
        v = verify_vision_result(
            vision_value="9999",
            tesseract_candidate="8999",
            known_permits={"1234", "5678"},
        )
        assert v.level == VerificationLevel.LIKELY_OCR_CONFUSION
        assert v.should_keyin is False
        assert v.in_known_list is False

    def test_insertion_one_char(self):
        """1234 vs 12345 → 差 1 字(插入)。

        注意:5 位數的 candidate 不會自然由 Tesseract 產生(find_permits 抓 4 位),
        但理論上保留行為一致性 — distance == 1 都歸 LIKELY_OCR_CONFUSION。
        """
        v = verify_vision_result(
            vision_value="1234",
            tesseract_candidate="12345",
            known_permits=set(),
        )
        assert v.level == VerificationLevel.LIKELY_OCR_CONFUSION
        assert v.should_keyin is False


# ═══════════════════════════════════════════════════════════════════════════
# LikelyOcrConfusion 子規則:Tesseract conf > 50 時優先採 Tesseract 值
# ═══════════════════════════════════════════════════════════════════════════

class TestTesseractConfThresholdInConfusion:
    """LIKELY_OCR_CONFUSION 分支的 final_value 選擇規則。

    設計動機:當 Tesseract conf > 50,代表 Tesseract 自己的判讀並非低信心。
    這時 Vision 的「修正」反而可能是錯誤的修正(例如 Vision 把清晰的 6 誤判為 8)。
    所以在差 1 字元的情境下:
      - Tesseract conf >  50  → 採 Tesseract 值
      - Tesseract conf <= 50  → 採 Vision 值
    不論採哪邊,should_keyin 都是 False — 兩引擎不一致,終究要人工確認。
    """

    def test_high_tesseract_conf_uses_tesseract_value(self):
        """Tesseract='3166'(conf=70) vs Vision='3186' → 採 Tesseract 值。

        這是使用者明確要求的範例:差 1 字、Tesseract 信心高 → 信 Tesseract。
        """
        v = verify_vision_result(
            vision_value="3186",
            tesseract_candidate="3166",
            known_permits=set(),
            tesseract_conf=70.0,
        )
        assert v.level == VerificationLevel.LIKELY_OCR_CONFUSION
        assert v.should_keyin is False               # 仍需人工
        assert v.final_value == "3166"               # 但採 Tesseract 值
        assert "優先採 Tesseract" in v.rationale
        assert "conf=70.0" in v.rationale            # rationale 顯示信心數字

    def test_low_tesseract_conf_uses_vision_value(self):
        """Tesseract='3166'(conf=30) vs Vision='3186' → 採 Vision 值"""
        v = verify_vision_result(
            vision_value="3186",
            tesseract_candidate="3166",
            known_permits=set(),
            tesseract_conf=30.0,
        )
        assert v.level == VerificationLevel.LIKELY_OCR_CONFUSION
        assert v.should_keyin is False
        assert v.final_value == "3186"               # 改採 Vision 值
        assert "採 Vision" in v.rationale
        assert "conf=30.0" in v.rationale

    def test_conf_exactly_at_50_boundary_uses_vision(self):
        """Tesseract conf 剛好等於 50 → 不滿足 > 50,採 Vision 值。

        鎖住邊界:條件是嚴格大於 50(>),不是大於等於(>=)。
        若未來想放寬到 >= 50,改測試先,改 production code 後。
        """
        v = verify_vision_result(
            vision_value="3186",
            tesseract_candidate="3166",
            known_permits=set(),
            tesseract_conf=50.0,
        )
        assert v.final_value == "3186"               # 50.0 不 > 50,採 Vision

    def test_conf_just_above_50_uses_tesseract(self):
        """Tesseract conf=50.1 → 剛好 > 50,採 Tesseract"""
        v = verify_vision_result(
            vision_value="3186",
            tesseract_candidate="3166",
            known_permits=set(),
            tesseract_conf=50.1,
        )
        assert v.final_value == "3166"

    def test_default_conf_zero_uses_vision(self):
        """未傳 tesseract_conf(預設 0.0) → 採 Vision 值(向後相容)"""
        v = verify_vision_result(
            vision_value="3186",
            tesseract_candidate="3166",
            known_permits=set(),
            # 不傳 tesseract_conf
        )
        assert v.final_value == "3186"               # 預設 0.0 不 > 50

    def test_conf_threshold_only_applies_to_likely_confusion(self):
        """門檻只在 LIKELY_OCR_CONFUSION 生效,DISAGREEMENT(差 2 字以上)不適用。

        Tesseract='3166'(conf=80) vs Vision='9999' → DISAGREEMENT
        DISAGREEMENT 一律採 Vision 值(避免「Tesseract 高信心讀錯」的情境),
        高 conf 不會推翻這個決定。
        """
        v = verify_vision_result(
            vision_value="9999",
            tesseract_candidate="3166",
            known_permits=set(),
            tesseract_conf=80.0,
        )
        assert v.level == VerificationLevel.DISAGREEMENT
        assert v.final_value == "9999"               # DISAGREEMENT 用 Vision

    def test_conf_threshold_does_not_promote_to_keyin(self):
        """Tesseract conf 不論多高都不能把 LIKELY_OCR_CONFUSION 升級為自動 key-in。

        鎖住保守策略:兩引擎不一致 = 絕對不自動寫入,不論誰的信心高。
        """
        for conf in [50.1, 70, 90, 99.9]:
            v = verify_vision_result(
                vision_value="3186",
                tesseract_candidate="3166",
                known_permits=set(),
                tesseract_conf=conf,
            )
            assert v.should_keyin is False, f"conf={conf} 不該推升為 keyin"

    def test_in_known_list_still_appended_to_rationale(self):
        """conf > 50 + 在已知清單 → rationale 同時包含兩種資訊"""
        v = verify_vision_result(
            vision_value="3186",
            tesseract_candidate="3166",
            known_permits={"3186"},
            tesseract_conf=70.0,
        )
        assert v.final_value == "3166"               # 採 Tesseract
        assert v.in_known_list is True               # Vision 值 3186 在清單內
        assert "優先採 Tesseract" in v.rationale
        assert "已知清單" in v.rationale


# ═══════════════════════════════════════════════════════════════════════════
# VisionOnly:Tesseract 沒讀到,僅 Vision 有值
# ═══════════════════════════════════════════════════════════════════════════

class TestVisionOnly:
    """只有 Vision 讀到的情境。

    **改點 2**:即使 Vision 值在已知清單內,因為只有單一 OCR 引擎確認,
    仍標人工審查。雙引擎獨立確認才是 key-in 的門檻。
    """

    def test_vision_only_not_in_known_list(self):
        """Tesseract 沒讀到 + Vision 值不在清單 → 標 review"""
        v = verify_vision_result(
            vision_value="1234",
            tesseract_candidate="",
            known_permits={"5678"},
        )
        assert v.level == VerificationLevel.VISION_ONLY
        assert v.should_keyin is False
        assert v.in_known_list is False
        assert "不在已知清單" in v.rationale

    def test_vision_only_in_known_list_still_not_keyin(self):
        """**改點 2 鎖住**:Tesseract 沒讀到 + Vision 值在清單 → 仍標 review。

        過去設計(已捨棄):此情境曾被視為「在清單背書下可採用」。
        現行設計:任何僅靠單一 OCR 引擎的判定都不自動寫入,清單僅用於
        提示審查者「這個值歷史上出現過,可優先處理」。
        """
        v = verify_vision_result(
            vision_value="1234",
            tesseract_candidate="",
            known_permits={"1234", "5678"},   # 1234 在清單內
        )
        assert v.level == VerificationLevel.VISION_ONLY
        assert v.should_keyin is False        # ← 重點:仍是 False
        assert v.in_known_list is True
        # rationale 應該標註「在已知清單,可優先處理」
        assert "已知清單" in v.rationale

    def test_vision_only_uses_vision_value_as_final(self):
        """VISION_ONLY 時,final_value 用 Vision 的值(因為 Tesseract 沒讀到)"""
        v = verify_vision_result(
            vision_value="1234",
            tesseract_candidate="",
            known_permits=set(),
        )
        assert v.final_value == "1234"

    def test_vision_only_with_none_known_permits(self):
        """known_permits=None → in_known_list=False"""
        v = verify_vision_result(
            vision_value="1234",
            tesseract_candidate="",
            known_permits=None,
        )
        assert v.level == VerificationLevel.VISION_ONLY
        assert v.should_keyin is False
        assert v.in_known_list is False


# ═══════════════════════════════════════════════════════════════════════════
# Disagreement:差 2 字元以上(兩引擎不一致)
# ═══════════════════════════════════════════════════════════════════════════

class TestDisagreement:
    """差 2 字元以上 → 兩引擎大幅不同意,最可疑。"""

    def test_two_char_diff(self):
        """1234 vs 1256 → 差 2 字"""
        v = verify_vision_result(
            vision_value="1234",
            tesseract_candidate="1256",
            known_permits=set(),
        )
        assert v.level == VerificationLevel.DISAGREEMENT
        assert v.should_keyin is False
        assert "差異 2 字元" in v.rationale

    def test_completely_different(self):
        """1234 vs 9999 → 差 4 字"""
        v = verify_vision_result(
            vision_value="1234",
            tesseract_candidate="9999",
            known_permits=set(),
        )
        assert v.level == VerificationLevel.DISAGREEMENT
        assert v.should_keyin is False

    def test_disagreement_in_known_list_still_not_keyin(self):
        """DISAGREEMENT + 在已知清單 → 仍 review(改點精神延伸)"""
        v = verify_vision_result(
            vision_value="1234",
            tesseract_candidate="9999",
            known_permits={"1234"},
        )
        assert v.level == VerificationLevel.DISAGREEMENT
        assert v.should_keyin is False
        assert v.in_known_list is True


# ═══════════════════════════════════════════════════════════════════════════
# FormatInvalid:Vision 無值或格式不符
# ═══════════════════════════════════════════════════════════════════════════

class TestFormatInvalid:
    """第一層格式驗證:Vision 必須回傳 4 位數字才能進入後續比對。"""

    def test_empty_vision_value(self):
        """Vision 沒讀到任何東西 → FORMAT_INVALID"""
        v = verify_vision_result(
            vision_value="",
            tesseract_candidate="1234",
            known_permits=set(),
        )
        assert v.level == VerificationLevel.FORMAT_INVALID
        assert v.should_keyin is False
        # final_value 退回 Tesseract 候選,方便審查者看
        assert v.final_value == "1234"
        assert "Vision 無正則命中" in v.rationale

    def test_five_digit_vision_value(self):
        """Vision 回傳 5 位數(超過 permit 規範) → FORMAT_INVALID"""
        v = verify_vision_result(
            vision_value="12345",
            tesseract_candidate="1234",
            known_permits=set(),
        )
        assert v.level == VerificationLevel.FORMAT_INVALID
        assert v.should_keyin is False
        assert "不符合 4 位數格式" in v.rationale

    def test_three_digit_vision_value(self):
        """Vision 回傳 3 位數 → FORMAT_INVALID"""
        v = verify_vision_result(
            vision_value="123",
            tesseract_candidate="1234",
            known_permits=set(),
        )
        assert v.level == VerificationLevel.FORMAT_INVALID
        assert v.should_keyin is False

    def test_alphabetic_vision_value(self):
        """Vision 回傳含字母(例如把日期讀進來) → FORMAT_INVALID"""
        v = verify_vision_result(
            vision_value="ABCD",
            tesseract_candidate="1234",
            known_permits=set(),
        )
        assert v.level == VerificationLevel.FORMAT_INVALID
        assert v.should_keyin is False


# ═══════════════════════════════════════════════════════════════════════════
# KnownListEffect:已知清單對決策的影響
# ═══════════════════════════════════════════════════════════════════════════

class TestKnownListEffect:
    """已知清單僅影響 rationale 文字,不單獨改變 should_keyin。

    這份測試確認設計意圖:「在已知清單」不是自動 key-in 的充分條件。
    """

    def test_known_list_does_not_promote_likely_confusion_to_keyin(self):
        """差 1 字元時,在清單內 ≠ 可自動採用"""
        v_in = verify_vision_result("1234", "1334", {"1234"})
        v_out = verify_vision_result("1234", "1334", set())
        assert v_in.should_keyin == v_out.should_keyin == False
        # 但 in_known_list 旗標不同
        assert v_in.in_known_list is True
        assert v_out.in_known_list is False

    def test_known_list_does_not_promote_vision_only_to_keyin(self):
        """僅 Vision 讀到時,在清單內 ≠ 可自動採用"""
        v_in = verify_vision_result("1234", "", {"1234"})
        v_out = verify_vision_result("1234", "", set())
        assert v_in.should_keyin == v_out.should_keyin == False

    def test_known_list_does_not_promote_disagreement_to_keyin(self):
        """差 2 字以上時,在清單內 ≠ 可自動採用"""
        v_in = verify_vision_result("1234", "9999", {"1234"})
        v_out = verify_vision_result("1234", "9999", set())
        assert v_in.should_keyin == v_out.should_keyin == False


# ═══════════════════════════════════════════════════════════════════════════
# 工具函數:_edit_distance
# ═══════════════════════════════════════════════════════════════════════════

class TestEditDistance:
    """Levenshtein 編輯距離工具測試。
    
    被 verify_vision_result 用來區分「差 1 字(疑似 OCR 混淆)」與
    「差很多字(兩引擎不一致)」。
    """

    def test_identical_strings_distance_zero(self):
        assert _edit_distance("1234", "1234") == 0

    @pytest.mark.parametrize("a,b,expected", [
        ("1234", "1334", 1),    # 替換
        ("1234", "12345", 1),   # 插入
        ("12345", "1234", 1),   # 刪除
        ("ABCD", "ABCE", 1),    # 替換(字母)
    ])
    def test_one_edit_distance_one(self, a, b, expected):
        assert _edit_distance(a, b) == expected

    @pytest.mark.parametrize("a,b,expected", [
        ("1234", "1256", 2),
        ("1234", "5678", 4),
        ("", "1234", 4),
        ("1234", "", 4),
    ])
    def test_multi_edit_distance(self, a, b, expected):
        assert _edit_distance(a, b) == expected


# ═══════════════════════════════════════════════════════════════════════════
# 工具函數:_is_valid_permit_format
# ═══════════════════════════════════════════════════════════════════════════

class TestValidPermitFormat:
    """4 位數格式檢查。"""

    @pytest.mark.parametrize("value", ["0000", "1234", "9999"])
    def test_valid_four_digits(self, value):
        assert _is_valid_permit_format(value) is True

    @pytest.mark.parametrize("value", ["", "123", "12345", "ABCD", "12.4", "12 3"])
    def test_invalid(self, value):
        assert _is_valid_permit_format(value) is False


# ═══════════════════════════════════════════════════════════════════════════
# 工具函數:load_known_permits_from_log
# ═══════════════════════════════════════════════════════════════════════════

class TestLoadKnownPermits:
    """從 upload_log.csv 累積已知 permit 集合。"""

    def test_missing_file_returns_empty_set(self, tmp_path):
        """檔案不存在 → 空集合(不拋例外)"""
        result = load_known_permits_from_log(tmp_path / "nope.csv")
        assert result == set()

    def test_empty_file_returns_empty_set(self, tmp_path):
        """檔案只有 header 沒有資料列 → 空集合"""
        log_path = tmp_path / "upload_log.csv"
        with open(log_path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["timestamp", "source_docx", "final_value", "status", "reason"])
        assert load_known_permits_from_log(log_path) == set()

    def test_loads_unique_final_values(self, tmp_path):
        """從 final_value 欄載入,自動去重"""
        log_path = tmp_path / "upload_log.csv"
        with open(log_path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["timestamp", "source_docx", "final_value", "status", "reason"])
            w.writerow(["2024-01-01 10:00:00", "a.docx", "1234", "keyed_in", ""])
            w.writerow(["2024-01-01 10:01:00", "b.docx", "5678", "vision", ""])
            w.writerow(["2024-01-01 10:02:00", "c.docx", "1234", "keyed_in", ""])  # dup
            w.writerow(["2024-01-01 10:03:00", "d.docx", "", "manual_review", ""])  # 空值跳過
        result = load_known_permits_from_log(log_path)
        assert result == {"1234", "5678"}

    def test_malformed_file_returns_empty_set_not_raise(self, tmp_path):
        """讀檔失敗時回傳空集合,不影響 pipeline 運作"""
        log_path = tmp_path / "broken.csv"
        log_path.write_bytes(b"\x00\x01\x02not a csv")
        # 不應拋例外(目前實作會嘗試讀並 silently 失敗)
        result = load_known_permits_from_log(log_path)
        assert isinstance(result, set)
