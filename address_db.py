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
# 自建補充路名:政府開放資料未收錄的新路(如 長發一路/富聯路)。
# 不直接改 AllData.json(原檔重新下載會洗掉手改),補充檔小、進版控、跨機同步。
_CUSTOM_PATH = Path(__file__).with_name("data") / "custom_roads.json"

# 縣市 / 行政區 尾字(用於判斷是否需去尾字做寬鬆比對)
_CITY_SUFFIX = "市縣"
_AREA_SUFFIX = "區鄉鎮市"


def _norm(s: str) -> str:
    """NFKC(全形→半形) + 臺→台 + 鍾→鐘 + 去空白。比對前一律過這關。
       鍾/鐘為常見異體互換(官方庫「鍾山新村」vs OCR/慣用「鐘山新村」)。"""
    s = unicodedata.normalize("NFKC", s or "").replace("臺", "台").replace("鍾", "鐘")
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
    _merge_custom_roads(cities)
    return cities


def _merge_custom_roads(cities) -> None:
    """把自建補充路名(data/custom_roads.json)併入對應行政區的路名清單。
       檔案缺失/格式錯誤安靜跳過;已存在同名路(官方檔更新後收錄了)不重複加。"""
    try:
        entries = json.loads(_CUSTOM_PATH.read_text(encoding="utf-8"))
    except Exception:
        return
    for e in entries:
        try:
            city = next(c for c in cities if c[1] == e["CityName"])
            area = city[3].get(_norm(e["AreaName"]))
            if not area:
                continue
            roads = area[3]
            rn = _norm(e["RoadName"])
            if any(r[0] == rn for r in roads):
                continue
            roads.append((rn, _strip_seg(rn), e["RoadName"], e["RoadEngName"]))
        except (StopIteration, KeyError):
            continue


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
    """縣市比對:先『完整縣市名』精確含(解嘉義縣/市同名),再去尾字寬鬆,最後模糊。

    回傳 (raw, eng, areas, tier);tier=1 完整名、2 去尾字、3 模糊。
    tier 3 只看行首 5 字且門檻僅 0.5,行首被印章汙染時易錯配,
    呼叫端(normalize_address)據此決定是否改用英文行錨定。
    """
    cities = _load()
    for cn, raw, eng, areas in cities:            # 1. 完整縣市名
        if cn in L:
            return raw, eng, areas, 1
    for cn, raw, eng, areas in cities:            # 2. 去尾字(缺「市/縣」時)
        core = cn[:-1] if cn[-1] in _CITY_SUFFIX else cn
        if core and core in L:
            return raw, eng, areas, 2
    _, hit = _best(L[:5], cities, key=lambda x: x[0], cutoff=0.5)  # 3. 模糊
    return (hit[1], hit[2], hit[3], 3) if hit else None


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


def _match_city_area_en(en_text: str):
    """用英文地址行反查縣市/行政區。

    動機:印章多蓋在中文行首(縣市被毀成「中共 雄市…」),而英文地址的
    County/City 慣例在行尾,常倖存。只做「官方英文名精確含」不做模糊,
    並取**最長命中**(避免 Taipei City 誤配進 New Taipei City)。

    回傳 ((city_raw, city_eng, areas), area_v或None);縣市查無回 None。
    """
    L = " ".join(en_text.split()).lower()
    if not L:
        return None
    city_hit = None
    for _cn, raw, eng, areas in _load():
        if eng.lower() in L and (city_hit is None or len(eng) > len(city_hit[1])):
            city_hit = (raw, eng, areas)
    if not city_hit:
        return None
    area_hit = None
    for v in city_hit[2].values():
        if v[1].lower() in L and (area_hit is None or len(v[1]) > len(area_hit[1])):
            area_hit = v
    return city_hit, area_hit


# 段號只在數字(阿拉伯或中文一二三…)後確實接『段』時才擷取,
# 避免把「德南路201巷」的 201 誤當段號。中文數字會轉成阿拉伯以對上官方庫。
# 結尾字用群組交替「大道|路|街|道」,不可寫成字元類 [路街道大道]——
# 字元類會拆成 {路,街,道,大} 四個單字,「大」被當結尾害「大眾東路」
# 被切成「…大」(31620 教訓,台灣大道的段別也因此誤配)。
_ROAD_RE = re.compile(
    r"([一-鿿A-Za-z]{1,8}?(?:大道|路|街|道))(?:\s*([0-9一二三四五六七八九十]+)\s*段)?")

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


def _dir_before_suffix(name: str):
    """路名結尾(路/街/道/大道)前一字若為方向字(東西南北)則回傳之,否則 None。
       中山「北」路、拕子「南」街是不同於中山路/拕子街的實體道路。"""
    m = re.search(r"([東西南北])(?:大道|路|街|道)$", name)
    return m.group(1) if m else None


def _match_road(L: str, roads: list, cutoff: float = 0.5):
    """路名比對。回傳 (road_raw, road_eng, score) 或 None。
    優先『含段號的完整路名精確配』,否則以路名主幹模糊配、再套回該段的官方英文;
    regex 路徑失敗時,以官方路名『子字串精確含』收尾——鄉村地名型路名(枋子林、
    廣興)與巷型正式路名(后尾巷)沒有 路/街/道 後綴,regex 抓不到,但官方庫有收。"""
    m = _ROAD_RE.search(L)
    if m:
        road_core = _norm(m.group(1))              # 例:辛亥路
        seg_num = _seg_to_arabic(m.group(2))       # 7 / 二→2 / 十二→12(可能為 None)
        # 1. 有段號 → 直接組出「辛亥路7段」做精確配,拿到正確段別英文
        if seg_num:
            want = f"{road_core}{seg_num}段"
            for rn, base, raw, eng in roads:
                if rn == want:
                    return raw, eng, 1.0
        # 2. 以路名主幹模糊配(比對各路的去段主幹)。
        #    方向字防護:結尾前的東西南北必須一致——「大眾東路」不可
        #    模糊配到「大眾路/大眾北路」(不同實體道路,配錯比留空糟)。
        core_dir = _dir_before_suffix(road_core)
        best_s, best_it = 0.0, None
        for it in roads:
            if _dir_before_suffix(it[1]) != core_dir:
                continue
            s = difflib.SequenceMatcher(None, road_core, it[1]).ratio()
            if s <= best_s:
                continue
            # 主幹防護:候選路名主幹(去方向字/結尾字)須真的出現在原文,
            # 除非整體相似度極高——「大眾東路」不可配到「大吉東路」
            # (方向相同但主幹不同=另一條路,配錯比留空糟)。
            stem = re.sub(r"[東西南北]?(?:大道|路|街|道)$", "", it[1])
            if stem and stem not in road_core and s < 0.75:
                continue
            best_s, best_it = s, it
        if best_it is not None and best_s >= cutoff:
            base_hit = best_it[1]
            # 3. 主幹配到後,若原文有段號,優先回傳「該主幹的第 N 段」官方條目
            if seg_num:
                want = f"{base_hit}{seg_num}段"
                for rn, base, raw, eng in roads:
                    if rn == want:
                        return raw, eng, best_s
            return best_it[2], best_it[3], best_s
    # 4. 收尾:官方路名子字串精確含,取最長命中(對官方清單精確比對,無誤配
    #    風險;較長條目如「后尾一橫巷」只有真的出現在行內才會蓋過「后尾巷」)。
    hit = None
    for rn, base, raw, eng in roads:
        if len(rn) >= 2 and rn in L and (hit is None or len(rn) > len(hit[0])):
            hit = (rn, raw, eng)
    if hit:
        return hit[1], hit[2], 1.0
    return None


_EN_ROAD_TOKEN = re.compile(
    r"([A-Za-z0-9'’.\- ]+?(?:Rd\.|St\.|Blvd\.|Ave\.|Dr\.))(?=\s*,|\s*$)", re.I)


def _match_road_en(en_text: str, roads: list, cutoff: float = 0.9):
    """英文行抽路名 token 對官方英名模糊比對(最後備援,寧缺勿錯)。

    契約上的英譯是仲介自翻,常與官方不同(Tuozi S. St. vs 官方 Tuozinan St.),
    模糊比對易錯配到同名系路(拕子一街),故門檻預設 0.9。
    取 Dist./Township 之前、最後一個路名 token(No./Lane/Alley 都在路名之前)。
    """
    head = re.split(r"\b(?:Dist|Township)\b", en_text, 1)[0]
    toks = _EN_ROAD_TOKEN.findall(head)
    if not toks:
        return None
    tok = toks[-1].strip().lower()
    best, score = None, 0.0
    for _rn, _base, raw, eng in roads:
        s = difflib.SequenceMatcher(None, tok, eng.lower()).ratio()
        if s > score:
            best, score = (raw, eng), s
    return (best[0], best[1], score) if best and score >= cutoff else None


# 數字與單位間容許空白:Vision 逐詞序列化時,中文與數字交界常被插入
# 斷詞空格(「名光街 38 號」),實體文件上並沒有,故比對時吸收、輸出時移除。
# 門牌支援多號列表(工廠連棟「11.13.15.17號」),點/頓/逗號分隔皆收;
# 樓層支援中文數字(「三樓之2」)。
_TAIL_RE = re.compile(
    r"((?:\d+\s*巷\s*)?(?:\d+\s*弄\s*)?\d+(?:\s*[.、,，]\s*\d+)*\s*(?:之\s*\d+)?"
    r"(?:[-–]\s*\d+)?\s*號(?:\s*[0-9一二三四五六七八九十]+\s*樓)?(?:\s*之\s*\d+)?)"
)


def _extract_tail(line: str) -> str:
    """抓門牌尾巴(…巷…弄…號…樓)。以原始行(非正規化)抓,保留數字;去除斷詞空白。"""
    m = _TAIL_RE.search(unicodedata.normalize("NFKC", line))
    return re.sub(r"\s+", "", m.group(1)) if m else ""


def _assemble_en(road_eng: str, area_eng: str, city_eng: str, detail: str) -> str:
    """組官方英文地址:樓層(含樓之N) → No.門牌號 → Aly.弄 → Ln.巷 → 路 → 區 → 縣市。"""
    parts = [p for p in (road_eng, area_eng, city_eng) if p]
    addr_en = ", ".join(parts)
    if detail and addr_en:
        # 巷/弄:官方縮寫 Ln./Aly.,順序為 No. → Aly. → Ln. → 路。
        lane = re.search(r"(\d+)巷", detail)
        if lane:
            addr_en = f"Ln. {lane.group(1)}, {addr_en}"
        alley = re.search(r"(\d+)弄", detail)
        if alley:
            addr_en = f"Aly. {alley.group(1)}, {addr_en}"
        # 英文門牌號取「N號」的號碼(而非 N巷/N弄);支援多號列表(11.13.15.17)
        # 與「號之N」後綴(41號之8 → No. 41-8);無「號」才退回開頭數字。
        num = re.search(r"(\d+(?:[.、,，]\d+)*(?:之\d+)?)號(?:之(\d+))?", detail)
        if num:
            no = num.group(1).replace("之", "-")   # 1之9 → 1-9
            if num.group(2):
                no += f"-{num.group(2)}"           # 41號之8 → 41-8
            addr_en = f"No. {no}, {addr_en}"
        else:
            head = re.match(r"(\d+(?:之\d+)?)", detail)
            if head:
                addr_en = f"No. {head.group(1).replace('之', '-')}, {addr_en}"
        # 樓層:台灣官方英文置於最前,如「3F., No. 15, ...」;
        # 樓後的「之N」(增建戶)併入樓層(2樓之1 → 2F.-1);中文數字樓層轉阿拉伯。
        flr = re.search(r"([0-9一二三四五六七八九十]+)樓(?:之(\d+))?", detail)
        if flr:
            f_en = f"{_seg_to_arabic(flr.group(1))}F." \
                   + (f"-{flr.group(2)}" if flr.group(2) else "")
            addr_en = f"{f_en}, {addr_en}"
    return addr_en


def _line_candidate(line: str, city_raw: str, city_eng: str, areas: dict,
                    forced_area, road_cutoff: float):
    """在指定縣市下,對單一中文行比對 區→路→門牌,回傳 (cand, rank)。

    forced_area:給定 (area_raw, area_eng, zip, roads) 時直接採用(英文錨定
    已確定行政區),否則從行內比對;比不到區則以全縣市路名配。
    """
    L = _norm(line)
    if forced_area:
        area_raw, area_eng, zipcode, roads = forced_area
    else:
        area = _match_area(L, areas)
        if area:
            area_raw, area_eng, zipcode, roads = area
        else:
            area_raw = area_eng = zipcode = ""
            roads = [r for v in areas.values() for r in v[3]]  # 缺區 → 全縣市路名
    # 抓路名前,先把已定位的縣市/區從字串剝掉,避免路名 regex 從行首把
    # 「彰化縣和美鎮德南」整段吞成路名主幹 → 誤配。
    cn = _norm(city_raw)
    rest = L.replace(cn, "", 1)
    if len(cn) >= 3:
        rest = rest.replace(cn[1:], "", 1)   # 縣市首字被印章蓋掉的殘留(「雄市」)
    if area_raw:
        rest = rest.replace(_norm(area_raw), "", 1)
    road = _match_road(rest, roads, cutoff=road_cutoff)
    road_raw, road_eng, rscore = road if road else ("", "", 0.0)
    detail = _extract_tail(line)

    cand = {
        "matched": True, "city": city_raw, "city_en": city_eng,
        "district": area_raw, "district_en": area_eng, "zip": zipcode,
        "road": road_raw, "road_en": road_eng, "detail": detail,
        "address_cn": f"{city_raw}{area_raw}{road_raw}{detail}",
        "address_en": _assemble_en(road_eng, area_eng, city_eng, detail),
        "road_score": round(rscore, 2),
    }
    # 評分:有區 +2、有路 +路分、有號樓 +0.5
    rank = (2 if area_raw else 0) + rscore + (0.5 if detail else 0)
    return cand, rank


def normalize_address(text: str, *, road_cutoff: float = 0.5,
                      en_text: str = "") -> dict:
    """把一段(可能雜訊/跨行)的中文地址標準化;可帶英文地址行輔助錨定。

    參數:
        text: OCR 出的中文地址字串,可為單行或多行區塊。
        road_cutoff: 路名模糊比對門檻(0~1),越高越嚴。
        en_text: (選)同一地址的 OCR 英文行。印章常蓋在中文行首(縣市被毀),
            英文縣市/區慣例在行尾而倖存;當中文縣市「僅靠模糊比對」或
            「完全比不到」時,以英文行反查縣市/區為錨,回頭用中文行配
            路名與門牌(中文路名通常未被汙染,且門牌數字以中文行最可靠)。

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

    def _best_over_lines(c_raw, c_eng, c_areas, forced_area):
        b, br = None, -1.0
        for line in lines:
            cand, rank = _line_candidate(line, c_raw, c_eng, c_areas,
                                         forced_area, road_cutoff)
            if rank > br:
                b, br = cand, rank
        return b

    best = None
    best_rank = -1.0
    best_tier = 9          # 縣市命中層級:1 完整名 2 去尾字 3 模糊(可疑)
    for line in lines:
        city = _match_city(_norm(line))
        if not city:
            continue
        city_raw, city_eng, areas, tier = city
        cand, rank = _line_candidate(line, city_raw, city_eng, areas,
                                     None, road_cutoff)
        if rank > best_rank:
            best, best_rank, best_tier = cand, rank, tier

    # ── 英文行錨定 ──────────────────────────────────────────────────────
    if en_text:
        anc = _match_city_area_en(en_text)
        if anc and (best is None or best_tier >= 3):
            # 中文縣市不可信(模糊命中易被行首汙染帶偏 / 完全比不到)
            # → 英文縣市(+區)為錨,重跑中文行的路名/門牌比對。
            (a_raw, a_eng, a_areas), a_area = anc
            ab = _best_over_lines(a_raw, a_eng, a_areas, a_area)
            if ab and not ab["road"]:
                # 中文行也配不到路 → 英文路名 token 高門檻備援
                roads = a_area[3] if a_area else \
                    [r for v in a_areas.values() for r in v[3]]
                hit = _match_road_en(en_text, roads)
                if hit:
                    ab["road"], ab["road_en"] = hit[0], hit[1]
                    ab["road_score"] = round(hit[2], 2)
                    ab["address_cn"] = (f"{ab['city']}{ab['district']}"
                                        f"{ab['road']}{ab['detail']}")
                    ab["address_en"] = _assemble_en(
                        ab["road_en"], ab["district_en"],
                        ab["city_en"], ab["detail"])
            if ab:
                best = ab
        elif anc and best is not None:
            # 中文縣市可信但比不到區,英文錨同縣市且有區 → 補區重配路名
            (a_raw, a_eng, a_areas), a_area = anc
            if a_area and best["city"] == a_raw and not best["district"]:
                ab = _best_over_lines(a_raw, a_eng, a_areas, a_area)
                if ab:
                    best = ab

    return best if best else dict(empty)


def find_roads(keyword: str, city_kw: str = "", area_kw: str = "") -> list:
    """查官方庫(含自建補充)路名:關鍵字子字串比對,經 _norm 正規化
       (臺/台、鍾/鐘異體、全半形都吸收)。回傳 [(縣市, 區, 路名, 英譯, 郵遞)]。"""
    kw = _norm(keyword)
    out = []
    for _cn, c_raw, _ce, areas in _load():
        if city_kw and _norm(city_kw) not in _norm(c_raw):
            continue
        for v in areas.values():
            a_raw, _ae, zipc, roads = v
            if area_kw and _norm(area_kw) not in _norm(a_raw):
                continue
            for rn, _b, raw, eng in roads:
                if kw in rn:
                    out.append((c_raw, a_raw, raw, eng, zipc))
    return out


if __name__ == "__main__":
    import sys

    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    if args:
        # 查詢模式:python address_db.py <路名關鍵字> [縣市] [行政區]
        # 例:python address_db.py 長發        → 全國含「長發」的路
        #    python address_db.py 大眾 宜蘭 五結 → 限縣市/區
        hits = find_roads(args[0], args[1] if len(args) > 1 else "",
                          args[2] if len(args) > 2 else "")
        if hits:
            print(f"找到 {len(hits)} 筆含「{args[0]}」的路名:")
            for c, a, r, e, z in hits[:50]:
                print(f"  {c}{a} {r}  ({e})  郵遞 {z}")
            if len(hits) > 50:
                print(f"  …(僅列前 50 筆,加縣市/區參數縮小範圍)")
        else:
            print(f"官方庫(含自建補充)查無含「{args[0]}」的路名"
                  + (f"(範圍:{args[1] if len(args) > 1 else '全國'}"
                     f"{args[2] if len(args) > 2 else ''})"))
            print("→ 確認 OCR 拼字無誤後,可加進 data/custom_roads.json")
        sys.exit(0)

    for t in [  # 無參數 → 簡易自測
        "宜蘭縣羅東鎮新群里新群一路16號有效",
        "台北市文山區辛亥路7段69巷15號3樓",
        "嘉義縣水上鄉寬士村崎子頭1之9號",
        "彰化縣和美鎮德南路201巷2號",
        "桃園市桃園區國聖二街25號1樓",
    ]:
        r = normalize_address(t)
        print(f"IN : {t}")
        print(f"OUT: {r['address_cn']} | {r['road_en']} | 郵遞 {r['zip']} | 路分 {r['road_score']}\n")
