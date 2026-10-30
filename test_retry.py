# -*- coding: utf-8 -*-
"""with_retry 有時間 seam(#21):要測「N 次失敗後放棄」不必真的睡。

以前 with_retry 直接 time.sleep,裝飾在 Vision 與 Sheets 上、import 時就綁定 —— 測
「4 次失敗後放棄」得真的等 2+4+8 秒,所以它零測試;max_attempts=0 還會安靜回 None。
"""
from pathlib import Path

import pytest

from retry import is_retryable_google_error, with_retry

API_ERRORS = Path(__file__).resolve().parent / "samples" / "api_errors"


class _Flaky:
    """前 failures 次丟 error,之後回 "ok";記下被呼叫幾次。"""

    def __init__(self, failures: int, error: Exception | None = None):
        self.failures, self.calls = failures, 0
        self.error = error or ConnectionError("斷線")

    def __call__(self):
        self.calls += 1
        if self.calls <= self.failures:
            raise self.error
        return "ok"


def _retried(flaky: _Flaky, slept: list, **kw):
    kw.setdefault("retryable_exceptions", (ConnectionError,))

    def call():
        return flaky()
    return with_retry(sleep=slept.append, **kw)(call)


class TestGivesUpAfterN:

    def test_raises_the_last_error_after_max_attempts(self):
        flaky, slept = _Flaky(failures=99), []
        with pytest.raises(ConnectionError):
            _retried(flaky, slept, max_attempts=4)()
        assert flaky.calls == 4
        assert len(slept) == 3                   # 最後一次失敗後不再等

    def test_succeeds_once_the_error_stops(self):
        flaky, slept = _Flaky(failures=2), []
        assert _retried(flaky, slept, max_attempts=4)() == "ok"
        assert flaky.calls == 3

    def test_first_try_success_never_waits(self):
        slept = []
        assert _retried(_Flaky(failures=0), slept)() == "ok"
        assert slept == []


class TestBackoff:

    def test_waits_double_each_time(self):
        slept = []
        with pytest.raises(ConnectionError):
            _retried(_Flaky(99), slept, max_attempts=4, initial_wait=2.0)()
        assert slept == [2.0, 4.0, 8.0]

    def test_wait_is_capped(self):
        slept = []
        with pytest.raises(ConnectionError):
            _retried(_Flaky(99), slept, max_attempts=6, initial_wait=2.0, max_wait=5.0)()
        assert slept == [2.0, 4.0, 5.0, 5.0, 5.0]


class TestWhatIsNotRetried:

    def test_exception_outside_the_retryable_list_raises_at_once(self):
        flaky, slept = _Flaky(99, ValueError("壞參數")), []
        with pytest.raises(ValueError):
            _retried(flaky, slept)()
        assert (flaky.calls, slept) == (1, [])

    def test_should_retry_false_raises_at_once(self):
        flaky, slept = _Flaky(99), []
        with pytest.raises(ConnectionError):
            _retried(flaky, slept, should_retry=lambda e: False)()
        assert (flaky.calls, slept) == (1, [])


@pytest.mark.parametrize("attempts", [0, -1])
def test_max_attempts_below_one_is_refused(attempts):
    """以前 for 迴圈一次都不跑、安靜回 None;呼叫端會當成「讀到空白」往下走。"""
    with pytest.raises(ValueError, match="max_attempts"):
        with_retry(max_attempts=attempts)


# 今天的分類:Sheets(HTTP)看狀態碼;Vision(gRPC)只認這四種 —— InternalServerError 等
# 其他 Google 錯誤今天並不重試。
RETRIED_GRPC = {"ServiceUnavailable", "DeadlineExceeded", "Aborted", "ResourceExhausted"}


def _recorded_errors() -> list[Path]:
    return sorted(API_ERRORS.glob("*.txt")) if API_ERRORS.is_dir() else []


@pytest.mark.parametrize("path", _recorded_errors() or [None], ids=lambda p: p.stem if p else "none")
def test_recorded_google_errors_keep_todays_classification(path):
    """真實素材:實際遇過的 Google API 錯誤,分類要與今天一致(#21)。

    素材放 samples/api_errors/(不進版控,可能含專案 ID),一種情況一個檔,內容是
    終端機上的錯誤訊息全文;檔名說是哪一種:
      sheets_<HTTP 狀態碼>.txt          例 sheets_429.txt
      vision_<google.api_core 例外名>.txt 例 vision_ResourceExhausted.txt
      auth_<任意>.txt                   憑證要重新登入(Reauthentication is needed)
    """
    if path is None:
        pytest.skip("沒有 samples/api_errors/*.txt(實際遇過的 Google 錯誤)")
    api, kind = path.stem.split("_", 1)
    message = path.read_text(encoding="utf-8")
    assert is_retryable_google_error(_rebuild(api, kind, message)) is _retried_today(api, kind)


def _retried_today(api: str, kind: str) -> bool:
    if api == "sheets":
        status = int(kind)
        return status == 429 or 500 <= status < 600
    return api == "vision" and kind in RETRIED_GRPC


def _rebuild(api: str, kind: str, message: str) -> BaseException:
    """依檔名重建那一種例外,訊息放錯誤全文。"""
    if api == "sheets":
        import httplib2
        from googleapiclient.errors import HttpError
        return HttpError(httplib2.Response({"status": int(kind)}), message.encode())
    if api == "vision":
        from google.api_core import exceptions
        return getattr(exceptions, kind)(message)
    if api == "auth":
        from google.auth.exceptions import RefreshError
        return RefreshError(message)
    raise ValueError(f"看不懂的素材檔名:{api}_{kind}(見本測試的說明)")
