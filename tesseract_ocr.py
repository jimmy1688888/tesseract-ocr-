# -*- coding: utf-8 -*-
"""Tesseract 一個 adapter(#76,候選 AG):執行檔路徑、語言、讀字、安裝檢查只住這裡。

許可證判讀、雇主擷取的認頁、preflight 都經過它。以前路徑靠 pipeline 的 import 副作用
設定,不先 import pipeline 的人(錄掃描快照、雇主擷取)只好各自再設一次,而規則還不一樣。

許可證「怎麼讀」(掃描設定、白名單、參數組裝)不在這裡,在 permit_reading.ocr。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytesseract
from pytesseract import Output

# 寫死、不檢查存在:沒裝的話由 preflight 報出來(換機器要退 PATH 是另一張行為票)。
TESSERACT_EXE = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
TESS_LANG = "ind+eng"
PSM_AUTO = "--psm 3"

# import 時只賦值、不碰檔案系統:用得到 Tesseract 的人一定經過這裡,路徑就一定設好了。
pytesseract.pytesseract.tesseract_cmd = TESSERACT_EXE


def ocr_with_conf(img, lang: str, tess_cfg: str) -> tuple[str, float]:
    """讀字並附平均信心(0~100);沒有字時信心 0。"""
    data = pytesseract.image_to_data(img, lang=lang, config=tess_cfg, output_type=Output.DICT)
    valid = [(w, int(c)) for w, c in zip(data["text"], data["conf"])
             if w.strip() and int(c) != -1]
    text = " ".join(w for w, _ in valid)
    avg_conf = round(sum(c for _, c in valid) / len(valid), 1) if valid else 0.0
    return text, avg_conf


def read_text(img, lang: str = TESS_LANG, tess_cfg: str = PSM_AUTO) -> str:
    """只要文字、不要信心(認頁用)。"""
    return pytesseract.image_to_string(img, lang=lang, config=tess_cfg)


@dataclass(frozen=True)
class Installation:
    """Tesseract 裝好了沒:只有事實,怎麼報給人看是 preflight 的事。"""
    exe: str
    found: bool
    version: str = ""
    version_error: str = ""           # 執行檔在、卻問不出版本時的錯誤(repr)
    missing_langs: tuple[str, ...] = ()
    langs_checked: bool = True        # 舊版 pytesseract 問不出語言清單時為 False


def installation() -> Installation:
    exe = pytesseract.pytesseract.tesseract_cmd
    if not Path(exe).exists():
        return Installation(exe, found=False)
    try:
        version = str(pytesseract.get_tesseract_version())
    except Exception as e:
        return Installation(exe, found=True, version_error=repr(e))
    try:
        have = set(pytesseract.get_languages(config=""))
    except Exception:
        return Installation(exe, found=True, version=version, langs_checked=False)
    missing = tuple(sorted(set(TESS_LANG.split("+")) - have))
    return Installation(exe, found=True, version=version, missing_langs=missing)
