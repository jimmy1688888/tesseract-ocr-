# -*- coding: utf-8 -*-
"""identity.py — 本專案的 Google 身分。

一件事：要模擬哪個服務帳號、用量記到哪個 GCP 專案，以及拿它換憑據。
呼叫端只需要 `credentials(scopes)`；誰要建 client 就跟這裡要，不跟 pipeline 要。

認證方式（不讀金鑰檔）：操作者本人登入一次取得 ADC（共用腳本
setup-google-adc.bat），程式再模擬服務帳號換取短期權杖。模擬由共用套件
gcp_identity 做 —— 為什麼不在 gcloud 登入時做：ADC 是機器全域的單一檔案、
只能記一個模擬目標，多專案各用不同服務帳號時會互相覆蓋。
見 gcp_identity/credentials.py 與 foreign-worker-query 的 docs/adr/0002。

本模組是 leaf：不 import 專案內任何東西。gcp_identity 延遲到第一次要身分時
才 import —— 沒裝它的機器（例如只跑測試）照樣 import 得了依賴本模組的程式。
"""
import os

# 這兩個值不是密鑰（光有它們拿不到任何權限），因此寫在這裡進版控，所有使用者
# 共用同一組、clone 下來不必修改。環境變數僅供臨時切換。
SA_EMAIL       = os.environ.get(
    "PIPELINE_SA_EMAIL", "lina-ocr@extreme-display-505910-s8.iam.gserviceaccount.com")
GCP_PROJECT_ID = os.environ.get("PIPELINE_GCP_PROJECT", "extreme-display-505910-s8")
LABEL          = "LinaOCRproject"

_current = None


def current():
    """目前的身分（gcp_identity.ProjectIdentity 或注入的替身），第一次呼叫才建。

    preflight 需要整個物件（共用套件的檢查吃 identity）；只要憑據的呼叫端用
    credentials() 就好。
    """
    global _current
    if _current is None:
        from gcp_identity import ProjectIdentity
        _current = ProjectIdentity(SA_EMAIL, GCP_PROJECT_ID, label=LABEL)
    return _current


def credentials(scopes: tuple[str, ...] = ()):
    """模擬服務帳號的憑據。scopes 是「要為服務帳號換哪種權杖」的宣告。"""
    return current().credentials(tuple(scopes))


def install(identity) -> None:
    """換掉目前的身分。測試掛護欄或替身、工具換服務帳號時用。"""
    global _current
    _current = identity


def reset() -> None:
    """丟掉目前的身分，下次 current() 重建。

    共用套件的權杖快取（ProjectIdentity._cache）掛在實例上，所以丟掉實例就是
    重設那一層 —— 不必碰它的私有屬性。依身分快取的 client 見 google_clients.reset。
    """
    global _current
    _current = None
