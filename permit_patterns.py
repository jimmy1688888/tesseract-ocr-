# -*- coding: utf-8 -*-
"""許可證號的取號 pattern 與取號函式:Tesseract 掃描與 Vision 驗證共用同一份(#41)。

兩個引擎讀到同樣的字,就必須取出同樣的號碼 —— 雙引擎交叉比對比的是「讀到的字」,
不該混進「兩邊取號規則不同」的差異。permit_lower 的 pattern 第一條冒號可省略
(切結書最後一頁的簽署欄常寫成「No. Izin 1234」),所以取號要看截圖屬於哪個 ROI。
"""
import re

RE_PERMIT_ID_LIST = [
    re.compile(r"No\.?\s*i[zjl1]in\s*[:::﹕]\s*(?:NO\.)?(\d{4}(?:-\d)?)(?!\d)", re.IGNORECASE),
    re.compile(r"[Nn]\w{0,5}n\s*[:::﹕]\s*(?:NO\.)?(\d{4}(?:-\d)?)(?!\d)",       re.IGNORECASE),
    re.compile(r"\bi[zjl1]in\s*[:::﹕]\s*(?:NO\.)?(\d{4}(?:-\d)?)(?!\d)",         re.IGNORECASE),
    re.compile(r"\bNO\.(\d{4}(?:-\d)?)(?!\d)",                                    re.IGNORECASE),
]

RE_PERMIT_ID_LIST_LOWER = [
    re.compile(r"No\.?\s*i[zjl1]in\s*[:::﹕]?\s*(?:NO\.)?(\d{4}(?:-\d)?)(?!\d)", re.IGNORECASE),
    re.compile(r"[Nn]\w{0,5}n\s*[:::﹕]\s*(?:NO\.)?(\d{4}(?:-\d)?)(?!\d)",        re.IGNORECASE),
    re.compile(r"\bi[zjl1]in\s*[:::﹕]\s*(?:NO\.)?(\d{4}(?:-\d)?)(?!\d)",          re.IGNORECASE),
    re.compile(r"\bNO\.(\d{4}(?:-\d)?)(?!\d)",                                     re.IGNORECASE),
]

RE_MOL_LIST = [
    re.compile(r"Agency'?s?\s+M[O0]L?\s+(?:L[i1I])?[i]?cense\s+Num\s*ber\s*[:::]\s*(\d{4}(?:-\d)?)(?!\d)", re.IGNORECASE),
    re.compile(r"A\w{3,6}'?s?\s+M[O0]L?\s+(?:L[i1I])?[i]?cense\s+Num\s*ber\s*[:::]\s*(\d{4}(?:-\d)?)(?!\d)", re.IGNORECASE),
    re.compile(r"(?:Num\s*ber|umber|[Nn]amber)\s*[:::]\s*(\d{4}(?:-\d)?)(?!\d)", re.IGNORECASE),
    re.compile(r"M[O0]L\D{0,30}(\d{4}(?:-\d)?)(?!\d)", re.IGNORECASE),
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


def permit_in_text(text: str, hit_roi: str) -> str:
    """一段 OCR 全文 → 許可證號,依截圖所屬的 ROI 選 pattern(與 Tesseract 掃該 ROI 時相同)。

    ROI 不明時先試 permit、再試 mol(舊的 Vision 取號就是這樣做)。
    """
    if hit_roi == "permit_lower":
        return find_permits(text, RE_PERMIT_ID_LIST_LOWER, find_mol=False)[0]
    if hit_roi == "permit_upper":
        return find_permits(text, RE_PERMIT_ID_LIST, find_mol=False)[0]
    if hit_roi == "mol":
        return find_permits(text, find_id=False)[2]
    id_, _, mol, _ = find_permits(text)
    return id_ or mol
