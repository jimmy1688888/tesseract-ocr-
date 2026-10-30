# -*- coding: utf-8 -*-
"""一份 docx 的圖 → 它在 matches.csv 的列(CONTEXT.md「small docx / large docx」,#57)。"""
from __future__ import annotations

import logging
from typing import NamedTuple

import docx_images
from matches_csv import DocxClass, ResultStatus

from .image import Reading, _empty_result
from .tiers import ReadImage, TierOutcome, read_large_docx

logger = logging.getLogger(__name__)

SMALL_DOCX_THRESHOLD = 3   # 圖片數 ≤ 此值 → small

# 全無命中時存哪幾張原圖(imageN 的 N)給人比對:large 是契約頁與兩張可能的許可證頁,
# small 是契約頁與次頁。
NO_HIT_REVIEW_LARGE = frozenset({2, 5, 6})
NO_HIT_REVIEW_SMALL = frozenset({2, 3})


def classify_by_count(image_count: int) -> DocxClass:
    """依圖片數量(原始張數)分 small / large。"""
    return DocxClass.SMALL if image_count <= SMALL_DOCX_THRESHOLD else DocxClass.LARGE


def unscannable(images: list[tuple[str, bytes]] | None) -> tuple[ResultStatus, str] | None:
    """docx 讀圖的結果沒東西可掃 → (狀態, 給人看的一句);有圖回傳 None。

    images 為 None 表示 docx 打不開(讀圖那端不拋,一份壞檔不能讓整批中斷,#47)。
    """
    if images is None:
        return ResultStatus.UNREADABLE, "docx 無法開啟"
    if not images:
        return ResultStatus.NO_IMAGES, "docx 沒有可讀的內嵌圖"
    return None


class DocxScan(NamedTuple):
    """一份 docx 的許可證掃描:它在 matches.csv 的列,與要存給人比對的原圖。

    只有三種形狀,用對應的建構式造:unreadable(讀不到圖)、small、large(附分層結果)。
    small 不收分層結果 —— 「small 配擴掃」造不出來。
    """
    rows: list[Reading]               # 依寫出順序;至少一列。裁切圖還沒寫出
    review_images: list[tuple[str, bytes]]   # 全無命中時要存給人比對的原圖;其餘為空
    docx_class: DocxClass | None      # 讀不到圖為 None
    tier: TierOutcome | None          # large 的分層結果;其餘為 None
    scanned: int                      # 實際掃了幾張圖

    @classmethod
    def unreadable(cls, placeholder: Reading) -> DocxScan:
        return cls([placeholder], [], None, None, 0)

    @classmethod
    def small(cls, rows: list[Reading], review_images: list[tuple[str, bytes]],
              scanned: int) -> DocxScan:
        return cls(rows, review_images, DocxClass.SMALL, None, scanned)

    @classmethod
    def large(cls, rows: list[Reading], review_images: list[tuple[str, bytes]],
              tier: TierOutcome, scanned: int) -> DocxScan:
        return cls(rows, review_images, DocxClass.LARGE, tier, scanned)


def _review_images(images: list[tuple[str, bytes]], wanted: frozenset[int]
                   ) -> list[tuple[str, bytes]]:
    """要存給人比對的原圖:圖序在 wanted 裡的(檔名沒有數字者略過)。"""
    return [(name, data) for name, data in images
            if docx_images.image_index(name) in wanted]


def scan_docx(docx_name: str, images: list[tuple[str, bytes]] | None,
              read_large: ReadImage, read_small: ReadImage, *,
              full_scan: bool = False, image_filter: str = "") -> DocxScan:
    """一份 docx 的圖 → 它在 matches.csv 的列。寫列的規則只在這裡,不碰硬碟。

    read_large / read_small:讀一張圖(docx 名、--roi、Ocr 由呼叫端綁好)。
    images 是 docx 內全部的圖(None = 打不開);image_filter(--image)只掃那一張,
    對不到是呼叫端該先擋下的錯,這裡拋 ValueError。

    每份 docx 至少一列 —— 沒有列,它在 matches.csv 與 Sheets 上都不存在(#47):
      讀不到圖     一列佔位列(沒有 small/large、標讀不到圖的狀態)
      large 有命中 每張命中圖一列;全無命中一列佔位列(擴掃過標第二層),存 image2/5/6
      small 有命中 只寫命中列;全無命中每列都寫、標 small 全無命中,存 image2/3
    """
    nothing = unscannable(images)
    if nothing:
        placeholder = _empty_result(docx_name, "", None)
        placeholder.status, placeholder.note = nothing
        logger.warning(f"  ⚠ {docx_name} {placeholder.note}")
        return DocxScan.unreadable(Reading(placeholder, []))

    # docx_class 一律用「原始張數」判定，必須在任何過濾之前算：把過濾後
    # 的張數丟進 classify_by_count，5 張圖的 docx 剩 3 張就會被判成
    # small，整份改走 mol-only 的另一條路（ADR-0005）。
    docx_class = classify_by_count(len(images))
    logger.info(f"  {docx_name}: {len(images)} 張 → {docx_class}")

    if image_filter:
        names = [n for n, _ in images]
        images = [(n, b) for n, b in images if n == image_filter]
        if not images:
            raise ValueError(f"找不到圖檔 {image_filter!r}({docx_name} 內含:{names})")

    if docx_class is DocxClass.SMALL:
        return _small_rows(docx_name, images, read_small)
    return _large_rows(docx_name, images, read_large, full_scan)


def _small_rows(docx_name: str, images: list[tuple[str, bytes]],
                read_small: ReadImage) -> DocxScan:
    # small 不分層：最多 3 張圖、只掃一個佔頁面 7% 的 mol ROI，最壞
    # 也才 2.3 分鐘；而 1~2 張圖的 docx 與 TIER1_IMAGES 可能毫無交集，
    # 過濾會把它掃成零張、產出一筆與真的讀不到無從分辨的「全無命中」。
    readings = [read_small(name, data) for name, data in images]
    # 有命中只寫命中列;整份全無命中則每列都寫、標 small 全無命中。
    # winner 與門檻在送審決策(ADR-0013)。
    hits = [r for r in readings if r.result.hit]
    if hits:
        rows, review = hits, []
    else:
        for r in readings:
            r.result.status, r.result.note = ResultStatus.SMALL_NO_HIT, "small:無命中"
        rows, review = readings, _review_images(images, NO_HIT_REVIEW_SMALL)
    for r in rows:
        logger.info(
            f"★ {docx_name} / {r.result.image_name}"
            f"  [small|{r.result.hit_roi or ''}]"
            f"  mol={r.result.mol!r}({r.result.mol_conf})"
        )
    return DocxScan.small(rows, review, len(images))


def _large_rows(docx_name: str, images: list[tuple[str, bytes]],
                read_large: ReadImage, full_scan: bool) -> DocxScan:
    scan = read_large_docx(docx_name, images, read_large, full_scan=full_scan)
    if scan.readings:
        return DocxScan.large(scan.readings, [], scan.tier, scan.scanned)

    # 全無命中：寫一列佔位列
    placeholder = _empty_result(docx_name, images[0][0], DocxClass.LARGE)
    placeholder.note   = "large:全無命中"
    placeholder.status = ResultStatus.LARGE_NO_HIT
    # 全無命中的份不會有任何命中列,這張佔位列是它在 CSV 裡的唯一
    # 代表。擴掃過就得標 2 —— 否則「大量擴掃且仍沒命中」(規則過期
    # 最典型的表現)在 scan_tier 欄上完全看不出來。
    placeholder.scan_tier = scan.tier.scan_tier
    return DocxScan.large([Reading(placeholder, [])], _review_images(images, NO_HIT_REVIEW_LARGE),
                          scan.tier, scan.scanned)
