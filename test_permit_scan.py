# -*- coding: utf-8 -*-
"""許可證掃描(permit_scan,#62)不依賴流程模組。

掃描快照的錄製與重放、送審快照都只要許可證掃描;以前 run_scan 住在 pipeline,
重放一次就得載入整條流程(Google 套件、preflight)。
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def _modules_loaded_by(statement: str) -> set[str]:
    """在乾淨的直譯器裡執行 statement,回傳因此載入的 module 名稱(含間接 import)。"""
    probe = f"import sys; {statement}; print('\\n'.join(sys.modules))"
    out = subprocess.run([sys.executable, "-B", "-c", probe], cwd=ROOT,
                         capture_output=True, text=True, check=True).stdout
    return set(out.split())


def test_permit_scan_does_not_load_pipeline():
    assert "pipeline" not in _modules_loaded_by("import permit_scan")


def test_scan_snapshot_replay_does_not_load_pipeline():
    replay_empty_batch = ("import pathlib, tempfile, scan_snapshot; "
                          "scan_snapshot.scan([], pathlib.Path(tempfile.mkdtemp()), ocr=None)")
    loaded = _modules_loaded_by(replay_empty_batch)
    assert "permit_scan" in loaded      # 真的跑到了 run_scan
    assert "pipeline" not in loaded
