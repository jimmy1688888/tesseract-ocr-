# -*- coding: utf-8 -*-
"""送審決策:一份 docx 的許可證掃描結果 → 一個決定(CONTEXT.md「送審決策」,#40)。

去向四選一:直接 key-in／送 Vision／反查救援／直接人工審查。人工審查通常不是去向,
是送 Vision 或反查救援之後的結果;只有 docx 本身讀不到圖時直接人工審查(#47)——
沒有圖,Vision 與反查救援都無從做起。以 docx 為單位(一份契約只有一個許可證,
Sheets 上只有一列);舊做法逐圖決定,同一份 docx 會進佇列多次。

多數票判斷與各信心門檻只記在這裡:停止擴掃(ADR-0005)與規則 A2 用同一個
is_permit_vote —— 兩者不一致時,分層掃描會在 A2 不採用的值上停下來。

輸入是 permit_reading 的 ScanResult(單張圖的讀值整理,decide_result 已寫好 final_value、
vision_review、note);這裡只讀屬性。permit_reading 反過來 import 這裡的門檻與
is_permit_vote,所以只在型別標註時 import 它,免得循環匯入。
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

from docx_images import image_sort_key

if TYPE_CHECKING:
    from permit_reading import ScanResult

# 信心門檻
CONF_KEY_IN       = 55   # 高於此值直接 key-in;低於則送 Google Vision
CONF_VOTE_MIN     = 45   # permit 多數票平均信心高於此值才算數(停擴掃、規則 A2)
CONF_MOL_VOTE_MIN = 50   # small docx mol 多數票平均信心高於此值直接 key-in
# 單一判讀(非多數票:small mol 單一命中 / large permit 部分命中)且信心低於此值:
# 該 ROI 位置品質差,再用 Vision 掃同一位置意義不大 → 改走反查救援。
CONF_NOVOTE_RESCUE_MAX = 50

# ScanResult.status 的值(ResultStatus 是 str Enum,與字串相等)
_LARGE_NO_HIT = "large_no_hit"
_SMALL_NO_HIT = "small_no_hit"
_PERMIT_PARTIAL = "permit_partial"

# docx 本身讀不到圖(#47)→ Sheets D 欄。只講事實。
_NO_IMAGE_REASONS = {
    "no_images":       "docx 無可讀的內嵌圖(word/media 底下沒有 png/jpg/bmp/tif/gif)",
    "unreadable":      "docx 無法開啟(不是有效的 docx 檔)",
}


class Destination(str, Enum):
    直接KEYIN = "直接key-in"
    送VISION  = "送Vision"
    反查救援  = "反查救援"
    直接人工審查 = "直接人工審查"


@dataclass(frozen=True)
class Decision:
    source_docx: str
    destination: Destination
    candidate_value: str
    candidate_conf: float
    image_name: str        # 被選的那張圖;全無命中時為空
    img_path: str          # 那張圖的截圖(送 Vision 用)
    hit_roi: str           # 送出那張截圖屬於哪個 ROI(Vision 取號依此選 pattern)
    reason: str


def is_permit_vote(r: ScanResult) -> bool:
    """可信的 permit 多數票:停止擴掃的唯一理由,也是規則 A2 直接 key-in 的條件。

    mol 命中不算(只證明是契約頁);PERMIT_PARTIAL 不算(湊不出多數)—— 見 ADR-0005。
    """
    return bool(r.id_from_vote and r.id and r.id_conf > CONF_VOTE_MIN)


def _crop(r: ScanResult) -> str:
    return r.permit_crop or r.mol_crop or ""


def _crop_roi(r: ScanResult) -> str:
    """_crop 選中的那張截圖屬於哪個 ROI。

    不能照抄 r.hit_roi:它記的是「第一個」命中的 ROI —— mol 先中、permit 後中時停在
    mol,送出去的卻是 permit 截圖。permit 截圖而 hit_roi 不是 permit 時,只知道是
    permit、不知道哪一半。
    """
    if r.permit_crop:
        return r.hit_roi if r.hit_roi.startswith("permit") else "permit"
    if r.mol_crop:
        return "mol"
    return r.hit_roi


def _decision(r: ScanResult, dest: Destination, value: str, conf: float,
              reason: str) -> Decision:
    return Decision(r.source_docx, dest, value, conf, r.image_name, _crop(r),
                    _crop_roi(r), reason)


def _low_conf_rescue(r: ScanResult, value: str, conf: float, reason: str) -> Decision:
    """單一判讀低信心:不送 Vision(再掃同一個 ROI 位置意義不大),改走反查救援;
    候選值當種子(救不回就留著)。理由寫明略過了 Vision —— 這段會進 Sheets D 欄。"""
    return _decision(r, Destination.反查救援, value, conf,
                     f"[單一判讀低信心 conf={conf:.1f}<{CONF_NOVOTE_RESCUE_MAX},"
                     f"略過 ROI Vision 改走次頁電話反查] {reason}")


def _no_hit(rows: list[ScanResult], reason: str,
            dest: Destination = Destination.反查救援) -> Decision:
    """沒有任何讀值可送的決定:沒有候選值、沒有截圖。"""
    return Decision(rows[0].source_docx, dest, "", 0.0, "", "", "", reason)


def _earliest(rows):
    return min(rows, key=lambda r: image_sort_key(r.image_name))


def decide(rows: list[ScanResult]) -> Decision:
    """一份 docx 的所有掃描結果 → 一個送審決策。"""
    why = _NO_IMAGE_REASONS.get(rows[0].status.value)
    if why:
        return _no_hit(rows, why, Destination.直接人工審查)
    if rows[0].docx_class == "large":
        return _decide_large(rows)
    return _decide_small(rows)


def _decide_large(rows: list[ScanResult]) -> Decision:
    if all(r.status == _LARGE_NO_HIT for r in rows):
        return _no_hit(rows, f"large docx：全 {len(rows)} 張圖掃描無許可證命中")

    mol_rows = [(r, r.mol, r.mol_conf) for r in rows if r.mol]
    permit_rows = [(r, r.id, r.id_conf) for r in rows if r.id]

    # 規則A:mol 與 permit 都有值
    if mol_rows and permit_rows:
        common = {v for _, v, _ in mol_rows} & {v for _, v, _ in permit_rows}
        if common:
            r, v, c = max((x for x in mol_rows + permit_rows if x[1] in common),
                          key=lambda x: x[2])
            if c > CONF_KEY_IN:
                return _decision(r, Destination.直接KEYIN, v, c,
                                 f"cross_match(file)_高信心 conf={c}")
            return _decision(r, Destination.送VISION, v, c,
                             f"cross_match(file)_低信心 conf={c}")
        # mol ≠ permit:permit ROI 目的明確,衝突時以 permit 最早圖為主
        r, v, c = min(permit_rows, key=lambda x: image_sort_key(x[0].image_name))
        return _decision(r, Destination.送VISION, v, c,
                         f"mol≠permit衝突_permit優先_最早圖({r.image_name}) conf={c}")

    # 規則A2:僅 permit 且有可信多數票 → 最早那張直接 key-in
    # (有 mol 又有 permit 已在規則A 回傳;只有 mol 則不會有 permit 多數票)
    votes = [r for r in rows if is_permit_vote(r)]
    if votes:
        r = _earliest(votes)
        return _decision(r, Destination.直接KEYIN, r.id, r.id_conf,
                         f"permit多數票_高信心_最早圖({r.image_name}) conf={r.id_conf}")

    # 規則B(部分命中無多數)與規則C(其餘需覆核):合併後只選一張
    cands: list[tuple[ScanResult, str, float, str]] = []
    for r in rows:
        if r.status == _PERMIT_PARTIAL:
            cands.append((r, r.id or r.final_value, r.id_conf or r.final_conf,
                          "permit部分命中無多數"))
        elif r.vision_review:
            cands.append((r, r.final_value, r.final_conf, r.note or "large:讀值信心不足"))
    if cands:
        r, v, c, why = _pick(cands)
        why = why + _others(cands, r)
        if r.status == _PERMIT_PARTIAL and c < CONF_NOVOTE_RESCUE_MAX:
            return _low_conf_rescue(r, v, c, why)
        return _decision(r, Destination.送VISION, v, c, why)

    # 沒有需覆核的列:信心最高的讀值直接 key-in
    valued = [r for r in rows if r.final_value]
    if valued:
        r = max(valued, key=lambda r: r.final_conf)
        return _decision(r, Destination.直接KEYIN, r.final_value, r.final_conf,
                         f"高信心直接key-in conf={r.final_conf}")
    return _no_hit(rows, f"large docx：{len(rows)} 張圖皆無可用讀值")


def _decide_small(rows: list[ScanResult]) -> Decision:
    hits = [r for r in rows if r.mol or r.id]
    if not hits or all(r.status == _SMALL_NO_HIT for r in rows):
        return _no_hit(rows, f"small docx：mol ROI 全 {len(rows)} 張圖無命中")
    # 與 aggregate_small_docx 同一個 winner:兩引擎信心取高者,同分取先出現者
    r = max(hits, key=lambda h: max(h.mol_conf, h.id_conf))
    v = r.final_value or r.mol or r.id
    if r.mol_from_vote and r.mol_conf > CONF_MOL_VOTE_MIN:
        return _decision(r, Destination.直接KEYIN, v, r.mol_conf,
                         f"mol多數票_高信心 avg_conf={r.mol_conf}")
    c = r.final_conf or max(r.mol_conf, r.id_conf)
    if r.mol_from_vote:
        return _decision(r, Destination.送VISION, v, c,
                         f"small:mol多數票但平均信心{r.mol_conf}未達直接key-in門檻"
                         f"{CONF_MOL_VOTE_MIN},需人工確認")
    why = f"small:mol非多數票(單一判讀 conf={c:.1f}),需人工複核"
    if c < CONF_NOVOTE_RESCUE_MAX:
        return _low_conf_rescue(r, v, c, why)
    return _decision(r, Destination.送VISION, v, c, why)


def _pick(cands):
    """信心最高;同分取圖序最前(grill Q2)。"""
    top = max(c[2] for c in cands)
    return min((c for c in cands if c[2] == top),
               key=lambda c: image_sort_key(c[0].image_name))


def _others(cands, chosen) -> str:
    rest = [f"{r.image_name}={v}(conf={c:.1f})" for r, v, c, _ in cands if r is not chosen]
    return ("；其他圖讀值:" + "、".join(rest)) if rest else ""
