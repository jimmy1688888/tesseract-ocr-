# -*- coding: utf-8 -*-
"""Google 身分與 client 的 seam。

這支檔案存在的理由是 #3:身分原本是 `pipeline` 在 import 時就建好的全域,外加
三層從來沒有人重設的 process 快取,而 `get_vision_client()`/`get_sheets_service()`
不吃參數 —— 沒有地方能注入身分,測試只能 monkeypatch 掉整個 getter。

所以這裡釘的是 seam 本身:

  1. 測試環境的護欄是活的 —— 走到 `credentials()` 會被擋,不會真的去打 Google
  2. 身分可以注入,client 依身分快取,快取有重設的路徑
  3. 一支量測工具不 import `pipeline` 就拿得到身分與 Sheets service
  4. preflight 裡打到 Google 的那幾項,穿過同一條 seam 被測到
"""
import subprocess
import sys
from pathlib import Path

import pytest

import google_clients
import identity
import pipeline

ROOT = Path(__file__).resolve().parent


class RecordingIdentity:
    """記下被要了哪些 scopes 的身分替身。"""

    def __init__(self, creds=None, sa_email="sa@test.iam", project_id="proj-test"):
        self.sa_email, self.project_id, self.label = sa_email, project_id, "test"
        self._creds = creds if creds is not None else object()
        self.asked: list[tuple[str, ...]] = []

    def credentials(self, scopes=()):
        self.asked.append(tuple(scopes))
        return self._creds


class TestGuard:
    """conftest 的護欄:不論本機有沒有裝 gcp-identity,測試都拿不到真憑據。

    原本的護欄掛在 stub 上,而 stub 只在「沒裝」時生效 —— 本機用
    `pip install -e` 裝了,護欄從來沒擋過任何東西。
    """

    def test_credentials_is_blocked(self):
        with pytest.raises(AssertionError, match="Google 憑據"):
            identity.credentials()

    def test_sheets_service_is_blocked(self):
        with pytest.raises(AssertionError, match="Google 憑據"):
            google_clients.sheets_service()

    def test_vision_client_is_blocked(self):
        with pytest.raises(AssertionError, match="Google 憑據"):
            google_clients.vision_client()


class TestIdentity:
    def test_default_identity_is_the_project_constants(self):
        pytest.importorskip("gcp_identity")
        identity.reset()                      # 拿掉護欄,看預設值長什麼樣
        ident = identity.current()
        assert ident.sa_email == identity.SA_EMAIL
        assert ident.project_id == identity.GCP_PROJECT_ID

    def test_current_is_built_once(self):
        pytest.importorskip("gcp_identity")
        identity.reset()
        assert identity.current() is identity.current()

    def test_reset_discards_the_instance_and_its_token_cache(self):
        """共用套件的權杖快取掛在 ProjectIdentity 實例上(私有的 _cache),
        所以「丟掉實例」就是重設那一層的路徑 —— 不必伸手去碰私有屬性。"""
        pytest.importorskip("gcp_identity")
        identity.reset()
        first = identity.current()
        identity.reset()
        assert identity.current() is not first

    def test_installed_identity_is_used(self):
        fake = RecordingIdentity()
        identity.install(fake)
        assert identity.current() is fake
        identity.credentials(("s",))
        assert fake.asked == [("s",)]


def _usable_creds():
    try:
        from google.auth.credentials import AnonymousCredentials
        return AnonymousCredentials()
    except ImportError:
        return object()


class TestClients:
    # 前兩支刻意不用 offline_identity:護欄仍是目前的身分,所以 getter 若忽略
    # 注入的身分、改走 identity.current(),就會撞上護欄而紅。
    def test_sheets_service_uses_injected_identity_with_sheets_scope(self):
        injected = RecordingIdentity(creds=_usable_creds())
        google_clients.sheets_service(injected)
        assert injected.asked == [tuple(google_clients.SHEETS_SCOPES)]

    def test_vision_client_uses_injected_identity_with_no_scope(self):
        """Vision client 自己會要 cloud-platform;傳 scopes 反而會蓋掉它。"""
        injected = RecordingIdentity(creds=_usable_creds())
        google_clients.vision_client(injected)
        assert injected.asked == [()]

    def test_falsy_identity_is_still_used(self):
        """「有沒有注入」看的是 None,不是真假值 —— 替身若判為假,不能被默默換掉。"""
        class Falsy(RecordingIdentity):
            def __bool__(self):
                return False

        injected = Falsy(creds=_usable_creds())
        google_clients.sheets_service(injected)
        google_clients.vision_client(injected)
        assert injected.asked == [tuple(google_clients.SHEETS_SCOPES), ()]

    def test_old_identity_is_let_go(self, offline_identity):
        """換了身分之後,快取不再抓著舊身分。

        共用套件的權杖快取掛在身分實例上;快取若一直抓著舊實例,identity.reset()
        就只重設了一半 —— 舊的權杖還活在 client 快取的鍵裡。
        """
        import gc
        import weakref

        old = RecordingIdentity(creds=_usable_creds())
        google_clients.sheets_service(old)
        google_clients.vision_client(old)
        gone = weakref.ref(old)
        del old

        google_clients.sheets_service(offline_identity)
        google_clients.vision_client(offline_identity)
        gc.collect()
        assert gone() is None

    def test_default_comes_from_installed_identity(self, offline_identity):
        google_clients.sheets_service()
        google_clients.vision_client()
        assert len(offline_identity.asked) == 2

    def test_same_identity_builds_once(self, offline_identity):
        """快取的理由不變:每次呼叫都重建 client 等於每次重開 TLS 連線。"""
        a = google_clients.sheets_service(offline_identity)
        b = google_clients.sheets_service(offline_identity)
        assert a is b
        assert len(offline_identity.asked) == 1

    def test_different_identity_is_not_served_from_cache(self, offline_identity):
        """快取依身分分開 —— 否則注入的身分會被前一個身分的 client 蓋掉。"""
        google_clients.sheets_service(offline_identity)
        other = RecordingIdentity(creds=offline_identity._creds)
        google_clients.sheets_service(other)
        assert other.asked == [tuple(google_clients.SHEETS_SCOPES)]

    def test_reset_forces_rebuild(self, offline_identity):
        google_clients.sheets_service(offline_identity)
        google_clients.reset()
        google_clients.sheets_service(offline_identity)
        assert len(offline_identity.asked) == 2


class TestNoPipelineNeeded:
    """#3 的驗收核心:量測工具不 import pipeline 就拿得到身分與 Sheets service。

    用子程序跑,因為本進程早就 import 過 pipeline,在這裡看 sys.modules 證明不了
    任何事。子程序不經過 conftest,所以不打 credentials()(會真的去打 Google),
    只確認身分建得起來、Sheets service 拿著它建得起來。
    """

    def test_identity_and_sheets_without_pipeline(self):
        for mod in ("gcp_identity", "googleapiclient", "google.cloud.vision", "google.auth"):
            pytest.importorskip(mod)
        script = (
            "import sys, identity, google_clients\n"
            "from google.auth.credentials import AnonymousCredentials\n"
            "ident = identity.current()\n"
            "assert ident.sa_email == identity.SA_EMAIL\n"
            "asked = []\n"
            "class Anon:\n"
            "    def credentials(self, scopes=()):\n"
            "        asked.append(tuple(scopes)); return AnonymousCredentials()\n"
            "svc = google_clients.sheets_service(Anon())\n"
            "assert hasattr(svc, 'spreadsheets'), type(svc)\n"
            "assert asked == [tuple(google_clients.SHEETS_SCOPES)], asked\n"
            "assert 'pipeline' not in sys.modules, 'pipeline 被拖進來了'\n"
            "assert 'employer_extract' not in sys.modules\n"
        )
        r = subprocess.run([sys.executable, "-c", script], cwd=ROOT,
                           capture_output=True, text=True, timeout=120)
        assert r.returncode == 0, r.stderr


# ─── preflight 打到 Google 的那幾項 ─────────────────────────────────────────
# 這幾項原本零測試:要測就得能換掉身分,而身分是 import 時就建好的全域。
# 這裡不換掉檢查函式本身,而是換掉身分(或 client library 的 build),讓
# 檢查的邏輯 —— 分辨哪種 403、blame 怎麼標 —— 真的跑一次。

class TestCheckToken:
    def test_identity_error_reports_its_fix(self):
        gcp_identity = pytest.importorskip("gcp_identity")

        class Unconfigured(RecordingIdentity):
            def credentials(self, scopes=()):
                raise gcp_identity.IdentityError("SA_EMAIL 尚未填寫。\n  請填入服務帳號")

        identity.install(Unconfigured())
        r = pipeline._check_token()
        assert not r.ok
        assert "尚未填寫" in r.detail
        assert "請填入服務帳號" in r.fix

    def test_skipped_row_uses_the_same_name(self):
        """ADC 壞掉時印「略過」的那一列，名稱要和這一項真的跑起來時相同。

        名稱是共用套件寫死的；它改名而這邊沒跟上，同一項就會印成兩個名字。
        """
        pytest.importorskip("gcp_identity")
        assert pipeline._check_token().name == pipeline._TOKEN_CHECK

    def test_permission_denied_is_blamed_on_admin(self):
        checks = pytest.importorskip("gcp_identity.checks")

        class Denied:
            def refresh(self, request):
                raise RuntimeError("403 PERMISSION_DENIED getAccessToken")

        identity.install(RecordingIdentity(creds=Denied()))
        r = pipeline._check_token()
        assert not r.ok
        assert r.blame == checks.ADMIN        # render 據此列進「管理者處理」那一區
        assert "serviceAccountTokenCreator" in r.fix

    def test_token_ok(self):
        pytest.importorskip("gcp_identity")

        class Fine:
            def refresh(self, request):
                pass

        identity.install(RecordingIdentity(creds=Fine()))
        assert pipeline._check_token().ok


class FakeSheets:
    """只夠 _check_sheets 用的 Sheets service:spreadsheets().get().execute()。"""

    def __init__(self, result=None, error=None):
        self._result, self._error = result, error

    def spreadsheets(self):
        return self

    def get(self, **kw):
        return self

    def execute(self):
        if self._error:
            raise self._error
        return self._result


@pytest.fixture
def sheets_returns(monkeypatch, offline_identity):
    """讓 google_clients 建出來的 Sheets service 是 FakeSheets。

    換的是 client library 的 build(第三方的邊界),不是 sheets_service 本身 ——
    身分照樣穿過 seam 被要一次 spreadsheets scope。
    """
    def install(**kw):
        monkeypatch.setattr(google_clients, "build", lambda *a, **k: FakeSheets(**kw))
    return install


def _http_error(status: int, message: str):
    httplib2 = pytest.importorskip("httplib2")
    from googleapiclient.errors import HttpError
    body = ('{"error":{"message":"%s"}}' % message).encode()
    return HttpError(httplib2.Response({"status": str(status)}), body)


class TestCheckSheets:
    @pytest.fixture(autouse=True)
    def _needs_real_libs(self):
        pytest.importorskip("gcp_identity")
        pytest.importorskip("googleapiclient")

    def test_quota_project_403_is_not_a_sharing_problem(self, sheets_returns):
        """計費專案不可用也是 403,但修的是 GCP IAM。叫人去改試算表分享會繞不出來。"""
        sheets_returns(error=_http_error(
            403, "Caller does not have required permission to use project x"))
        r = pipeline._check_sheets()
        assert not r.ok
        assert "Service Usage Consumer" in r.fix
        assert "共用" not in r.fix.split("⚠️")[0]
        # 修法是「請管理者…」，就要列進管理者那一區，不能叫執行者自己處理
        from gcp_identity.checks import ADMIN
        assert r.blame == ADMIN

    def test_plain_403_is_a_sharing_problem(self, sheets_returns):
        sheets_returns(error=_http_error(403, "The caller does not have permission"))
        r = pipeline._check_sheets()
        assert not r.ok
        assert "共用" in r.fix
        from gcp_identity.checks import SELF
        assert r.blame == SELF                # 分享試算表是執行者自己能做的

    def test_404_points_at_spreadsheet_id(self, sheets_returns):
        sheets_returns(error=_http_error(404, "Requested entity was not found"))
        r = pipeline._check_sheets()
        assert not r.ok
        assert google_clients.SPREADSHEET_ID in r.fix

    def test_missing_sheet_name(self, sheets_returns):
        sheets_returns(result={"properties": {"title": "名冊"},
                               "sheets": [{"properties": {"title": "別的表"}}]})
        r = pipeline._check_sheets()
        assert not r.ok
        assert "別的表" in r.detail
        assert "google_clients.py" in r.fix

    def test_ok(self, sheets_returns):
        sheets_returns(result={
            "properties": {"title": "名冊"},
            "sheets": [{"properties": {"title": google_clients.SHEET_NAME}}]})
        assert pipeline._check_sheets().ok

