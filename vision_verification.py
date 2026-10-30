# -*- coding: utf-8 -*-
"""Vision 驗證:一個「送 Vision」的送審決策 → 一個驗證結果,絕不回空(#41)。

驗證結果只交事實(B 欄的值、要不要自動寫入、Vision 那一段說明);D 欄怎麼組
(標籤、分隔、順序)在 permit_outcome(#72)。

Vision(VisionReader,截圖 bytes → 全文)放在 seam 後,由呼叫端傳入;取號與 Tesseract 共用
permit_patterns,依截圖所屬的 ROI 選 pattern。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from permit_patterns import permit_in_text
from submission_decision import Decision
from vision_reader import VisionCallFailed, VisionReader

logger = logging.getLogger(__name__)

__all__ = ["VerificationLevel", "VerifiedResult", "VisionCheck", "run_vision_items",
           "verify_vision_result"]


@dataclass(frozen=True)
class VisionCheck:
    """一個送 Vision 的決定驗完的結果。

    value   :B 欄的值(差 1 字元時為空、Vision 沒取到號時留 Tesseract 候選值)。
    keyin   :雙引擎一致才 True(自動寫入);其餘人工審查。
    finding :Vision 那一段說明 —— verified 時是交叉比對的理由,否則是沒驗成的事實。
    verified:有送出且拿到 Vision 的回應(交叉比對過)。
    """
    decision: Decision
    value: str
    keyin: bool
    finding: str
    verified: bool


def run_vision_items(decisions: list[Decision], vision: VisionReader) -> list[VisionCheck]:
    """送 Vision 那一步:每一個送 Vision 的決定都有一個驗證結果(同序),絕不丟掉。

    vision 讀截圖上的零星字(read_sparse;正式流程是 GoogleVisionReader,測試換假的)。
    取號依截圖所屬的 ROI(d.hit_roi),與 Tesseract 同一個函式。
    雙引擎完全一致才自動寫入;其餘進人工審查(交叉比對見 verify_vision_result)。
    """
    return [_verify(d, vision) for d in decisions]


def _verify(d: Decision, vision: VisionReader) -> VisionCheck:
    """一個送 Vision 的決定 → 它的驗證結果。"""
    # 截圖不在:舊做法 continue,整份 docx 從 Sheets 消失(#39)。改進人工審查,
    # 比照 Vision 沒讀到值:B 欄留 Tesseract 候選值、理由只講事實。
    if not d.img_path or not Path(d.img_path).exists():
        logger.warning(f"  ⚠ 找不到圖檔：{d.img_path}（{d.source_docx}）→ 人工審查")
        return _unverified(d, "截圖檔不在,未送 Vision")
    try:
        text = vision.read_sparse(Path(d.img_path).read_bytes())
    except VisionCallFailed as e:
        # 沒問到 ≠ 沒取到號(ADR-0015):只影響這一份,其他份照常驗。
        logger.warning(f"  ⚠ {d.source_docx} {e} → 人工審查")
        return _unverified(d, "Vision 呼叫失敗,未驗證")
    vision_value = permit_in_text(text, d.hit_roi)
    verified = verify_vision_result(
        vision_value=vision_value,
        tesseract_candidate=d.candidate_value,
        tesseract_conf=d.candidate_conf,
    )
    logger.info(
        f"  Vision → {d.source_docx} / {d.image_name}"
        f"  candidate={d.candidate_value!r}(conf={d.candidate_conf:.1f})"
        f"  vision={vision_value!r}"
        f"  → {verified.level.value}(keyin={verified.should_keyin})"
    )
    return VisionCheck(d, verified.final_value, verified.should_keyin, verified.rationale,
                       verified=True)


def _unverified(d: Decision, fact: str) -> VisionCheck:
    """沒驗成的那一份:人工審查,B 欄留 Tesseract 候選值,說明只講事實。"""
    return VisionCheck(d, d.candidate_value, False, fact, verified=False)


# ═══════════════════════════════════════════════════════════════════════════
# ⑧b Vision 結果交叉比對
# ═══════════════════════════════════════════════════════════════════════════
# 目的:Vision 自己讀到一個值不代表就對。本層用兩層檢查:
#   1. Vision 有沒有取到號 — 沒有就人工審查
#   2. 雙引擎交叉 — Vision 值與 Tesseract 原本讀到的值是否一致
# 取號(permit_patterns.permit_in_text)只會回空或「4 位數、可帶 -x」的值,所以這裡
# 不再另驗格式(#59:「有值但格式不符」那條路走不到)。
#
# 設計原則:保守。**只有當 Tesseract 與 Vision 完全一致時才自動 key-in**,
# 其餘所有情境一律標記人工審查。

class VerificationLevel(str, Enum):
    CONFIRMED            = "confirmed"             # 雙引擎完全一致
    LIKELY_OCR_CONFUSION = "likely_ocr_confusion"  # 差 1 字元(視覺相近字常見)
    VISION_ONLY          = "vision_only"           # Tesseract 沒讀到,僅 Vision 有值
    DISAGREEMENT         = "disagreement"          # 兩引擎差異 2 字元以上
    VISION_NO_HIT        = "vision_no_hit"         # Vision 沒取到號(無正則命中)


@dataclass
class VerifiedResult:
    """交叉比對後的決定。

    should_keyin:是否可自動寫入 Sheets;False 一律進人工審查批次。
    rationale  :給人看的決策理由(會寫到 Sheets 的 reason 欄)。
    """
    final_value: str
    level: VerificationLevel
    should_keyin: bool
    rationale: str


def _edit_distance(a: str, b: str) -> int:
    """計算 a 與 b 的 Levenshtein 編輯距離。

    用於辨識「視覺相近字」混淆:Vision 跟 Tesseract 對同一張圖讀到的值若僅差 1 字,
    高機率是 0/O、1/l、5/S 等 OCR 常見混淆,而非完全不同的兩個 permit。
    """
    if len(a) < len(b):
        a, b = b, a
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a):
        curr = [i + 1]
        for j, cb in enumerate(b):
            curr.append(min(
                prev[j + 1] + 1,         # 刪除 a[i]
                curr[j] + 1,             # 插入 b[j]
                prev[j] + (ca != cb),    # 替換(相同時 +0)
            ))
        prev = curr
    return prev[-1]


def verify_vision_result(
    vision_value: str,
    tesseract_candidate: str,
    *,
    tesseract_conf: float = 0.0,
) -> VerifiedResult:
    """對 Vision 的結果做交叉比對(有沒有取到號 → 雙引擎)。

    決策表(保守版,僅 CONFIRMED 自動 key-in):
      Level                   | Action                | final_value 來源
      ─────────────────────── | ───────────────────── | ─────────────────
      CONFIRMED               | should_keyin=True     | vision_value(等於 tesseract)
      LIKELY_OCR_CONFUSION    | should_keyin=False    | 空(不自動填,由人工輸入)
      VISION_ONLY             | should_keyin=False    | vision_value
      DISAGREEMENT            | should_keyin=False    | vision_value
      VISION_NO_HIT           | should_keyin=False    | tesseract_candidate(若有)或空

    LIKELY_OCR_CONFUSION(差 1 字元):不自動挑值,final_value 為空;兩個候選與各自
      的信心寫進 rationale 供人工參考。

    參數:
      vision_value         : Vision OCR 萃取的值(permit_in_text:空,或 4 位數可帶 -x)。
                             空字串代表 Vision 沒有正則命中。
      tesseract_candidate  : 該圖在 Tesseract 階段讀到的最佳候選(可為空字串)。
      tesseract_conf       : 該 tesseract_candidate 對應的 Tesseract 信心(0-100);
                             只寫進 LIKELY_OCR_CONFUSION 的 rationale,不影響取值。

    回傳:VerifiedResult,呼叫端據此決定該列進 key-in 批次或人工審查批次。
    """
    # ── 第一層:Vision 沒取到號 ─────────────────────────────────────────
    if not vision_value:
        return VerifiedResult(
            final_value=tesseract_candidate,
            level=VerificationLevel.VISION_NO_HIT,
            should_keyin=False,
            rationale="Vision 無正則命中,需人工審查",
        )

    # ── 第二層:跟 Tesseract 比對 ────────────────────────────────────
    # VISION_ONLY:Tesseract 沒讀到值,只有 Vision 給出候選
    if not tesseract_candidate:
        return VerifiedResult(
            final_value=vision_value,
            level=VerificationLevel.VISION_ONLY,
            should_keyin=False,
            rationale=f"僅 Vision 讀到 {vision_value!r},需人工審查",
        )

    # 兩引擎完全一致 → 唯一自動 key-in 的路徑
    if vision_value == tesseract_candidate:
        return VerifiedResult(
            final_value=vision_value,
            level=VerificationLevel.CONFIRMED,
            should_keyin=True,
            rationale=f"Tesseract 與 Vision 雙引擎一致:{vision_value!r}",
        )

    distance = _edit_distance(vision_value, tesseract_candidate)

    # 差 1 字元 → 高機率 OCR 視覺相近字混淆(如 0/O、1/l、6/8)。
    # 兩引擎僅差 1 字時無法可靠判斷誰對(Tesseract 高信心也可能讀錯,
    # Vision 的「修正」也可能是錯誤的修正),因此不自動填入任何值,
    # 一律標人工審查、由人工輸入;rationale 列出兩候選與 Tesseract 信心供參考。
    if distance == 1:
        rationale = (
            f"Tesseract={tesseract_candidate!r}(conf={tesseract_conf:.1f}) vs "
            f"Vision={vision_value!r} 差 1 字元(疑似 OCR 視覺相近字混淆);"
            f"不自動填值,需人工輸入"
        )
        return VerifiedResult(
            final_value="",
            level=VerificationLevel.LIKELY_OCR_CONFUSION,
            should_keyin=False,
            rationale=rationale,
        )

    # 差 2 字元以上 → 兩引擎不一致,最可疑
    return VerifiedResult(
        final_value=vision_value,
        level=VerificationLevel.DISAGREEMENT,
        should_keyin=False,
        rationale=(
            f"Tesseract={tesseract_candidate!r} 與 Vision={vision_value!r} "
            f"差異 {distance} 字元,兩引擎不一致,需人工確認"
        ),
    )
