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

import logging
from typing import Protocol, runtime_checkable

from google.api_core import exceptions as gax_exceptions

import google_clients
from retry import is_retryable_google_error, with_retry

logger = logging.getLogger(__name__)

__all__ = ["VisionCallFailed", "VisionReader", "GoogleVisionReader"]


class VisionCallFailed(Exception):
    """Vision 呼叫失敗:沒問到,不是「問了、答案是沒有」(CONTEXT)。"""


@runtime_checkable
class VisionReader(Protocol):
    def read_sparse(self, image: bytes) -> str: ...
    def read_document(self, image: bytes) -> str: ...


class GoogleVisionReader:
    """VisionReader 的正式 adapter。不給 client 就跟 google_clients 要 —— 在建構時要,
    所以拿不到憑證會在建 reader 的那一步拋出(呼叫端據此決定整批中止或降級)。"""

    def __init__(self, client=None):
        self._client = google_clients.vision_client() if client is None else client

    @with_retry(
        max_attempts=4,
        initial_wait=2.0,
        retryable_exceptions=(
            ConnectionError, TimeoutError,
            gax_exceptions.GoogleAPICallError,
            gax_exceptions.RetryError,
        ),
        should_retry=is_retryable_google_error,
    )
    def read_sparse(self, image: bytes) -> str:
        response = self._client.text_detection(image=_image(image))
        if response.error.message:
            logger.error(f"Vision API 錯誤: {response.error.message}")
            return ""
        return _full_text(response)

    def read_document(self, image: bytes) -> str:
        response = self._client.document_text_detection(image=_image(image))
        if response.error.message:
            raise VisionCallFailed(f"Vision API 錯誤: {response.error.message}")
        return _full_text(response)


def _image(content: bytes):
    from google.cloud import vision as gvision
    return gvision.Image(content=content)


def _full_text(response) -> str:
    return response.full_text_annotation.text if response.full_text_annotation else ""
