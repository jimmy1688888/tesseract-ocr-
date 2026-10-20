# -*- coding: utf-8 -*-
"""H~O 快照:真實素材的雇主欄位解析結果,守住「純重構逐格不變」(#32)。

快照是**機器輸出、不是真值**(CONTEXT.md「快照」)。它記的是 Vision 全文經
extract_employer_fields 解析出的 8 個輸出欄位(與 Sheets 的 H~O 同一組鍵)加
_note(錨定失敗原因);內部鍵不記 —— 疑慮怎麼組裝是內部的事,重組了不該變紅。
地址標準化與郵遞區號會查門牌庫,所以一併記下門牌庫的雜湊:對不上時分得出是
「資料檔變了」還是「程式變了」。

快照不進版控(在 samples/ 底下),沒有 git diff 可看,所以:
  - pytest 只比對、絕不寫入
  - 重錄只能用 `python snapshots.py record`:先印逐格 diff(這就是審查畫面),
    舊快照加日期留存,不直接覆蓋

新素材(放進 samples/docs/ 的 docx)由同一個指令先補 Vision 全文:列出選中的契約頁
請人確認、說好共幾次 Vision 才打,每份只打一次(fill_missing_vision)。
"""
import hashlib
import json
import sys
from datetime import date
from pathlib import Path
from typing import Callable

import real_samples as rs

# 解析輸出的 8 欄(寫 Sheets 的 H~O 一律取自這裡)加錨定失敗原因。快照記解析輸出、
# 不是 Sheets 欄:Sheets 少寫幾欄(#30)時值照算,這裡不跟著少。
KEYS = (
    "雇主名稱_中", "雇主名稱_英",
    "地址_中_標準", "地址_中",
    "疑慮標示", "地址_英",
    "郵遞區號", "電話",
    "_note",
)


def snapshot_path() -> Path:
    return rs.ROOT / "snapshots" / "employer_fields.json"


def alldata_sha256() -> str:
    """門牌庫的雜湊;沒有門牌庫就是空字串。"""
    import address_db
    p = address_db._DB_PATH
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else ""


def _default_extract(text: str) -> dict:
    from employer_extract import extract_employer_fields
    return extract_employer_fields(text)


def current_fields(stems: list[str] | None = None,
                   extract: Callable[[str], dict] | None = None) -> dict[str, dict[str, str]]:
    """{stem: 8 欄 + _note} —— 對每份 Vision 全文現在解析出來的樣子。"""
    extract = extract or _default_extract
    out = {}
    for stem in (rs.vision_stems() if stems is None else stems):
        f = extract(rs.employer_text(stem))
        out[stem] = {k: f.get(k, "") for k in KEYS}
    return out


def load() -> dict | None:
    """存好的快照 {alldata_sha256, recorded, fields: {stem: {...}}};沒錄過是 None。"""
    p = snapshot_path()
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def _changed_cells(stem: str, old: dict | None, new: dict) -> list[str]:
    """逐格列出差異。沒有舊值(新素材)時每一格都列。"""
    lines = []
    for k in KEYS:
        if old is None:
            lines.append(f"{stem} {k}: {new[k]!r}")
        elif old.get(k, "") != new[k]:
            lines.append(f"{stem} {k}: {old.get(k, '')!r} → {new[k]!r}")
    return lines


def mismatch_message(stem: str, saved: dict, now: dict[str, str], data_hash: str) -> str | None:
    """現在的解析結果與快照不同時的說明;相同則 None。只讀,絕不寫。"""
    cells = _changed_cells(stem, saved["fields"].get(stem, {}), now)
    if not cells:
        return None
    head = "快照對不上(行為變了,不一定是錯;是刻意的改動就用 python snapshots.py record 重錄並逐格看過):"
    if data_hash != saved.get("alldata_sha256"):
        head = ("AllData.json 變了(錄快照時與現在的門牌庫雜湊不同):先確認是不是資料檔更新"
                "造成的地址/郵遞區號差異,再判斷程式有沒有變。\n" + head)
    return "\n".join([head, *cells])


def _keep_path(today: str, p: Path | None = None) -> Path:
    """舊快照的留存檔名;同一天錄多次就加序號,絕不蓋掉已留存的。"""
    p = p or snapshot_path()
    keep = p.with_name(f"{p.stem}.{today}.json")
    n = 2
    while keep.exists():
        keep = p.with_name(f"{p.stem}.{today}.{n}.json")
        n += 1
    return keep


def record(out: Callable[[str], None] = print,
           extract: Callable[[str], dict] | None = None,
           data_hash: str | None = None,
           today: str | None = None) -> Path | None:
    """重錄快照:印逐格 diff → 舊快照加日期留存 → 寫入新快照。沒有變化就什麼都不寫。"""
    data_hash = alldata_sha256() if data_hash is None else data_hash
    today = today or date.today().isoformat()
    if not rs.vision_stems():
        out("沒有任何 Vision 全文,不錄(否則每份素材都會被當成已移除)。")
        return None
    new = current_fields(extract=extract)
    saved = load()
    old_fields = saved["fields"] if saved else {}

    lines = []
    for stem in new:
        lines += _changed_cells(stem, old_fields.get(stem), new[stem])
    removed = sorted(set(old_fields) - set(new))
    lines += [f"{stem}: 素材已不在,從快照移除" for stem in removed]
    hash_changed = saved is not None and saved.get("alldata_sha256") != data_hash
    if hash_changed:
        lines.insert(0, "⚠ AllData.json 與上次錄製時不同:地址/郵遞區號的差異可能來自資料檔")

    if not lines:
        out("快照沒有變化,不寫入。")
        return None
    for line in lines:
        out(line)

    p = snapshot_path()
    if p.exists():
        keep = _keep_path(today)
        p.rename(keep)
        out(f"舊快照留存為 {keep.name}")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"alldata_sha256": data_hash, "recorded": today, "fields": new},
                            ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
    out(f"已寫入 {p}({len(new)} 份)")
    return p


# ─── 送審快照(#38):許可證掃描結果 → 每份 docx 的送審決策 ─────────────────
#
# 送審決策(CONTEXT.md)要收成一個 module(#40),這張網守住重構前後每份 docx 的
# 去向、候選值、被選的那張圖。與 H~O 快照同一套規矩:pytest 只比對,重錄先印 diff、
# 舊檔加日期留存。記成 {docx: [決定, …]} 的清單:#40 之前一份 docx 可能產生多筆,
# 快照錄的就是那時的樣子;#40 之後每份只有一筆 —— 哪天又出現兩筆,diff 會直接看得到。

def decisions_snapshot_path() -> Path:
    return rs.ROOT / "snapshots" / "submission_decisions.json"


def _default_decisions(csv_path: Path) -> dict[str, list[dict[str, str]]]:
    """現行送審程式 → 快照的形狀。#40 重構時只換這一層。"""
    import pipeline
    vision, manual, keyin = pipeline.build_vision_queue(csv_path)
    out: dict[str, list[dict[str, str]]] = {}

    def add(docx, dest, value, image):
        out.setdefault(docx, []).append({"去向": dest, "候選值": value, "圖": image})

    for i in keyin:
        add(i.source_docx, "直接key-in", i.candidate_value, i.image_name)
    for i in vision:
        add(i.source_docx, "反查救援" if i.no_vote_rescue else "送Vision",
            i.candidate_value, i.image_name)
    for d in manual:                      # 全無命中:主流程一律交反查救援
        add(d["source_docx"], "反查救援", "", "")
    return out


def current_decisions(decide: Callable[[Path], dict] | None = None) -> dict[str, list[dict[str, str]]]:
    """{docx: [決定…]} —— 每份許可證掃描結果現在的送審決策。"""
    decide = decide or _default_decisions
    out: dict[str, list[dict[str, str]]] = {}
    for csv_path in rs.permit_scans():
        out.update(decide(csv_path))
    return out


def load_decisions() -> dict | None:
    p = decisions_snapshot_path()
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def _decision_changes(old: dict, new: dict) -> list[str]:
    lines = []
    for docx in sorted(set(old) | set(new)):
        if docx not in new:
            lines.append(f"{docx}: 素材已不在,從快照移除")
        elif docx not in old:
            lines.append(f"{docx}: {new[docx]}")
        elif old[docx] != new[docx]:
            lines.append(f"{docx}: {old[docx]} → {new[docx]}")
    return lines


def decisions_mismatch_message(saved: dict, now: dict) -> str | None:
    """現在的送審決策與快照不同時的說明;相同則 None。只讀,絕不寫。"""
    lines = _decision_changes(saved["decisions"], now)
    if not lines:
        return None
    head = ("送審快照對不上(行為變了,不一定是錯;是刻意的改動就用 python snapshots.py record"
            " 重錄並逐份看過):")
    return "\n".join([head, *lines])


def record_decisions(out: Callable[[str], None] = print,
                   decide: Callable[[Path], dict] | None = None,
                   today: str | None = None) -> Path | None:
    """重錄送審快照:印 diff → 舊快照加日期留存 → 寫入。沒有變化就什麼都不寫。"""
    today = today or date.today().isoformat()
    if not rs.permit_scans():
        out("沒有任何許可證掃描結果,不錄送審快照。")
        return None
    new = current_decisions(decide)
    saved = load_decisions()
    lines = _decision_changes(saved["decisions"] if saved else {}, new)
    if not lines:
        out("送審快照沒有變化,不寫入。")
        return None
    for line in lines:
        out(line)
    p = decisions_snapshot_path()
    if p.exists():
        keep = _keep_path(today, p)
        p.rename(keep)
        out(f"舊送審快照留存為 {keep.name}")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"recorded": today, "decisions": new},
                            ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
    out(f"已寫入 {p}({len(new)} 份)")
    return p


# ─── 新素材:補 Vision 全文與契約頁核對值(#33) ──────────────────────────

ASK_PAGE = "  y=對 / 圖檔名=改用那張 / g=只打不記核對值 / Enter=跳過:"


def _vision_ok() -> tuple[bool, str]:
    """真的送一張小圖確認 Vision 可用(約 1 unit)。"""
    import identity
    from gcp_identity.checks import check_vision
    r = check_vision(identity.current())
    return (r.ok and not r.skipped), (r.detail or r.fix)


def _fetch_text(content: bytes) -> str:
    import google_clients
    from employer_extract import vision_full_text
    return vision_full_text(content, client=google_clients.vision_client())


def _prepare(raw: bytes) -> bytes:
    """與正式流程同樣的送 Vision 前處理:ROI 裁切 → 去紅章。"""
    from employer_extract import EMPLOYER_ROI, crop_fraction, deink_red_stamp
    return deink_red_stamp(crop_fraction(raw, EMPLOYER_ROI))


def _score_pages(docx: Path) -> list[tuple[int, str, bytes]]:
    from employer_extract import _scored_pages
    return _scored_pages(str(docx))


def fill_missing_vision(ask: Callable[[str], str] = input,
                        out: Callable[[str], None] = print,
                        vision_ok: Callable[[], tuple[bool, str]] = _vision_ok,
                        fetch_text: Callable[[bytes], str] = _fetch_text,
                        score_pages: Callable[[Path], list] = _score_pages,
                        prepare: Callable[[bytes], bytes] = _prepare) -> list[str]:
    """有 docx、沒有 Vision 全文的素材:選契約頁 → 人確認 → 每份打一次 Vision 存全文。

    契約頁用正式流程同一套規則(pick_contract_page)。人回答「對」或指定別張,
    那張才記成契約頁核對值;只說「打」不記 —— 程式不把機器的選擇升格成真值。
    打之前先列出共幾次並等確認,再確認 Vision 可用;不可用就停,不重試。
    回傳這次存了全文的 stem。
    """
    from employer_extract import EMPLOYER_ROI, pick_contract_page

    missing = [d for d in rs.docx_samples() if not rs.vision_path(d.stem).exists()]
    if not missing:
        return []
    out(f"{len(missing)} 份素材還沒有 Vision 全文。請打開圖檔確認契約頁:")

    plan: list[tuple[str, str, bytes]] = []
    for docx in missing:
        scored = score_pages(docx)
        pick = pick_contract_page(scored)
        if pick is None:
            out(f"{docx.name}:docx 無內嵌圖,略過")
            continue
        score, name, raw, found = pick
        second = max((s for s, n, _b in scored if n != name), default=0)
        # 每一頁都匯出:人要改指定別張,得先看得到別張。
        review_dir = rs.ROOT / "review"
        review_dir.mkdir(parents=True, exist_ok=True)
        for _s, n, b in scored:
            (review_dir / f"{docx.stem}_{n}").write_bytes(b)
        review = review_dir / f"{docx.stem}_{name}"
        out(f"{docx.name} → {name}(分數 {score},次高 {second})"
            + ("" if found else "  ⚠ 未達門檻,正式流程不會送 Vision")
            + f"  圖:{review}")
        pages = {n: b for _s, n, b in scored}
        while True:
            answer = ask(ASK_PAGE).strip()
            if answer.lower() in ("y", "g"):
                answer = answer.lower()
            if answer == "":
                break
            if answer == "y" or answer in pages:
                page = name if answer == "y" else answer
                rs.record_checked_contract_page(docx.name, page)
                plan.append((docx.stem, page, pages[page]))
                if page != name or not found:
                    out(f"  ⚠ 正式流程不會選 {page}(它選 {name}"
                        + ("" if found else ",且未達門檻") + "):契約頁評分該修,"
                        "pytest -m real 的契約頁測試會因此變紅")
                break
            if answer == "g":
                if not found:
                    out("  未達門檻的候選頁不送 Vision:請指定圖檔名,或 Enter 跳過")
                    continue
                plan.append((docx.stem, name, raw))
                break
            out(f"  看不懂 {answer!r},請重答")

    if not plan:
        return []
    if ask(f"共 {len(plan)} 次 Vision,開打?[y/N]").strip().lower() != "y":
        out("未打 Vision。")
        return []
    ok, detail = vision_ok()
    if not ok:
        out(f"Vision 不可用,停止(不重試):{detail}"
            "\n若是要求重新登入,請先跑 重新登入.bat")
        return []

    roi = str(EMPLOYER_ROI[3])     # ROI 下緣比例,與既有素材的 texts 鍵同格式("0.35")
    done = []
    for stem, page, raw in plan:
        rs.save_vision_text(stem, page, roi, fetch_text(prepare(raw)))
        done.append(stem)
        out(f"{stem}:已存 Vision 全文({page})")
    return done


def main(argv: list[str]) -> int:
    if argv[1:] != ["record"]:
        print("用法:python snapshots.py record")
        return 2
    fill_missing_vision()
    record()
    record_decisions()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
