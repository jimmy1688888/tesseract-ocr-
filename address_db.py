# -*- coding: utf-8 -*-
"""台灣地址:異體折疊與郵遞區號判定(縣市區表 data/tw_districts.json)。

對外只有兩樣,用途不同,別混用:
  - fold_variants(s)            → 比對用中間字串(會把「臺」變「台」,不可輸出)
  - determine_zip(cn, en_text)  → 郵遞區號判定:郵遞區號、判出的縣市與兩類事實

本模組是「台灣文字正規化」的唯一擁有者:臺/台、鍾/鐘、全半形、段號寫法
一律在 fold_variants 收斂。permit_lookup 反查名冊時比對機構地址／名稱,
也呼叫同一個函式,不另寫一套(見 docs/adr/0001)。

只到「區」這一層,不收路名(ADR-0016):路名清單持續在長、官方庫也有漏收,
維護成本高;縣市區幾乎不變,郵遞區號也只需要到區。

import 時不讀任何檔(ADR-0001):縣市區表第一次判定時才載入並快取。
表在 repo 裡,讀不到就讓它拋出 —— 那是程式或安裝壞了,不是「這份查不到」。
"""
from __future__ import annotations

import difflib
import json
import re
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import NamedTuple

_TABLE_PATH = Path(__file__).with_name("data") / "tw_districts.json"

# 縣市 / 行政區 尾字(用於判斷是否需去尾字做寬鬆比對)
_CITY_SUFFIX = "市縣"
_AREA_SUFFIX = "區鄉鎮市"
# 區名去尾字後至少要這麼多字才拿來比:單字的「北」「東」會配到「北大路」「東門街」。
_AREA_CORE_MIN_LEN = 2
# 縣市名至少這麼長才剝「去掉首字的殘留」:兩字的殘留只剩一字,會誤剝別的字。
_CITY_RESIDUE_MIN_LEN = 3

# 模糊比對的門檻與範圍。縣市模糊只看行首:縣市慣例寫在最前面,看整行會被路名帶偏。
_CITY_FUZZY_HEAD = 5
_CITY_FUZZY_CUTOFF = 0.5
_AREA_FUZZY_CUTOFF = 0.6

# 縣市命中層級:1 完整名、2 去尾字、3 模糊。只有 1、2 算精確。
_TIER_FULL, _TIER_NO_SUFFIX, _TIER_FUZZY = 1, 2, 3

# 郵遞區號留空的成因(三條最多一條,講最上游卡住的那一關)。只陳述事實:
# 這條訊息自己可能是錯的,寫成因或建議會誘導人做錯事(ADR-0003)。
_CITY_UNMATCHED = "縣市未比對到官方資料庫"
_CITY_FUZZY = "縣市僅模糊比對命中,郵遞區號一併未採用"
_DISTRICT_UNDETERMINED = "行政區未判定"

_CN_NUM = {c: i for i, c in enumerate("零一二三四五六七八九", 0)}


class ZipDetermination(NamedTuple):
    """郵遞區號判定的結果。事實不冠欄名 —— 地址庫不知道雇主欄位。

    zip 只在可信時才有(寧空勿錯);留空時 zip_blank_reason 講卡在哪一關。
    city 是判出的中文縣市,照表上的官方寫法(正體「臺」)。
    """
    zip: str = ""
    city: str = ""
    zip_blank_reason: str = ""
    city_mismatch: str = ""


def _seg_to_arabic(seg: str) -> str:
    """段號轉阿拉伯:'二'→'2'、'十'→'10'、'十二'→'12'。純阿拉伯原樣回傳。"""
    if seg.isdigit():
        return seg
    if seg == "十":
        return "10"
    if "十" in seg:                       # 十X / X十 / X十Y
        a, _, b = seg.partition("十")
        tens = _CN_NUM.get(a, 1) if a else 1
        ones = _CN_NUM.get(b, 0) if b else 0
        return str(tens * 10 + ones)
    return str(_CN_NUM.get(seg, seg))


def fold_variants(s: str) -> str:
    """台灣地址／機構名稱的「異體折疊」:把同一個字的不同寫法收斂成一種。

    NFKC(全形→半形) + 臺→台 + 鍾→鐘 + 去空白 + 段號統一阿拉伯。
    比對前一律過這關。鍾/鐘為常見異體互換(官方「鍾山新村」vs OCR/慣用
    「鐘山新村」);段號統一是因契約慣用國字(「一段」)而名冊國字/阿拉伯各半,
    不統一則子字串精確比對必漏接。

    ⚠ 這是**比對用**的中間字串,不可輸出 —— 它會把「臺」寫成「台」。

    公開的原因:permit_lookup 反查名冊時要用同一套折疊規則比對機構地址／
    名稱,否則名冊的「臺北市…1段」與契約 OCR 的「台北市…一段」會被算成
    不相似(名冊地址 38% 含「臺」,而契約印的是「台」)。見 ADR-0001。
    """
    s = unicodedata.normalize("NFKC", s or "").replace("臺", "台").replace("鍾", "鐘")
    s = re.sub(r"([0-9一二三四五六七八九十]+)段",
               lambda m: _seg_to_arabic(m.group(1)) + "段", s)
    return re.sub(r"\s+", "", s)


class _Area(NamedTuple):
    name: str
    eng: str
    zip: str


class _City(NamedTuple):
    folded: str
    name: str
    eng: str
    areas: dict[str, _Area]          # {折疊後的區名: 區}


@lru_cache(maxsize=1)
def _load() -> tuple[_City, ...]:
    """載入縣市區表。讀不到或格式壞了就拋出(見模組說明)。"""
    data = json.loads(_TABLE_PATH.read_text(encoding="utf-8"))
    return tuple(
        _City(fold_variants(c["CityName"]), c["CityName"], c["CityEngName"],
              {fold_variants(a["AreaName"]): _Area(a["AreaName"], a["AreaEngName"], a["ZipCode"])
               for a in c["AreaList"]})
        for c in data)


def _fuzzy_best(query: str, items, key, cutoff: float):
    """difflib 模糊比對,回傳最像的那一個;低於 cutoff 回 None。"""
    best_score, best = 0.0, None
    for it in items:
        score = difflib.SequenceMatcher(None, query, key(it)).ratio()
        if score > best_score:
            best_score, best = score, it
    return best if best_score >= cutoff else None


def _match_city(folded_line: str) -> tuple[_City, int] | None:
    """縣市比對:先完整縣市名精確含(解嘉義縣/市同名),再去尾字,最後模糊。"""
    cities = _load()
    for c in cities:
        if c.folded in folded_line:
            return c, _TIER_FULL
    for c in cities:
        core = _strip_suffix(c.folded, _CITY_SUFFIX)
        if core and core in folded_line:
            return c, _TIER_NO_SUFFIX
    hit = _fuzzy_best(folded_line[:_CITY_FUZZY_HEAD], cities, lambda c: c.folded,
                      _CITY_FUZZY_CUTOFF)
    return (hit, _TIER_FUZZY) if hit else None


def _strip_suffix(name: str, suffixes: str) -> str:
    """去掉尾字(缺「市/縣」「區/鄉」時的寬鬆比對用)。"""
    return name[:-1] if name[-1] in suffixes else name


def _longest_contained(text: str, keyed: dict) -> object | None:
    """keyed 裡被 text 包含、鍵最長的那一個。取最長:「安南區」包含「南區」。"""
    hits = [k for k in keyed if k and k in text]
    return keyed[max(hits, key=len)] if hits else None


def _match_area(body: str, areas: dict[str, _Area]) -> _Area | None:
    """行政區比對:完整區名精確含 → 去尾字(至少兩字)→ 模糊,精確含一律取最長命中。"""
    hit = _longest_contained(body, areas)
    if hit:
        return hit
    cores = {}
    for an, area in areas.items():
        core = _strip_suffix(an, _AREA_SUFFIX)
        if len(core) >= _AREA_CORE_MIN_LEN:
            cores.setdefault(core, area)
    hit = _longest_contained(body, cores)
    if hit:
        return hit
    fuzzy = _fuzzy_best(body, areas, lambda an: an, _AREA_FUZZY_CUTOFF)
    return areas[fuzzy] if fuzzy else None


def _strip_city(folded_line: str, city: _City) -> str:
    """剝掉縣市名再配區:縣市名自己會餵進去尾字階段 ——「桃園區」去尾剩「桃園」,
    而「桃園市…」裡就有這兩字,沒寫區的地址就被安上桃園區(32538)。同名的縣治
    (宜蘭縣/宜蘭市)不受影響:剝掉的是縣市名,區名還在。"""
    body = folded_line.replace(city.folded, "", 1)
    if len(city.folded) >= _CITY_RESIDUE_MIN_LEN:
        body = body.replace(city.folded[1:], "", 1)   # 縣市首字被印章蓋掉的殘留(「雄市」)
    return body


def _area_in_lines(lines: list[str], city: _City) -> _Area | None:
    """在指定縣市下,第一個配得到區的中文行的那個區。"""
    for line in lines:
        area = _match_area(_strip_city(fold_variants(line), city), city.areas)
        if area:
            return area
    return None


def _match_en(en_text: str) -> tuple[_City, _Area | None] | None:
    """用英文地址行反查縣市/區:只做官方英文名精確含、取最長命中(Taipei City
    不誤配 New Taipei City),不做模糊 —— 所以錨出來的縣市算精確。"""
    text = " ".join(en_text.split()).lower()
    if not text:
        return None
    hits = [c for c in _load() if c.eng.lower() in text]
    if not hits:
        return None
    city = max(hits, key=lambda c: len(c.eng))
    areas = [a for a in city.areas.values() if a.eng.lower() in text]
    return city, (max(areas, key=lambda a: len(a.eng)) if areas else None)


class _Located(NamedTuple):
    city: _City
    tier: int
    area: _Area | None


def _locate_in_chinese(lines: list[str]) -> _Located | None:
    """逐行找縣市與區;多行時取第一個配得到區的行,都配不到區就取第一個配到縣市的行。"""
    fallback = None
    for line in lines:
        folded = fold_variants(line)
        hit = _match_city(folded)
        if not hit:
            continue
        city, tier = hit
        area = _match_area(_strip_city(folded, city), city.areas)
        if area:
            return _Located(city, tier, area)
        fallback = fallback or _Located(city, tier, None)
    return fallback


def _anchor_by_english(found: _Located | None, anchor: tuple[_City, _Area | None] | None,
                       lines: list[str]) -> _Located | None:
    """英文行錨定:紅章常蓋毀中文行首的縣市,英文行尾的縣市倖存。

    - 中文縣市比不到、或只是模糊命中 → 改以英文行的縣市為準(算精確),區取英文行的,
      英文行沒有區就回頭從中文行配。
    - 中文縣市精確但比不到區,英文行同縣市且有區 → 補上區。
    英文行只是輸入,不改寫英文地址欄。
    """
    if not anchor:
        return found
    city, area = anchor
    if found is None or found.tier == _TIER_FUZZY:
        return _Located(city, _TIER_FULL, area or _area_in_lines(lines, city))
    if found.area is None and area and found.city == city:
        return found._replace(area=area)
    return found


def _city_mismatch(city: _City, anchor: tuple[_City, _Area | None] | None) -> str:
    """判出的中文縣市 ≠ 英文行精確包含的官方英文縣市名 → 縣市中英不符。
    英文側配不到就不標(寧可漏報);不比到區(區名拼法變體多)。專抓「挑到別人的
    地址」:33704 仲介的中文地址夾進雇主區塊被挑中,英文地址卻是雇主的。"""
    if not anchor or anchor[0] == city:
        return ""
    return f"縣市中英不符({city.name}／{anchor[0].eng})"


def determine_zip(cn_text: str, en_text: str = "") -> ZipDetermination:
    """郵遞區號判定:從中文地址行(可多行)判出縣市與區,回填郵遞區號。

    en_text 是同一地址的英文行,當英文行錨定的輸入。寧空勿錯:縣市精確命中、區也
    命中才填;縣市非精確命中一律不填 —— 配錯時會安靜地換成另一個縣市,而區名
    (中山區、中正區)全台通用,錯誤縣市底下照樣配得到,沒有其他佐證可以翻案。
    空的中文地址算縣市未比對到(呼叫端該自己講「未擷取到」)。
    縣市區表讀不到時拋出。
    """
    lines = [line for line in re.split(r"[\r\n]+", cn_text or "") if line.strip()]
    if not lines:
        return ZipDetermination(zip_blank_reason=_CITY_UNMATCHED)
    anchor = _match_en(en_text) if en_text else None
    located = _anchor_by_english(_locate_in_chinese(lines), anchor, lines)
    if located is None:
        return ZipDetermination(zip_blank_reason=_CITY_UNMATCHED)
    mismatch = _city_mismatch(located.city, anchor)
    if located.tier == _TIER_FUZZY:
        return ZipDetermination("", located.city.name, _CITY_FUZZY, mismatch)
    if located.area is None:
        return ZipDetermination("", located.city.name, _DISTRICT_UNDETERMINED, mismatch)
    return ZipDetermination(located.area.zip, located.city.name, "", mismatch)
