# -*- coding: utf-8 -*-
"""台灣地址標準化：以官方地址庫 (data/AllData.json) 校正 OCR 地址。

OCR 出來的地址常缺字、跨行、無英文、無郵遞區號。本模組把一段(可能雜訊的)
中文地址,逐層(縣市 → 行政區 → 路街 → 段/號樓)比對官方庫,還原成標準地址,
並附帶官方英文譯名與郵遞區號。

用法:
    from address_db import normalize_address
    r = normalize_address("宜蘭縣羅東鎮新群里新群一路16號有效")
    # r["address_cn"] == "宜蘭縣羅東鎮新群一路16號"
    # r["road_en"]    == "Xinqun 1st Rd."
    # r["zip"]        == "265"

設計重點(對應 OCR 常見失誤):
  - 臺↔台 異體字統一。
  - 縣市/行政區同名衝突(嘉義縣vs嘉義市、宜蘭縣vs宜蘭市):先比對「完整名稱」。
  - 路名比對「先鎖縣市/區、再比同區路」,避免全庫 4 萬條同名誤配。
  - 段號(N段)以 regex 直接覆蓋,不靠模糊比對(辛亥路7段 不會被配成 4段)。
  - 地址行缺「區」時,退回以「全縣市路名」比對。
"""
from __future__ import annotations

import difflib
import json
import re
import unicodedata
from functools import lru_cache
from pathlib import Path

_DB_PATH = Path(__file__).with_name("data") / "AllData.json"

# 縣市 / 行政區 尾字(用於判斷是否需去尾字做寬鬆比對)
_CITY_SUFFIX = "市縣"
_AREA_SUFFIX = "區鄉鎮市"


def _norm(s: str) -> str:
    """NFKC(全形→半形) + 臺→台 + 去空白。比對前一律過這關。"""
    s = unicodedata.normalize("NFKC", s or "").replace("臺", "台")
    return re.sub(r"\s+", "", s)


def _strip_seg(road_norm: str) -> str:
    """去掉路名尾端的『N段』,取得路名主幹(辛亥路1段 → 辛亥路)。"""
    return re.sub(r"\d+段$", "", road_norm)


@lru_cache(maxsize=1)
def _load() -> list:
    """載入並建索引:回傳 [(city_norm, city_raw, city_eng, areas)],
    areas = {area_norm: (area_raw, area_eng, zip, roads)},
    roads = [(road_norm, base_norm, road_raw, road_eng)] 。lru_cache 只載一次。"""
    data = json.loads(_DB_PATH.read_text(encoding="utf-8"))
    cities = []
    for c in data:
        areas = {}
        for a in c["AreaList"]:
            roads = []
            for r in a["RoadList"]:
                rn = _norm(r["RoadName"])
                roads.append((rn, _strip_seg(rn), r["RoadName"], r["RoadEngName"]))
            areas[_norm(a["AreaName"])] = (
                a["AreaName"], a["AreaEngName"], a["ZipCode"], roads,
            )
        cities.append((_norm(c["CityName"]), c["CityName"], c["CityEngName"], areas))
    return cities


def _best(query: str, items, key, cutoff: float):
    """對 items 做 difflib 模糊比對,回傳 (score, item);低於 cutoff 回 (score, None)。"""
    qn = _norm(query)
    best_s, best_it = 0.0, None
    for it in items:
        s = difflib.SequenceMatcher(None, qn, key(it)).ratio()
        if s > best_s:
            best_s, best_it = s, it
    return (best_s, best_it) if best_s >= cutoff else (best_s, None)


def _match_city(L: str):
    """縣市比對:先『完整縣市名』精確含(解嘉義縣/市同名),再去尾字寬鬆,最後模糊。"""
    cities = _load()
    for cn, raw, eng, areas in cities:            # 1. 完整縣市名
        if cn in L:
            return raw, eng, areas
    for cn, raw, eng, areas in cities:            # 2. 去尾字(缺「市/縣」時)
        core = cn[:-1] if cn[-1] in _CITY_SUFFIX else cn
        if core and core in L:
            return raw, eng, areas
    _, hit = _best(L[:5], cities, key=lambda x: x[0], cutoff=0.5)  # 3. 模糊
    return (hit[1], hit[2], hit[3]) if hit else None


def _match_area(L: str, areas: dict):
    """行政區比對:先『完整區名』精確含(解宜蘭縣→宜蘭市誤配),再去尾字,最後模糊。"""
    for an, v in areas.items():                   # 1. 完整區名(含 區/鎮/鄉/市)
        if an in L:
            return v
    for an, v in areas.items():                   # 2. 去尾字
        core = an[:-1] if an[-1] in _AREA_SUFFIX else an
        if core and len(core) >= 2 and core in L:
            return v
    items = [(k,) + v for k, v in areas.items()]  # 3. 模糊
    _, hit = _best(L, items, key=lambda x: x[0], cutoff=0.6)
    return (hit[1], hit[2], hit[3], hit[4]) if hit else None


# 段號只在數字(阿拉伯或中文一二三…)後確實接『段』時才擷取,
# 避免把「德南路201巷」的 201 誤當段號。中文數字會轉成阿拉伯以對上官方庫。
_ROAD_RE = re.compile(r"([一-鿿A-Za-z]{1,8}?[路街道大道])(?:\s*([0-9一二三四五六七八九十]+)\s*段)?")

_CN_NUM = {c: i for i, c in enumerate("零一二三四五六七八九", 0)}


def _seg_to_arabic(seg: str) -> str:
    """段號轉阿拉伯:'二'→'2'、'十'→'10'、'十二'→'12'。純阿拉伯原樣回傳。"""
    if seg is None:
        return None
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


def _match_road(L: str, roads: list, cutoff: float = 0.5):
    """路名比對。回傳 (road_raw, road_eng, score) 或 None。
    優先『含段號的完整路名精確配』,否則以路名主幹模糊配、再套回該段的官方英文。"""
    m = _ROAD_RE.search(L)
    if not m:
        return None
    road_core = _norm(m.group(1))                 # 例:辛亥路
    seg_num = _seg_to_arabic(m.group(2))           # 7 / 二→2 / 十二→12(可能為 None)
    # 1. 有段號 → 直接組出「辛亥路7段」做精確配,拿到正確段別英文
    if seg_num:
        want = f"{road_core}{seg_num}段"
        for rn, base, raw, eng in roads:
            if rn == want:
                return raw, eng, 1.0
    # 2. 以路名主幹模糊配(比對各路的去段主幹)
    best_s, best_it = 0.0, None
    for it in roads:
        s = difflib.SequenceMatcher(None, road_core, it[1]).ratio()
        if s > best_s:
            best_s, best_it = s, it
    if best_it is None or best_s < cutoff:
        return None
    base_hit = best_it[1]
    # 3. 主幹配到後,若原文有段號,優先回傳「該主幹的第 N 段」官方條目
    if seg_num:
        want = f"{base_hit}{seg_num}段"
        for rn, base, raw, eng in roads:
            if rn == want:
                return raw, eng, best_s
    return best_it[2], best_it[3], best_s


_TAIL_RE = re.compile(
    r"((?:\d+巷)?(?:\d+弄)?\d+(?:之\d+)?(?:[-–]\d+)?號(?:\d+樓)?(?:之\d+)?)"
)


def _extract_tail(line: str) -> str:
    """抓門牌尾巴(…巷…弄…號…樓)。以原始行(非正規化)抓,保留數字。"""
    m = _TAIL_RE.search(unicodedata.normalize("NFKC", line))
    return m.group(1) if m else ""


def normalize_address(text: str, *, road_cutoff: float = 0.5) -> dict:
    """把一段(可能雜訊/跨行)的中文地址標準化。

    參數:
        text: OCR 出的地址字串,可為單行或多行區塊。
        road_cutoff: 路名模糊比對門檻(0~1),越高越嚴。

    回傳 dict:
        matched(bool)、city/city_en、district/district_en、zip、
        road/road_en、detail(號樓)、address_cn、address_en、
        road_score(路名相似度,精確配為 1.0)。
        完全比不到縣市時 matched=False,其餘欄位為空。
    """
    empty = {
        "matched": False, "city": "", "city_en": "", "district": "",
        "district_en": "", "zip": "", "road": "", "road_en": "",
        "detail": "", "address_cn": "", "address_en": "", "road_score": 0.0,
    }
    if not text or not text.strip():
        return dict(empty)

    # 多行 → 逐行嘗試,取「能定位到縣市且路名分數最高」者
    lines = [l for l in re.split(r"[\r\n]+", text) if l.strip()]
    if not lines:
        lines = [text]

    best = None
    for line in lines:
        L = _norm(line)
        city = _match_city(L)
        if not city:
            continue
        city_raw, city_eng, areas = city
        area = _match_area(L, areas)
        if area:
            area_raw, area_eng, zipcode, roads = area
        else:
            area_raw = area_eng = zipcode = ""
            roads = [r for v in areas.values() for r in v[3]]  # 缺區 → 全縣市路名
        # 抓路名前,先把已定位的縣市/區從字串剝掉,避免路名 regex 從行首把
        # 「彰化縣和美鎮德南」整段吞成路名主幹 → 誤配。
        rest = L.replace(_norm(city_raw), "", 1)
        if area_raw:
            rest = rest.replace(_norm(area_raw), "", 1)
        road = _match_road(rest, roads, cutoff=road_cutoff)
        road_raw, road_eng, rscore = road if road else ("", "", 0.0)
        detail = _extract_tail(line)

        addr_cn = f"{city_raw}{area_raw}{road_raw}{detail}"
        en_parts = [p for p in (road_eng, area_eng, city_eng) if p]
        addr_en = ", ".join(en_parts)
        if detail and addr_en:
            # 英文門牌號取「N號」的號碼(而非 N巷/N弄);無「號」才退回開頭數字。
            num = re.search(r"(\d+(?:之\d+)?)號", detail) or re.match(r"(\d+(?:之\d+)?)", detail)
            if num:
                no = num.group(1).replace("之", "-")   # 1之9 → 1-9
                addr_en = f"No. {no}, {addr_en}"
            # 樓層:台灣官方英文置於最前,如「3F., No. 15, ...」;
            # 樓後的「之N」(增建戶)併入樓層,如「2樓之1」→「2F.-1」。
            flr = re.search(r"(\d+)樓(?:之(\d+))?", detail)
            if flr:
                f_en = f"{flr.group(1)}F." + (f"-{flr.group(2)}" if flr.group(2) else "")
                addr_en = f"{f_en}, {addr_en}"

        cand = {
            "matched": True, "city": city_raw, "city_en": city_eng,
            "district": area_raw, "district_en": area_eng, "zip": zipcode,
            "road": road_raw, "road_en": road_eng, "detail": detail,
            "address_cn": addr_cn, "address_en": addr_en,
            "road_score": round(rscore, 2),
        }
        # 評分:有區 +2、有路 +路分、有號樓 +0.5
        rank = (2 if area_raw else 0) + rscore + (0.5 if detail else 0)
        if best is None or rank > best[0]:
            best = (rank, cand)

    return best[1] if best else dict(empty)


if __name__ == "__main__":  # 簡易自測
    for t in [
        "宜蘭縣羅東鎮新群里新群一路16號有效",
        "台北市文山區辛亥路7段69巷15號3樓",
        "嘉義縣水上鄉寬士村崎子頭1之9號",
        "彰化縣和美鎮德南路201巷2號",
        "桃園市桃園區國聖二街25號1樓",
    ]:
        r = normalize_address(t)
        print(f"IN : {t}")
        print(f"OUT: {r['address_cn']} | {r['road_en']} | 郵遞 {r['zip']} | 路分 {r['road_score']}\n")
