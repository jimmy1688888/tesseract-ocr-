"""許可證 → 機構資料(名稱／地址／電話)對照查表。

資料來源:勞動部「跨國人力仲介公司許可名冊」(data.gov.tw dataset 6682)的官方 JSON。
刻意用 JSON 而非 Excel 匯出的 CSV:Excel 會把分支許可證(如 1-73)自動判讀成日期
(→ Jan-73),並吃掉前導零(0234 → 234);JSON 原樣保留 "0001"、"0002-1" 等格式。

資料檔:只讀本地檔 data/agency_roster.json,不自動連網。更新方式為手動——
    用瀏覽器開啟下方 ROSTER_JSON_URL 下載 JSON,覆蓋該檔即可。
    (mol.gov.tw 憑證常被 Python SSL 擋下,故不在程式內連網。)

用法:
    from permit_lookup import PermitLookup
    pl = PermitLookup()                 # 讀本地 data/agency_roster.json
    info = pl.lookup("2340")            # → {'機構名稱': ..., '機構地址': ..., '電話': ...}
    if info is None:                    # 查無 → 交人工補
        ...
"""
from __future__ import annotations

import difflib
import json
import logging
import re
import time
from collections import defaultdict
from pathlib import Path

# 手動更新用:瀏覽器開此連結下載 JSON,覆蓋 ROSTER_PATH
ROSTER_JSON_URL = "https://apiservice.mol.gov.tw/OdService/download/A17000000J-020001-QHy"
ROSTER_PATH = Path(__file__).with_name("data") / "agency_roster.json"
ROSTER_STALE_DAYS = 30   # 超過此天數只記提示,仍照常使用

logger = logging.getLogger(__name__)


def fetch_roster(path: Path = ROSTER_PATH) -> list[dict]:
    """讀本地名冊檔(唯一來源,不連網)。

    檔案不存在 → 拋 FileNotFoundError 並給手動下載指引。
    檔案偏舊(超過 ROSTER_STALE_DAYS 天)→ 只記一行提示,照常使用。
    """
    if not path.exists():
        raise FileNotFoundError(
            f"找不到名冊檔 {path}。請用瀏覽器開啟 {ROSTER_JSON_URL} "
            f"下載 JSON,存到該路徑後再執行。"
        )
    age_days = (time.time() - path.stat().st_mtime) / 86400
    if age_days > ROSTER_STALE_DAYS:
        logger.info(f"名冊檔已 {age_days:.0f} 天未更新({path}),建議手動重新下載。")
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _parse_permit(permit: str) -> tuple[int, str] | None:
    """把許可證拆成 (母公司號 int, 分支後綴 str)。

    - '2340'   → (2340, '')      母公司,無後綴
    - '0234'   → (234,  '')      去前導零
    - '2340-2' → (2340, '2')     分支(數字後綴去前導零)
    - '2340-A' → (2340, 'A')     分支(字母後綴轉大寫)
    母公司號非數字 → None。
    """
    head, _, tail = str(permit).strip().partition("-")
    head = head.strip()
    if not head.isdigit():
        return None
    tail = tail.strip()
    if tail:
        suffix = str(int(tail)) if tail.isdigit() else tail.upper()
    else:
        suffix = ""
    return int(head), suffix


def _branch_key(base: int, suffix: str) -> str:
    """分支精確鍵,例如 (2340,'2') → '2340-2'。"""
    return f"{base}-{suffix}"


def _is_active(rec: dict) -> bool:
    """目前有效 = 未廢止 且 未終止營業。"""
    return not str(rec.get("廢止許可日期", "")).strip() and not str(
        rec.get("終止營業日期", "")
    ).strip()


def _info(rec: dict) -> dict:
    return {
        "機構名稱": str(rec.get("機構名稱", "")).strip(),
        "機構地址": str(rec.get("機構地址", "")).strip(),
        "電話": str(rec.get("電話", "")).strip(),
        "許可證": str(rec.get("許可證", "")).strip(),
        "有效": _is_active(rec),
    }


def build_indices(records: list[dict]) -> tuple[dict[int, dict], dict[str, dict]]:
    """建兩個索引:

    - main_index:  母公司號 int → 母公司列(僅無後綴的列)
    - branch_index: '母公司號-後綴' str → 該分公司列

    OCR 讀到分支號(如 2340-2)時要列該分公司、不回退母公司,故分開建索引。
    同鍵有多列(歷史)時,取「目前有效」者優先。
    """
    main_index: dict[int, dict] = {}
    main_rank: dict[int, tuple] = {}
    branch_index: dict[str, dict] = {}
    branch_rank: dict[str, tuple] = {}
    for rec in records:
        parsed = _parse_permit(str(rec.get("許可證", "")))
        if parsed is None:
            continue
        base, suffix = parsed
        rank = (_is_active(rec),)          # 有效優先
        if suffix:
            key = _branch_key(base, suffix)
            if key not in branch_rank or rank > branch_rank[key]:
                branch_rank[key] = rank
                branch_index[key] = _info(rec)
        else:
            if base not in main_rank or rank > main_rank[base]:
                main_rank[base] = rank
                main_index[base] = _info(rec)
    return main_index, branch_index


def _norm_phone(phone: str) -> str:
    """電話正規化:去掉所有非數字(02-2727-6999 → 0227276999),供反查比對。
    國際格式 +886(去非數字後為 886…)還原為國內前導 0:886227276999 → 0227276999。
    國內號一律 0 開頭、無 886 前綴,故此轉換只會命中國際格式,不誤傷國內號。"""
    n = re.sub(r"\D", "", str(phone or ""))
    if n.startswith("886"):
        n = "0" + n[3:].lstrip("0")   # 去國碼、還原單一前導 0(容 +886-02… 這種多寫 0)
    return n


def build_phone_index(records: list[dict]) -> dict[str, list[dict]]:
    """建「正規化電話 → 有效機構列」索引,供全無命中時以仲介電話反查許可證。
    只收目前有效者;同電話多家(約 2.6%)保留全部,由呼叫端判定模糊不採用。"""
    idx: dict[str, list[dict]] = defaultdict(list)
    for rec in records:
        if not _is_active(rec):
            continue
        n = _norm_phone(rec.get("電話"))
        if n:
            idx[n].append(_info(rec))
    return idx


# ── 名稱／地址反查(電話反查的後備管道)─────────────────────────────────────
# 電話是精確反查;名稱/地址靠 OCR,會有誤字,故改用「正規化後相似度」比對,
# 取相似度最高且達門檻者當候選(仍一律 manual_review,由人工核對)。
NAME_MATCH_MIN = 0.78   # 機構名稱相似度門檻(名稱獨特,設較高避免誤命中)
ADDR_MATCH_MIN = 0.72   # 機構地址相似度門檻(地址長、雜訊多,略放寬但仍保守)


def _norm_name(s: str) -> str:
    """機構名稱正規化:只留中英數(去空白/括號/標點),英文轉大寫,供相似度比對。"""
    return re.sub(r"[^0-9A-Za-z一-鿿]", "", str(s or "")).upper()


def _norm_addr(s: str) -> str:
    """地址正規化:去空白與常見標點/括號,全形數字轉半形,供相似度比對。"""
    s = str(s or "").translate(str.maketrans("０１２３４５６７８９", "0123456789"))
    return re.sub(r"[\s,，、.。\-—()（）:：]", "", s).upper()


def _best_match(query: str, records: list[tuple[str, dict]], min_ratio: float) -> dict | None:
    """在 (正規化字串, info) 清單中找與 query 相似度最高者;達門檻才回傳。
    回傳 info 複本並附 _ratio;查無或未達門檻 → None。"""
    if len(query) < 4:
        return None                       # 太短不比(易誤命中)
    best, best_r = None, 0.0
    sm = difflib.SequenceMatcher()
    sm.set_seq2(query)
    for norm, info in records:
        if not norm:
            continue
        sm.set_seq1(norm)
        # quick_ratio 為 ratio 的上界,先粗篩省去多數精算
        if sm.quick_ratio() <= best_r:
            continue
        r = sm.ratio()
        if r > best_r:
            best, best_r = info, r
    if best is not None and best_r >= min_ratio:
        return {**best, "_ratio": round(best_r, 3)}
    return None


class PermitLookup:
    """許可證查表器。建構一次、重複查詢。"""

    def __init__(self, records: list[dict] | None = None):
        if records is None:
            records = fetch_roster()
        self.main_index, self.branch_index = build_indices(records)
        self.phone_index = build_phone_index(records)
        # 名稱/地址反查索引:只收目前有效機構的 (正規化字串, info)。
        self._name_records: list[tuple[str, dict]] = []
        self._addr_records: list[tuple[str, dict]] = []
        for rec in records:
            if not _is_active(rec):
                continue
            info = _info(rec)
            self._name_records.append((_norm_name(info["機構名稱"]), info))
            self._addr_records.append((_norm_addr(info["機構地址"]), info))

    def lookup(self, permit: str) -> dict | None:
        """回傳 {機構名稱, 機構地址, 電話, 許可證, 有效};查無 → None。

        - 有分支後綴(2340-2)→ 精確查該分公司,查無即 None(不回退母公司)
        - 無後綴(2340)→ 查母公司
        """
        parsed = _parse_permit(str(permit))
        if parsed is None:
            return None
        base, suffix = parsed
        if suffix:
            return self.branch_index.get(_branch_key(base, suffix))
        return self.main_index.get(base)

    def agencies_by_phone(self, phone: str) -> list[dict]:
        """電話反查機構:回傳所有命中的有效機構(依許可證去重),0/1/多筆皆可能。
        用於全無命中救援——多筆(同電話多家台仲)時由呼叫端列出供人工擇一。"""
        n = _norm_phone(phone)
        if not n:
            return []
        by_permit = {h["許可證"]: h for h in self.phone_index.get(n, [])}
        return list(by_permit.values())

    def lookup_by_phone(self, phone: str) -> dict | None:
        """電話反查:唯一命中才回傳,查無或多家(模糊)→ None。"""
        m = self.agencies_by_phone(phone)
        return m[0] if len(m) == 1 else None

    def best_by_name(self, name: str, min_ratio: float = NAME_MATCH_MIN) -> dict | None:
        """仲介名稱反查:與名冊「機構名稱」正規化後比相似度,取最高且達門檻者。
        回傳 info(附 _ratio 相似度);查無/未達門檻 → None。電話後備管道。"""
        return _best_match(_norm_name(name), self._name_records, min_ratio)

    def best_by_address(self, address: str, min_ratio: float = ADDR_MATCH_MIN) -> dict | None:
        """仲介地址反查:與名冊「機構地址」正規化後比相似度,取最高且達門檻者。
        回傳 info(附 _ratio 相似度);查無/未達門檻 → None。電話後備管道。"""
        return _best_match(_norm_addr(address), self._addr_records, min_ratio)


if __name__ == "__main__":
    import sys

    pl = PermitLookup()
    print(f"名冊索引筆數:母公司 {len(pl.main_index)}／分公司 {len(pl.branch_index)}"
          f"／電話 {len(pl.phone_index)}")
    for permit in sys.argv[1:] or ["2340", "2639", "0001", "99999"]:
        info = pl.lookup(permit)
        if info:
            print(f"  {permit} → {info['機構名稱']} ｜ {info['機構地址']} ｜ {info['電話']}"
                  f"（有效={info['有效']}）")
        else:
            print(f"  {permit} → 查無,需人工補")
