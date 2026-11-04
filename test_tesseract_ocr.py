# -*- coding: utf-8 -*-
"""Tesseract 只有一個 adapter(#76,候選 AG)。

釘三件事:執行檔路徑、語言、讀字呼叫只有 tesseract_ocr 一個主人;不必先 import
pipeline 就拿得到路徑(以前靠 pipeline 的 import 副作用);preflight 只把 adapter 回報的
安裝事實包成檢查結果,四種結果與搬家前相同。
"""
import ast
import subprocess
import sys

import pytest

import tesseract_ocr
from conftest import ROOT, module_sources, project_sources

# leaf adapter 只認識標準函式庫與 pytesseract:白名單,新的專案模組不必記得來這裡登記。
_ALLOWED_IMPORTS = set(sys.stdlib_module_names) | {"pytesseract"}
# 直接碰 pytesseract 的寫法:設路徑、讀字、問版本與語言包。
_PYTESSERACT_USES = ("tesseract_cmd", "image_to_data(", "image_to_string(",
                     "get_tesseract_version(", "get_languages(")


def _non_test_sources():
    return [p for p in project_sources() if not p.name.startswith(("test_", "conftest"))]


class TestOneOwner:

    def test_only_the_adapter_touches_pytesseract(self):
        owner = set(module_sources(tesseract_ocr))
        callers = sorted(p.name for p in _non_test_sources() if p not in owner
                         and any(u in p.read_text(encoding="utf-8") for u in _PYTESSERACT_USES))
        assert callers == []

    def test_adapter_imports_no_project_module(self):
        imported = set()
        for path in module_sources(tesseract_ocr):
            for n in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(n, ast.Import):
                    imported |= {a.name.split(".")[0] for a in n.names}
                elif isinstance(n, ast.ImportFrom) and n.module:
                    imported.add(n.module.split(".")[0])
        assert imported <= _ALLOWED_IMPORTS, imported - _ALLOWED_IMPORTS

    def test_language_is_spelled_once(self):
        spelled = sorted(p.name for p in _non_test_sources()
                         if any(f"{q}ind+eng{q}" in p.read_text(encoding="utf-8") for q in "\"'"))
        assert spelled == ["tesseract_ocr.py"]


def test_permit_reading_gets_the_exe_without_pipeline():
    """只 import 許可證判讀(不碰 pipeline),路徑就已經是 adapter 的那一個。"""
    # 先讀路徑、最後才自己 import adapter:否則測試自己的 import 就把路徑設好了。
    code = ("import sys, permit_reading, pytesseract\n"
            "assert 'pipeline' not in sys.modules\n"
            "cmd = pytesseract.pytesseract.tesseract_cmd\n"
            "import tesseract_ocr\n"
            "print(cmd == tesseract_ocr.TESSERACT_EXE)")
    out = subprocess.run([sys.executable, "-B", "-c", code], cwd=ROOT,
                         capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "True"


class TestPreflightCheck:
    """preflight 只包裝 adapter 的事實;四種結果與搬家前一字不差(提示改指新 module)。"""

    @pytest.fixture(autouse=True)
    def _gchecks(self):
        pytest.importorskip("gcp_identity.checks")

    def _check(self, monkeypatch, **facts):
        import pipeline
        base = dict(exe=tesseract_ocr.TESSERACT_EXE, found=True, version="5.3.0",
                    version_error="", missing_langs=(), langs_checked=True)
        monkeypatch.setattr(tesseract_ocr, "installation",
                            lambda: tesseract_ocr.Installation(**{**base, **facts}))
        return pipeline._check_tesseract()

    def test_all_present_passes(self, monkeypatch):
        r = self._check(monkeypatch)
        assert (r.ok, r.name, r.detail) == (True, "Tesseract 執行檔", "v5.3.0，語言包 ind+eng 齊全")

    def test_missing_exe_fails_and_points_to_the_adapter(self, monkeypatch):
        r = self._check(monkeypatch, found=False, version="")
        assert (r.ok, r.name) == (False, "Tesseract 執行檔")
        assert r.detail == f"找不到 {tesseract_ocr.TESSERACT_EXE}"
        assert "tesseract_ocr.py" in r.fix and "pipeline.py" not in r.fix

    def test_unrunnable_exe_fails(self, monkeypatch):
        r = self._check(monkeypatch, version="", version_error="OSError('壞了')")
        assert (r.ok, r.detail, r.fix) == (False, "OSError('壞了')", "確認 Tesseract 可正常執行")

    def test_missing_language_pack_fails(self, monkeypatch):
        r = self._check(monkeypatch, missing_langs=("ind",))
        assert (r.ok, r.name, r.detail) == (False, "Tesseract 語言包", "缺少 ['ind']")

    def test_unknown_languages_pass_unchecked(self, monkeypatch):
        r = self._check(monkeypatch, langs_checked=False)
        assert (r.ok, r.detail) == (True, "v5.3.0（語言包未檢查）")
