# -*- coding: utf-8 -*-
"""ScanResult ↔ matches.csv 的 round-trip 對稱測試(#14)。

to_csv_row 寫出字面值("Y"/"✓"/"80.0"),from_csv_row 比對同一組字面值讀回。
兩端必須手動保持對齊:只改寫出端的 "✓"、忘了改讀回端,cross_match 會永遠讀回
False,整批安靜地改走衝突路徑——不拋錯、不少欄。

這裡不逐格釘字面值,而是從 dataclass 自動列舉所有欄位:
  - 每個欄位單獨設成非預設值 → 寫成 CSV 檔 → 讀回 → 要相等
    (一次只動一格,兩欄互相寫錯位置也抓得到)
  - 所有欄位同時設成非預設值 → 同上
以後 ScanResult 新增欄位,自動被涵蓋;忘了加進 CSV_FIELDS 也會紅。

round-trip 抓不到「寫出端與讀回端一起改」——但那會讓磁碟上既有的 matches.csv
讀錯,所以另外用一列磁碟格式釘住 bool 欄位的字面值。

CSV 檔的寫法(CSV_FIELDS、utf-8-sig、newline="")照 pipeline 寫 matches.csv
與 build_vision_queue 讀回的方式,讓 "✓" 這類非 ASCII 字面值真的過一次編碼。
"""

import csv
import dataclasses
import typing
from enum import Enum

import pytest

from pipeline import CSV_FIELDS, ResultStatus, ScanResult


_HINTS = typing.get_type_hints(ScanResult)
_FIELDS = list(dataclasses.fields(ScanResult))


def _non_default(field: dataclasses.Field):
    """給欄位一個「與預設值不同、且 CSV 能原樣表達」的值。"""
    t = _HINTS[field.name]
    if t is bool:
        return True
    if t is int:
        return 2                     # scan_tier 預設 1,其他預設 0
    if t is float:
        return 67.3                  # to_csv_row 寫一位小數
    if isinstance(t, type) and issubclass(t, Enum):
        members = [m for m in t if m != field.default]
        return members[-1]
    if t is str:
        return f"值-{field.name}"
    raise TypeError(f"{field.name}: 測試不認得型別 {t!r},請在 _non_default 補上")


def _minimal_result() -> ScanResult:
    """只填必填欄位,其餘維持預設值。"""
    return ScanResult(source_docx="a.docx", image_name="image1.png", docx_class="large")


def _through_csv_file(r: ScanResult, tmp_path) -> ScanResult:
    path = tmp_path / "matches.csv"
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerow(r.to_csv_row())
    with open(path, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 1
    return ScanResult.from_csv_row(rows[0])


def test_csv_fields_cover_every_scanresult_field():
    assert sorted(CSV_FIELDS) == sorted(f.name for f in _FIELDS)
    assert len(CSV_FIELDS) == len(set(CSV_FIELDS))


def test_defaults_round_trip(tmp_path):
    r = _minimal_result()
    assert _through_csv_file(r, tmp_path) == r


@pytest.mark.parametrize("field", _FIELDS, ids=lambda f: f.name)
def test_each_field_round_trips_alone(field, tmp_path):
    r = dataclasses.replace(_minimal_result(), **{field.name: _non_default(field)})
    assert getattr(r, field.name) != getattr(_minimal_result(), field.name), "測試值必須與預設不同,否則驗不到東西"
    assert _through_csv_file(r, tmp_path) == r


def test_all_fields_non_default_round_trip(tmp_path):
    r = ScanResult(**{f.name: _non_default(f) for f in _FIELDS})
    for f in _FIELDS:
        if f.default is not dataclasses.MISSING:
            assert getattr(r, f.name) != f.default, f.name
    assert _through_csv_file(r, tmp_path) == r


@pytest.mark.parametrize("status", list(ResultStatus), ids=lambda s: s.name)
def test_every_status_round_trips(status, tmp_path):
    r = dataclasses.replace(_minimal_result(), status=status)
    assert _through_csv_file(r, tmp_path) == r


# 既有 matches.csv 上的字面值。要改格式，得連舊檔的讀法一起想清楚，不能兩端一起換字。
_DISK_LITERALS = {
    "mol_from_vote": "Y",
    "id_from_vote":  "Y",
    "cross_match":   "✓",
    "vision_review": "Y",
}


def test_bool_literals_match_existing_csv_files():
    on_disk = {"source_docx": "a.docx", "image_name": "image1.png",
               "docx_class": "large", **_DISK_LITERALS}
    r = ScanResult.from_csv_row(on_disk)
    for name in _DISK_LITERALS:
        assert getattr(r, name) is True, name
    written = r.to_csv_row()
    assert {k: written[k] for k in _DISK_LITERALS} == _DISK_LITERALS
