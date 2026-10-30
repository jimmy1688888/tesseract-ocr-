"""名冊:讀本地 JSON、併入自建補充仲介、建許可證索引(母公司/分公司)。"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path

# 資料檔在 repo 根目錄的 data/(本檔在 permit_lookup/ 底下,所以往上兩層)
_DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# 手動更新用:瀏覽器開此連結下載 JSON,覆蓋 ROSTER_PATH
ROSTER_JSON_URL = "https://apiservice.mol.gov.tw/OdService/download/A17000000J-020001-QHy"
ROSTER_PATH = _DATA_DIR / "agency_roster.json"
ROSTER_STALE_DAYS = 30   # 超過此天數只記提示,仍照常使用
# 自建補充仲介:官方名冊尚未收錄或資料已變更者。
# 不直接改 agency_roster.json(原檔重新下載會整份覆蓋,手改的列會靜靜消失,
# 而且是下次跑才發現那家又查不到)。補充檔小、進版控、跨機同步——與
# address_db 的 custom_roads.json 同一個模式。
CUSTOM_ROSTER_PATH = _DATA_DIR / "custom_agencies.json"

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
    return merge_custom_agencies(json.loads(path.read_text(encoding="utf-8-sig")))


def merge_custom_agencies(records: list[dict],
                          path: Path = CUSTOM_ROSTER_PATH) -> list[dict]:
    """把自建補充仲介併入名冊,回傳合併後的清單(不改動輸入)。

    同一張許可證兩邊都有時**自訂優先**:會手動加一筆,正是因為官方那筆不合用
    (常見是新設分公司尚未收錄,或名稱/電話已變更而名冊未更新)。比對用
    _parse_permit 的正規化結果,所以自訂寫 "9001" 也蓋得掉官方的 "09001"。

    補充檔缺失或格式壞掉一律安靜跳過:那是個可有可無的補丁,不該讓它擋掉整份
    官方名冊——後者才是許可證與仲介機構之間的權威對照。
    """
    try:
        extra = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return records
    if not isinstance(extra, list) or not extra:
        return records
    keys = {_parse_permit(str(e.get("許可證", ""))) for e in extra}
    keys.discard(None)
    kept = [r for r in records
            if _parse_permit(str(r.get("許可證", ""))) not in keys]
    logger.info(f"名冊補充 {len(extra)} 筆(覆蓋官方 {len(records) - len(kept)} 筆):{path}")
    return kept + extra


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
