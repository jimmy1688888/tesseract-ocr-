# -*- coding: utf-8 -*-
"""Google Vision 只有一個 port(#64):VisionReader 的兩個讀法、唯一的正式 adapter。

read_sparse 讀截圖上的零星字(許可證),read_document 讀整頁文件(契約頁、次頁、
切結書頁);兩者在 Google 那邊是不同的 feature,換了讀值可能不同,所以不統一。
"""
import ast
import types

import pytest

from conftest import module_sources
import vision_reader
from vision_reader import GoogleVisionReader, VisionCallFailed, VisionReader

PNG = b"\x89PNG fake"


def _response(text: str = "", error: str = ""):
    return types.SimpleNamespace(
        error=types.SimpleNamespace(message=error),
        full_text_annotation=types.SimpleNamespace(text=text) if text else None)


class _FakeClient:
    """記下呼叫了哪個 feature、送了什麼 bytes;回傳指定的回應。"""

    def __init__(self, response):
        self.response, self.calls = response, []

    def text_detection(self, image):
        self.calls.append(("text_detection", image.content))
        return self.response

    def document_text_detection(self, image):
        self.calls.append(("document_text_detection", image.content))
        return self.response


class TestEachReadUsesItsOwnFeature:
    def test_sparse_reads_with_text_detection(self):
        client = _FakeClient(_response("No jin:1234"))
        assert GoogleVisionReader(client).read_sparse(PNG) == "No jin:1234"
        assert client.calls == [("text_detection", PNG)]

    def test_document_reads_with_document_text_detection(self):
        client = _FakeClient(_response("甲方 王小明"))
        assert GoogleVisionReader(client).read_document(PNG) == "甲方 王小明"
        assert client.calls == [("document_text_detection", PNG)]

    @pytest.mark.parametrize("read", ["read_sparse", "read_document"])
    def test_nothing_read_is_empty_string(self, read):
        assert getattr(GoogleVisionReader(_FakeClient(_response())), read)(PNG) == ""


class TestErrorReportedInTheResponse:
    """#64 只換路徑、不改行為:兩條讀法對 API 回報的錯誤仍各走今天的路(改在 #65)。"""

    def test_sparse_still_returns_empty(self):
        assert GoogleVisionReader(_FakeClient(_response(error="Bad image data"))).read_sparse(PNG) == ""

    def test_document_raises_vision_call_failed(self):
        with pytest.raises(VisionCallFailed, match="Bad image data"):
            GoogleVisionReader(_FakeClient(_response(error="Bad image data"))).read_document(PNG)


def test_google_adapter_satisfies_the_port():
    assert isinstance(GoogleVisionReader(_FakeClient(_response())), VisionReader)


def test_vision_reader_imports_no_pipeline_module():
    """leaf module:只依賴 google_clients 與 retry,不認識任何流程模組。"""
    allowed_project = {"google_clients", "retry"}
    flows = {"pipeline", "employer_extract", "vision_verification", "snapshots",
             "permit_scan", "permit_reading", "submission_decision"}
    imported = set()
    for path in module_sources(vision_reader):
        for n in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(n, ast.Import):
                imported |= {a.name.split(".")[0] for a in n.names}
            elif isinstance(n, ast.ImportFrom) and n.module:
                imported.add(n.module.split(".")[0])
    assert not imported & flows
    assert imported & allowed_project == allowed_project


def test_only_the_adapter_calls_vision_features():
    """Google 的讀字方法只在 vision_reader 出現;其他地方都經過 VisionReader。"""
    from conftest import project_sources
    callers = [p.name for p in project_sources()
               if not p.name.startswith("test_") and p.name != "vision_reader.py"
               and "text_detection(" in p.read_text(encoding="utf-8")]
    assert callers == []
