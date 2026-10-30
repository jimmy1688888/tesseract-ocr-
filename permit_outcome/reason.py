"""Sheets D 欄理由的組字(#72,候選 AB):各階段交自己那段話,這裡只負責接。

每段話仍由原主人寫(門檻、信心這些只有它知道):送審決策的理由、Vision 驗證的說明、
決策梯的反查管道、救援沒做成的事實。標籤、分隔與接的順序只在這裡 —— 改一個不必猜
另外幾個。措辭逐字照舊(test_permit_outcome 釘住);要改措辭是行為變更。
"""
from __future__ import annotations

from vision_verification import VisionCheck

VISION_REASON_LABEL = "[送Vision原因]"   # 送審決策為什麼送 Vision
VISION_RESULT_LABEL = "[Vision結果]"     # Vision 交叉比對的說明(沒驗成時不加,只講事實)
STAGE_SEPARATOR = " ／ "                 # 送 Vision 的原因 與 Vision 那一段之間
APPEND_SEPARATOR = "；"                  # 反查救援接在種子列理由之後


def vision_reason(check: VisionCheck) -> str:
    """送 Vision 的那一份:送審決策的理由,接 Vision 那一段。"""
    finding = (f"{VISION_RESULT_LABEL} {check.finding}" if check.verified
               else check.finding)
    return f"{VISION_REASON_LABEL} {check.decision.reason}{STAGE_SEPARATOR}{finding}"


def appended(seed_reason: str, fragment: str) -> str:
    """反查救援的一段(救回的管道、要人工擇一的各家、沒做成的事實)接在種子理由後。"""
    return f"{seed_reason}{APPEND_SEPARATOR}{fragment}"
