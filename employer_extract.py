"""從勞動契約圖片擷取「雇主資料」(名稱中/英、地址中/英、電話)。

用 Google Vision DOCUMENT_TEXT_DETECTION 取全文後,以內容啟發式解析。
與許可證流程(VisionReader.read_sparse + permit_in_text 抓 4 碼號碼)分開,互不影響。

難點與對策:
- 版面是中印尼雙語兩欄表,Vision 逐欄序列化常錯行 → 不依賴嚴格「標籤緊接值」,
  改用內容特徵(中日韓字元、地址關鍵字、電話重複)判斷。
- 仲介公司 block 在標題「勞動契約」之上,會混入公司名/地址 → 從雇主標籤處才開始解析。
- 紅章常蓋在名稱上 → 名稱信心較低,呼叫端應標人工複核。
- **去紅章前處理(選項 A)**:送 Vision 前先 deink_red_stamp() 抽紅色通道並抹白
  明顯紅印章像素,讓黑字保留、印章變淡,提升被章尾污染的名稱/地址辨識率。
  像素處理都在 pixels.py,與許可證掃描共用同一份(#16)。
"""
from __future__ import annotations

import logging
import re
from dataclasses import replace
from typing import TYPE_CHECKING, NamedTuple

# docx 內嵌圖與圖序只有一份,與 pipeline 共用(#5);像素前處理也是(#16)。
# 兩側不互相 import。
from contract_template import COMPANY_WORDS, Template, contract_template
from pixels import crop_png, deink_red_stamp
from employer_result import (VALUE_KEYS, WHOLE, Doubt, EmployerExtraction, EmployerValues,
                             ExtractionStatus, Source)
from contract_text import (CJK, CN_ADDR, FAX_SPLIT, INSTITUTION_NAME_LABEL, LABEL_WORDS,
                           has_cjk)
from tw_phone import phones_in
# 地址庫 import 零 I/O(ADR-0001),縣市區表第一次判定時才載入。
from address_db import determine_zip

from vision_reader import IDENTITY_REFUSED, VisionCallFailed
from page_identification import PageIdentification

if TYPE_CHECKING:
    from vision_reader import VisionReader

logger = logging.getLogger(__name__)

# 電話標籤行(不含「傳真」,避免傳真號被當電話):優先取此類行上的號碼。
PHONE_LINE_LABEL = re.compile(r"電話|Nomor\s*Telepon", re.I)
EN_ADDR_KW = re.compile(
    r"\b(No\.|Rd\.|St\.|Dist\.|Lane|Alley|Sec\.|Village|Township|County|City|Taiwan)\b",
    re.I,
)
# 甲方(雇主)標籤。「以下簡稱」刻意帶上「甲方」二字:乙方那行寫的是
# 「勞工姓名(以下簡稱乙方)」,同樣含「以下簡稱」,不限定就會錨到乙方——
# 平常甲方在前先命中看不出問題,一旦甲方那行被紅章毀掉(32417 的
# 「甲方名稱」被讀成「用戶名稱」、「以下簡稱為甲方」被讀成「以下統為甲方」),
# 就會靜默錨到乙方,把整個雇主區塊切在起點之前 → 五欄全空。
NAME_LABEL  = re.compile(
    rf"(甲方名稱|雇主名[稱称]|{INSTITUTION_NAME_LABEL}|以下簡稱為?甲方)")
ID_LABEL    = re.compile(r"Nama\s*(Majikan|Perusahaan)", re.I)
# 機構類的印尼文標籤。與 ID_LABEL 分開,因為退到它時**不往回退一行**:機構全名
# 長、Vision 斷成兩行排在它之上(33703),往回一行只拿得到後半截名字,括號補齊後
# 看起來像個正常的名字。從標籤那行起算,中文名留空交人工(L 欄會標未擷取到)。
ID_LABEL_INSTITUTION = re.compile(r"Nama\s*Panti\s*Jompo", re.I)
# 甲方區塊的結束線:印尼文樣板「SELANJUTNYA DISEBUT PIHAK PERTAMA」
# (以下簡稱甲方)固定收在甲方資料之後,其後即乙方(勞工)資料。
PARTY_END   = re.compile(
    r"SELANJUTNYA\s*DISEBUT|勞工姓名|Nama\s*Pekerja|PIHAK\s*KEDUA|以下簡稱乙方",
    re.I)
ADDR_LABEL  = re.compile(r"(地址|Alamat)")
PHONE_LABEL = re.compile(r"(電話|Nomor\s*Telepon|傳真)", re.I)
def _is_org_name_line(line: str, template: Template) -> bool:
    """機構類／工廠類樣板裡,這行英文是不是雇主名稱(而不是地址)。

    樣板由文件判一次(ADR-0010),不再從英文名的字樣逐行猜。名稱與地址靠
    「不含數字」分:地址一定有門牌號,機構名、公司名沒有。機構英文名常含縣市
    (「XX County Private…Long-term Care」),公司名常含 Taiwan —— 不認得的話
    會被 EN_ADDR_KW 當成地址行(33703)。家庭類的英文行一律不是機構名。
    """
    return template.is_organisation and not re.search(r"\d", line)


# 機構類樣板的標題「(NURSING HOME)」:含 Nursing 但不是任何機構的名字。
_EN_INSTITUTION_TITLE = re.compile(r"\(?\s*NURSING\s*HO(?:ME|USE)\s*\)?", re.I)
# 表格樣板/浮水印字,絕非雇主名稱
BOILERPLATE = re.compile(
    r"SEKTOR|INFORMAL|FORMAL|SELANJUTNYA|DISEBUT|PIHAK|PERTAMA|PERJANJIAN"
    r"|MAJIKAN|DENGAN|PEKERJA|ANTARA|Perawat|Care\s*Giver|Agency"
    r"|Nama|Perusahaan|Alamat|Nomor|Telepon",
    re.I,
)


# 雇主 block ROI(相對比例):上緣 → Nomor Telepon 一帶,涵蓋整個雇主區(標題、
# 甲方名稱、地址中/英、電話/Nomor Telepon),下緣停在乙方標籤前,避免抓到勞工資料。
EMPLOYER_ROI = (0.0, 0.0, 1.0, 0.35)


def _balance_brackets(s: str) -> str:
    """補齊不成對的括號。擷取名稱時 strip 頭尾會剝掉單邊括號(如「某公司(股)」
    尾端 ) 被剝成「某公司(股」),或 OCR 漏讀一邊,寫進 Sheets 就少一邊。
    全形（）與半形()分別配對:開比閉多 → 尾端補閉;閉比開多 → 頭端補開。"""
    if not s:
        return s
    for op, cl in (("（", "）"), ("(", ")")):
        diff = s.count(op) - s.count(cl)
        if diff > 0:
            s = s + cl * diff
        elif diff < 0:
            s = op * (-diff) + s
    return s


def _strip_label_residue(s: str) -> str:
    """剝掉標籤留下的殘骸:空括號、頭尾的標點、不成對的那一邊括號。

    成對的括號屬於名字,不剝:工廠類、機構類的雇主名稱是「名稱(負責人)」,
    舊做法 strip 掉結尾的「)」,括號補齊再補回來,就成了「補出來的」假疑慮(#35)。
    整個值被一對括號包住(「(王小明)」)仍當殘骸剝掉,與舊做法一致。
    """
    s = re.sub(r"[(（]\s*[)）]", "", s)
    while True:
        before = s
        s = s.strip(" :：;；")
        for op, cl in (("(", ")"), ("（", "）")):
            if s.startswith(cl) or (s.startswith(op) and s.count(op) > s.count(cl)):
                s = s[1:]
            if s.endswith(op) or (s.endswith(cl) and s.count(cl) > s.count(op)):
                s = s[:-1]
            if s.startswith(op) and s.endswith(cl) and _balanced(s[1:-1], op, cl):
                s = s[1:-1]
        if s == before:
            return s


def _balanced(s: str, op: str, cl: str) -> bool:
    depth = 0
    for ch in s:
        depth += (ch == op) - (ch == cl)
        if depth < 0:
            return False
    return depth == 0


class EmployerBlock(NamedTuple):
    """雇主區塊在全文行裡的上下界:lines[start:end]。"""
    start: int
    end: int                       # 不含;沒有甲方段落結束標記時是行數


class BlockNotAnchored(NamedTuple):
    """雇主區塊錨不到。detail 只進日誌(擷取狀況是雇主區塊未錨定)。"""
    detail: str


def locate_employer_block(lines: list[str]) -> EmployerBlock | BlockNotAnchored:
    """雇主區塊的上下界(上界跳過仲介 block,下界切在乙方之前)。

    起點① 甲方標籤;② 標籤被 OCR 毀掉時退印尼文標籤 Nama Majikan——
      實測 7 份契約它一律完好,且其「前一行」永遠是甲方姓名那行,故往回退一行
      以納入中文姓名。它落在仲介框之下,退到這裡不會把仲介電話捲進來。
      ③ 機構類退 Nama Panti Jompo,不往回退(見 ID_LABEL_INSTITUTION)。
    刻意不再退「MAJIKAN DENGAN」:那是標題字,而 Vision 對兩欄版面是逐欄
      序列化,標題屬右欄、雇主資料屬左欄,輸出順序與版面上下無關
      (32417:標題 y=104 卻排在第 15 行,雇主 y=166 反而排在第 10 行),
      拿它當起點等於賭 Vision 這次怎麼切欄。
    終點:甲方段落結束標記,把乙方(勞工)資料排除在統計之外。
    錨都失效 → BlockNotAnchored,不退回「從第 0 行開始」:實測 7 份契約中有 6 份
      會因此把仲介框的名稱/地址/電話整組當成雇主填進去(32447、32417 甚至抓到
      仲介的傳真號)。那是「看起來完全合理的錯誤資料」,人工審核看不出破綻,
      比留空危險得多。比照 32098 與台仲區塊的既有決定:錨不到就不猜,交人工。
    """
    start = next((i for i, l in enumerate(lines) if NAME_LABEL.search(l)), None)
    if start is None:
        i = next((i for i, l in enumerate(lines) if ID_LABEL.search(l)), None)
        if i is not None:
            start = max(0, i - 1)
        else:
            start = next((i for i, l in enumerate(lines)
                          if ID_LABEL_INSTITUTION.search(l)), None)
        if start is None:
            return BlockNotAnchored("甲方標籤、Nama Majikan、Nama Panti Jompo 皆未命中")
    end = next((i for i in range(start + 1, len(lines))
                if PARTY_END.search(lines[i])), len(lines))
    return EmployerBlock(start, end)


def pick_employer_phone(seg: list[str]) -> str:
    """雇主區塊內挑出雇主電話(電話標準形);挑不到回空字串。

    三個實測教訓,各有一組測試(test_employer_phone.py):
    - 只吃雇主區塊:雇主電話被格式/紅章毀掉時,退回全文統計會把仲介電話
      誤當雇主電話(32098),故寧可留空也不取區塊外的號碼。
    - 標籤行優先:「電話/Nomor Telepon」行上的號碼先算;農業契約地址含地號,
      會被電話規則誤當電話(32262)。標籤行取不到才退回全區塊。
    - 同行的「傳真」後段先切掉:常寫成「電話:03-5310852,傳真:03-5277128」,
      不切則傳真號進統計,且該行就是標籤行,傳真會靠標籤行優先勝出(32447/32417)。
    出現次數多者優先(電話+Nomor Telepon 各一),同分取先出現的;同號以標準形計數(#48)。
    """
    labeled_counts: dict[str, int] = {}
    all_counts: dict[str, int] = {}
    for l in seg:
        l = FAX_SPLIT.split(l, maxsplit=1)[0]
        on_label = bool(PHONE_LINE_LABEL.search(l))
        for m in phones_in(l):
            all_counts[m] = all_counts.get(m, 0) + 1
            if on_label:
                labeled_counts[m] = labeled_counts.get(m, 0) + 1
    pool = labeled_counts or all_counts
    return max(pool, key=pool.get, default="")


class EmployerAddress(NamedTuple):
    """雇主區塊裡的地址,都是 OCR 原文(郵遞區號另由 _check_address 判定)。"""
    cn: str                        # 地址_中
    en: str                        # 地址_英
    # 紅章殘影把中文字黏進的英文地址行:不輸出,只給英文行錨定用(CONTEXT.md)。
    en_hint: str

    @property
    def en_anchor(self) -> str:
        """餵給英文行錨定的英文:英文地址,讀不到才用 en_hint。"""
        return self.en or self.en_hint


def pick_employer_address(seg: list[str], template: Template) -> EmployerAddress:
    """雇主區塊內的中文地址、英文地址與英文提示;缺項為空字串。

    中文靠行政區+街道 pattern;英文靠地址關鍵字。
    中文先看雇主區塊內「地址」標籤之後幾行:兩欄版面被逐欄序列化時,仲介框的
    下半段(含仲介的中文地址)會夾進雇主區塊、排在雇主地址之前(33704)。
    找不到才退回「區塊內第一個像地址的行」—— 標籤與地址也會被拆開(32510、
    32518、33701)。只看區塊內的標籤:仲介框也有地址標籤(ADR-0003 追加)。
    機構類／工廠類的英文名稱行不是英文地址(_is_org_name_line,ADR-0010)。
    """
    addr_cn = _labelled_cn_address(seg)
    addr_en = en_hint = ""
    for idx, l in enumerate(seg):
        if not addr_cn and _is_cn_address(l):
            addr_cn = _clean_cn_address(l)
        if (not addr_en and not has_cjk(l) and EN_ADDR_KW.search(l) and len(l) > 12
                and not _is_org_name_line(l, template)):
            en = l
            # 併入下一行的 R.O.C. 之類接續
            if idx + 1 < len(seg) and re.fullmatch(r"[A-Za-z.()\s]{2,8}", seg[idx + 1]):
                en = en + " " + seg[idx + 1]
            addr_en = re.sub(r"^Alamat\s*[:：]?\s*", "", en, flags=re.I).strip()
        # 錨定線索:紅章殘影常把中文字黏進英文地址行(「司賜聼12.Ln.18…」),
        # 這種行不能當 地址_英 輸出,但行尾的英文縣市/區仍完好,
        # 留給 address_db 的英文錨定反查用(不寫入任何輸出欄位)。
        if not en_hint and has_cjk(l) and EN_ADDR_KW.search(l) and len(l) > 12:
            en_hint = l
    return EmployerAddress(addr_cn, addr_en, en_hint)


class EmployerNames(NamedTuple):
    """雇主區塊裡的名稱,OCR 原樣:不成對的括號由呼叫端補齊並掛疑慮。"""
    cn: str                        # 雇主名稱_中
    en: str                        # 雇主名稱_英


def pick_employer_names(seg: list[str], address: EmployerAddress,
                        template: Template) -> EmployerNames:
    """雇主區塊內的中英文名稱;缺項為空字串。地址抽取挑中的行不當名字。

    掃整個雇主區塊,排除地址/標籤,依中日韓/拉丁分中英。
    掃整段而非前 9 行:Vision 對兩欄版面逐欄序列化,中文名與它的標籤未必相鄰——
    32508 的「呂偉」排在區塊第 9 行(索引剛好落在舊窗口外),中間隔著從另一欄
    串進來的契約標題區塊,於是標題「勞動契約 家庭幫傭」反而先命中成了雇主名。
    """
    name_cn = name_en = ""
    for idx, l in enumerate(seg):
        if ADDR_LABEL.search(l) or PHONE_LABEL.search(l) or l in (address.cn, address.en):
            continue
        nxt = seg[idx + 1] if idx + 1 < len(seg) else ""
        # 英文名:優先取 Nama 標籤後的值(同行),否則取整行(去冒號前綴)。
        # 機構英文名不去冒號前綴:名字裡就有冒號(33703 的機構英文名括號裡就有一個),
        # 也不因含縣市字被當地址;下一行若是整行括號的拼音(負責人)就接上。
        if not name_en and not _EN_INSTITUTION_TITLE.fullmatch(l):
            is_inst = _is_org_name_line(l, template)
            cand_en = re.sub(
                r"^.*Nama\s*(Majikan|Perusahaan|Panti\s*Jompo(\s*/\s*Rumah\s*Sakit)?)\s*[:：]?",
                "", l, flags=re.I)
            if cand_en == l and not is_inst:          # 該行無 Nama 標籤
                cand_en = re.sub(r".*[:：]", "", l)
            cand_en = cand_en.strip()
            if (cand_en and not has_cjk(cand_en) and re.search(r"[A-Za-z]", cand_en)
                    and not BOILERPLATE.search(cand_en)
                    and (is_inst or not EN_ADDR_KW.search(cand_en))
                    and len(cand_en) > 2):
                name_en = cand_en
                # 先補齊本行的括號再接:OCR 吃掉的右括號屬於本行(33703 的
                # 「…Private(…::JLong-term Care」),補在整串尾端會變成「…))」。
                if is_inst and re.fullmatch(r"\([A-Za-z.,\s-]+\)", nxt):
                    name_en = f"{_balance_brackets(name_en)} {nxt}"
        # 中文名:有中日韓字、非地址(中式或英式)、去標籤字。
        # EN_ADDR_KW 檢查:紅章殘影字有時會黏在英文地址行首(「司賜聼12.Ln.18…」),
        # 該行含 CJK 又無中文縣市字,若只擋 CN_ADDR 會被誤收為中文名。
        if not name_cn:
            # 分隔符含 ;；:OCR 常把全形冒號讀成分號,不吃就會把標籤連同分號
            # 一起留在值裡(32521 的「; 林瑞峰」)。
            core = re.sub(r".*[:：;；]", "", l).strip()
            if has_cjk(core) and not CN_ADDR.search(core) and not EN_ADDR_KW.search(core):
                cand = LABEL_WORDS.sub("", core)
                cand = _strip_label_residue(re.sub(r"Nama.*", "", cand, flags=re.I))
                if cand and has_cjk(cand) and _is_name_like(cand):
                    name_cn = cand + _name_continuation(cand, nxt)

    # 工廠類:Nama Perusahaan 被拆成好幾行,「第一個像英文名的行」可能先中別的字串
    # (30770 抓成一個編號)。含公司字樣的行優先,找不到才用上面的現行規則(#37)。
    if template is Template.工廠類:
        name_en = _company_name_in_block(seg) or name_en
    return EmployerNames(name_cn, name_en)


def extract_employer_fields(text: str) -> EmployerExtraction:
    """從 Vision 全文擷取雇主欄位(缺項為空字串)。

    擷取狀況只會是擷取成功或雇主區塊未錨定;來源只填契約樣板(圖檔、全文由呼叫端補)。
    """
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    # 契約樣板整份判一次(ADR-0010),下面的英文地址、英文名、字數音節數檢查都看它。
    # 錨定失敗的早退也帶著它:之後的切結書救援要依樣板決定做不做音節檢查。
    template = contract_template(text)

    block = locate_employer_block(lines)
    if isinstance(block, BlockNotAnchored):
        return EmployerExtraction(ExtractionStatus.雇主區塊未錨定, source=Source(template),
                                  detail=block.detail)
    seg = lines[block.start:block.end]
    phone = pick_employer_phone(seg)
    address = pick_employer_address(seg, template)
    names = pick_employer_names(seg, address, template)

    checked = _check_address(address)
    values = EmployerValues(
        # 補齊不成對括號:擷取時 strip 或 OCR 可能只留單邊,寫進 Sheets 會缺一邊。
        雇主名稱_中=_balance_brackets(names.cn),
        雇主名稱_英=_balance_brackets(names.en),
        地址_中=address.cn,
        地址_英=address.en,
        郵遞區號=checked.zip,
        電話=phone,
    )
    # 順序即 L 欄的順序:樣板 → 名稱 → 地址_中(未擷取／縣市中英不符／門牌)→ 郵遞區號。
    doubts = (
        # 樣板決定整套擷取規則,講的是一整份,不只名稱。
        *_doubts(WHOLE, "契約樣板未辨識,以家庭類規則擷取"
                 if template is Template.認不出 else ""),
        # 括號是補出來的就要講:斷行沒接上時(_name_continuation 只接緊鄰的下一行,
        # 而 Vision 逐欄序列化不保證相鄰),補齊後的「…機構(王)」看起來完全正常,
        # 而機構名又不做字數檢查 —— 不標的話就是看不見的錯值。
        *_doubts(_NAME_CN, "中文名括號不成對,已補齊"
                 if values.雇主名稱_中 != names.cn else ""),
        *_doubts(_NAME_CN, _name_doubt(values.雇主名稱_中, values.雇主名稱_英, template)),
        *checked.doubts,
    )
    return EmployerExtraction(ExtractionStatus.擷取成功, values, doubts, Source(template))


# 中文樣板字。BOILERPLATE 從頭到尾只有印尼文與英文,且只用在英文名那條路徑——
# 中文名完全沒有樣板過濾,於是契約標題「勞動契約 家庭幫傭／監護工」在標籤行沒抓到
# 名字時就會頂上來當雇主名(32508/32510)。「以下」則是標籤自己的殘骸:OCR 把
# 「甲方名稱(以下簡稱為甲方)」咬成「甲方名稱(以下」,LABEL_WORDS 認不出殘缺的
# 完整片語,剝掉「甲方名稱」後剩下的「以下」就成了值(32527)。
CN_BOILERPLATE = re.compile(
    r"勞動契約|家庭幫傭|監護工|看護工|家庭看護|製造工|廠工|漁工|"
    r"以下簡稱|以下$|^以下")


def _company_name_in_block(seg: list[str]) -> str:
    """區塊內第一個含公司字樣的公司名稱行,去掉同行的 Perusahaan 標籤;沒有就空。"""
    for l in seg:
        if (not has_cjk(l) and _is_org_name_line(l, Template.工廠類)
                and COMPANY_WORDS.search(l)):
            return re.sub(r"^.*?(Nama\s*)?Perusahaan\s*[:：]?", "", l, flags=re.I).strip()
    return ""


# 只認行首的「地址」:標籤一定在行首,內文提到地址(「通訊地址…」)的不是標籤。
_CN_ADDR_LABEL = re.compile(r"^地址")
# 標籤之後看幾行:逐欄序列化會在標籤與值之間夾進別欄的一兩行(印尼文標籤、職類)。
_LABEL_LOOKAHEAD = 3


def _is_cn_address(line: str) -> bool:
    return has_cjk(line) and bool(CN_ADDR.search(line))


def _clean_cn_address(line: str) -> str:
    return LABEL_WORDS.sub("", line).lstrip(":： ").strip()


def _labelled_cn_address(seg: list[str]) -> str:
    """雇主區塊內「地址」標籤那行(含)起幾行內的第一個中文地址;沒有就空字串。"""
    for i, l in enumerate(seg):
        if not _CN_ADDR_LABEL.search(l):
            continue
        for cand in seg[i:i + 1 + _LABEL_LOOKAHEAD]:
            if _is_cn_address(cand):
                return _clean_cn_address(cand)
    return ""


def _name_continuation(cand: str, nxt: str) -> str:
    """中文名斷行時,下一行要接上的那段;不是斷行回空字串。

    只在「名字裡有沒閉合的左括號,而下一行是短的中文、帶著右括號」時才接:機構全名
    長,Vision 會斷成「…長照機構(負責人姓」+「名)」(33703)。不接的話只剩第一行,
    _balance_brackets 再補出一個假的右括號,變成看起來正常的「…機構(王)」。
    條件刻意窄:家庭類的名字後面接的是別的欄位,不能被吃進來。
    """
    if cand.count("(") + cand.count("（") <= cand.count(")") + cand.count("）"):
        return ""
    s = nxt.strip()
    if (len(s) <= 12 and has_cjk(s) and re.search(r"[)）]", s)
            and not re.search(r"[:：;；\d]", s) and not LABEL_WORDS.search(s)):
        return s
    return ""


def _is_name_like(s: str) -> bool:
    """像不像「雇主名稱」——排除樣板字與帶門牌號的地址殘骸。

    不限字數也不限純中文:雇主可以是公司(見 CONTEXT.md),公司名可長可帶括號。
    改以「含阿拉伯數字」排除地址:門牌號一定有數字,人名與公司名都不會有。
    這條是掃整個區塊之後才需要的——窗口放寬後,縣市被 OCR 毀掉而 CN_ADDR 抓不到
    的地址殘行(「新竹騒麒西鎮大同里8鄰水坑2之8號」)會有機會頂上來當名字。
    """
    return not CN_BOILERPLATE.search(s) and not re.search(r"\d", s)


def _en_syllables(name_en: str) -> int:
    """英文姓名的音節段數:以空白/逗號/連字號切,SONG, MING-FANG → 3。"""
    return len([p for p in re.split(r"[\s,\-]+", name_en.strip()) if p])


def _name_doubt(name_cn: str, name_en: str,
                template: Template = Template.家庭類) -> str:
    """中文名的疑慮。措辭只陳述事實,不推斷成因(同 _address_doubt 的規矩)。

    中文名的可靠度遠低於英文名:紅章蓋的是中文那一欄,羅馬拼音在另一欄反而倖存。
    實測 21 份契約,可比對的 13 筆裡有 7 筆中文字數與英譯音節數對不上——涵蓋
    樣板字、雜訊、重複字、以及最危險的「截斷」(「明芳」少了姓「宋」、「彭武」
    少了第三字),而截斷後的名字看起來完全像個正常的名字,人核對時滑過去就過了。

    這是**結構性計數**不是音譯比對:ADR-0003 當初排除名稱是因為威妥瑪／漢語／
    通用拼音並存、無權威對照表,那個理由對「數數量」不成立。代價是同字數的錯字
    抓不到(「洪清廷」vs HUNG YAN TING 都是 3),那要真的比對音譯才行,不做。

    空值一律只寫「未擷取到」,不猜原因:32510 的「姜信安」被紅章壓住讀不出來,
    32503 的契約本身就只填羅馬拼音——兩者在 OCR 文字上一模一樣,分不出來。
    寫成「契約未載中文名」會誤導人「不必去翻契約」,而那正好是錯的那一半。
    """
    if not name_cn:
        return "未擷取到中文名"
    # 機構類、工廠類的雇主名是「名稱(負責人)」,字數與音節數沒有對應關係(ADR-0004;
    # 判定方式見 ADR-0010:看文件的契約樣板,不看英文名有沒有 Co/Ltd)。
    if not name_en or template.is_organisation:
        return ""
    n_cn = len(CJK.findall(name_cn))
    n_en = _en_syllables(name_en)
    if n_cn != n_en:
        return f"中文 {n_cn} 字與英譯 {n_en} 音節不符"
    return ""


# 切結書頁的雇主姓名欄。兩種版面都要吃:
#   中英分行     「中華民國雇主:張鈺珠」 + 另一行「Majikan R.O.C: ZHANG YU ZHU」
#   中英同行斜線 「中華民國雇主/Majikan R.O.C: 姜信安/JIANG SIN AN」
# 後者是 32510 的實況,兩個標籤之間隔了 14 個字元 —— 一開始寫死「標籤後 8 字內」
# 就是因此抓空,而不是 Vision 沒讀到。
_PLEDGE_LABEL = re.compile(r"中\s*華\s*民\s*國\s*雇\s*主[^\n]{0,24}?[::：﹕]\s*([^\n]{1,30})")
# 值的結束處:斜線(接英譯)、括號(簽章註記)、拉丁字母(接英文標籤)、或下一個欄名。
_PLEDGE_CUT = re.compile(r"[/／(（]|[A-Za-z]|負責人|簽章|日期|Tanggal")


def _pledge_name(text: str) -> str:
    """從切結書頁的 Vision 全文取中文雇主名;取不到回空字串。

    刻意**不**用同頁的「負責人或代表人簽章」那一行當來源,雖然它也印著名字:
      - 32510 那一行只讀到「信安」,少了姓 —— 手寫簽章的辨識率遠低於印刷欄位
      - 公司型雇主的負責人不是雇主本身,兩者根本不是同一個人
    """
    m = _PLEDGE_LABEL.search(text)
    if not m:
        return ""
    val = m.group(1)
    cut = _PLEDGE_CUT.search(val)
    if cut:
        val = val[:cut.start()]
    val = re.sub(r"\s+", "", val)
    return val if CJK.search(val) and _is_name_like(val) else ""


def rescue_name_from_pledge(pages: PageIdentification, vision: VisionReader
                            ) -> tuple[str, str]:
    """契約頁抽不到中文名時,改從切結書頁取。回傳 (名字, 圖檔名),救不回回 ("", "");
    Vision 呼叫失敗往上拋(VisionCallFailed),由 _apply_pledge_rescue 寫進 L 欄。

    **用 Vision 而不是本機 Tesseract**,這是這條管道能不能用的關鍵。同一格實測:

        引擎                     32572      32574
        Vision                   張鈺珠 ✔   鍾滄榮 ✔
        Tesseract chi_tra 紅通道  張鈺珠 ✔   鍾濃榮 ✘
        Tesseract chi_tra 去紅章  整行消失 ✘  鍾澹榮 ✘

    同一格、同一個語言包,換個前處理就讀出三個不同的字(滄/濃/澹)。漢字正是兩個
    引擎差距最大的地方,而契約頁那邊的中文名本來就是 Vision 讀的 —— 拿弱引擎去
    補強引擎沒有意義。代價是每次救援 +1 次 Vision,而救援只在中文名為空時觸發
    (上一批 21 份會觸發 4 次)。
    """
    for page in pages.pledge_name_pages():
        # Vision 呼叫失敗就往上拋、不換下一頁再打:多半是服務那一端,換一張也一樣
        # (ADR-0015);沒問到要寫在 L 欄,由 _apply_pledge_rescue 接。其他例外只是
        # 少一格值,不該連契約頁讀到的 H~O 一起變成擷取失敗(ADR-0006)。
        try:
            got = _pledge_name(vision.read_document(page.image_bytes))
        except (VisionCallFailed, *IDENTITY_REFUSED):
            raise
        except Exception as e:
            logger.warning(f"  ⚠ 切結書頁讀取失敗({page.name}):{e!r}")
            return "", ""
        if got:
            return got, page.name
    return "", ""


# 門牌號:中文取「N號」(含「N之M號」,以及直接寫成「N-M號」的,如 33703),
# 英文取「No. N」(含「N-M」)。
_HOUSE_NO_CN = re.compile(r"(\d+(?:\s*[之-]\s*\d+)*)\s*號")
_HOUSE_NO_EN = re.compile(r"\bNo\.?\s*(\d+(?:\s*-\s*\d+)*)", re.I)


def _house_number_doubt(addr_cn: str, addr_en: str) -> str:
    """中文與英文地址的門牌號不一致即標示。

    門牌號是**阿拉伯數字**,沒有拼音變體的問題——這正是英文地址整體不做判定
    (Panshih/Panshi 分不出「拼法不同」與「讀錯」)卻仍能拿門牌號來對的原因。
    與名稱的字數/音節數檢查同一個性質:結構性比對,不是語意判斷。

    32510 的中文門牌被讀壞(圖上是 62 號,OCR 讀成 7 號),不比對就是一格看不見的
    錯值,正是 ADR-0003 要防的事。

    「N之M號」與「No. N-M」是同一件事的兩種寫法,比對前統一成 N-M,
    否則 32509 的「8之15之2號」對「No.8-15-2」會被誤報成不符。
    """
    a = _HOUSE_NO_CN.search(addr_cn or "")
    b = _HOUSE_NO_EN.search(addr_en or "")
    if not (a and b):
        return ""
    norm = lambda s: re.sub(r"\s", "", s).replace("之", "-")   # noqa: E731
    cn, en = norm(a.group(1)), norm(b.group(1))
    return "" if cn == en else f"門牌號中英不符({cn}／{en})"


_NAME_CN = "雇主名稱_中"
_ADDR_CN = "地址_中"
# 郵遞區號留空的成因冠這一格(#29、#78)。
_ZIP = "郵遞區號"
_NAME_MISSING = Doubt(_NAME_CN, "未擷取到中文名")
# 沒問到 ≠ 救不回:兩條並列,審查的人知道重跑也許能補回來(ADR-0015)。
_PLEDGE_VISION_FAILED = Doubt(_NAME_CN, "切結書頁 Vision 呼叫失敗")

def _doubts(field: str, fact: str) -> tuple[Doubt, ...]:
    """有事實才成一條疑慮;空字串代表沒有。"""
    return (Doubt(field, fact),) if fact else ()


class CheckedAddress(NamedTuple):
    """中文地址核對的結果:郵遞區號與地址疑慮(依 L 欄順序)。"""
    zip: str                       # 郵遞區號
    doubts: tuple[Doubt, ...] = ()


def _check_address(address: EmployerAddress) -> CheckedAddress:
    """郵遞區號判定交給地址庫,這裡只做三件事:空輸入的事實、把地址庫給的兩類事實
    冠上欄名、門牌號比對。順序:地址_中 的幾條在前,郵遞區號留空的成因在後(#78)。

    英文行(讀不到時用 en_hint)是餵給英文行錨定的**輸入**,不改寫 地址_英:英文欄
    純粹是 OCR 讀到什麼就是什麼。縣市區表讀不到時地址庫會拋出 → 擷取失敗。
    """
    if not address.cn.strip():
        return CheckedAddress("", _doubts(_ADDR_CN, "中文地址未擷取到"))
    zip_result = determine_zip(address.cn, address.en_anchor)
    return CheckedAddress(zip_result.zip, (
        *_doubts(_ADDR_CN, zip_result.city_mismatch),
        *_doubts(_ADDR_CN, _house_number_doubt(address.cn, address.en)),
        *_doubts(_ZIP, zip_result.zip_blank_reason),
    ))


def extract_employer_from_docx(pages: PageIdentification, vision: VisionReader,
                               deink: bool = True,
                               roi: tuple | None = EMPLOYER_ROI,
                               crop_dir: str = "") -> EmployerExtraction:
    """端到端:認頁結果的契約頁 → ROI 裁切 → 去紅章 → Vision → 擷取雇主欄位。

    crop_dir:非空時,把「去紅章前」的 ROI 裁切圖存到該資料夾
      ({docx主檔名}_{圖檔名}),保留紅章原貌供人工複查 H~O 欄位值。
      **每份 docx 都會存**:找不到契約頁時存「最高分候選頁」(全零分則退
      image2 慣例位置),欄位留空但截圖必在,人工審核不會沒圖可看。

    來源另含實際採用(或候選)的圖檔名、已存檔的 ROI 截圖檔名(未存檔則為空)、
    Vision 全文(未送 Vision 時為空字串)。
    """
    from pathlib import Path

    contract = pages.contract_page()
    if contract is None:
        return EmployerExtraction(ExtractionStatus.docx無內嵌圖)
    name, found = contract.page.name, contract.found

    content = contract.page.image_bytes
    if roi:
        content = crop_png(content, roi)
    crop_name = ""
    if crop_dir:
        d = Path(crop_dir)
        d.mkdir(parents=True, exist_ok=True)
        crop_name = f"{Path(pages.docx_path).stem}_{name}"
        (d / crop_name).write_bytes(content)

    if not found:
        # 特徵不足不送 Vision(頁面身分不明,抽出的欄位不可信),只留截圖。切結書頁
        # 備援照跑:它讀另一頁、靠自己的標籤,契約頁找不到碰不到它。
        return _apply_pledge_rescue(EmployerExtraction(
            ExtractionStatus.找不到契約頁, source=Source(image=name, crop=crop_name),
            detail=f"特徵字命中不足,最高分 {contract.page.contract_score};已存候選頁截圖"),
            pages, vision)

    if deink:
        content = deink_red_stamp(content)
    # 全文一併傳出:它是這條流程唯一花錢買來的東西,而每一次解析修正都要拿它
    # 當素材。丟掉的話,改一次名稱/地址的抽取規則就得再跑一輪 Vision 才驗得了。
    try:
        text = vision.read_document(content)
    except VisionCallFailed as e:
        # 沒問到 ≠ 擷取失敗(程式或檔案的問題):自己一種狀況,切結書頁也不跑(ADR-0015)。
        logger.warning(f"  ⚠ {Path(pages.docx_path).name} 契約頁 {e}")
        return EmployerExtraction(ExtractionStatus.契約頁Vision呼叫失敗,
                                  source=Source(image=name, crop=crop_name), detail=str(e))
    result = extract_employer_fields(text)
    if not result.values.雇主名稱_中:
        result = _apply_pledge_rescue(result, pages, vision)
    return replace(result, source=replace(result.source, image=name, crop=crop_name, text=text))


def _apply_pledge_rescue(result: EmployerExtraction, pages: PageIdentification,
                         vision: VisionReader) -> EmployerExtraction:
    """切結書頁救回中文名 → 回傳填了中文名、名稱疑慮換過的結果;救不回來原樣回傳。

    只救中文名,不動英文名:英文名本來就是可靠的那一欄(ADR-0004,紅章蓋的是中文
    那一欄),而切結書頁的英文反而讀壞過 —— 32503 讀成 `FU BIN ANI FU`。拿它去
    覆蓋契約頁的英文名是負向交換。

    救不回來時什麼都不改:H 欄維持空、L 欄維持「H:未擷取到中文名」。這一格的
    兩種空值成因(紅章壓死 vs 契約本身只填拼音)從文字上仍然分不出來,ADR-0004
    那條「不猜原因」不因為多了一條管道而改變 —— 32503 正是救不回來的那一半。
    """
    from pathlib import Path

    try:
        got, page = rescue_name_from_pledge(pages, vision)
    except VisionCallFailed as e:
        logger.warning(f"  ⚠ 切結書頁 {e}({Path(pages.docx_path).name})")
        # 「未擷取到中文名」仍然是真的:保留,緊接著並列「沒問到」;沒有那一條
        # (整份沒擷取)就只加「沒問到」。
        doubts = result.doubts
        if _NAME_MISSING in doubts:
            i = doubts.index(_NAME_MISSING) + 1
            doubts = doubts[:i] + (_PLEDGE_VISION_FAILED,) + doubts[i:]
        else:
            doubts += (_PLEDGE_VISION_FAILED,)
        return replace(result, doubts=doubts)
    if not got:
        logger.info(f"  切結書頁未取得中文名:{Path(pages.docx_path).name}")
        return result
    count = _name_doubt(got, result.values.雇主名稱_英, result.source.template)
    rescued = (*_doubts(_NAME_CN, f"中文名取自切結書頁({page})"), *_doubts(_NAME_CN, count))
    # 「未擷取到中文名」已經變成假的:原位換成救援這兩條,其他疑慮不動。
    doubts = _at_missing_name(result.doubts, rescued)
    logger.info(f"  ★ 切結書頁救回中文名:{got}({Path(pages.docx_path).name}/{page})")
    return replace(result, values=replace(result.values, 雇主名稱_中=got), doubts=doubts)


def _at_missing_name(doubts: tuple[Doubt, ...],
                     replacement: tuple[Doubt, ...]) -> tuple[Doubt, ...]:
    """「未擷取到中文名」那一條原位換成 replacement;沒有那一條就接在最後。"""
    if _NAME_MISSING not in doubts:
        return doubts + replacement
    i = doubts.index(_NAME_MISSING)
    return doubts[:i] + replacement + doubts[i + 1:]


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="雇主資料擷取(契約頁偵測 + ROI + 去紅章)")
    ap.add_argument("path", help=".docx 檔路徑")
    ap.add_argument("--full", action="store_true", help="不裁 ROI,對整張圖處理")
    args = ap.parse_args()

    # 自動找契約頁 → ROI → 去紅章 → Vision
    from page_identification import PageIdentifier
    from vision_reader import GoogleVisionReader

    res = extract_employer_from_docx(PageIdentifier().identify(args.path), GoogleVisionReader(),
                                     deink=True,
                                     roi=None if args.full else EMPLOYER_ROI)
    print(f"採用契約頁:{res.source.image or '(無)'}  {res.status.value} {res.detail}")
    for k in (*VALUE_KEYS, "疑慮標示"):
        print(f"  {k}: {res.cell(k)!r}")
