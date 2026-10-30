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
from google.api_core import exceptions as gax_exceptions
from vision_reader import GoogleVisionReader, VisionCallFailed, VisionReader

PNG = b"\x89PNG fake"


def _response(text: str = "", error: str = ""):
    return types.SimpleNamespace(
        error=types.SimpleNamespace(message=error),
        full_text_annotation=types.SimpleNamespace(text=text) if text else None)


class _FakeClient:
    """記下呼叫了哪個 feature、送了什麼 bytes;依序丟出 errors 裡的例外,之後回傳 response。"""

    def __init__(self, response, errors=()):
        self.response, self.errors, self.calls = response, list(errors), []

    def _answer(self, feature, image):
        self.calls.append((feature, image.content))
        if self.errors:
            raise self.errors.pop(0)
        return self.response

    def text_detection(self, image):
        return self._answer("text_detection", image)

    def document_text_detection(self, image):
        return self._answer("document_text_detection", image)


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


READS = ["read_sparse", "read_document"]


def _reader(errors=(), response=None):
    """不真的睡的 reader;回傳 (reader, client, 等過的秒數)。"""
    slept: list[float] = []
    client = _FakeClient(response or _response("No jin:1234"), errors)
    return GoogleVisionReader(client, sleep=slept.append), client, slept


class TestVisionCallFailed:
    """錯誤怎麼分(ADR-0015):換下一份會不會一樣失敗。兩種讀法同一套。"""

    @pytest.mark.parametrize("read", READS)
    def test_error_in_the_response_fails_without_retry(self, read):
        """Google 已經回答了這張圖;再送一次結果也一樣。以前 read_sparse 回空字串、
        被判成「Vision 沒取到號」。"""
        reader, client, slept = _reader(response=_response(error="Bad image data"))
        with pytest.raises(VisionCallFailed, match="Bad image data"):
            getattr(reader, read)(PNG)
        assert (len(client.calls), slept) == (1, [])

    @pytest.mark.parametrize("read", READS)
    @pytest.mark.parametrize("error", [
        gax_exceptions.ServiceUnavailable("503"), gax_exceptions.ResourceExhausted("429"),
        gax_exceptions.DeadlineExceeded("逾時"), ConnectionError("斷線"), TimeoutError("逾時")])
    def test_transient_errors_retry_four_times_then_fail(self, read, error):
        reader, client, slept = _reader(errors=[error] * 4)
        with pytest.raises(VisionCallFailed):
            getattr(reader, read)(PNG)
        assert len(client.calls) == 4
        assert slept == [2.0, 4.0, 8.0]

    @pytest.mark.parametrize("read", READS)
    def test_transient_error_that_clears_is_not_a_failure(self, read):
        reader, client, _ = _reader(errors=[gax_exceptions.ServiceUnavailable("503")])
        assert getattr(reader, read)(PNG) == "No jin:1234"
        assert len(client.calls) == 2

    @pytest.mark.parametrize("read", READS)
    @pytest.mark.parametrize("error", [
        gax_exceptions.InvalidArgument("圖壞了"), gax_exceptions.InternalServerError("500")])
    def test_other_google_errors_fail_without_retry(self, read, error):
        reader, client, slept = _reader(errors=[error])
        with pytest.raises(VisionCallFailed):
            getattr(reader, read)(PNG)
        assert (len(client.calls), slept) == (1, [])

    @pytest.mark.parametrize("read", READS)
    @pytest.mark.parametrize("error", [
        gax_exceptions.PermissionDenied("403"), gax_exceptions.Unauthenticated("401")])
    def test_identity_refused_is_not_a_vision_call_failure(self, read, error):
        """換下一份也一樣失敗:往上拋,整批中止。"""
        reader, client, _ = _reader(errors=[error])
        with pytest.raises(type(error)):
            getattr(reader, read)(PNG)
        assert len(client.calls) == 1

    @pytest.mark.parametrize("read", READS)
    def test_bugs_are_not_caught(self, read):
        reader, _, _ = _reader(errors=[KeyError("程式寫錯")])
        with pytest.raises(KeyError):
            getattr(reader, read)(PNG)

    def test_vision_call_failed_is_never_an_empty_string(self):
        """空字串只代表「讀到了、沒有字」。"""
        reader, _, _ = _reader(response=_response())
        assert reader.read_sparse(PNG) == "" and reader.read_document(PNG) == ""


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
