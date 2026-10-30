"""反查救援:次頁台仲區塊 → 名冊挑一家 → 種子列換成救回的列(CONTEXT.md「反查救援」)。

挑哪一家(決策梯)在 permit_lookup.pick_agency;這裡管開 docx、讀次頁、把結果接回列。
"""
from __future__ import annotations

import logging
from dataclasses import replace
from pathlib import Path

# 讀次頁走模組屬性(employer_extract.agency_block_from_next_page),不 from-import:
# 測試在外部邊界換掉的是 employer_extract 上的那個名字。
import employer_extract
from batch import Batch
from permit_lookup import PermitLookup, pick_agency
from sheet_row import SheetRow
from vision_reader import IDENTITY_REFUSED, VisionCallFailed, VisionReader

from .reason import appended

logger = logging.getLogger(__name__)

RESCUE_NOT_DONE = "反查救援未完成:次頁 Vision 呼叫失敗"
_NO_BLOCK = {"phones": [], "name": "", "address": ""}


def _next_page_block(docx_path: Path, vision: VisionReader) -> dict | None:
    """一份 docx 的次頁台仲區塊;Vision 呼叫失敗回 None,其他失敗當作沒有區塊。
    身分或權限被拒往上拋(整批中止)。"""
    try:
        return employer_extract.agency_block_from_next_page(str(docx_path), vision)
    except IDENTITY_REFUSED:
        raise
    except VisionCallFailed as e:
        logger.warning(f"  ⚠ {docx_path.name} 次頁 {e}")
        return None
    except Exception as e:
        logger.warning(f"  ⚠ {docx_path.name} 次頁仲介區塊擷取失敗:{e!r}")
        return dict(_NO_BLOCK)


def rescue_from_next_page(batch: Batch, seeds: list[SheetRow], roster: PermitLookup | None,
                          vision: VisionReader) -> list[SheetRow]:
    """整頁 OCR 契約次一頁(1 次 Vision),以『台灣仲介』欄位標籤錨定區塊(台仲在左在右
    皆可,靠標籤非位置),取其「電話／名稱／地址」反查名冊回推許可證。

    列進、列出:回傳與 seeds 同序的列 —— 救回或要人工擇一的換成新列(B 欄換成救回的
    許可證、D 欄接上反查管道),救不回的原列照回;不改傳入的列。

    種子列是送審決策判成反查救援的 docx:B 欄是候選值(單一判讀低信心)或空(全無命中)。
    名冊降級(None)時整段跳過、不花 Vision。要開的 docx 跟 batch 查完整路徑(列只帶檔名)。
    同一 docx 的次頁只送一次 Vision(block_cache),多列共用不重複計費。"""
    if roster is None or not seeds:
        return list(seeds)
    out: list[SheetRow] = []
    rescued = 0
    # source_docx → {phones,name,address};None = 次頁 Vision 呼叫失敗(避免重複送 Vision)
    block_cache: dict[str, dict | None] = {}
    for row in seeds:
        docx_name = row.source_docx
        docx_path = batch.path_of(docx_name)
        if docx_path is None:
            out.append(row)
            continue
        if docx_name not in block_cache:
            block_cache[docx_name] = _next_page_block(docx_path, vision)
        if block_cache[docx_name] is None:
            # 沒救 ≠ 救不回:救不回不註明,沒問到要說(CONTEXT「反查救援」、ADR-0015)
            out.append(replace(row, reason=appended(row.reason, RESCUE_NOT_DONE)))
            continue
        pick = pick_agency(block_cache[docx_name], roster)
        if pick.note:
            # 要人工擇一時沒有許可證:B 欄留種子值(全無命中是空、單一判讀是候選值)
            row = replace(row, permit=pick.permit or row.permit,
                          reason=appended(row.reason, pick.note))
            rescued += bool(pick.permit)
            logger.info(f"  {'↻' if pick.permit else '⚠'} {docx_name} {pick.note}")
        out.append(row)
    logger.info(f"  反查救回 {rescued}/{len(seeds)} 筆(填許可證+機構欄)")
    return out
