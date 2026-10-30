"""許可證查表器:建構一次、重複查詢;一批載一次名冊(load_roster)。"""
from __future__ import annotations

import logging
from pathlib import Path

from tw_phone import PHONE_MIN_LEN, standard_form

from .matching import (ADDR_MATCH_MIN, NAME_MATCH_MIN, _best_match, _norm_addr, _norm_name,
                       build_phone_index)
from .roster import (ROSTER_PATH, _branch_key, _info, _is_active, _parse_permit, build_indices,
                     fetch_roster)

logger = logging.getLogger(__name__)


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
        n = standard_form(phone)
        if not n:
            return []
        by_permit = {h["許可證"]: h for h in self.phone_index.get(n, [])}
        return list(by_permit.values())

    def agencies_by_phone_prefix(self, phone: str,
                                 min_len: int = PHONE_MIN_LEN) -> list[dict]:
        """電話『前綴』反查(OCR 少碼容錯):以「一方為另一方前綴且共同長度 ≥ min_len」
        比對,回傳去重後的有效機構(0/1/多筆);呼叫端只在唯一命中時採用。
        比精確反查寬鬆,故設 8 碼門檻——台灣市話/手機標準形 9~10 碼,容許少 1~2 碼,
        又足夠長以避免誤命中(如 32401:022371760 少一碼,前綴唯一命中正確機構)。"""
        q = standard_form(phone)
        if len(q) < min_len:
            return []
        hits: dict[str, dict] = {}
        for np_, ags in self.phone_index.items():
            short, lng = (q, np_) if len(q) <= len(np_) else (np_, q)
            if len(short) >= min_len and lng.startswith(short):
                for a in ags:
                    hits[a["許可證"]] = a
        return list(hits.values())

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


def load_roster(path: Path = ROSTER_PATH) -> PermitLookup | None:
    """一批載一次名冊(main 呼叫,往下傳)。載不起來降級成 None:E~G 留空、反查救援
    跳過,一批照常跑 —— 名冊是降級來源,不是閘門(test_preflight)。"""
    try:
        roster = PermitLookup(fetch_roster(path))
    except Exception as e:
        logger.warning(f"  ⚠ 名冊載入失敗,機構名稱/地址/電話欄將留空:{e!r}")
        return None
    logger.info(f"  ✔ 載入仲介名冊:母公司 {len(roster.main_index)} 筆"
                f"／分支 {len(roster.branch_index)} 筆")
    return roster
