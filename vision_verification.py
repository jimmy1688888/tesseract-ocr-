# -*- coding: utf-8 -*-
"""Vision 驗證:一個「送 Vision」的送審決策 → 一列 Sheets 結果,絕不回空(#41)。

Vision client(截圖 → 全文)放在 seam 後,由呼叫端傳入;取號與 Tesseract 共用
permit_patterns,依截圖所屬的 ROI 選 pattern。這裡不 import pipeline(循環匯入),
VisionQueueItem 只讀屬性。
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from permit_patterns import permit_in_text
from submission_decision import CONF_NOVOTE_RESCUE_MAX

if TYPE_CHECKING:
    from pipeline import VisionQueueItem

logger = logging.getLogger(__name__)

__all__ = ["VerificationLevel", "VerifiedResult", "run_vision_items",
           "verify_vision_result"]


def run_vision_items(items: list[VisionQueueItem], known_permits: set[str],
                     read_text: Callable[[str], str]
                     ) -> tuple[list[dict], list[dict], list[dict]]:
    """送 Vision 那一步:每一筆都分到 (自動寫入, 人工審查, 反查救援) 其中**一處**,絕不丟掉。

    read_text 是 Vision client:「截圖路徑 → 全文」(正式流程是 pipeline.vision_text,
    測試換假的)。取號依截圖所屬的 ROI(item.hit_roi),與 Tesseract 同一個函式。
    雙引擎完全一致才自動寫入;其餘進人工審查(三層交叉比對見 verify_vision_result)。
    """
    auto: list[dict] = []        # 雙引擎一致(CONFIRMED) → 自動寫
    review: list[dict] = []      # 任何不一致 → 人工審查
    rescue: list[dict] = []      # 單一判讀低信心:略過 ROI Vision,改走次頁電話反查
    for item in items:
        # 單一判讀且 conf 過低(ROI 位置品質差)→ 不再掃同一位置,直接改走契約次頁
        # 台仲電話反查救援(比照全無命中);那格 ROI Vision 額度省下來。
        if item.no_vote_rescue:
            rescue.append({
                "source_docx":     item.source_docx,
                "candidate_value": item.candidate_value,   # 種子:電話查無時保留原候選值
                "reason": (
                    f"[單一判讀低信心 conf={item.candidate_conf:.1f}<{CONF_NOVOTE_RESCUE_MAX},"
                    f"略過 ROI Vision 改走次頁電話反查] {item.reason}"
                ),
            })
            continue
        # 截圖不在:舊做法 continue,整份 docx 從 Sheets 消失(#39)。改進人工審查,
        # 比照 Vision 沒讀到值:B 欄留 Tesseract 候選值、理由只講事實。
        if not item.img_path or not Path(item.img_path).exists():
            logger.warning(f"  ⚠ 找不到圖檔：{item.img_path}（{item.source_docx}）→ 人工審查")
            review.append({
                "source_docx":     item.source_docx,
                "candidate_value": item.candidate_value,
                "reason":          f"[送Vision原因] {item.reason} ／ 截圖檔不在,未送 Vision",
            })
            continue
        vision_value = permit_in_text(read_text(item.img_path), item.hit_roi)
        verified = verify_vision_result(
            vision_value=vision_value,
            tesseract_candidate=item.candidate_value,
            known_permits=known_permits,
            tesseract_conf=item.candidate_conf,
        )
        logger.info(
            f"  Vision → {item.source_docx} / {item.image_name}"
            f"  candidate={item.candidate_value!r}(conf={item.candidate_conf:.1f})"
            f"  vision={vision_value!r}"
            f"  → {verified.level.value}(keyin={verified.should_keyin})"
        )
        row = {
            "source_docx":     item.source_docx,
            "candidate_value": verified.final_value,
            "reason":          f"[送Vision原因] {item.reason} ／ [Vision結果] {verified.rationale}",
        }
        (auto if verified.should_keyin else review).append(row)
    return auto, review, rescue


# ═══════════════════════════════════════════════════════════════════════════
# ⑧b Vision 結果交叉比對
# ═══════════════════════════════════════════════════════════════════════════
# 目的:Vision 自己讀到一個值不代表就對。本層用三組獨立證據交叉驗證:
#   1. 格式驗證 — Vision 值必須是 4 位數(基本 sanity check)
#   2. 雙引擎交叉 — Vision 值與 Tesseract 原本讀到的值是否一致
#   3. 已知清單 — 是否在歷史成功 key-in 過的值集合內(來自 upload_log.csv)
#
# 設計原則:保守。**只有當 Tesseract 與 Vision 完全一致時才自動 key-in**,
# 其餘所有情境(包括「Vision 值剛好在已知清單內」)一律標記人工審查。
# 已知清單僅用於補充 rationale,不會單獨作為自動採用的依據。

class VerificationLevel(str, Enum):
    CONFIRMED            = "confirmed"             # 雙引擎完全一致
    LIKELY_OCR_CONFUSION = "likely_ocr_confusion"  # 差 1 字元(視覺相近字常見)
    VISION_ONLY          = "vision_only"           # Tesseract 沒讀到,僅 Vision 有值
    DISAGREEMENT         = "disagreement"          # 兩引擎差異 2 字元以上
    FORMAT_INVALID       = "format_invalid"        # Vision 無值或值不符 4 位數格式


@dataclass
class VerifiedResult:
    """三層交叉比對後的決定。

    should_keyin:是否可自動寫入 Sheets;False 一律進人工審查批次。
    rationale  :給人看的決策理由(會寫到 Sheets 的 reason 欄)。
    """
    final_value: str
    level: VerificationLevel
    should_keyin: bool
    rationale: str
    in_known_list: bool = False


# 4 位數格式檢查:Vision 回傳的值必須是純 4 位數字才接受,或 4 位數+破折號+1 位數(xxxx-x)。
# 這是「最低門檻」:Vision 偶爾會把日期、頁碼、其他不相關文字解析為候選值,
# 此 regex 把這類雜訊濾掉。接受格式:
#   1234     (4 位純數字)
#   1234-5   (4 位數+破折號+1 位數)
_RE_PERMIT_VALUE = re.compile(r"^\d{4}(?:-\d)?$")


def _is_valid_permit_format(value: str) -> bool:
    """Vision 值是否符合 4 位數 permit/mol 格式。"""
    return bool(value and _RE_PERMIT_VALUE.match(value))


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
    known_permits: set[str] | None = None,
    *,
    tesseract_conf: float = 0.0,
) -> VerifiedResult:
    """對 Vision 的結果做三層交叉比對。

    決策表(保守版,僅 CONFIRMED 自動 key-in):
      Level                   | Action                | final_value 來源
      ─────────────────────── | ───────────────────── | ─────────────────
      CONFIRMED               | should_keyin=True     | vision_value(等於 tesseract)
      LIKELY_OCR_CONFUSION    | should_keyin=False    | 空(不自動填,由人工輸入)
      VISION_ONLY             | should_keyin=False    | vision_value
      DISAGREEMENT            | should_keyin=False    | vision_value
      FORMAT_INVALID          | should_keyin=False    | tesseract_candidate(若有)或空

    LIKELY_OCR_CONFUSION(差 1 字元):不自動挑值,final_value 為空;兩個候選與各自
      的信心寫進 rationale 供人工參考。

    參數:
      vision_value         : Vision OCR 萃取的值。空字串代表 Vision 沒有正則命中。
      tesseract_candidate  : 該圖在 Tesseract 階段讀到的最佳候選(可為空字串)。
      known_permits        : 已知合法 permit 集合;僅影響 rationale,不影響 should_keyin。
      tesseract_conf       : 該 tesseract_candidate 對應的 Tesseract 信心(0-100);
                             只寫進 LIKELY_OCR_CONFUSION 的 rationale,不影響取值。

    回傳:VerifiedResult,呼叫端據此決定該列進 key-in 批次或人工審查批次。
    """
    in_known_list = bool(known_permits and vision_value in known_permits)

    # ── 第一層:格式 ──────────────────────────────────────────────────
    if not _is_valid_permit_format(vision_value):
        return VerifiedResult(
            final_value=vision_value or tesseract_candidate,
            level=VerificationLevel.FORMAT_INVALID,
            should_keyin=False,
            rationale=(
                "Vision 無正則命中,需人工審查"
                if not vision_value
                else f"Vision 值 {vision_value!r} 不符合 4 位數格式"
            ),
            in_known_list=False,
        )

    # ── 第二層:跟 Tesseract 比對 ────────────────────────────────────
    # VISION_ONLY:Tesseract 沒讀到值,只有 Vision 給出候選
    if not tesseract_candidate:
        rationale = (
            f"僅 Vision 讀到 {vision_value!r}(值在已知清單內,可優先處理),需人工確認"
            if in_known_list
            else f"僅 Vision 讀到 {vision_value!r},不在已知清單,需人工審查"
        )
        return VerifiedResult(
            final_value=vision_value,
            level=VerificationLevel.VISION_ONLY,
            should_keyin=False,
            rationale=rationale,
            in_known_list=in_known_list,
        )

    # 兩引擎完全一致 → 唯一自動 key-in 的路徑
    if vision_value == tesseract_candidate:
        rationale = f"Tesseract 與 Vision 雙引擎一致:{vision_value!r}"
        if in_known_list:
            rationale += "(且在已知清單內)"
        return VerifiedResult(
            final_value=vision_value,
            level=VerificationLevel.CONFIRMED,
            should_keyin=True,
            rationale=rationale,
            in_known_list=in_known_list,
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
        if in_known_list:
            rationale += ";Vision 候選在已知清單內"
        return VerifiedResult(
            final_value="",
            level=VerificationLevel.LIKELY_OCR_CONFUSION,
            should_keyin=False,
            rationale=rationale,
            in_known_list=in_known_list,
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
        in_known_list=in_known_list,
    )
