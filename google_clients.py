# -*- coding: utf-8 -*-
"""google_clients.py — 用本專案的 Google 身分建好的 Vision client 與 Sheets service。

兩個 getter 都吃一個可選的 identity：不給就用 identity.current()。client 依身分
快取（同一個身分不重建 client 與 TLS 連線），reset() 把快取清掉。

Sheets 的位置（哪本試算表、哪個工作表）也在這裡：讀 Sheets 的工具 —— 不論是
pipeline 還是量測工具 —— 只需要 import 本模組，不必 import pipeline。
"""
from google.cloud import vision as gvision
from googleapiclient.discovery import build

import identity as _identity

SPREADSHEET_ID = "1zL5sRhaJHHXd-FBcY7rEm28Sfhz48l32gBwn-ATHalM"
SHEET_NAME     = "工作表1"                        # ← 改為實際工作表名稱

# Sheets 需要明確要求 spreadsheets scope；Vision client 自行要求 cloud-platform。
# 走模擬時這些 scope 是「要為 SA 換取哪種權杖」的宣告，由程式端決定，
# 不需要在 gcloud auth application-default login 時用 --scopes 指定。
SHEETS_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

# (種類, 身分物件) → client。以身分物件本身當鍵：注入的身分不會拿到前一個
# 身分建的 client。
_cache: dict[tuple[str, object], object] = {}


def vision_client(identity=None) -> gvision.ImageAnnotatorClient:
    """Vision client。

    憑證「明確傳入」，不讓 client library 自己去拿 ADC：ADC 是執行者本人的
    使用者憑據，用它直接打 Vision 會以「你本人」的身分呼叫——那可能還是通的
    （你自己有權限），但 Sheets 會 403（試算表分享給服務帳號、沒分享給你），
    而錯誤訊息完全看不出根因。統一走同一個身分就沒有這個歧義。
    """
    ident = identity or _identity.current()
    key = ("vision", ident)
    if key not in _cache:
        _cache[key] = gvision.ImageAnnotatorClient(credentials=ident.credentials())
    return _cache[key]


def sheets_service(identity=None):
    """Sheets service。

    與 Vision 不同，Sheets 必須明確傳入 scopes——googleapiclient 不會自己推斷，
    漏掉會在第一次寫入時回 "Request had insufficient authentication scopes"。
    """
    ident = identity or _identity.current()
    key = ("sheets", ident)
    if key not in _cache:
        _cache[key] = build("sheets", "v4",
                            credentials=ident.credentials(tuple(SHEETS_SCOPES)))
    return _cache[key]


def reset() -> None:
    """清掉所有快取的 client。身分本身的重設見 identity.reset。"""
    _cache.clear()
