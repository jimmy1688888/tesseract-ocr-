# -*- coding: utf-8 -*-
"""測試用:使用者提供的真實素材在哪、不在時怎麼略過。

素材放在 samples/docs/(含個資,被 .gitignore 的 samples/ 擋住,不進版控)。
換一台沒有素材的機器,用到它的測試一律乾淨 skip,不得紅。
"""
import json
from pathlib import Path

import pytest

SAMPLES = Path(__file__).resolve().parent / "samples" / "docs"
# 打過一次 Vision 存下的全文(同樣含個資、不進版控),測試離線重放、不再花錢。
VISION = SAMPLES.parent / "vision"

# 排除 Word 的鎖定檔 ~$*.docx:有人正開著那份素材時它就在,而且不是 zip。
REAL_DOCX = sorted(p for p in SAMPLES.glob("*.docx")
                   if not p.name.startswith("~$")) if SAMPLES.is_dir() else []

needs_samples = pytest.mark.skipif(not REAL_DOCX, reason="沒有真實素材 samples/docs/*.docx")


def sample(name: str) -> Path:
    """一份指定的素材;不在就 skip 呼叫它的測試。"""
    p = SAMPLES / name
    if not p.exists():
        pytest.skip(f"沒有 samples/docs/{name}")
    return p


def vision_sample(stem: str) -> dict:
    """一份存好的 Vision 全文素材(samples/vision/{stem}.json);不在就 skip。"""
    p = VISION / f"{stem}.json"
    if not p.exists():
        pytest.skip(f"沒有 samples/vision/{stem}.json")
    return json.loads(p.read_text(encoding="utf-8"))


def needs_tesseract() -> None:
    """與正式流程同一套方式找 tesseract.exe;找不到就 skip。"""
    import shutil
    import pytesseract
    import employer_extract
    employer_extract._ensure_tesseract()
    cmd = pytesseract.pytesseract.tesseract_cmd
    if not Path(cmd).exists() and not shutil.which(cmd):
        pytest.skip("沒有 Tesseract")
