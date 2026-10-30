# -*- coding: utf-8 -*-
"""vision_reader.py — Google Vision 的唯一入口:「影像 bytes → 全文」。

兩種讀法,名字講用途、不講 API:
  read_sparse   截圖上的零星字(許可證)    → Google 的 TEXT_DETECTION
  read_document 整頁文件(契約頁/次頁/切結書頁)→ Google 的 DOCUMENT_TEXT_DETECTION
兩個 feature 在 Google 那邊是不同的處理,換了讀值可能不同,所以刻意不統一。

呼叫端拿的是 VisionReader(會讀字的東西),不是 Google 的 client;測試換假的 reader。
回傳空字串 = 讀到了、沒有字;沒問到時 raise VisionCallFailed(見 CONTEXT、ADR-0015)。
"""
from __future__ import annotations

import time
from typing import Callable, Protocol, runtime_checkable

from google.api_core import exceptions as gax_exceptions
from google.cloud import vision as gvision

import google_clients
from retry import is_retryable_google_error, with_retry


__all__ = ["VisionCallFailed", "VisionReader", "GoogleVisionReader", "IDENTITY_REFUSED"]


class VisionCallFailed(Exception):
    """Vision 呼叫失敗:沒問到,不是「問了、答案是沒有」(CONTEXT)。"""


@runtime_checkable
class VisionReader(Protocol):
    def read_sparse(self, image: bytes) -> str: ...
    def read_document(self, image: bytes) -> str: ...


# 換下一份也一樣失敗:身分或權限被拒,往上拋讓整批中止,不算 Vision 呼叫失敗(ADR-0015)。
IDENTITY_REFUSED = (gax_exceptions.PermissionDenied, gax_exceptions.Unauthenticated)
# 打 Vision 時會出現的錯誤;重試用盡(或不值得重試)就是 Vision 呼叫失敗。
# 其餘例外(程式寫錯、拿不到憑證)不攔。
_CALL_ERRORS = (ConnectionError, TimeoutError,
                gax_exceptions.GoogleAPICallError, gax_exceptions.RetryError)
MAX_ATTEMPTS = 4
INITIAL_WAIT_SECONDS = 2.0


class GoogleVisionReader:
    """VisionReader 的正式 adapter:錯誤分類與重試只在這裡(ADR-0015)。

    不給 client 就跟 google_clients 要 —— 在建構時要,所以拿不到憑證會在建 reader 的
    那一步拋出(呼叫端據此決定整批中止或降級)。sleep 是重試的等待方式,測試換掉。
    """

    def __init__(self, client=None, sleep: Callable[[float], None] = time.sleep):
        self._client = google_clients.vision_client() if client is None else client
        self._annotate_with_retry = with_retry(
            max_attempts=MAX_ATTEMPTS, initial_wait=INITIAL_WAIT_SECONDS,
            retryable_exceptions=_CALL_ERRORS, should_retry=is_retryable_google_error,
            sleep=sleep)(self._annotate)

    def read_sparse(self, image: bytes) -> str:
        return self._read("text_detection", image)

    def read_document(self, image: bytes) -> str:
        return self._read("document_text_detection", image)

    def _read(self, feature: str, image: bytes) -> str:
        try:
            response = self._annotate_with_retry(feature, image)
        except IDENTITY_REFUSED:
            raise
        except _CALL_ERRORS as e:
            raise VisionCallFailed(f"Vision 呼叫失敗: {e!r}") from e
        if response.error.message:
            raise VisionCallFailed(f"Vision API 錯誤: {response.error.message}")
        return _full_text(response)

    def _annotate(self, feature: str, image: bytes):
        return getattr(self._client, feature)(image=gvision.Image(content=image))


def _full_text(response) -> str:
    return response.full_text_annotation.text if response.full_text_annotation else ""
