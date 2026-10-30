"""反查名冊的比對:電話欄拆號碼與電話索引、機構名稱/地址正規化與相似度。"""
from __future__ import annotations

import difflib
import re
from collections import defaultdict

# 台灣文字正規化的唯一來源(臺/台、鍾/鐘、全半形、段號寫法)。
# address_db 只依賴標準函式庫、且 import 時不讀任何資料檔,故可安全頂層 import
# ——即使 data/AllData.json 缺席也不影響本模組。見 docs/adr/0001。
from address_db import fold_variants
from tw_phone import PHONE_MIN_LEN, standard_form

from .roster import _info, _is_active


# 名冊「電話」欄常把多支號碼或分機擠在同一格(全表 24 筆,其中有效機構 12 筆):
#   '03-3253456/3253507'、'072114387、0925510286'、'037-364897.364898' → 兩支號碼
#   '(03)-3554436#171'、'02-29819295*13'                             → 主號 + 分機
# 整欄去非數字會串成一長串(→ '0332534563253507'),精確反查必然落空,只有主號
# 恰為前綴時才靠前綴反查勉強救回,第二支號碼則完全查不到。
# 實測全表分隔符只有這幾種:'/'(5)、'、'(2)、'.'(2) 分號碼;'#'(14)、'*'(1) 接分機。
# '-'(4592) 與括號(413) 是號碼內的寫法,絕不能當分隔。
_PHONE_FIELD_SEP = re.compile(r"[/、.]")
_PHONE_EXT_SEP = re.compile(r"[#*]")


def split_phone_field(raw: str) -> list[str]:
    """名冊電話欄 → 電話標準形清單(去重保序);單一號碼即回單元素清單。

    先拆多支號碼,再切掉各段的分機。不足 PHONE_MIN_LEN 碼的殘段丟棄:那是分機
    (#171)或缺區碼的第二支(如 '03-3253456/3253507' 的 3253507),短到只能靠
    完全相等命中,而 Vision 抽到的台灣號一律 9~10 碼,留著只會徒增誤撞。
    """
    out: list[str] = []
    for seg in _PHONE_FIELD_SEP.split(str(raw or "")):
        n = standard_form(_PHONE_EXT_SEP.split(seg, maxsplit=1)[0])
        if len(n) >= PHONE_MIN_LEN and n not in out:
            out.append(n)
    return out


def build_phone_index(records: list[dict]) -> dict[str, list[dict]]:
    """建「電話標準形 → 有效機構列」索引,供全無命中時以仲介電話反查許可證。

    只收目前有效者;同電話多家(約 2.6%)保留全部,由呼叫端判定模糊不採用。
    一筆記錄可產生多把 key(電話欄塞了兩支號碼時),見 split_phone_field。
    """
    idx: dict[str, list[dict]] = defaultdict(list)
    for rec in records:
        if not _is_active(rec):
            continue
        for n in split_phone_field(rec.get("電話")):
            idx[n].append(_info(rec))
    return idx


# ── 名稱／地址反查(電話反查的後備管道)─────────────────────────────────────
# 電話是精確反查;名稱/地址靠 OCR,會有誤字,故改用「正規化後相似度」比對,
# 取相似度最高且達門檻者當候選(仍一律 manual_review,由人工核對)。
NAME_MATCH_MIN = 0.78   # 機構名稱相似度門檻(名稱獨特,設較高避免誤命中)
ADDR_MATCH_MIN = 0.72   # 機構地址相似度門檻(地址長、雜訊多,略放寬但仍保守)


def _norm_name(s: str) -> str:
    """機構名稱正規化:先異體折疊,再只留中英數(去空白/括號/標點),英文轉大寫。

    折疊的實測效益在名稱側很小(名冊僅 8 筆機構名稱含「臺」,多數登記為「台」),
    納入是為了讓名稱與地址共用同一套規則——只改一邊等於把「兩套正規化」
    這個問題留一半。實測折疊後正規化撞名 0 → 0,不會把兩家不同機構併成同鍵。"""
    return re.sub(r"[^0-9A-Za-z一-鿿]", "", fold_variants(str(s or ""))).upper()


def _norm_addr(s: str) -> str:
    """地址正規化:先異體折疊(臺→台、一段→1段、全形→半形),再去空白與標點/括號。

    折疊委由 address_db.fold_variants,不在此另寫一套:名冊機構地址 38% 用官方
    體例的「臺」、契約 OCR 印的是「台」,名冊自身的段號也是國字/阿拉伯各半
    (308 / 313 筆),不折疊等於在每次比對憑空扣掉相似度。實測乾淨輸入下命中率
    不變(本來就 100%),但 OCR 受損 20% 時多救回 25 件、誤配僅 +1。見 ADR-0002。

    註:全形數字由 fold_variants 的 NFKC 一併處理,故不需再自行 translate。"""
    return re.sub(r"[\s,，、.。\-—()（）:：]", "", fold_variants(str(s or ""))).upper()


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
