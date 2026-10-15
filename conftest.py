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
# 是 .parent 不是 .parent.parent：本檔就在 repo 根目錄，parent.parent 會插到
# repo 的上一層（C:\projects），等於什麼都沒做。
#
# 寫錯了也不會被發現，因為平常有兩層東西替它做事：pytest 預設的 import mode
# 會把 rootdir 塞進 sys.path，而 `python -m pytest` 又會把 cwd 塞進 sys.path[0]。
# 兩層都拿掉才看得到：`pytest --import-mode=importlib`（用執行檔，不是 python -m）
# 在寫錯時會收集失敗、報 ModuleNotFoundError: No module named 'pipeline'。
ROOT = Path(__file__).resolve().parent
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
# pipeline.py 已不再使用金鑰檔（見 foreign-worker-query/docs/adr/0002）。
# 這個 stub 留著只為了讓沒裝 google-auth 的環境仍能 import。
_ensure_module("google.oauth2.service_account", {
    "Credentials": type("Credentials", (), {
        "from_service_account_file": staticmethod(lambda *a, **kw: None)
    })
})

# gcp_identity（共用套件，靠本機路徑 pip install -e 安裝）
# 測試不該要求它已安裝：認證是執行期的事，而測試碰不到 Google。
class _StubProjectIdentity:
    def __init__(self, sa_email, project_id, label=""):
        self.sa_email, self.project_id, self.label = sa_email, project_id, label

    def credentials(self, scopes=()):
        raise AssertionError("測試不應真的取得 Google 憑據")


class _StubChecks:
    SELF, ADMIN = "self", "admin"


_ensure_module("gcp_identity", {
    "ProjectIdentity": _StubProjectIdentity,
    "checks": _StubChecks,
})

# googleapiclient
_ensure_module("googleapiclient.discovery", {"build": lambda *a, **kw: None})
_ensure_module("googleapiclient.errors", {
    "HttpError": type("HttpError", (Exception,), {})
})
