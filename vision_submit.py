# -*- coding: utf-8 -*-
"""
vision_submit.py
================
讀取 scan.py 產生的 matches.csv，依規則決定每個 docx 要送 Google Vision 的圖。
輸出 vision_queue.csv，欄位：source_docx, image_name, img_path, candidate_value, reason

規則：
  Large docx：
    A. mol 與 permit 均有值
       - 值相同（檔案層級 cross match）→ 取最高 conf 那張；conf > 55 不送 Vision
       - 值不同（衝突）                  → 取最高 conf 那張送 Vision
    B. 僅 permit，且 note 含「permit部分命中無多數」
       → 該張 permit_crop 送 Vision
    C. 其餘已標記 vision_review=Y
       → 照原標記送 Vision
  Small docx：
    全部列 note 為「small:無命中」→ 不送 Vision，標記 manual_review
    否則 → vision_review=Y 的圖送 Vision
"""

import csv
import sys
from pathlib import Path
from collections import defaultdict

INPUT_CSV  = Path("scan_results/matches.csv")
OUTPUT_CSV = Path("scan_results/vision_queue.csv")
CONF_KEY_IN = 55


def to_f(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def best_img_path(row: dict) -> str:
    return row.get("permit_crop") or row.get("mol_crop") or ""


def process_large(rows: list[dict]) -> list[dict]:
    """large docx：檔案層級合併判斷，回傳要送 Vision 的列。"""
    queue = []

    mol_rows    = [(r, r["mol"], to_f(r["mol_conf"]))
                   for r in rows if r.get("mol")]
    permit_rows = [(r, r["id"],  to_f(r["id_conf"]))
                   for r in rows if r.get("id")]

    # ── 規則A：mol + permit 均有值 ─────────────────────────────────────
    if mol_rows and permit_rows:
        mol_values    = {v for _, v, _ in mol_rows}
        permit_values = {v for _, v, _ in permit_rows}
        common        = mol_values & permit_values

        if common:
            # 檔案層級 cross match：取最高 conf 那張
            candidates = [(r, v, c) for r, v, c in mol_rows + permit_rows
                          if v in common]
            best_row, best_val, best_conf = max(candidates, key=lambda x: x[2])
            if best_conf > CONF_KEY_IN:
                return []  # 高信心且吻合 → 直接 key-in，不送 Vision
            queue.append({
                "source_docx":     best_row["source_docx"],
                "image_name":      best_row["image_name"],
                "img_path":        best_img_path(best_row),
                "candidate_value": best_val,
                "reason":          f"cross_match(file)_低信心 conf={best_conf}",
            })
            return queue
        else:
            # 值衝突：取最高 conf 那張送 Vision
            all_candidates = mol_rows + permit_rows
            best_row, best_val, best_conf = max(all_candidates, key=lambda x: x[2])
            queue.append({
                "source_docx":     best_row["source_docx"],
                "image_name":      best_row["image_name"],
                "img_path":        best_img_path(best_row),
                "candidate_value": best_val,
                "reason":          f"mol≠permit衝突_最高conf={best_conf}",
            })
            return queue

    # ── 規則B：permit 部分命中無多數 ────────────────────────────────────
    partial_rows = [r for r in rows
                    if "permit部分命中無多數" in r.get("note", "")]
    for r in partial_rows:
        queue.append({
            "source_docx":     r["source_docx"],
            "image_name":      r["image_name"],
            "img_path":        r.get("permit_crop") or best_img_path(r),
            "candidate_value": r.get("id") or r.get("final_value", ""),
            "reason":          "permit部分命中無多數",
        })

    # ── 規則C：其餘 vision_review=Y ─────────────────────────────────────
    handled = {r["image_name"] for r in partial_rows}
    for r in rows:
        if r.get("vision_review") == "Y" and r["image_name"] not in handled:
            queue.append({
                "source_docx":     r["source_docx"],
                "image_name":      r["image_name"],
                "img_path":        best_img_path(r),
                "candidate_value": r.get("final_value", ""),
                "reason":          r.get("note", "vision_review=Y"),
            })

    return queue


def process_small(rows: list[dict]) -> list[dict]:
    """small docx：全部無命中 → 人工審查；否則依 vision_review 送件。"""
    all_no_match = all("small:無命中" in r.get("note", "") for r in rows)
    if all_no_match:
        return []  # 不送 Vision，人工審查

    queue = []
    for r in rows:
        if r.get("vision_review") == "Y":
            queue.append({
                "source_docx":     r["source_docx"],
                "image_name":      r["image_name"],
                "img_path":        best_img_path(r),
                "candidate_value": r.get("final_value", ""),
                "reason":          r.get("note", "vision_review=Y"),
            })
    return queue


def main():
    if not INPUT_CSV.exists():
        print(f"找不到 {INPUT_CSV}")
        sys.exit(1)

    # 讀取 CSV，依 source_docx 分組
    groups: dict[str, list[dict]] = defaultdict(list)
    with open(INPUT_CSV, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            groups[row["source_docx"]].append(row)

    fieldnames = [
        "source_docx", "image_name", "img_path",
        "candidate_value", "reason",
    ]
    manual_review = []
    total_queue   = 0

    OUTPUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for docx_name, rows in sorted(groups.items()):
            docx_class = rows[0]["docx_class"]

            if docx_class == "large":
                queue = process_large(rows)
            else:
                queue = process_small(rows)
                # 全部無命中 → 記錄人工審查清單
                if not queue and all("small:無命中" in r.get("note", "") for r in rows):
                    manual_review.append(docx_name)

            for item in queue:
                writer.writerow(item)
                total_queue += 1

    print(f"\n送件清單：{OUTPUT_CSV}  ({total_queue} 筆)")
    if manual_review:
        print(f"\n人工審查（small 全無命中，{len(manual_review)} 件）：")
        for d in manual_review:
            print(f"  {d}")


if __name__ == "__main__":
    main()
