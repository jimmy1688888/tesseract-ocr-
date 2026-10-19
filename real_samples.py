# -*- coding: utf-8 -*-
"""真實素材 corpus:素材在哪、核對值是什麼、不在時怎麼略過。

用語見 CONTEXT.md「驗證」一節。samples/ 底下全部含個資或只對素材有意義,
被 .gitignore 擋住、不進版控;換一台沒有素材的機器,用到它的測試一律乾淨 skip:

  samples/docs/*.docx            真實素材本體
  samples/vision/{stem}.json     打過一次 Vision 存下的全文,離線重放、不再花錢
                                 {image, roi, texts: {roi: 全文}}
  samples/checked_values.json    核對值(人看過原圖確認的值),依 stem 分:
                                 {stem: {"契約頁": 圖檔名, "雇主欄位": {欄: 值}}}
                                 允許只填部分;沒填的不驗
  samples/snapshots/             快照(機器輸出,不是真值),見 snapshots.py
  samples/legacy/                舊格式檔(如 #15 之前寫出的 matches.csv)
  samples/review/                錄製新素材時匯出的各頁圖,供人確認契約頁

核對值跟著素材放、不放測試程式裡:沒有素材,核對值毫無用處。
"""
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent / "samples"


def _docs() -> Path:
    return ROOT / "docs"


def _vision() -> Path:
    return ROOT / "vision"


def _checked_file() -> Path:
    return ROOT / "checked_values.json"


def docx_samples() -> list[Path]:
    """全部 docx 素材,依檔名排序。

    排除 Word 的鎖定檔 ~$*.docx:有人正開著那份素材時它就在,而且不是 zip。
    """
    if not _docs().is_dir():
        return []
    return sorted(p for p in _docs().glob("*.docx") if not p.name.startswith("~$"))


# 給 parametrize 用:收集測試時就得知道有哪些素材。
REAL_DOCX = docx_samples()

needs_samples = pytest.mark.skipif(not REAL_DOCX, reason="沒有真實素材 samples/docs/*.docx")


def sample(name: str) -> Path:
    """一份指定的素材;不在就 skip 呼叫它的測試。"""
    p = _docs() / name
    if not p.exists():
        pytest.skip(f"沒有 samples/docs/{name}")
    return p


# ─── Vision 全文 ─────────────────────────────────────────────────────────

def vision_path(stem: str) -> Path:
    return _vision() / f"{stem}.json"


def vision_stems() -> list[str]:
    """有 Vision 全文的素材 stem,依序。"""
    if not _vision().is_dir():
        return []
    return sorted(p.stem for p in _vision().glob("*.json"))


def vision_sample(stem: str) -> dict:
    """一份存好的 Vision 全文素材;不在就 skip。"""
    p = vision_path(stem)
    if not p.exists():
        pytest.skip(f"沒有 samples/vision/{stem}.json")
    return json.loads(p.read_text(encoding="utf-8"))


def employer_text(stem: str) -> str:
    """正式流程送 Vision 的那一段(texts[roi])的全文;不在就 skip。"""
    s = vision_sample(stem)
    return s["texts"][s["roi"]]


def save_vision_text(stem: str, image: str, roi: str, text: str) -> Path:
    """存一份新的 Vision 全文素材。已存在就拒絕:每份素材的全文只打一次。"""
    p = vision_path(stem)
    if p.exists():
        raise FileExistsError(f"{p} 已存在:每份素材的全文只打一次,不覆蓋")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"image": image, "roi": roi, "texts": {roi: text}},
                            ensure_ascii=False, indent=1), encoding="utf-8")
    return p


# ─── 核對值 ──────────────────────────────────────────────────────────────

def _checked_all() -> dict:
    p = _checked_file()
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def checked_contract_page(docx_name: str) -> str | None:
    """人確認過的契約頁圖檔名;沒有核對值就是 None(不拿機器的選擇頂替)。"""
    return _checked_all().get(Path(docx_name).stem, {}).get("契約頁")


def checked_employer_fields(stem: str) -> dict[str, str]:
    """人確認過的雇主欄位(可能只有部分欄);沒有就是空 dict。"""
    return dict(_checked_all().get(stem, {}).get("雇主欄位", {}))


def record_checked_contract_page(docx_name: str, image_name: str) -> None:
    """記下人確認過的契約頁。只在人看過圖之後呼叫 —— 程式不自動升格。"""
    data = _checked_all()
    data.setdefault(Path(docx_name).stem, {})["契約頁"] = image_name
    p = _checked_file()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True),
                 encoding="utf-8")


# ─── 舊格式檔 ────────────────────────────────────────────────────────────

def old_matches_csv() -> Path:
    """#15 之前寫出的真實 matches.csv(21 份實跑的備份,唯讀);不在就 skip。"""
    p = ROOT / "legacy" / "matches.21份.backup.csv"
    if not p.exists():
        pytest.skip(f"沒有 samples/legacy/{p.name}")
    return p


def needs_tesseract() -> None:
    """與正式流程同一套方式找 tesseract.exe;找不到就 skip。"""
    import shutil
    import pytesseract
    import employer_extract
    employer_extract._ensure_tesseract()
    cmd = pytesseract.pytesseract.tesseract_cmd
    if not Path(cmd).exists() and not shutil.which(cmd):
        pytest.skip("沒有 Tesseract")
