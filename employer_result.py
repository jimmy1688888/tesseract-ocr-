# -*- coding: utf-8 -*-
"""雇主擷取結果(CONTEXT.md):一份 docx 的雇主資料擷取產出,一個有形狀的值(#45)。

四塊:擷取狀況、H~O 的欄位值、疑慮(一條條「哪一格,什麼事實」)、來源。舊做法是一個
扁平 dict,混著輸出欄、內部疑慮、流程中介(`_image`、`_note`…),「哪些鍵算輸出」
在至少三處各列一份。

這裡沒有任何重的相依(不碰 Vision、地址庫),pipeline 可以直接 import —— 雇主擷取
本身載入失敗時,寫 Sheets 那一側照樣拿得到欄位對照表。
"""
from __future__ import annotations

from dataclasses import dataclass, field, fields
from enum import Enum

from contract_template import Template
from tw_phone import display_form


class ExtractionStatus(Enum):
    """擷取狀況,五選一。除了擷取成功,其餘四種的 H~O 一律未擷取,L 欄必寫原因。"""
    擷取成功 = "擷取成功"
    雇主區塊未錨定 = "雇主區塊未錨定"
    找不到契約頁 = "找不到契約頁"
    docx無內嵌圖 = "docx 無內嵌圖"
    擷取失敗 = "擷取失敗"          # 擷取時拋例外


# 一條疑慮講的是一整份時,「哪一格」寫這個(L 欄冠 H~O)。
WHOLE = "整份"

# 沒擷取的原因(L 欄)。只陳述確定的事實;例外名稱不寫,看 Sheets 的人用不到,日誌有。
_NOT_EXTRACTED = {
    ExtractionStatus.雇主區塊未錨定: "契約頁雇主區塊未錨定,未擷取",
    ExtractionStatus.找不到契約頁: "找不到契約頁,未擷取",
    ExtractionStatus.docx無內嵌圖: "docx 無內嵌圖,未擷取",
    ExtractionStatus.擷取失敗: "雇主擷取失敗(例外),未擷取",
}


@dataclass(frozen=True)
class EmployerValues:
    """H~O 的欄位值:解析出來的 7 個值。疑慮標示(L)不是欄位值,由疑慮組成。

    這是「哪些值算輸出」唯一的一份清單;Sheets 寫哪幾欄由 EMPLOYER_COLUMNS 決定
    (#30 拿掉 I、J 兩欄時值照算)。
    """
    雇主名稱_中: str = ""
    雇主名稱_英: str = ""
    地址_中: str = ""
    地址_中_標準: str = ""
    地址_英: str = ""
    郵遞區號: str = ""
    電話: str = ""


VALUE_KEYS = tuple(f.name for f in fields(EmployerValues))


@dataclass(frozen=True)
class Doubt:
    """一條疑慮:哪一格(欄位值的名稱)、什麼事實。

    事實只陳述確定的事,不推斷成因(CONTEXT.md「疑慮標示」)。欄位字母不寫在事實裡,
    由 compose_doubts 依 EMPLOYER_COLUMNS 加 —— L 欄的寫法只在那一處。
    """
    field: str
    fact: str

    def __post_init__(self):
        # 欄位名打錯要在建構時就炸,不要等到組 L 欄才 KeyError。
        if self.field not in (*VALUE_KEYS, WHOLE):
            raise ValueError(f"疑慮指向不存在的欄位:{self.field!r}")


# Sheets 的 H~O:哪一欄放哪個值。寫 Sheets 與疑慮冠的欄位字母都看這一張。
DOUBT_KEY = "疑慮標示"
EMPLOYER_COLUMNS = (
    ("H", "雇主名稱_中"), ("I", "雇主名稱_英"),
    ("J", "地址_中_標準"), ("K", "地址_中"),
    ("L", DOUBT_KEY), ("M", "地址_英"),
    ("N", "郵遞區號"), ("O", "電話"),
)
_COLUMN_OF = {key: col for col, key in EMPLOYER_COLUMNS} | {WHOLE: "H~O"}


def compose_doubts(doubts: tuple[Doubt, ...]) -> str:
    """把疑慮清單組成 L 欄一格,每條冠上欄位字母指明是哪一格。L 欄只在這裡組。

    與 D 欄分開的理由不變(D 欄專講許可證判讀,不同主題);但名稱與地址同屬
    雇主資料,是同一個主題下的多個欄位,合在一格不算一詞多義。
    """
    return "；".join(f"{_COLUMN_OF[d.field]}:{d.fact}" for d in doubts)


@dataclass(frozen=True)
class Source:
    """擷取的來源:契約樣板、契約頁(或候選頁)圖檔名、ROI 截圖檔名、Vision 全文。"""
    template: Template = Template.認不出
    image: str = ""
    crop: str = ""
    text: str = ""


@dataclass(frozen=True)
class EmployerExtraction:
    """雇主擷取結果。detail 是擷取狀況的補充(例如找不到契約頁時的最高分),只進日誌。"""
    status: ExtractionStatus
    values: EmployerValues = field(default_factory=EmployerValues)
    doubts: tuple[Doubt, ...] = ()
    source: Source = field(default_factory=Source)
    detail: str = ""

    @property
    def 疑慮標示(self) -> str:
        """L 欄。沒擷取時原因排第一條(由擷取狀況帶出,不會漏寫),其後是疑慮清單
        —— 例如切結書頁救回的中文名。"""
        reason = _NOT_EXTRACTED.get(self.status)
        return compose_doubts(((Doubt(WHOLE, reason),) if reason else ()) + self.doubts)

    def cell(self, key: str) -> str:
        """H~O 某一欄的內容(key 是 EMPLOYER_COLUMNS 的值)。
        電話的值是電話標準形(比對用),寫 O 欄這一刻才轉成給人讀的顯示形(#49)。"""
        if key == DOUBT_KEY:
            return self.疑慮標示
        if key == "電話":
            return display_form(self.values.電話)
        return getattr(self.values, key)
