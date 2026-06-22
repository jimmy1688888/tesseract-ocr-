# -*- coding: utf-8 -*-
"""pytest 設定。

兩件事：
  1. 把專案根目錄加入 sys.path，讓 `import pipeline` 可用
  2. 若環境內沒有實際安裝 google-cloud-vision / googleapiclient，建立 stub module
     讓 pipeline.py 能成功 import；測試只跑純邏輯函數，不會真的呼叫 API。
"""
import sys
import types
from pathlib import Path

# ─── 路徑設定 ──────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


# ─── Stub 缺席的 Google 套件 ──────────────────────────────────────────────
def _stub_module(name: str, attrs: dict | None = None) -> None:
    """建立指定 module（含 parent packages），並注入屬性。"""
    parts = name.split(".")
    for i in range(1, len(parts) + 1):
        partial = ".".join(parts[:i])
        if partial not in sys.modules:
            sys.modules[partial] = types.ModuleType(partial)
    if attrs:
        for k, v in attrs.items():
            setattr(sys.modules[name], k, v)


def _ensure_module(name: str, attrs: dict | None = None) -> None:
    """嘗試 import 真實 module；失敗才補 stub。"""
    try:
        __import__(name)
    except ImportError:
        _stub_module(name, attrs)


# google.cloud.vision
_ensure_module("google.cloud.vision", {
    "ImageAnnotatorClient": type("ImageAnnotatorClient", (), {}),
    "Image": type("Image", (), {}),
})

# google.api_core.exceptions
_ensure_module("google.api_core.exceptions", {
    name: type(name, (Exception,), {})
    for name in (
        "GoogleAPICallError", "RetryError",
        "ServiceUnavailable", "DeadlineExceeded",
        "Aborted", "ResourceExhausted",
    )
})

# google.oauth2.service_account
_ensure_module("google.oauth2.service_account", {
    "Credentials": type("Credentials", (), {
        "from_service_account_file": staticmethod(lambda *a, **kw: None)
    })
})

# googleapiclient
_ensure_module("googleapiclient.discovery", {"build": lambda *a, **kw: None})
_ensure_module("googleapiclient.errors", {
    "HttpError": type("HttpError", (Exception,), {})
})
