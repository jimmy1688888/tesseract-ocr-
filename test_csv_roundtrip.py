# -*- coding: utf-8 -*-
"""ScanResult ↔ matches.csv 的 round-trip 對稱測試(#14)。

to_csv_row 寫出字面值("Y"/"✓"/"80.0"),from_csv_row 比對同一組字面值讀回。
兩端必須手動保持對齊:只改寫出端的 "✓"、忘了改讀回端,cross_match 會永遠讀回
False,整批安靜地改走衝突路徑——不拋錯、不少欄。

這裡不逐格釘字面值,而是從 dataclass 自動列舉所有欄位:
  - 每個欄位單獨設成非預設值 → 寫成 CSV 檔 → 讀回 → 要相等
    (一次只動一格,兩欄互相寫錯位置也抓得到)
  - 所有欄位同時設成非預設值 → 同上
以後 ScanResult 新增欄位,自動被涵蓋;忘了加進 FIELDS 也會紅。

round-trip 抓不到「寫出端與讀回端一起改」——但那會讓磁碟上既有的 matches.csv
讀錯,所以另外用一列磁碟格式釘住 bool 欄位的字面值。

寫檔、讀檔都經 matches_csv(與許可證掃描、送審決策同一套),讓 "✓" 這類非 ASCII
字面值真的過一次編碼。
"""

import dataclasses
import typing
from enum import Enum

import pytest

import matches_csv
from matches_csv import FIELDS, ResultStatus, ScanResult, UnknownStatus


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
    matches_csv.write(path, [r])
    [[back]] = matches_csv.read(path).values()
    return back


def test_csv_fields_cover_every_scanresult_field():
    assert sorted(FIELDS) == sorted(f.name for f in _FIELDS)
    assert len(FIELDS) == len(set(FIELDS))


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
    "manual_review": "Y",
}


def test_bool_literals_match_existing_csv_files():
    on_disk = {"source_docx": "a.docx", "image_name": "image1.png",
               "docx_class": "large", **_DISK_LITERALS}
    r = ScanResult.from_csv_row(on_disk)
    for name in _DISK_LITERALS:
        assert getattr(r, name) is True, name
    written = r.to_csv_row()
    assert {k: written[k] for k in _DISK_LITERALS} == _DISK_LITERALS


# ADR-0013:matches.csv 只存讀值;判斷(要不要送 Vision、winner、最終值)只在送審決策。
_JUDGEMENT_COLUMNS = {"final_value", "final_conf", "vision_review"}


def test_matches_csv_holds_readings_only():
    assert not _JUDGEMENT_COLUMNS & set(FIELDS)
    assert not _JUDGEMENT_COLUMNS & {f.name for f in dataclasses.fields(ScanResult)}


def test_old_files_with_judgement_columns_still_read():
    """ADR-0013 之前寫的 matches.csv(真實素材的某日掃描、舊 21 份)帶這三欄:讀進來忽略。"""
    base = {"source_docx": "a.docx", "image_name": "image1.png", "docx_class": "large",
            "id": "1234", "id_conf": "70.0"}
    old = {**base, "final_value": "1234", "final_conf": "70.0", "vision_review": "Y"}
    assert ScanResult.from_csv_row(old) == ScanResult.from_csv_row(base)


# ─── status:不認得的值拋例外,空字串是沒有這欄的舊檔(#56) ───────────────

def test_unknown_status_raises_naming_docx_and_value():
    with pytest.raises(UnknownStatus, match=r"32503\.docx.*'vision_pending'"):
        ScanResult.from_csv_row({"source_docx": "32503.docx", "status": "vision_pending"})


def test_unknown_status_in_file_raises_on_read(tmp_path):
    path = tmp_path / "matches.csv"
    path.write_text("source_docx,image_name,docx_class,status\n"
                    "a.docx,image5.jpeg,large,LARGE_NO_HIT\n", encoding="utf-8-sig")
    with pytest.raises(UnknownStatus, match="a.docx"):
        matches_csv.read(path)


def test_empty_status_is_ok():
    """沒有 status 欄的舊檔:當正常讀值。"""
    assert ScanResult.from_csv_row({"source_docx": "a.docx"}).status is ResultStatus.OK
    assert ScanResult.from_csv_row({"source_docx": "a.docx", "status": ""}).status is ResultStatus.OK


# ─── 讀回成每份 docx 的列 ─────────────────────────────────────────────────

def test_read_groups_by_docx_sorted_by_name_keeping_row_order(tmp_path):
    def r(docx, img):
        return ScanResult(source_docx=docx, image_name=img, docx_class="large")
    path = tmp_path / "matches.csv"
    matches_csv.write(path, [r("b.docx", "image6.jpeg"), r("a.docx", "image5.jpeg"),
                             r("b.docx", "image2.jpeg")])

    groups = matches_csv.read(path)

    assert list(groups) == ["a.docx", "b.docx"]
    assert [x.image_name for x in groups["b.docx"]] == ["image6.jpeg", "image2.jpeg"]


def test_matches_csv_imports_nothing_from_the_project():
    """leaf module:讀 matches.csv 不必載入掃描或整條流程。"""
    import ast
    from pathlib import Path
    tree = ast.parse(Path(matches_csv.__file__).read_text(encoding="utf-8"))
    imported = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    imported |= {n.module.split(".")[0] for n in ast.walk(tree)
                 if isinstance(n, ast.ImportFrom) and n.module}
    project = {p.stem for p in Path(matches_csv.__file__).parent.glob("*.py")}
    assert not imported & project
