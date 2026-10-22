# -*- coding: utf-8 -*-
"""拿不到 Google 憑證時,三個用 Vision 的地方各降級成什麼(#16)。

以前 employer_extract 自己延遲 import pipeline 拿 Vision client,憑證失敗會從
「import 時」搬到「第一次呼叫時」、被雇主擷取的 try/except 吞成一行 warning;
許可證那一側卻是整批中止。同一條 seam 的兩個方向錯誤模式不對稱,而兩側都沒寫下來。
#3 之後三處都跟 google_clients 要 client,這裡把現況釘住:

  許可證判讀(步驟 4a 送 Vision)  → 不降級,例外往上拋,整批中止(還沒寫 Sheets)
  雇主欄位(步驟 4b 前置)         → 不降級,例外往上拋,整批中止(還沒寫 Sheets)
  次頁電話反查救援(4b 前置②③)  → 降級:記 warning、救回 0 筆,各列維持原樣

前兩者寧可整批不寫,也不要寫一批「B 欄沒驗、H~O 全空」看起來正常的列;正常情況下
preflight 的憑證檢查會在掃描前先攔下來,這裡是 --skip-preflight 或憑證中途失效時的
最後一道。救援本來就是「救得回幾筆算幾筆」,少了它各列仍是人工審查,不會錯值。
"""
import pytest

import identity
import pipeline as P
from batch import Batch
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
    item = P.VisionQueueItem(source_docx="a.docx", image_name="image5.jpeg",
                             img_path=str(crop), reason="permit部分命中無多數",
                             hit_roi="permit_lower", candidate_value="1234",
                             candidate_conf=60.0)
    with pytest.raises(NoCredentials):
        run_vision_items([item], read_text=P.vision_text)


def test_employer_fields_abort_the_run(tmp_path):
    with pytest.raises(NoCredentials):
        P.collect_employer_fields(Batch.from_dir(tmp_path))


def test_agency_rescue_degrades_to_nothing_rescued(tmp_path, monkeypatch):
    monkeypatch.setattr(P, "_permit_lookup", lambda: object())   # 名冊在,才走得到 client
    rows = [{"source_docx": "a.docx", "final_value": "", "reason": "全無命中"}]
    before = [dict(r) for r in rows]
    assert P.rescue_manual_review_via_agency_phone(Batch.from_dir(tmp_path), rows) == 0
    assert rows == before
