# -*- coding: utf-8 -*-
"""分層掃描(large 專用,CONTEXT.md「分層掃描」、ADR-0005):先掃第一層,拿不到就擴掃。"""
from __future__ import annotations

from enum import Enum
import logging
from typing import Callable, NamedTuple

import docx_images
from matches_csv import ScanResult
# 停止擴掃與規則 A2 用同一個多數票謂詞(ADR-0005);送審門檻不在這裡用
from submission_decision import is_permit_vote

from .image import Reading

logger = logging.getLogger(__name__)

# ─── 分層掃描（large 專用，見 ADR-0005）────────────────────────────────────
# 許可證印在「切結書」的最後一頁(簽署方欄 VI. Pihak yang menandatangani),而
# docx 內的圖序前段是固定的:
#     image1 雜訊/空白 │ image2 契約頁 │ image3 次頁 │ image4~ 切結書 │ 尾端空白圖
# 切結書長度 2~3 頁不定,故許可證落在 image5 或 image6;mol ROI 則只有契約頁
# (image2)讀得到。21 份實測:19/19 許可證命中都在 {5,6},mol 命中全在 {2}。
#
# 用絕對序號而非「倒數第 N 張」:尾端掛著數量不定的空白圖(32510 的 image6、
# 32509 的 image7 都是空的),倒數會數到空白;前段的內容順序才是穩定的。
#
# 猜錯不會錯值,只會慢 —— 第一層拿不到 permit 多數票就把其餘圖全掃完,回到與
# 全掃相同的輸入。代價是規則會安靜地過期,故 CSV 留 scan_tier 欄供觀測。
TIER1_IMAGES = {2, 5, 6}


def split_tiers(images: list[tuple[str, bytes]]
                ) -> tuple[list[tuple[str, bytes]], list[tuple[str, bytes]]]:
    """把圖分成 (第一層, 其餘),兩者聯集恆等於輸入,順序不變。

    命名不是 imageN 的圖一律歸第二層 —— 第一層是一組「賭它在這裡」的位置,
    賭不到就擴掃,把身分不明的檔名放進第一層只會白花時間。
    """
    tier1  = [x for x in images if docx_images.image_index(x[0]) in TIER1_IMAGES]
    rest   = [x for x in images if docx_images.image_index(x[0]) not in TIER1_IMAGES]
    return tier1, rest


def has_permit_vote(rows: list[ScanResult]) -> bool:
    """這批結果裡有沒有「可信的 permit 多數票」——即不必再擴掃的唯一理由。

    刻意排除兩種「看起來有值」的情況,它們都不足以停止擴掃:

      mol 命中          mol 只證明這是契約頁,不是許可證的讀值。32523/32529 的
                        mol 信心是 37 與 28,拿這種值擋掉擴掃等於用雜訊做決策。
      PERMIT_PARTIAL    幾個設定各讀出不同的值、湊不出多數。r.id 雖有值,但
                        送審決策不會把它當成可信讀值 —— 而真正的多數票
                        可能就在還沒掃的那幾張圖上(見 ADR-0005)。
    """
    return any(is_permit_vote(r) for r in rows)


# 讀一張圖:(圖檔名, image bytes) → 判讀;全無命中為 None。docx 名、--roi、Ocr 由呼叫端綁好。
ReadImage = Callable[[str, bytes], "Reading | None"]


class TierOutcome(Enum):
    """一份 large docx 的分層掃描怎麼結束的。"""
    第一層命中 = "第一層命中"   # 第一層就拿到 permit 多數票,沒有擴掃
    無可擴     = "無可擴"       # 第一層沒拿到,但沒有其餘的圖可擴(含 --full-scan)
    擴掃       = "擴掃"         # 第一層沒拿到、擴掃了其餘的圖

    @property
    def scan_tier(self) -> int:
        """matches.csv scan_tier 欄的值:擴掃過為 2,其餘 1。"""
        return 2 if self is TierOutcome.擴掃 else 1


class LargeDocxReading(NamedTuple):
    """一份 large docx 的分層掃描結果。"""
    readings: list[Reading]   # 有命中的圖,依掃描順序;標好 scan_tier
    tier: TierOutcome
    scanned: int              # 實際掃了幾張


def _read_tier(docx_name: str, images: list[tuple[str, bytes]], read_image: ReadImage,
               tier: int) -> list[Reading]:
    """掃一層的圖，回傳有命中的判讀（標上 scan_tier）。"""
    out: list[Reading] = []
    for img_name, img_bytes in images:
        reading = read_image(img_name, img_bytes)
        if not reading:
            logger.debug(f"  {docx_name} / {img_name}  未命中")
            continue
        result = reading.result
        result.scan_tier = tier
        out.append(reading)
        logger.info(
            f"★ {docx_name}/{img_name}"
            f"  [large|T{tier}|{result.hit_roi}]"
            f"  mol={result.mol!r}({result.mol_conf}) id={result.id!r}({result.id_conf})"
        )
    return out


def read_large_docx(docx_name: str, images: list[tuple[str, bytes]],
                    read_image: ReadImage, full_scan: bool = False) -> LargeDocxReading:
    """先掃第一層;拿不到可信的 permit 多數票就把其餘的圖掃完(擴掃決策,ADR-0005)。

    猜錯不會錯值,只會慢:擴掃後的輸入與全掃相同。full_scan 關掉分層,一次掃完。
    """
    tier1, rest = (images, []) if full_scan else split_tiers(images)
    readings = _read_tier(docx_name, tier1, read_image, 1)
    if has_permit_vote([r.result for r in readings]):
        return LargeDocxReading(readings, TierOutcome.第一層命中, len(tier1))
    if not rest:
        return LargeDocxReading(readings, TierOutcome.無可擴, len(tier1))
    logger.info(
        f"  ↻ {docx_name} 第一層({len(tier1)}張)無 permit 多數票"
        f" → 擴掃其餘 {len(rest)} 張"
    )
    readings += _read_tier(docx_name, rest, read_image, 2)
    return LargeDocxReading(readings, TierOutcome.擴掃, len(tier1) + len(rest))
