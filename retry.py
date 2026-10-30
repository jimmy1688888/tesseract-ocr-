# -*- coding: utf-8 -*-
"""retry.py — 打 Google API 的重試:指數退避 decorator 與「這個錯誤值不值得重試」。

Vision(vision_reader)與 Sheets append(pipeline)共用同一套;只依賴標準函式庫與
Google 的例外型別,不 import 任何流程模組。
"""
import functools
import logging
import time
from typing import Callable, Optional

from google.api_core import exceptions as gax_exceptions
from googleapiclient.errors import HttpError

logger = logging.getLogger(__name__)

__all__ = ["with_retry", "is_retryable_google_error"]


def with_retry(
    max_attempts: int = 5,
    initial_wait: float = 2.0,
    backoff_factor: float = 2.0,
    max_wait: float = 30.0,
    retryable_exceptions: tuple = (Exception,),
    should_retry: Optional[Callable[[BaseException], bool]] = None,
):
    """指數退避重試 decorator（純 stdlib，無需 tenacity）。

    參數：
      max_attempts         ：總嘗試次數（含第一次）。
      initial_wait         ：第一次失敗後等待秒數。
      backoff_factor       ：每次失敗後等待時間乘以此倍數。
      max_wait             ：等待時間上限。
      retryable_exceptions ：哪些 exception 才會觸發重試。
      should_retry         ：額外的判斷函數；回傳 False 表示此次例外不重試（直接 raise）。

    生產環境建議改用 tenacity 套件；此處用 stdlib 是為了不新增相依。
    """
    def decorator(fn: Callable) -> Callable:
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            wait = initial_wait
            for attempt in range(1, max_attempts + 1):
                try:
                    return fn(*args, **kwargs)
                except retryable_exceptions as e:
                    if should_retry is not None and not should_retry(e):
                        raise
                    if attempt == max_attempts:
                        logger.error(
                            f"{fn.__name__} 重試 {max_attempts} 次後仍失敗：{e!r}"
                        )
                        raise
                    logger.warning(
                        f"{fn.__name__} 第 {attempt}/{max_attempts} 次失敗：{e!r}；"
                        f"{wait:.1f}s 後重試"
                    )
                    time.sleep(wait)
                    wait = min(wait * backoff_factor, max_wait)
        return wrapper
    return decorator


def is_retryable_google_error(exc: BaseException) -> bool:
    """判斷 Google API 例外是否值得重試。

    可重試：
      - 連線 / 逾時錯誤
      - HttpError 429（rate limit）、5xx（伺服器端錯誤）
      - google.api_core 的 ServiceUnavailable / DeadlineExceeded / Aborted
    不可重試（直接拋出）：
      - 4xx 其他錯誤（如 403 權限、404 找不到資源）→ 重試也沒用
    """
    if isinstance(exc, (ConnectionError, TimeoutError)):
        return True
    if isinstance(exc, HttpError):
        status = getattr(exc.resp, "status", 0)
        return status == 429 or 500 <= status < 600
    if isinstance(exc, (gax_exceptions.ServiceUnavailable,
                        gax_exceptions.DeadlineExceeded,
                        gax_exceptions.Aborted,
                        gax_exceptions.ResourceExhausted)):
        return True
    return False
