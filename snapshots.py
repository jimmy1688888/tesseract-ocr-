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
"""
import hashlib
import json
import sys
from datetime import date
from pathlib import Path
from typing import Callable

import real_samples as rs

# 與 Sheets H~O 同一組鍵(pipeline._EMPLOYER_COL_KEYS),外加錨定失敗原因。
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


def _keep_path(today: str) -> Path:
    """舊快照的留存檔名;同一天錄多次就加序號,絕不蓋掉已留存的。"""
    p = snapshot_path()
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


def main(argv: list[str]) -> int:
    if argv[1:] != ["record"]:
        print("用法:python snapshots.py record")
        return 2
    record()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
