# -*- coding: utf-8 -*-
"""拿不到 Google 憑證時,三個用 Vision 的地方各降級成什麼(#16)。

以前 employer_extract 自己延遲 import pipeline 拿 Vision client,憑證失敗會從
「import 時」搬到「第一次呼叫時」、被雇主擷取的 try/except 吞成一行 warning;
許可證那一側卻是整批中止。同一條 seam 的兩個方向錯誤模式不對稱,而兩側都沒寫下來。
#3 之後三處都跟 google_clients 要 client;#71 起 main 一批只建一個 Vision reader,
三處共用,這裡把現況釘住:

  許可證判讀(送 Vision)          → 不降級,例外往上拋,整批中止(還沒寫 Sheets)
  雇主欄位                        → 不降級,例外往上拋,整批中止(還沒寫 Sheets)
  一次執行(main)                → reader 在掃描後、送審決策之前建;建不起來整批中止,
                                     Sheets 一列都不寫 —— 次頁反查救援因此不會走到

寧可整批不寫,也不要寫一批「B 欄沒驗、H~O 全空」看起來正常的列;正常情況下
preflight 的憑證檢查會在掃描前先攔下來,這裡是 --skip-preflight 或憑證中途失效時的
最後一道。救援以前自己建 reader、建不起來就降級成救回 0 筆 —— 在 main 裡那條路
走不到(送 Vision 那步先建 reader、先中止),#71 刪掉。
"""
import argparse

import pytest

import identity
from submission_decision import Decision, Destination
import pipeline as P
from batch import Batch
from vision_reader import GoogleVisionReader
from vision_verification import run_vision_items


class NoCredentials(Exception):
    """代表任何「換不到憑據」的失敗(ADC 過期、沒有模擬權限……)。"""


class _NoCredentialsIdentity:
    sa_email, project_id, label = "sa@test.iam", "proj-test", "no-creds"

    def credentials(self, scopes=()):
        raise NoCredentials("換不到憑據")


@pytest.fixture(autouse=True)
def no_credentials():
    identity.install(_NoCredentialsIdentity())


def test_permit_reading_aborts_the_run(tmp_path):
    crop = tmp_path / "crop.png"
    crop.write_bytes(b"x")
    item = Decision(source_docx="a.docx", destination=Destination.送VISION,
                      image_name="image5.jpeg", img_path=str(crop),
                      reason="permit部分命中無多數", hit_roi="permit_lower",
                      candidate_value="1234", candidate_conf=60.0)
    with pytest.raises(NoCredentials):
        run_vision_items([item], GoogleVisionReader())


def test_employer_fields_abort_the_run(tmp_path):
    with pytest.raises(NoCredentials):
        P.collect_employer_fields(Batch.from_dir(tmp_path))


def test_run_aborts_before_writing_sheets(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "a.docx").write_bytes(b"x")
    sheets = []
    monkeypatch.setattr(P, "run_scan", lambda batch, **kw: tmp_path / "matches.csv")
    monkeypatch.setattr(P, "load_roster", lambda: None)
    monkeypatch.setattr(P, "_sheets_append_atomic", sheets.extend)
    opts = argparse.Namespace(file="", image="", roi="", full_scan=False,
                              preflight_only=False, skip_preflight=True)
    with pytest.raises(NoCredentials):
        P.main(opts)
    assert sheets == []
