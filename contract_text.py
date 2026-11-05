# -*- coding: utf-8 -*-
"""契約全文的共用文字樣式:雇主擷取與台仲區塊(CONTEXT「台仲區塊」)都要認的字。

只放兩邊真的共用的;只有一邊用的留在那一邊。兩邊都從這裡 import,台仲區塊因此
不必認識雇主擷取(#79)。
"""
from __future__ import annotations

import re

CJK = re.compile(r"[一-鿿]")
# 傳真起點:同一行常寫成「電話:03-5310852,傳真:03-5277128」,取號碼前先切掉後段,
# 否則傳真號會與電話一起進統計(32447/32417 的仲介框正是這種寫法)。
FAX_SPLIT = re.compile(r"傳真|Fax", re.I)
CN_ADDR = re.compile(r"[縣市].{0,20}?[路街道段巷弄號樓]")
# 機構類(養護機構/醫院)樣板的甲方標籤是「養護機構/醫院名稱 (以下簡稱甲方)」(33703)。
INSTITUTION_NAME_LABEL = r"養護機構\s*/\s*醫院名稱"
# 取值時要從行上去掉的標籤字。
LABEL_WORDS = re.compile(
    rf"(甲方名稱|雇主名[稱称]|{INSTITUTION_NAME_LABEL}|以下簡稱為?甲方|地址|電話|傳真)")


def has_cjk(s: str) -> bool:
    return bool(CJK.search(s))
