# -*- coding: utf-8 -*-
"""台仲區塊:次頁上台灣仲介那一欄 → 電話／名稱／地址(CONTEXT「台仲區塊」,#79)。

  agency_block_in(text)                     Vision 全文 → AgencyBlock(純函式)
  agency_block_from_next_page(pages, vision)  認頁結果的次頁 → 整頁送 Vision 一次 → AgencyBlock

只供反查救援(取值後查名冊回推許可證),不直接寫進 E~G。
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING, NamedTuple

from contract_text import CJK, CN_ADDR, FAX_SPLIT, LABEL_WORDS, has_cjk
from pixels import NEXT_PAGE_DEINK, preprocess, to_png
from tw_phone import phones_in

if TYPE_CHECKING:
    from page_identification import PageIdentification
    from vision_reader import VisionReader

# 台灣/印尼仲介區塊標記。台印電話都以 0 開頭、格式會重疊,無法靠純格式區分,
# 故用「標記錨定」切出台仲區塊。台仲區塊在次頁左右不定(32345 在右、32324 在左),
# 不能靠幾何裁半;改為整頁 OCR(仍只 1 次 Vision),以『台灣仲介』欄位標籤錨定起點、
# 到其後的仲介標籤或印尼電話標籤(Telp/Telepon)為止,結構性排除印尼電話。
_TW_AGENCY_LABEL = re.compile(r"[台臺]灣仲介|Agen(?:cy|si)\s*Taiwan", re.I)
# 台仲標籤被紅章毀掉時的後備錨(僅在嚴格標籤完全找不到時才用)。
# 32426 實例:『台灣仲介 Agensi Taiwan:』被章蓋成『tensi Taiwan:』——中文三字與
# Agen 全失,嚴格式對不上就整份放棄,連同頁上完好的『電話:04-23263526』一起丟掉。
# 放寬的同時必須擋住同頁其他含 Taiwan 的行,故要求兩個條件同時成立:
#   ① 保留 Agensi/Agency 的殘骸(gen/nsi/si/cy 其一)
#   ② Taiwan 後(容一個右括號,如『(Agency Taiwan):』)緊跟冒號——標籤才有,
#      同頁的『TAIWAN BLVD NORTH』『TAIWAN(R.O.C.)』都沒有
# 再加上 _is_agency_label_line 的「短行+非句號結尾」,可濾掉條文裡的
# 『ke Instansi Pemerintah Taiwan yang…diverifikasi.』(長且以句號結尾)。
_TW_AGENCY_LABEL_LOOSE = re.compile(
    r"(?:gen|nsi|si|cy)\w*\s*Taiwan\s*[)）]?\s*[:：]", re.I)
_ID_AGENCY_LABEL = re.compile(r"印尼仲介|P3MI|Agen(?:cy|si)\s*Indonesia|Perwakilan", re.I)
# 印尼電話標籤(Telp/Telepon):台仲用「電話」、印尼用 Telp,作為區塊下界的補強
# (當印尼仲介欄位標籤本身 OCR 糊掉沒對上時,仍能在印尼電話行前收尾)。
_ID_PHONE_LABEL = re.compile(r"\bTelp(?:on|epon)?\b", re.I)
# 乙方標籤:台印仲介兩欄並排時,台仲標籤緊接印尼標籤、資料排在其後並與印尼交錯,
# 原定界圈不到資料 → 放寬到「乙方(PIHAK KEDUA)」或頁尾。
_PARTY_B_LABEL = re.compile(r"乙方|PIHAK\s*KEDUA", re.I)
# 起點到下界只有這麼幾行(含標籤行本身)= 標籤相鄰、圈不到資料,要放寬。
_ADJACENT_LABELS_SPAN = 2
# 印尼資料行特徵(印尼地址/機構詞/國際電話):放寬區塊後,用來把印尼行排除,
# 只留台灣仲介的名稱/地址/電話(台印在同段交錯時的特徵分離)。
_ID_LINE = re.compile(
    r"JL\.|JALAN|KELURAHAN|KECAMATAN|\bKEC\b|\bKOTA\b|PROVINSI|DESA|DUSUN|"
    r"PPTKIS|P3MI|Pengirim|Perwakilan|\bNTB\b|LOMBOK|MATARAM|JAKARTA|"
    r"\bTelp(?:on|epon)?\b|\+62", re.I)
# 真標籤短(甲/乙欄名);超過這個長度的是條文 prose。
_MAX_LABEL_LINE_LEN = 40

# 台仲名稱關鍵字(用來從區塊挑出「機構名稱」行);與標籤詞(台灣仲介公司)區隔。
_AGENCY_NAME_KW    = re.compile(r"(公司|人力|仲介|企業|顧問|管理|服務|股份|有限)")
_AGENCY_LABEL_WORD = re.compile(
    r"[台臺]灣仲介公司|[台臺]灣仲介|Agen(?:cy|si)\s*Taiwan", re.I)
_MIN_AGENCY_NAME_LEN = 4


class AgencyBlock(NamedTuple):
    """台仲區塊取到的值;全空(AgencyBlock())就是沒有台仲區塊。"""
    phones: tuple[str, ...] = ()
    name: str = ""
    address: str = ""


def agency_block_from_next_page(pages: PageIdentification, vision: VisionReader) -> AgencyBlock:
    """認頁結果的次頁 → 台仲區塊(整頁 1 次 Vision)。

    找不到契約頁(不猜)或契約頁是最後一張 → 沒有次頁,不花 Vision。
    Vision 呼叫失敗照常拋出,由反查救援決定怎麼記(ADR-0015)。
    """
    next_page = pages.next_page()
    if next_page is None:
        return AgencyBlock()
    # 次頁前處理後整頁送 Vision(台仲可能在右半,不裁切)。
    # 設定與契約頁不同(放大+中值、不抹白),理由見 pixels.NEXT_PAGE_DEINK。
    png = to_png(preprocess(next_page.image_bytes, NEXT_PAGE_DEINK))
    return agency_block_in(vision.read_document(png))


def agency_block_in(text: str) -> AgencyBlock:
    """次頁 Vision 全文 → 台仲區塊的電話／名稱／地址;錨不到 → AgencyBlock()(不猜)。"""
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    block = _locate(lines)
    if not block:
        return AgencyBlock()
    return AgencyBlock(_phones_in_block(block), _name_in_block(block), _address_in_block(block))


def _is_agency_label_line(line: str, pat: re.Pattern) -> bool:
    """是否為「仲介欄位標籤」行(表格欄名),而非內文 prose 誤配。
    真標籤短(甲/乙欄名,如『台灣仲介公司(Agency Taiwan):』),prose 是長句、以句號結尾
    (『…另一份由台灣仲介公司存查。』『oleh agency Taiwan dan…』)——以長度+非句號濾除。"""
    s = line.strip()
    return bool(pat.search(s)) and len(s) <= _MAX_LABEL_LINE_LEN and not s.endswith(("。", "．", "."))


def _first_label(lines: list[str], pat: re.Pattern) -> int | None:
    return next((i for i, l in enumerate(lines) if _is_agency_label_line(l, pat)), None)


def _locate(lines: list[str]) -> list[str]:
    """以『台灣仲介』欄位標籤錨定台仲區塊的行;錨不到 → 空清單。

    台印電話/名稱格式重疊,無法靠格式或幾何位置分,故以標籤(短、非句號)錨定起點
    → 到其後的仲介欄位標籤 / 印尼電話標籤(Telp)為止。台仲在左在右皆可。
    """
    start = _first_label(lines, _TW_AGENCY_LABEL)
    if start is None:
        # 嚴格標籤找不到 → 試殘缺標籤(紅章蓋掉『台灣仲介 Agen』只剩『tensi Taiwan:』)。
        start = _first_label(lines, _TW_AGENCY_LABEL_LOOSE)
    if start is None:
        return []                          # 兩種都錨不到 → 不猜,交人工(避免誤抓印尼資料)
    end = next((i for i in range(start + 1, len(lines))
                if _is_agency_label_line(lines[i], _ID_AGENCY_LABEL)
                or _is_agency_label_line(lines[i], _TW_AGENCY_LABEL)
                or _ID_PHONE_LABEL.search(lines[i])), len(lines))
    # 台仲標籤與印尼標籤相鄰(兩欄並排版面)→ 原定界只圈到標籤行、圈不到資料。
    # 放寬到「乙方標籤 / 頁尾」的較大範圍,取值端再用 _ID_LINE 排除印尼行。
    if end - start <= _ADJACENT_LABELS_SPAN:
        end = next((i for i in range(start + 1, len(lines))
                    if _PARTY_B_LABEL.search(lines[i])), len(lines))
    return lines[start:end]


def _phones_in_block(block: list[str]) -> tuple[str, ...]:
    """區塊內抽台灣電話:排除印尼行(交錯版面),切掉「傳真/Fax」後段;去重保序。
    台灣電話為 0 開頭格式,印尼 +62 本就不被 phones_in 抓,再加 _ID_LINE 雙保險。"""
    phones: dict[str, None] = {}
    for l in block:
        if _ID_LINE.search(l):
            continue
        head = FAX_SPLIT.split(l, maxsplit=1)[0]
        phones.update(dict.fromkeys(phones_in(head)))
    return tuple(phones)


def _clean_agency_name(s: str) -> str:
    """清掉台仲中文名前後黏到的英文/符號雜訊(英文名 CO.,LTD 斷行黏進來,
    如 'LTD(裕倉國際開發有限公司' → '裕倉國際開發有限公司')。
    掐頭到第一個中文字、去尾到最後一個中文字或右括號,保留中間的『(股)』等。
    無中文 → 空字串。反查用 _norm_name 會去標點,此清理主要讓輸出/比對更乾淨。"""
    m = CJK.search(s)
    if not m:
        return ""
    s = s[m.start():]
    tail = None
    for mm in re.finditer(r"[一-鿿）)]", s):
        tail = mm
    return s[:tail.end()] if tail else s


def _name_in_block(block: list[str]) -> str:
    """區塊內挑「台仲機構名稱」:標籤冒號後的值 or 含機構關鍵字的中文行;
    去掉標籤詞(台灣仲介公司/Agency Taiwan)與前後英文/符號雜訊,排除印尼行與地址行。"""
    for l in block:
        if _ID_LINE.search(l):             # 交錯版面:排除印尼機構/地址行
            continue
        core = re.sub(r".*[:：]", "", l).strip() or l   # 冒號後的值;無冒號取整行
        core = _clean_agency_name(_AGENCY_LABEL_WORD.sub("", core))
        if (core and _AGENCY_NAME_KW.search(core)
                and not CN_ADDR.search(core) and len(core) >= _MIN_AGENCY_NAME_LEN):
            return core
    return ""


def _address_in_block(block: list[str]) -> str:
    """區塊內挑「台仲地址」:去『地址/Alamat』標籤後,取含縣市+街道 pattern 的中文行;
    排除印尼行(交錯版面)。註:台仲地址若僅為英文,CN_ADDR 抓不到 → 回空(改靠名稱反查)。"""
    for l in block:
        if _ID_LINE.search(l):
            continue
        core = re.sub(r".*(地址|Alamat)\s*[:：]", "", l, flags=re.I)
        if has_cjk(core) and CN_ADDR.search(core):
            return LABEL_WORDS.sub("", core).lstrip(":： ").strip()
    return ""
