# -*- coding: utf-8 -*-
"""契約樣板:整份契約只判一次,由文件上印的字判定(ADR-0010)。

用語見 CONTEXT.md「契約樣板」。三類樣板的字彙(中文標題、印尼文標籤、SEKTOR)
只記在這裡;選契約頁將來若要依樣板加權,引用這裡的字彙,不另抄一份。

判定吃雇主擷取的同一段 Vision 全文。順序依 36 份全文實測(ADR-0010):
  1. 中文標題「勞動契約 ○○」—— 36/36 讀得到,一個訊號分得出三類
  2. 印尼文標籤,單字即算 —— 工廠類的 Nama Perusahaan 5 份全被 Vision 拆成多行
  3. SEKTOR INFORMAL → 家庭類;只有 FORMAL 不推定(機構類與工廠類都是 FORMAL)
  4. 都沒有 → 認不出
訊號矛盾時以標題為準:矛盾只可能是 OCR 讀錯,整行印刷大字的標題最可靠。
"""
import re
from enum import Enum


class Template(str, Enum):
    """值就是 CONTEXT.md 的類別名(核對值也存這個字串)。"""
    家庭類 = "家庭類"
    機構類 = "機構類"
    工廠類 = "工廠類"
    認不出 = "認不出"

    @property
    def is_organisation(self) -> bool:
        """雇主是機構或公司:名稱是「名稱(負責人)」的寫法,不是人名。"""
        return self in (Template.機構類, Template.工廠類)


# 中文標題後的類別字。「業勞動契約 監護工」這種黏了別欄字的也吃(33704)。
TITLE = re.compile(r"勞動契約\s*(監護工|家庭幫傭|養護機構|醫院|工廠)")
_TITLE_TEMPLATE = {
    "監護工": Template.家庭類, "家庭幫傭": Template.家庭類,
    "養護機構": Template.機構類, "醫院": Template.機構類,
    "工廠": Template.工廠類,
}

# 印尼文雇主名稱標籤,單字即算,依序比(較專門的在前)。
# Majikan 要排除標題字「MAJIKAN DENGAN PEKERJA」—— 那是每一類都有的標題,不是標籤。
LABELS = (
    (re.compile(r"Perusahaan", re.I), Template.工廠類),
    (re.compile(r"Panti\s*Jompo", re.I), Template.機構類),
    (re.compile(r"Majikan(?!\s*DENGAN)", re.I), Template.家庭類),
)

SEKTOR_INFORMAL = re.compile(r"\bINFORMAL\b", re.I)

# 工廠類的雇主英文名是公司名:含這些字樣之一(30766~30770 實測)。
COMPANY_WORDS = re.compile(
    r"\b(?:CO|LTD|CORP|CORPORATION|INC|COMPANY|LIMITED|ENTERPRISE|INDUSTR\w*)\b", re.I)


def contract_template(text: str) -> Template:
    """這份契約的樣板;文件上讀不到任何樣板字就是「認不出」。"""
    m = TITLE.search(text or "")
    if m:
        return _TITLE_TEMPLATE[m.group(1)]
    for pat, template in LABELS:
        if pat.search(text or ""):
            return template
    if SEKTOR_INFORMAL.search(text or ""):
        return Template.家庭類
    return Template.認不出
