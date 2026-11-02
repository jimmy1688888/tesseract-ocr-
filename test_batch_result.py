"""一批的結果(CONTEXT.md):main 交出跑完/被擋下,入口轉成結束碼(#75)。

中途出錯不在這裡:它是往上拋的例外(Python 給 1),由 test_no_credentials 守。
"""
import argparse
import logging
import subprocess
import sys

import pytest

import pipeline as P
from batch_result import BatchResult
from conftest import ROOT
from test_unreadable_docx import _docx, _png, not_zip


def _opts(**kw):
    base = dict(file="", image="", roi="", full_scan=False,
                preflight_only=False, skip_preflight=True)
    base.update(kw)
    return argparse.Namespace(**base)


class _Started(Exception):
    """掃描被叫到 = 已經開跑;本檔只關心開跑前的結果。"""


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def started(*a, **kw):
        raise _Started
    monkeypatch.setattr(P, "load_roster", lambda: None)
    monkeypatch.setattr(P, "run_scan", started)


def test_preflight_failed_is_blocked(monkeypatch):
    monkeypatch.setattr(P, "run_preflight", lambda batch, roster: False)
    assert P.main(_opts(skip_preflight=False)) is BatchResult.BLOCKED


def test_preflight_only_passed_is_finished(monkeypatch):
    monkeypatch.setattr(P, "run_preflight", lambda batch, roster: True)
    assert P.main(_opts(skip_preflight=False, preflight_only=True)) is BatchResult.FINISHED


@pytest.mark.parametrize("nothing", ["missing_file", "empty_docs", "missing_docs"])
def test_nothing_to_process_is_blocked(monkeypatch, tmp_path, nothing):
    """--skip-preflight 時沒有閘門,main 仍以同一個判斷擋下 —— 也是被擋下。"""
    monkeypatch.chdir(tmp_path)
    if nothing == "empty_docs":
        (tmp_path / "docs").mkdir()
    file = str(tmp_path / "32417.docx") if nothing == "missing_file" else ""
    assert P.main(_opts(file=file)) is BatchResult.BLOCKED


@pytest.mark.parametrize("make", [
    not_zip,                                                    # docx 打不開
    lambda d: _docx(d / "has_other.docx", {"image1.png": _png()}),   # 打得開,沒有那張圖
], ids=["unreadable", "no_such_image"])
def test_image_filter_miss_is_blocked(monkeypatch, tmp_path, make):
    # 單檔模式會把 root logger 調成 DEBUG;測試後還原
    monkeypatch.setattr(logging.getLogger(), "level", logging.getLogger().level)
    docx = make(tmp_path)
    assert P.main(_opts(file=str(docx), image="image9.jpeg")) is BatchResult.BLOCKED


# ─── 入口:真的起行程,結束碼是給執行.bat 看的(不碰 Google)──────────────────

def _run_pipeline(*args: str) -> int:
    return subprocess.run([sys.executable, "-B", str(ROOT / "pipeline.py"), *args],
                          cwd=ROOT, capture_output=True, timeout=120).returncode


def test_entry_exits_2_when_blocked(tmp_path):
    missing = str(tmp_path / "不存在.docx")
    assert _run_pipeline("--skip-preflight", "--file", missing) == BatchResult.BLOCKED.exit_code == 2


def test_entry_exits_2_when_arguments_are_wrong():
    """argparse 參數打錯本來就是 2 —— 同樣是開跑前就停,與被擋下一致。"""
    assert _run_pipeline("--roi", "不存在的ROI") == 2
