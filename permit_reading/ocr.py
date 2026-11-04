# -*- coding: utf-8 -*-
"""許可證怎麼讀(Tesseract 設定)與它唯一的外部依賴(Ocr)的預設實作。

Tesseract 本身(執行檔、語言、呼叫)在 tesseract_ocr(#76)。
"""
from __future__ import annotations

from typing import Callable

from pixels import preprocess
from tesseract_ocr import TESS_LANG, ocr_with_conf

# ─── Tesseract configs ────────────────────────────────────────────────────
WHITELIST_LATIN = (
    "0123456789"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "abcdefghijklmnopqrstuvwxyz"
    " :.'-,"
)

SCAN_CONFIGS = [
    {"name": "紅通道_2x_中值3",  "channel": "R",    "scale": 2, "median": 3, "contrast": (2, 98), "psm": 3},
    {"name": "紅通道_原尺寸",    "channel": "R",    "scale": 1, "median": 0, "contrast": (2, 98), "psm": 3},
    {"name": "灰階_2x_中值3",    "channel": "gray", "scale": 2, "median": 3, "contrast": (2, 98), "psm": 3},
    {"name": "紅通道_銳化_PSM6", "channel": "R",    "scale": 2, "median": 3, "contrast": (2, 98), "psm": 6, "sharpen": True},
    {"name": "紅通道_2x_PSM6",   "channel": "R",    "scale": 2, "median": 3, "contrast": (2, 98), "psm": 6},
    {"name": "灰階_2x_PSM6",     "channel": "gray", "scale": 2, "median": 3, "contrast": (2, 98), "psm": 6},
    {"name": "灰階_原尺寸",      "channel": "gray", "scale": 1, "median": 0, "contrast": (2, 98), "psm": 3},
    {"name": "紅通道_2x_PSM11",  "channel": "R",    "scale": 2, "median": 3, "contrast": (2, 98), "psm": 11},
    {"name": "灰階_2x_PSM11",    "channel": "gray", "scale": 2, "median": 3, "contrast": (2, 98), "psm": 11},
]

FALLBACK_CONFIGS = [
    {"name": "英數白名單_PSM6", "channel": "R",    "scale": 2, "median": 3,
     "contrast": (2, 98), "psm": 6, "lang": "eng", "whitelist": WHITELIST_LATIN},
    {"name": "英數白名單_PSM3", "channel": "gray", "scale": 2, "median": 3,
     "contrast": (2, 98), "psm": 3, "lang": "eng", "whitelist": WHITELIST_LATIN},
]

_VOTE_NAMES = {
    "紅通道_2x_中值3", "灰階_2x_中值3", "紅通道_原尺寸",
    "紅通道_2x_PSM6", "灰階_2x_PSM6", "紅通道_銳化_PSM6",
}
PERMIT_VOTE_CONFIGS = [c for c in SCAN_CONFIGS if c["name"] in _VOTE_NAMES]


def build_tess_config(cfg: dict) -> str:
    parts = [f"--psm {cfg.get('psm', 3)}"]
    if "whitelist" in cfg and cfg["whitelist"]:
        parts.append(f"-c tessedit_char_whitelist={cfg['whitelist']}")
    return " ".join(parts)


# 讀一個 ROI:(image bytes, 設定, ROI 相對比例) → (文字, 信心)。這是本模組唯一的
# 外部依賴;測試傳假的進來,就能不跑 Tesseract、不需要真的影像(#19)。
Ocr = Callable[[bytes, dict, tuple], tuple[str, float]]


def tesseract(image_bytes: bytes, cfg: dict, roi: tuple) -> tuple[str, float]:
    """預設的 Ocr:前處理(pixels.preprocess)後送 Tesseract。"""
    img = preprocess(image_bytes, {**cfg, "roi": roi})
    return ocr_with_conf(img, cfg.get("lang", TESS_LANG), build_tess_config(cfg))
