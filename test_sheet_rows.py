# -*- coding: utf-8 -*-
"""寫進 Sheets 的列:六種來源各自建出的列,十五欄逐格釘住(#12)。

#12 之前四種形狀匯進 _row_to_sheet_values(VisionQueueItem、manual_review dict、
Vision 結果 dict、novote_rescue dict),還有 main 裡一次現場翻譯;重構只能改形狀,
不能改寫進 Sheets 的任何一格。這裡跑真的 main,替身只放在邊界:掃描(run_scan)、
送審決策(decide)、Vision 全文、次頁台仲區塊、名冊、雇主擷取、Sheets。

名冊、台仲區塊、許可證全部捏造。
"""
import argparse
import csv

import pytest

import employer_extract
import google_clients
import pipeline as P
from employer_result import EmployerExtraction, EmployerValues, ExtractionStatus
from submission_decision import Decision, Destination


# ─── 捏造名冊 ─────────────────────────────────────────────────────────────

def _agency(permit, name):
    return {"許可證": permit, "機構名稱": f"{name}仲介", "機構地址": f"{name}地址",
            "電話": f"02{permit * 2}"}


AGENCIES = {p: _agency(p, n) for p, n in
            [("1111", "甲"), ("2222", "乙"), ("3333", "丙"), ("4444", "丁"),
             ("5555", "戊"), ("6666", "己"), ("9999", "庚")]}


class FakeRoster:
    """名冊替身:四級反查各有一條路。"""
    by_phone = {"0211111111": ["1111"], "0244444444": ["4444", "5555"]}
    by_prefix = {"022222222": ["2222"]}
    by_name = {"丙仲介": ("3333", 0.9)}
    by_address = {"己地址": ("6666", 0.8), "丙地址": ("3333", 0.95)}

    def lookup(self, permit):
        return AGENCIES.get(permit)

    def agencies_by_phone(self, phone):
        return [AGENCIES[p] for p in self.by_phone.get(phone, [])]

    def agencies_by_phone_prefix(self, phone):
        return [AGENCIES[p] for p in self.by_prefix.get(phone, [])]

    def _best(self, table, key):
        hit = table.get(key)
        return {**AGENCIES[hit[0]], "_ratio": hit[1]} if hit else None

    def best_by_name(self, name):
        return self._best(self.by_name, name)

    def best_by_address(self, address):
        return self._best(self.by_address, address)


def _block(phones=(), name="", address=""):
    return {"phones": list(phones), "name": name, "address": address}


# 次頁台仲區塊(依 docx)
BLOCKS = {
    "nv_phone.docx":  _block(["0211111111"]),
    "nv_miss.docx":   _block(),
    "nh_phone.docx":  _block(["0299999999", "0211111111"]),   # 第一支查無、第二支唯一
    "nh_prefix.docx": _block(["022222222"]),
    "nh_name.docx":   _block(name="丙仲介", address="己地址"),   # 名稱 0.9 勝地址 0.8
    "nh_addr.docx":   _block(name="查無", address="丙地址"),
    "nh_ambig.docx":  _block(["0244444444"]),
    "nh_ambig_name.docx": _block(["0244444444"], name="丙仲介"),  # 多家但名稱救得回
    "nh_none.docx":   _block(),
}


def _decision(name, dest, value="", conf=0.0, image="", crop="", roi="", reason=""):
    return Decision(source_docx=name, destination=dest, candidate_value=value,
                    candidate_conf=conf, image_name=image, img_path=crop, hit_roi=roi,
                    reason=reason)


@pytest.fixture
def world(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    docs = tmp_path / "docs"
    docs.mkdir()
    crop_ok, crop_bad = tmp_path / "ok.png", tmp_path / "bad.png"
    crop_ok.write_bytes(b"x")
    crop_bad.write_bytes(b"x")
    texts = {str(crop_ok): "No. Izin: 2222", str(crop_bad): "No. Izin: 8888"}

    V, R = Destination.送VISION, Destination.反查救援
    decisions = [
        _decision("k.docx", Destination.直接KEYIN, "1111", 80.0, "image5.jpeg",
                  reason="large:permit多數票"),
        _decision("v_ok.docx", V, "2222", 40.0, "image5.jpeg", str(crop_ok),
                  "permit_upper", "large:讀值信心不足"),
        _decision("v_bad.docx", V, "3333", 40.0, "image5.jpeg", str(crop_bad),
                  "permit_upper", "large:讀值信心不足"),
        _decision("v_nocrop.docx", V, "4444", 40.0, "image5.jpeg",
                  str(tmp_path / "gone.png"), "permit_upper", "large:讀值信心不足"),
        _decision("nv_phone.docx", R, "9999", 30.0, "image5.jpeg", reason="permit部分命中無多數"),
        _decision("nv_miss.docx", R, "9999", 30.0, "image5.jpeg", reason="permit部分命中無多數"),
        *[_decision(n, R, reason="large:全無命中") for n in
          ("nh_phone.docx", "nh_prefix.docx", "nh_name.docx", "nh_addr.docx",
           "nh_ambig.docx", "nh_ambig_name.docx", "nh_none.docx")],
        _decision("dr.docx", Destination.直接人工審查, reason="docx 無法開啟(不是有效的 docx 檔)"),
    ]
    by_name = {d.source_docx: d for d in decisions}
    for d in decisions:
        (docs / d.source_docx).write_bytes(b"not a zip")

    def fake_scan(batch, **kw):
        path = batch.matches_csv
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=P.CSV_FIELDS)
            w.writeheader()
            for p in batch.docx_files:
                w.writerow(P.ScanResult(source_docx=p.name, image_name="x", docx_class="large").to_csv_row())
        return path

    employer = {"k.docx": EmployerExtraction(
        ExtractionStatus.擷取成功,
        values=EmployerValues(雇主名稱_中="王小明", 雇主名稱_英="WANG XIAO MING",
                              地址_中="某市某路1號", 地址_中_標準="某市某路1號",
                              地址_英="No.1, X Rd.", 郵遞區號="999", 電話="0912345678"))}

    sheets = []
    monkeypatch.setattr(P, "run_scan", fake_scan)
    monkeypatch.setattr(P, "decide", lambda rows: by_name[rows[0].source_docx])
    monkeypatch.setattr(P, "vision_text", lambda path: texts[path])
    monkeypatch.setattr(P, "collect_employer_fields", lambda batch: employer)
    monkeypatch.setattr(P, "_permit_lookup", lambda: FakeRoster())
    monkeypatch.setattr(employer_extract, "agency_block_from_next_page",
                        lambda path, client: BLOCKS[path.rsplit("\\", 1)[-1].rsplit("/", 1)[-1]])
    monkeypatch.setattr(google_clients, "vision_client", lambda: object())
    monkeypatch.setattr(P, "_sheets_append_atomic", sheets.extend)
    return sheets


def _opts():
    return argparse.Namespace(file="", image="", roi="", full_scan=False,
                              preflight_only=False, skip_preflight=True)


def _agency_cols(permit):
    a = AGENCIES.get(permit)
    return [a["機構名稱"], a["機構地址"], a["電話"]] if a else ["", "", ""]


def _row(docx, permit, status, reason, employer=("",) * 8):
    return [docx, permit, status, reason, *_agency_cols(permit), *employer]


KEYED, REVIEW = "keyed-in", "manual_review"
NOVOTE = "[單一判讀低信心 conf=30.0<50,略過 ROI Vision 改走次頁電話反查] permit部分命中無多數"
NOHIT = "large:全無命中"

# #12 之前的程式寫出的十五欄(快照,不是核對值),逐格(順序也是:key-in 組在前、人工審查組在後)。
BEFORE_12 = [
    _row("k.docx", "1111", KEYED, "large:permit多數票",
         ("王小明", "WANG XIAO MING", "某市某路1號", "某市某路1號", "",
          "No.1, X Rd.", "999", "0912-345-678")),
    _row("v_ok.docx", "2222", KEYED,
         "[送Vision原因] large:讀值信心不足 ／ [Vision結果] Tesseract 與 Vision 雙引擎一致:'2222'"),
    _row("v_bad.docx", "8888", REVIEW,
         "[送Vision原因] large:讀值信心不足 ／ [Vision結果] Tesseract='3333' 與 Vision='8888' "
         "差異 4 字元,兩引擎不一致,需人工確認"),
    _row("v_nocrop.docx", "4444", REVIEW,
         "[送Vision原因] large:讀值信心不足 ／ 截圖檔不在,未送 Vision"),
    _row("nv_miss.docx", "9999", REVIEW, NOVOTE),
    _row("nv_phone.docx", "1111", REVIEW,
         f"{NOVOTE}；[電話反查救回] 次頁台仲電話 0211111111 → 許可證 1111／甲仲介(仍請人工核對)"),
    _row("nh_addr.docx", "3333", REVIEW,
         f"{NOHIT}；[地址反查救回·電話無唯一命中] 次頁台仲地址 → 許可證 3333／丙仲介"
         f"(名冊相似度 0.95,仍請人工核對)"),
    _row("nh_ambig.docx", "", REVIEW,
         f"{NOHIT}；[電話反查·需人工擇一] 台仲電話 0244444444 命中2 家:4444／丁仲介、5555／戊仲介"),
    _row("nh_ambig_name.docx", "3333", REVIEW,
         f"{NOHIT}；[名稱反查救回·電話無唯一命中] 次頁台仲名稱 → 許可證 3333／丙仲介"
         f"(名冊相似度 0.9,仍請人工核對)"),
    _row("nh_name.docx", "3333", REVIEW,
         f"{NOHIT}；[名稱反查救回·電話無唯一命中] 次頁台仲名稱 → 許可證 3333／丙仲介"
         f"(名冊相似度 0.9,仍請人工核對)"),
    _row("nh_none.docx", "", REVIEW, NOHIT),
    _row("nh_phone.docx", "1111", REVIEW,
         f"{NOHIT}；[電話反查救回] 次頁台仲電話 0211111111 → 許可證 1111／甲仲介(仍請人工核對)"),
    _row("nh_prefix.docx", "2222", REVIEW,
         f"{NOHIT}；[電話前綴反查救回·電話OCR可能少碼] 次頁台仲電話 022222222 "
         f"前綴唯一命中 → 許可證 2222／乙仲介(仍請人工核對)"),
    _row("dr.docx", "", REVIEW, "docx 無法開啟(不是有效的 docx 檔)"),
]


def test_fifteen_columns_cell_by_cell(world):
    P.main(_opts())
    assert world == BEFORE_12
