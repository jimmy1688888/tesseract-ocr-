# -*- coding: utf-8 -*-
"""matches.csv(許可證掃描結果):一列的形狀、欄位、寫、讀,以及舊檔相容(CONTEXT.md)。

許可證掃描用它寫,送審決策用它讀。只依賴標準函式庫、不 import 專案內其他 module ——
讀一份 matches.csv 不必載入掃描或整條流程。

  ScanResult / ResultStatus   一列:一張圖讀到的值與掃描觀測、命中狀態
  FIELDS                      欄位順序(寫與讀同一份)
  writer(path)                寫一份新的 matches.csv
  read(path)                  讀回成 {docx 檔名: [列]},依檔名排序

只存讀值與掃描觀測,不存判斷(ADR-0013)。
"""
from __future__ import annotations

import csv
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Iterator


class ResultStatus(str, Enum):
    """OCR 掃描結果的狀態。供下游送審決策(submission_decision)做分派判斷。

    用 enum 取代過去用 note 文字比對（例如 "large:全無命中"）的方式：
    note 是給人看的描述，會因措辭調整而變動；status 才是給程式判斷的單一真相來源。
    """
    OK             = "ok"               # 正常命中，走後續決策
    LARGE_NO_HIT   = "large_no_hit"     # large docx 全部圖片都沒命中
    SMALL_NO_HIT   = "small_no_hit"     # small docx 全部圖片都沒命中
    PERMIT_PARTIAL = "permit_partial"   # permit 有部分命中但無多數票
    # docx 本身讀不到圖(#47):沒有可掃的東西,佔位列是它在 matches.csv 的唯一代表
    NO_IMAGES      = "no_images"        # 0 張可讀內嵌圖(沒有 word/media,或只有 .emf 之類)
    UNREADABLE     = "unreadable"       # 打不開(不是有效的 zip,或壓縮資料損壞)


# ─── 欄位順序（同時供寫入與讀取使用，避免兩端走樣） ───────────────────────
FIELDS = [
    "source_docx", "image_name", "docx_class", "scan_tier",
    "mol", "mol_layer", "mol_conf", "mol_from_vote",
    "id",  "id_layer",  "id_conf",  "id_from_vote",
    "cross_match",
    "note", "hit_config", "hit_roi",
    "mol_crop", "permit_crop",
    "status",
]


class UnknownStatus(ValueError):
    """matches.csv 的 status 欄是不認得的值:壞檔或未來的格式,不能當成正常讀值。"""


@dataclass
class ScanResult:
    """一張圖的 OCR 掃描結果。

    這個 dataclass 取代了過去用 dict 表達結果的方式，解掉三類問題：

    1. 型別契約：mol_conf 過去可能是 "" / float / "67.3"（從 CSV 讀回時）三種型別，
       下游每次都得呼叫 _to_f() 翻譯。現在它就是 float，永遠是 float。

    2. 拼字防呆：r["mol_cnof"] = 80 過去會默默建立一個沒人讀的新 key 而不報錯；
       r.mol_cnof = 80 現在會在執行時拋 AttributeError，IDE 也標紅。

    3. 隱性狀態：cross_match 過去是 "✓" / ""，靠字串比對判斷真假；
       現在是 bool，意圖明確。

    CSV 邊界由 to_csv_row()/from_csv_row() 處理。寫到磁碟仍是字串
    （"Y" / "✓" / "" / "80.0"），人類看 CSV 跟以前一樣。
    """
    # ─── 識別欄位（必填） ─────────────────────────────────────────────────
    source_docx: str
    image_name: str
    docx_class: str            # "small" / "large"
    # 這張圖是在第幾層被掃到的:1=第一層(TIER1_IMAGES)，2=第一層沒拿到 permit
    # 多數票後的擴掃。small 與 --full-scan 一律為 1。純觀測欄位，不參與任何決策
    # ——它存在是為了讓「第一層命中率崩掉」這件事看得見(見 ADR-0005)。
    scan_tier: int = 1

    # ─── OCR 結果 ────────────────────────────────────────────────────────
    mol: str = ""
    mol_layer: int = 0
    mol_conf: float = 0.0
    id: str = ""
    id_layer: int = 0
    id_conf: float = 0.0

    # ─── 讀值與掃描觀測(判斷不在這裡,在送審決策,ADR-0013) ─────────────
    cross_match: bool = False  # mol 與 permit 兩 ROI 互證
    note: str = ""             # 掃描當下的觀測(例如佔位列為什麼沒有讀值)
    status: ResultStatus = ResultStatus.OK

    # ─── 詮釋資料 ────────────────────────────────────────────────────────
    hit_config: str = ""       # 命中時用的 Tesseract config 名稱
    hit_roi: str = ""          # 命中時的 ROI 名稱
    mol_crop: str = ""         # mol crop 圖檔路徑
    permit_crop: str = ""      # permit crop 圖檔路徑

    # ─── 內部 flag（取代過去的 _id_from_vote） ───────────────────────────
    id_from_vote:  bool = False
    mol_from_vote: bool = False   # mol 值來自多數票（PERMIT_VOTE_CONFIGS 那幾組的多數票）

    # ─── 序列化 ──────────────────────────────────────────────────────────
    def to_csv_row(self) -> dict[str, str]:
        """轉成 CSV 用的 str dict。

        慣例（與重構前的 CSV 完全一致，使人類觀察 CSV 無感）：
          - bool 欄位 → "Y" / "✓" / ""
          - float 為 0 → ""（保留「未設定」的視覺差異）
          - int 為 0 → ""
          - enum  → 其 .value 字串
        """
        return {
            "source_docx":   self.source_docx,
            "image_name":    self.image_name,
            "docx_class":    self.docx_class,
            "scan_tier":     str(self.scan_tier),
            "mol":           self.mol,
            "mol_layer":     str(self.mol_layer) if self.mol_layer else "",
            "mol_conf":      f"{self.mol_conf:.1f}" if self.mol_conf else "",
            "mol_from_vote": "Y" if self.mol_from_vote else "",
            "id":            self.id,
            "id_layer":      str(self.id_layer) if self.id_layer else "",
            "id_conf":       f"{self.id_conf:.1f}" if self.id_conf else "",
            "id_from_vote":  "Y" if self.id_from_vote else "",
            "cross_match":   "✓" if self.cross_match else "",
            "note":          self.note,
            "hit_config":    self.hit_config,
            "hit_roi":       self.hit_roi,
            "mol_crop":      self.mol_crop,
            "permit_crop":   self.permit_crop,
            "status":        self.status.value,
        }

    @classmethod
    def from_csv_row(cls, row: dict[str, str]) -> "ScanResult":
        """從 CSV 一列重建 ScanResult。

        集中處理 str → 型別轉換，過去這段邏輯散在 _to_f / 各種讀取點。
        現在只此一處需要關心 CSV 格式細節。
        """
        def to_float(s: str) -> float:
            try:
                return float(s) if s else 0.0
            except ValueError:
                return 0.0

        def to_int(s: str) -> int:
            try:
                return int(s) if s else 0
            except ValueError:
                return 0

        return cls(
            source_docx   = row.get("source_docx", ""),
            image_name    = row.get("image_name", ""),
            docx_class    = row.get("docx_class", ""),
            # 舊 CSV 沒有這欄 → 視為第一層,不讓缺欄變成無意義的 0
            scan_tier     = to_int(row.get("scan_tier", "")) or 1,
            mol           = row.get("mol", ""),
            mol_layer     = to_int(row.get("mol_layer", "")),
            mol_conf      = to_float(row.get("mol_conf", "")),
            mol_from_vote = (row.get("mol_from_vote", "") == "Y"),
            id            = row.get("id", ""),
            id_layer      = to_int(row.get("id_layer", "")),
            id_conf       = to_float(row.get("id_conf", "")),
            id_from_vote  = (row.get("id_from_vote", "") == "Y"),
            cross_match   = (row.get("cross_match", "") == "✓"),
            # 舊檔還帶判斷欄,不讀(ADR-0013):final_value/final_conf/vision_review,
            # 以及 manual_review(值是 "Y" 或整句理由)、low_conf(低信心截圖路徑)
            note          = row.get("note", ""),
            hit_config    = row.get("hit_config", ""),
            hit_roi       = row.get("hit_roi", ""),
            mol_crop      = row.get("mol_crop", ""),
            permit_crop   = row.get("permit_crop", ""),
            status        = _status(row),
        )


def _status(row: dict[str, str]) -> ResultStatus:
    """status 欄 → ResultStatus。空字串是沒有這欄的舊檔,當正常;不認得的值拋例外。

    以前不認得的值安靜當成正常(OK),壞檔或未來格式的佔位列就會被當成讀值去決策。
    """
    value = row.get("status", "")
    if not value:
        return ResultStatus.OK
    try:
        return ResultStatus(value)
    except ValueError:
        raise UnknownStatus(
            f"matches.csv 的 status 不認得:{row.get('source_docx', '')} 的 {value!r}") from None


class _Writer:
    def __init__(self, f):
        self._w = csv.DictWriter(f, fieldnames=FIELDS)
        self._w.writeheader()

    def write(self, result: ScanResult) -> None:
        self._w.writerow(result.to_csv_row())


@contextmanager
def writer(path: Path) -> Iterator[_Writer]:
    """寫一份新的 matches.csv(覆寫):`with writer(p) as w: w.write(result)`。"""
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        yield _Writer(f)


def write(path: Path, results) -> None:
    """一次寫完一串列(測試、工具用;掃描邊掃邊寫用 writer)。"""
    with writer(path) as w:
        for r in results:
            w.write(r)


def read(path: Path) -> dict[str, list[ScanResult]]:
    """讀回成 {docx 檔名: [列]}:依檔名排序,每份 docx 內維持檔案裡的順序。"""
    groups: dict[str, list[ScanResult]] = {}
    with open(path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            r = ScanResult.from_csv_row(row)
            groups.setdefault(r.source_docx, []).append(r)
    return dict(sorted(groups.items()))
