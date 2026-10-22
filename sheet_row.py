# -*- coding: utf-8 -*-
"""寫進 Google Sheets 的一列:一份 docx 的許可證判讀結果(#12)。

一份 docx 在 Sheets 上只有一列(CONTEXT.md「許可證」)。結果在哪一步確定,就在那一步
建好這一列:直接 key-in 與直接人工審查在送審決策、送 Vision 的在 Vision 驗證、反查救援
的在救援之後。A~D 四欄全在這裡;E~G(查名冊)與 H~O(雇主擷取)由 pipeline 組列時
依 B 欄與檔名補上 —— 十五欄的對應只在 pipeline._row_to_sheet_values 一處。

leaf module:只依賴標準函式庫,vision_verification 與 pipeline 都直接 import。
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

__all__ = ["SheetRow", "SheetStatus"]


class SheetStatus(str, Enum):
    """寫入 Google Sheets 時的 status 欄值。"""
    KEYED_IN      = "keyed-in"
    MANUAL_REVIEW = "manual_review"


@dataclass(frozen=True)
class SheetRow:
    """Sheets 的 A~D:檔名、許可證、status、理由。

    permit 可以是空的(全無命中救不回、Vision 差 1 字元不自動填值、讀不到圖)。
    值改了就建一列新的(dataclasses.replace),不就地改寫。
    """
    source_docx: str
    permit: str
    status: SheetStatus
    reason: str
