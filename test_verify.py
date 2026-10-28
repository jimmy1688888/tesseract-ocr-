# -*- coding: utf-8 -*-
"""verify_vision_result 與輔助函數的行為測試。

這層是 Vision 結果的最後一道閘門:對每個 Vision OCR 回傳值,做兩層檢查
(有沒有取到號 → 雙引擎比對),決定可否自動 key-in。

設計原則:**只有雙引擎(Tesseract + Vision)完全一致才自動 key-in**。
這份測試以可執行規格的形式鎖住這個保守策略。任何放寬(例如「差 1 字元就採 Vision」)
都應該先改測試、看清楚變動範圍,再改 production code。

測試組織:
  TestConfirmed             — 雙引擎一致(唯一自動 key-in 的情境)
  TestLikelyOcrConfusion    — 差 1 字元(視覺相近字混淆,仍需審查)
  TestVisionOnly            — Tesseract 沒讀到、僅 Vision 有值(仍需審查)
  TestDisagreement          — 差 2 字元以上(兩引擎不同意)
  TestFormatInvalid         — Vision 沒取到號(無正則命中)
  TestEditDistance          — _edit_distance 工具函數
"""

import pytest

from permit_patterns import find_permits
from vision_verification import (
    verify_vision_result,
    VerifiedResult,
    VerificationLevel,
    _edit_distance,
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
        )
        assert v.level == VerificationLevel.CONFIRMED
        assert v.should_keyin is True
        assert v.final_value == "1234"
        assert "雙引擎一致" in v.rationale


# ═══════════════════════════════════════════════════════════════════════════
# LikelyOcrConfusion:差 1 字元(疑似視覺相近字)
# ═══════════════════════════════════════════════════════════════════════════

class TestLikelyOcrConfusion:
    """差 1 字元的情境。常見於 0/O、1/l、5/S 等視覺相近字混淆。

    兩引擎僅差 1 字時無法可靠判斷誰對,一律標人工審查。
    """

    def test_zero_vs_O_confusion(self):
        """1234 vs 12O4(0 與 O 混淆) → LIKELY_OCR_CONFUSION,不 keyin"""
        v = verify_vision_result(
            vision_value="1234",
            tesseract_candidate="12O4",  # 假設 Tesseract 把 0 看成 O
        )
        # Tesseract 候選可帶字母;比對只看兩值的差距
        assert v.level == VerificationLevel.LIKELY_OCR_CONFUSION
        assert v.should_keyin is False

    def test_one_char_substitution(self):
        """1234 vs 1334 → 差 1 字元"""
        v = verify_vision_result(
            vision_value="1234",
            tesseract_candidate="1334",
        )
        assert v.level == VerificationLevel.LIKELY_OCR_CONFUSION
        assert v.should_keyin is False
        assert "差 1 字元" in v.rationale

    def test_one_char_diff_at_first_digit(self):
        """9999 vs 8999 → 差在第一位,一樣是差 1 字元"""
        v = verify_vision_result(
            vision_value="9999",
            tesseract_candidate="8999",
        )
        assert v.level == VerificationLevel.LIKELY_OCR_CONFUSION
        assert v.should_keyin is False

    def test_insertion_one_char(self):
        """1234 vs 12345 → 差 1 字(插入)。

        注意:5 位數的 candidate 不會自然由 Tesseract 產生(find_permits 抓 4 位),
        但理論上保留行為一致性 — distance == 1 都歸 LIKELY_OCR_CONFUSION。
        """
        v = verify_vision_result(
            vision_value="1234",
            tesseract_candidate="12345",
        )
        assert v.level == VerificationLevel.LIKELY_OCR_CONFUSION
        assert v.should_keyin is False


# ═══════════════════════════════════════════════════════════════════════════
# LikelyOcrConfusion 子規則:差 1 字元一律不自動填值,交人工輸入
# ═══════════════════════════════════════════════════════════════════════════

class TestConfusionNoAutoPick:
    """LIKELY_OCR_CONFUSION 分支不自動挑值。

    設計變更:過去會依 Tesseract conf 是否 > 50 決定採 Tesseract 或 Vision 值。
    現行策略更保守 — 兩引擎差 1 字元時無法可靠判斷誰對(Tesseract 高信心也可能
    讀錯,Vision 的「修正」也可能是錯的),因此不填入任何值(final_value=""),
    一律標人工審查、由人工輸入。rationale 仍列出兩候選與 Tesseract 信心供參考。
    """

    def test_high_tesseract_conf_no_autofill(self):
        """Tesseract='3166'(conf=70) vs Vision='3186' → 不填值,交人工。"""
        v = verify_vision_result(
            vision_value="3186",
            tesseract_candidate="3166",
            tesseract_conf=70.0,
        )
        assert v.level == VerificationLevel.LIKELY_OCR_CONFUSION
        assert v.should_keyin is False               # 仍需人工
        assert v.final_value == ""                   # 不自動填值
        assert "不自動填值" in v.rationale
        # rationale 列出兩候選與信心供審查者參考
        assert "3166" in v.rationale
        assert "3186" in v.rationale
        assert "conf=70.0" in v.rationale

    def test_low_tesseract_conf_no_autofill(self):
        """Tesseract='3166'(conf=30) vs Vision='3186' → 一樣不填值。"""
        v = verify_vision_result(
            vision_value="3186",
            tesseract_candidate="3166",
            tesseract_conf=30.0,
        )
        assert v.level == VerificationLevel.LIKELY_OCR_CONFUSION
        assert v.should_keyin is False
        assert v.final_value == ""                   # 不自動填值
        assert "conf=30.0" in v.rationale

    def test_conf_does_not_change_final_value(self):
        """不論 Tesseract conf 高低,final_value 恆為空字串。"""
        for conf in [0.0, 30.0, 50.0, 50.1, 70.0, 99.9]:
            v = verify_vision_result(
                vision_value="3186",
                tesseract_candidate="3166",
                tesseract_conf=conf,
            )
            assert v.final_value == "", f"conf={conf} 不該自動填值"

    def test_disagreement_still_uses_vision_value(self):
        """對照組:差 2 字以上是 DISAGREEMENT,行為不變(仍採 Vision 值)。

        Tesseract='3166'(conf=80) vs Vision='9999' → DISAGREEMENT
        DISAGREEMENT 一律採 Vision 值,高 conf 不會推翻這個決定。
        """
        v = verify_vision_result(
            vision_value="9999",
            tesseract_candidate="3166",
            tesseract_conf=80.0,
        )
        assert v.level == VerificationLevel.DISAGREEMENT
        assert v.final_value == "9999"               # DISAGREEMENT 用 Vision

    def test_conf_does_not_promote_to_keyin(self):
        """Tesseract conf 不論多高都不能把 LIKELY_OCR_CONFUSION 升級為自動 key-in。

        鎖住保守策略:兩引擎不一致 = 絕對不自動寫入,不論誰的信心高。
        """
        for conf in [50.1, 70, 90, 99.9]:
            v = verify_vision_result(
                vision_value="3186",
                tesseract_candidate="3166",
                tesseract_conf=conf,
            )
            assert v.should_keyin is False, f"conf={conf} 不該推升為 keyin"


# ═══════════════════════════════════════════════════════════════════════════
# VisionOnly:Tesseract 沒讀到,僅 Vision 有值
# ═══════════════════════════════════════════════════════════════════════════

class TestVisionOnly:
    """只有 Vision 讀到的情境。

    只有單一 OCR 引擎確認,仍標人工審查。雙引擎獨立確認才是 key-in 的門檻。
    """

    def test_vision_only_needs_review(self):
        """Tesseract 沒讀到 + Vision 有值 → 標 review"""
        v = verify_vision_result(
            vision_value="1234",
            tesseract_candidate="",
        )
        assert v.level == VerificationLevel.VISION_ONLY
        assert v.should_keyin is False
        assert v.rationale == "僅 Vision 讀到 '1234',需人工審查"

    def test_vision_only_uses_vision_value_as_final(self):
        """VISION_ONLY 時,final_value 用 Vision 的值(因為 Tesseract 沒讀到)"""
        v = verify_vision_result(
            vision_value="1234",
            tesseract_candidate="",
        )
        assert v.final_value == "1234"


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
        )
        assert v.level == VerificationLevel.DISAGREEMENT
        assert v.should_keyin is False
        assert "差異 2 字元" in v.rationale

    def test_completely_different(self):
        """1234 vs 9999 → 差 4 字"""
        v = verify_vision_result(
            vision_value="1234",
            tesseract_candidate="9999",
        )
        assert v.level == VerificationLevel.DISAGREEMENT
        assert v.should_keyin is False


# ═══════════════════════════════════════════════════════════════════════════
# FormatInvalid:Vision 沒取到號
# ═══════════════════════════════════════════════════════════════════════════

class TestFormatInvalid:
    """第一層:Vision 沒取到號就人工審查。

    取號只會回空或 4 位數(可帶 -x)的值,「有值但格式不符」不會發生(#59 刪掉那條路)。
    """

    def test_empty_vision_value(self):
        """Vision 沒讀到任何東西 → FORMAT_INVALID"""
        v = verify_vision_result(
            vision_value="",
            tesseract_candidate="1234",
        )
        assert v.level == VerificationLevel.FORMAT_INVALID
        assert v.should_keyin is False
        # final_value 退回 Tesseract 候選,方便審查者看
        assert v.final_value == "1234"
        assert "Vision 無正則命中" in v.rationale


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


class TestRegexExtractsDashSuffix:
    """find_permits 從文字抽取時也需支援 xxxx-x 格式。"""

    def test_permit_id_with_dash_suffix_extracted(self):
        """文字含 'No.izin: 3422-1' → 完整抽出 '3422-1'"""
        text = "No.izin: 3422-1"
        id_, _, mol, _ = find_permits(text, find_mol=False, find_id=True)
        assert id_ == "3422-1"

    def test_mol_with_dash_suffix_extracted(self):
        """文字含 MOL 段落帶 -x 後綴 → 完整抽出"""
        text = "Agency's MOL License Number: 5678-2"
        id_, _, mol, _ = find_permits(text, find_mol=True, find_id=False)
        assert mol == "5678-2"

    def test_four_digit_only_still_works_without_dash(self):
        """既有格式 xxxx(不帶 dash)依然可抽出"""
        text = "No.izin: 1234"
        id_, _, mol, _ = find_permits(text, find_mol=False, find_id=True)
        assert id_ == "1234"

    def test_five_digit_does_not_match(self):
        """xxxxx(5 位純數字)不該被誤認為 xxxx-x"""
        # 與舊行為一致:5 位數時 (?!\d) 失敗 → 不抽
        text = "No.izin: 12345"
        id_, _, mol, _ = find_permits(text, find_mol=False, find_id=True)
        assert id_ == ""

    def test_xxxx_dash_xx_falls_back_to_xxxx(self):
        """xxxx-xx(後綴 2 位數)→ regex 透過 backtrack 退回到 xxxx 匹配。

        regex 引擎行為:
          1. 嘗試 (\\d{4}(?:-\\d)?) 貪婪匹配 → 抓到 '1234-1'
          2. (?!\\d) lookahead 檢查:後面是 '2'(digit) → 失敗
          3. backtrack:(?:-\\d)? 改成不匹配 → 抓 '1234'
          4. (?!\\d) lookahead:後面是 '-'(非 digit) → 成功
          5. 結果:'1234'
        這與舊行為(只支援 xxxx)一致 — 等同認定「-12 是 permit 後的雜訊」。
        """
        text = "No.izin: 1234-12"
        id_, _, mol, _ = find_permits(text, find_mol=False, find_id=True)
        assert id_ == "1234"
