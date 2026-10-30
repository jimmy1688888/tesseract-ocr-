"""反查救援的決策梯:次頁台仲區塊 → 名冊挑一家(CONTEXT.md「反查救援」)。

開 docx、讀次頁、接 D 欄理由不在這裡 —— 那些碰一批、Vision 與列,屬於救援迴圈。
"""
from __future__ import annotations

from dataclasses import dataclass

from .lookup import PermitLookup


@dataclass(frozen=True)
class AgencyPick:
    """反查救援從名冊挑一家的結果。

    permit:救回的許可證;電話多家要人工擇一、或救不回時為空。
    note  :接在 D 欄理由後面的反查管道說明;救不回時為空。
    """
    permit: str = ""
    note: str = ""


def pick_agency(block: dict, roster: PermitLookup) -> AgencyPick:
    """次頁台仲區塊({phones, name, address})→ 名冊挑一家。純函式:不開檔、不打 Vision。

    roster 只用到 agencies_by_phone / agencies_by_phone_prefix / best_by_name /
    best_by_address 四種查法(測試替身照這四個寫)。
    反查優先序(結果一律 manual_review、D 欄註明管道供人工核對):
      ① 電話精確唯一命中 → 最可靠,直接填 B。
      ② 電話前綴唯一命中(OCR 少 1~2 碼容錯)→ 填 B;比通用後段名稱可靠,故排名稱前。
      ③ (後備)名稱、地址各比名冊相似度,取相似度(CONF)高者填 B。
      ④ 電話多家、且前綴/名稱/地址皆救不回 → 不填 B,列出各家供人工擇一。
    """
    # ── 電話反查(最可靠):唯一命中優先,多家暫記待人工 ──
    ambiguous = None    # (matches, ph):第一支命中多家的電話
    for ph in block["phones"]:
        matches = roster.agencies_by_phone(ph)
        if len(matches) == 1:
            # ① 電話精確唯一命中
            info = matches[0]
            return AgencyPick(info["許可證"], (
                f"[電話反查救回] 次頁台仲電話 {ph} → "
                f"許可證 {info['許可證']}／{info['機構名稱']}(仍請人工核對)"))
        if matches and ambiguous is None:
            ambiguous = (matches, ph)

    # ② 電話『前綴』唯一命中(OCR 少 1~2 碼容錯;比通用後段名稱可靠,故排在名稱前)
    for ph in block["phones"]:
        mp = roster.agencies_by_phone_prefix(ph)
        if len(mp) == 1:
            info = mp[0]
            return AgencyPick(info["許可證"], (
                f"[電話前綴反查救回·電話OCR可能少碼] 次頁台仲電話 {ph} "
                f"前綴唯一命中 → 許可證 {info['許可證']}／{info['機構名稱']}(仍請人工核對)"))

    # ③ 電話後備:名稱、地址各與名冊比相似度,取「相似度(CONF)高者」填 B
    cand_n = roster.best_by_name(block["name"]) if block.get("name") else None
    cand_a = roster.best_by_address(block["address"]) if block.get("address") else None
    picks = [(c, lbl) for c, lbl in ((cand_n, "名稱"), (cand_a, "地址")) if c]
    if picks:
        best_c, via = max(picks, key=lambda x: x[0]["_ratio"])
        return AgencyPick(best_c["許可證"], (
            f"[{via}反查救回·電話無唯一命中] 次頁台仲{via} → "
            f"許可證 {best_c['許可證']}／{best_c['機構名稱']}"
            f"(名冊相似度 {best_c['_ratio']},仍請人工核對)"))

    # ④ 電話多家(模糊)且前綴/名稱/地址都救不回 → 列出台仲供人工擇一,不填 B
    if ambiguous:
        matches, ph = ambiguous
        cand = "、".join(f"{m['許可證']}／{m['機構名稱']}" for m in matches)
        return AgencyPick("", f"[電話反查·需人工擇一] 台仲電話 {ph} 命中{len(matches)} 家:{cand}")
    return AgencyPick()
