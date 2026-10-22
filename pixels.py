# -*- coding: utf-8 -*-
"""像素前處理:轉正、相對比例裁切、通道/放大/對比/去雜、去紅章(見 CONTEXT.md「去紅章」)。

三個呼叫端共用同一個 preprocess,差異只在設定值:
  - 許可證掃描(pipeline,送 Tesseract):SCAN_CONFIGS / FALLBACK_CONFIGS 那幾組
  - 契約頁(employer_extract,送 Vision):CONTRACT_PAGE_DEINK
  - 契約次頁(employer_extract,送 Vision 找台仲):NEXT_PAGE_DEINK
以前是三份實作(其中一份從 pipeline 延遲 import),彼此的關係沒有寫在任何地方;
換個前處理讀出的字就不一樣(ADR-0006 的滄/濃/澹),所以差異要看得見(#16)。

本模組是 leaf:只依賴標準函式庫、PIL 與 numpy。
"""
from __future__ import annotations

from io import BytesIO

import numpy as np
from PIL import Image, ImageFilter, ImageOps

# 契約頁送 Vision 前的去紅章。比其他兩組多一步抹白:契約頁的雇主名稱/地址常被
# 大紅章蓋住,只靠紅通道章尾仍會殘留灰影;Vision 自己會二值化,不需要放大與中值。
#   whiten_red=(門檻, 最低亮度):R - max(G,B) > 門檻 且 R > 最低亮度 的像素抹成白。
#   只清白底上的章;壓在黑字上的暗紅像素 R 偏低,保留,免得連筆畫一起抹掉。
#   門檻越小抹得越積極,最低亮度越高越保護暗處筆畫。
CONTRACT_PAGE_DEINK = {"channel": "R", "whiten_red": (45, 120), "contrast": (2, 98)}

# 契約次頁送 Vision 前的處理:就是許可證掃描的第一組「紅通道_2x_中值3」(去掉
# Tesseract 才用的 psm)。次頁的台仲字小、與章交錯,2x 放大 + 中值去雜讓它清楚;
# 不抹白 —— 次頁的章多半壓在台仲字上,抹白會吃掉筆畫。
NEXT_PAGE_DEINK = {"channel": "R", "scale": 2, "median": 3, "contrast": (2, 98)}


def open_upright(image_bytes: bytes) -> Image.Image:
    """解碼成 RGB 並依 EXIF 轉正。相對比例的 ROI 都以轉正後的影像為準。"""
    return ImageOps.exif_transpose(Image.open(BytesIO(image_bytes)).convert("RGB"))


def crop(img: Image.Image, roi: tuple) -> Image.Image:
    """依相對比例 roi=(x1, y1, x2, y2) 裁切(0~1,無條件捨去成整數像素)。"""
    w, h = img.size
    x1, y1, x2, y2 = roi
    return img.crop((int(x1 * w), int(y1 * h), int(x2 * w), int(y2 * h)))


def to_png(img: Image.Image) -> bytes:
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def crop_png(image_bytes: bytes, roi: tuple) -> bytes:
    """轉正 → 裁切 → PNG bytes(送 Vision 或存截圖用)。"""
    return to_png(crop(open_upright(image_bytes), roi))


def preprocess(image_bytes: bytes, cfg: dict) -> Image.Image:
    """依設定把影像處理成單通道,順序固定:

    轉正 → 裁切(roi)→ 取通道(channel: "R" 或 "gray")→ 抹白紅章(whiten_red)
    → 放大(scale)→ 百分位對比拉伸(contrast)→ 中值去雜(median)→ 銳化(sharpen)

    紅通道為底時紅印章與白紙都偏亮、黑字維持暗,章自然變淡;這是三個呼叫端
    共同的去紅章手法。cfg 裡其他鍵(name、psm、lang、whitelist)這裡不看。
    """
    img = open_upright(image_bytes)
    if "roi" in cfg:
        img = crop(img, cfg["roi"])
    rgb = np.array(img)
    arr = rgb[:, :, 0].copy() if cfg["channel"] == "R" else np.mean(rgb, axis=2).astype(np.uint8)
    if "whiten_red" in cfg:
        thresh, min_red = cfg["whiten_red"]
        c = rgb.astype(np.int16)
        r = c[:, :, 0]
        arr[((r - np.maximum(c[:, :, 1], c[:, :, 2])) > thresh) & (r > min_red)] = 255
    pil = Image.fromarray(arr)
    scale = cfg.get("scale", 1)
    if scale > 1:
        pil = pil.resize((pil.width * scale, pil.height * scale), Image.Resampling.LANCZOS)
    lo, hi = cfg.get("contrast", (2, 98))
    a = np.array(pil)
    low, high = np.percentile(a, lo), np.percentile(a, hi)
    if high > low:
        a = np.clip((a - low) * 255.0 / (high - low), 0, 255).astype(np.uint8)
    pil = Image.fromarray(a)
    if cfg.get("median", 0) > 0:
        pil = pil.filter(ImageFilter.MedianFilter(size=cfg["median"]))
    if cfg.get("sharpen", False):
        pil = pil.filter(ImageFilter.SHARPEN).filter(ImageFilter.SHARPEN)
    return pil


def deink_red_stamp(image_bytes: bytes) -> bytes:
    """契約頁去紅章(CONTRACT_PAGE_DEINK),回傳 PNG bytes 供 Vision 與契約頁評分使用。"""
    return to_png(preprocess(image_bytes, CONTRACT_PAGE_DEINK))
