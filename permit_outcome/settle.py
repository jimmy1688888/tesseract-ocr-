"""一批送審決策 → 許可證判讀結果(CONTEXT.md):Sheets A~D,一份 docx 恰好一列。

每個決定依去向走完(直接 key-in／送 Vision 驗證／反查救援／直接人工審查),結果依寫入
順序排好交出;E~G(查名冊)與 H~O(雇主)不在這裡,在組十五欄那一步補。

status 只有兩種,理由欄(D)負責歸因:
  KEYED_IN      = 直接 key-in(Tesseract 高信心)+ Vision 確認(雙引擎一致)
  MANUAL_REVIEW = Vision 未過(各種不一致、沒驗成)+ 反查救援 + 直接人工審查(讀不到圖)
讓人只依 status 篩就知道哪些要看,看 D 欄就知道為什麼。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from batch import Batch
from permit_lookup import PermitLookup
from sheet_row import SheetRow, SheetStatus
from submission_decision import Decision, Destination
from vision_reader import VisionReader
from vision_verification import VisionCheck, run_vision_items

from .reason import vision_reason
from .rescue import rescue_from_next_page

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PermitOutcomes:
    """一批的許可證判讀結果,依從哪個去向定下來分組;rows 是寫入順序。"""
    keyin: list[SheetRow]             # 直接 key-in
    vision_confirmed: list[SheetRow]  # 送 Vision、雙引擎一致
    vision_review: list[SheetRow]     # 送 Vision、未過或沒驗成
    rescue: list[SheetRow]            # 反查救援(救回、要人工擇一、救不回)
    direct_review: list[SheetRow]     # 讀不到圖,直接人工審查

    @property
    def keyed_in(self) -> list[SheetRow]:
        return self.keyin + self.vision_confirmed

    @property
    def review(self) -> list[SheetRow]:
        return self.vision_review + self.rescue + self.direct_review

    @property
    def rows(self) -> list[SheetRow]:
        """寫進 Sheets 的順序:自動寫入組在前、人工審查組在後。"""
        return self.keyed_in + self.review

    def summary(self) -> str:
        return (f"auto_keyin={len(self.keyed_in)} 筆"
                f"(其中 Tesseract 直接 {len(self.keyin)} 筆 + Vision 確認 {len(self.vision_confirmed)} 筆) "
                f"/ manual_review={len(self.review)} 筆"
                f"(其中 Vision 交叉比對失敗 {len(self.vision_review)} 筆 "
                f"+ 反查救援 {len(self.rescue)} 件 "
                f"+ 讀不到圖 {len(self.direct_review)} 件)")


def settle(decisions: list[Decision], batch: Batch, vision: VisionReader,
           roster: PermitLookup | None) -> PermitOutcomes:
    """一批送審決策(一份 docx 一個)→ 它們的許可證判讀結果。

    vision 讀截圖(Vision 驗證)與次頁(反查救援);roster 是反查救援要查的名冊,
    降級為 None 時救援跳過、種子列原樣。身分或權限被拒往上拋(整批中止)。
    """
    by_destination = _by_destination(decisions)
    _log_destinations(by_destination)

    # 雙引擎(Tesseract + Vision)完全一致才自動 key-in;其餘人工審查,避免任何單一
    # OCR 引擎的判讀直接決定資料寫入。差 1 字元也不自動填值(見 verify_vision_result)。
    logger.info("── 步驟 4a：Google Vision 判讀 + 交叉比對 ──")
    checks = run_vision_items(by_destination[Destination.送VISION], vision)

    seeds = [SheetRow(d.source_docx, d.candidate_value, SheetStatus.MANUAL_REVIEW, d.reason)
             for d in by_destination[Destination.反查救援]]
    if seeds:
        logger.info("── 步驟 4b：反查救援 → 次頁台仲區塊反查名冊 ──")
    rescued = rescue_from_next_page(batch, seeds, roster, vision)

    return PermitOutcomes(
        keyin=[SheetRow(d.source_docx, d.candidate_value, SheetStatus.KEYED_IN, d.reason)
               for d in by_destination[Destination.直接KEYIN]],
        vision_confirmed=[_vision_row(c) for c in checks if c.keyin],
        vision_review=[_vision_row(c) for c in checks if not c.keyin],
        rescue=rescued,
        direct_review=[SheetRow(d.source_docx, "", SheetStatus.MANUAL_REVIEW, d.reason)
                       for d in by_destination[Destination.直接人工審查]],
    )


def _vision_row(check: VisionCheck) -> SheetRow:
    status = SheetStatus.KEYED_IN if check.keyin else SheetStatus.MANUAL_REVIEW
    return SheetRow(check.decision.source_docx, check.value, status, vision_reason(check))


def _by_destination(decisions: list[Decision]) -> dict[Destination, list[Decision]]:
    grouped: dict[Destination, list[Decision]] = {d: [] for d in Destination}
    for d in decisions:
        grouped[d.destination].append(d)
    return grouped


def _log_destinations(by_destination: dict[Destination, list[Decision]]) -> None:
    logger.info(f"  直接 key-in：{len(by_destination[Destination.直接KEYIN])} 筆")
    logger.info(f"  送 Vision  ：{len(by_destination[Destination.送VISION])} 筆")
    logger.info(f"  反查救援   ：{len(by_destination[Destination.反查救援])} 件")
    logger.info(f"  直接人工審查(讀不到圖):{len(by_destination[Destination.直接人工審查])} 件")
