# -*- coding: utf-8 -*-
"""許可證號的取號 pattern 與取號函式:Tesseract 掃描與 Vision 驗證共用同一份(#41)。

兩個引擎讀到同樣的字,就必須取出同樣的號碼 —— 雙引擎交叉比對比的是「讀到的字」,
不該混進「兩邊取號規則不同」的差異。permit_lower 的 pattern 第一條冒號可省略
(切結書最後一頁的簽署欄常寫成「No. Izin 1234」),所以取號要看截圖屬於哪個 ROI。

#42:Vision 會把 `No ijin` 讀成 `No jin`,所以第 1 條 `No` 後的 i 可有可無(要有 `No`
錨點;第 3 條無錨點,不放寬)。第 5 條認中文標籤「許可證號」,排最後、不改既有 id_layer;
Tesseract 讀不到中文,對它沒有作用。同頁同位置,不算額外的獨立證據。
"""
import re

# 冒號:半形 `:`、全形 `：`、小型 `﹕`、比例 `∶`。permit 與 mol 全部共用這一份。
_COLON = "[:：﹕∶]"
_VALUE = r"(\d{4}(?:-\d)?)(?!\d)"   # 4 位數,可帶分公司後綴(1234-2)

# 第 5 條:中文標籤,冒號可省略。
_RE_CHINESE_LABEL = re.compile(rf"許可證[號号]\s*{_COLON}?\s*{_VALUE}")

RE_PERMIT_ID_LIST = [
    re.compile(rf"No\.?\s*i?[zjl1]in\s*{_COLON}\s*(?:NO\.)?{_VALUE}", re.IGNORECASE),
    re.compile(rf"[Nn]\w{{0,5}}n\s*{_COLON}\s*(?:NO\.)?{_VALUE}",     re.IGNORECASE),
    re.compile(rf"\bi[zjl1]in\s*{_COLON}\s*(?:NO\.)?{_VALUE}",        re.IGNORECASE),
    re.compile(rf"\bNO\.{_VALUE}",                                    re.IGNORECASE),
    _RE_CHINESE_LABEL,
]

RE_PERMIT_ID_LIST_LOWER = [
    re.compile(rf"No\.?\s*i?[zjl1]in\s*{_COLON}?\s*(?:NO\.)?{_VALUE}", re.IGNORECASE),
    *RE_PERMIT_ID_LIST[1:],
]

RE_MOL_LIST = [
    re.compile(rf"Agency'?s?\s+M[O0]L?\s+(?:L[i1I])?[i]?cense\s+Num\s*ber\s*{_COLON}\s*{_VALUE}", re.IGNORECASE),
    re.compile(rf"A\w{{3,6}}'?s?\s+M[O0]L?\s+(?:L[i1I])?[i]?cense\s+Num\s*ber\s*{_COLON}\s*{_VALUE}", re.IGNORECASE),
    re.compile(rf"(?:Num\s*ber|umber|[Nn]amber)\s*{_COLON}\s*{_VALUE}", re.IGNORECASE),
    re.compile(rf"M[O0]L\D{{0,30}}{_VALUE}", re.IGNORECASE),
]


def find_permits(text: str, permit_id_list=None,
                 find_mol: bool = True, find_id: bool = True) -> tuple[str, int, str, int]:
    if permit_id_list is None:
        permit_id_list = RE_PERMIT_ID_LIST
    id_, id_layer = None, 0
    if find_id:
        for i, p in enumerate(permit_id_list, 1):
            id_ = p.search(text)
            if id_:
                id_layer = i
                break
    mol, mol_layer = None, 0
    if find_mol:
        for i, p in enumerate(RE_MOL_LIST, 1):
            mol = p.search(text)
            if mol:
                mol_layer = i
                break
    return (
        id_.group(1).strip() if id_ else "",
        id_layer,
        mol.group(1).strip() if mol else "",
        mol_layer,
    )


def permit_id_patterns(roi: str) -> list[re.Pattern]:
    """permit ROI → 取號 pattern。Tesseract 掃描與 Vision 取號都經由這裡選,不各寫一份。

    "permit"(知道是 permit 截圖、不知道哪一半)用 LOWER:它是一般那套的超集
    (只多了「冒號可省略」),寬一點也不會造成錯值 —— 自動寫入還要兩引擎一致。
    """
    return RE_PERMIT_ID_LIST if roi == "permit_upper" else RE_PERMIT_ID_LIST_LOWER


def permit_in_text(text: str, roi: str) -> str:
    """一段 OCR 全文 → 許可證號,依截圖所屬的 ROI 選 pattern(與 Tesseract 掃該 ROI 時相同)。

    roi:permit_upper / permit_lower / permit(哪一半不明)/ mol;不明時先試 permit、再試 mol。
    """
    if roi.startswith("permit"):
        return find_permits(text, permit_id_patterns(roi), find_mol=False)[0]
    if roi == "mol":
        return find_permits(text, find_id=False)[2]
    id_, _, mol, _ = find_permits(text)
    return id_ or mol
