# -*- coding: utf-8 -*-
"""pytest 設定。

三件事：
  1. 把專案根目錄加入 sys.path，讓 `import pipeline` 可用
  2. 若環境內沒有實際安裝 google-cloud-vision / googleapiclient，建立 stub module
     讓 pipeline.py 能成功 import；測試只跑純邏輯函數，不會真的呼叫 API。
  3. 每支測試都掛上 Google 身分的護欄、前後重設所有身分與 client 快取
     （不論本機有沒有裝 gcp-identity）。需要建得起 client 的測試用 offline_identity。
"""
import sys
import types
from pathlib import Path

# ─── 路徑設定 ──────────────────────────────────────────────────────────────
# 是 .parent 不是 .parent.parent：本檔就在 repo 根目錄，parent.parent 會插到
# repo 的上一層，等於什麼都沒做。
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
    "ImageAnnotatorClient": type("ImageAnnotatorClient", (), {
        "__init__": lambda self, *a, **kw: None,
    }),
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

# gcp_identity 刻意不 stub。只有 identity.py 與 preflight 會碰它，而且都是在
# 函式裡延遲 import，所以沒裝的機器照樣 import 得了 pipeline；需要它的測試用
# importorskip 乾淨略過。護欄改掛在下面的 fixture 上 —— 掛在 stub 上的話，
# 裝了真套件的機器（stub 不生效）就沒有護欄。

# googleapiclient
_ensure_module("googleapiclient.discovery", {"build": lambda *a, **kw: None})
_ensure_module("googleapiclient.errors", {
    "HttpError": type("HttpError", (Exception,), {})
})


# ─── Google 身分的護欄 ────────────────────────────────────────────────────
import pytest  # noqa: E402

import google_clients  # noqa: E402  stub 要先就位，這兩行才能 import
import identity  # noqa: E402


class _GuardIdentity:
    """測試預設的身分：看得到設定值，拿不到憑據。"""

    sa_email, project_id, label = identity.SA_EMAIL, identity.GCP_PROJECT_ID, "test-guard"

    def credentials(self, scopes=()):
        raise AssertionError("測試不應真的取得 Google 憑據")


@pytest.fixture(autouse=True)
def _google_identity_guard():
    """每支測試前後重設所有身分與 client 快取，中間掛上護欄。

    重設是雙向的：前面的測試建的 client 不會漏給後面，後面的測試也不會撿到
    一個真身分。共用套件那層權杖快取掛在 ProjectIdentity 實例上，丟掉實例就是
    重設它。
    """
    identity.reset()
    google_clients.reset()
    identity.install(_GuardIdentity())
    yield
    identity.reset()
    google_clients.reset()


class _OfflineIdentity(_GuardIdentity):
    """建得起真 client、但打不出任何請求的身分；記下被要了哪些 scopes。"""

    def __init__(self):
        try:
            from google.auth.credentials import AnonymousCredentials
            self._creds = AnonymousCredentials()
        except ImportError:          # 沒裝 google-auth 時 client 也是 stub，收什麼都行
            self._creds = object()
        self.asked: list[tuple[str, ...]] = []

    def credentials(self, scopes=()):
        self.asked.append(tuple(scopes))
        return self._creds


@pytest.fixture
def offline_identity():
    """需要「建得起 client」的測試用這個，而不是 monkeypatch 掉 getter。"""
    ident = _OfflineIdentity()
    identity.install(ident)
    return ident



# ─── 真實素材 + 真的跑 Tesseract 的測試共用 ─────────────────────────────────
@pytest.fixture
def real_ocr():
    """找得到 tesseract.exe 才跑;前後清掉契約頁評分快取,不讓前一支測試的分數漏進來。"""
    import employer_extract
    from real_samples import needs_tesseract
    needs_tesseract()
    employer_extract._page_score_cache.clear()
    yield
    employer_extract._page_score_cache.clear()
