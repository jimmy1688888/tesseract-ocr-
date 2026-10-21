# -*- coding: utf-8 -*-
"""H~O 快照(#32):離線比對真實素材的雇主欄位解析,守住「純重構逐格不變」。

快照是機器輸出、不是真值(CONTEXT.md「快照」):對不上代表行為變了,不代表錯了。
行為改動要用 `python snapshots.py record` 刻意重錄,重錄印出的逐格 diff 要人看過。
pytest 絕不寫快照。
"""
import json

import pytest

import real_samples as rs
import snapshots as snap


# ─── 真實素材:每份有 Vision 全文的素材都比對它的快照 ─────────────────────

@pytest.mark.parametrize("stem", rs.vision_stems())
def test_employer_fields_match_snapshot(stem):
    saved = snap.load()
    if saved is None or stem not in saved["fields"]:
        pytest.skip(f"{stem} 沒有快照:執行 python snapshots.py record 錄製(會先印出內容給你看)")
    msg = snap.mismatch_message(stem, saved, snap.current_fields([stem])[stem],
                                snap.alldata_sha256())
    assert msg is None, msg


def test_every_sheets_employer_column_is_in_the_snapshot():
    """快照記的是解析輸出,不是 Sheets 欄:Sheets 拿掉欄(#30)時值照算,快照的鍵不跟著少;
    但 Sheets 寫出的每一欄都必須被快照守著。"""
    import pipeline
    assert set(pipeline._EMPLOYER_COL_KEYS) <= set(snap.KEYS)
    assert "_note" in snap.KEYS


# ─── 錄製與比對本身(tmp 底下的假 corpus、假解析器) ─────────────────────

@pytest.fixture
def corpus(tmp_path, monkeypatch):
    monkeypatch.setattr(rs, "ROOT", tmp_path / "samples")
    for stem in ("33701", "33702"):
        p = rs.vision_path(stem)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"image": "image2.png", "roi": "0.35",
                                 "texts": {"0.35": f"全文-{stem}"}}, ensure_ascii=False),
                     encoding="utf-8")
    return tmp_path / "samples"


def _parser(**override):
    """假解析器:每欄回「欄名@全文」,另帶一個內部鍵(不該進快照)。"""
    def extract(text):
        f = {k: f"{k}@{text}" for k in snap.KEYS}
        f["_note"] = ""
        f["疑慮"] = ("內部",)
        f.update(override)
        return f
    return extract


def _record(lines, **kw):
    kw.setdefault("extract", _parser())
    kw.setdefault("data_hash", "hash-A")
    kw.setdefault("today", "2026-10-19")
    return snap.record(out=lines.append, **kw)


class TestRecord:

    def test_first_record_prints_every_cell(self, corpus):
        lines = []
        _record(lines)
        text = "\n".join(lines)
        for stem in ("33701", "33702"):
            for k in snap.KEYS:
                assert f"{stem} {k}" in text

    def test_snapshot_keeps_output_keys_only_and_the_data_hash(self, corpus):
        _record([])
        saved = snap.load()
        assert saved["alldata_sha256"] == "hash-A"
        assert set(saved["fields"]["33701"]) == set(snap.KEYS)
        assert "疑慮" not in saved["fields"]["33701"]
        assert "_note" in snap.KEYS

    def test_rerecord_prints_only_changed_cells(self, corpus):
        _record([])
        lines = []
        _record(lines, extract=_parser(電話="新電話"))
        changed = [l for l in lines if "→" in l]
        assert len(changed) == 2                           # 兩份素材的電話各一格
        assert all("電話" in l and "新電話" in l for l in changed)

    def test_old_snapshot_is_kept_with_a_date(self, corpus):
        _record([])
        before = snap.snapshot_path().read_text(encoding="utf-8")
        _record([], extract=_parser(電話="新電話"))
        kept = snap.snapshot_path().with_name("employer_fields.2026-10-19.json")
        assert kept.read_text(encoding="utf-8") == before

    def test_same_day_rerecord_never_overwrites_a_kept_snapshot(self, corpus):
        _record([])
        _record([], extract=_parser(電話="二"))
        _record([], extract=_parser(電話="三"))
        kept = sorted(p.name for p in snap.snapshot_path().parent.glob("employer_fields.*.json"))
        assert kept == ["employer_fields.2026-10-19.2.json", "employer_fields.2026-10-19.json"]

    def test_no_vision_text_at_all_refuses_to_record(self, corpus):
        """vision/ 整個不見時錄下去會是一份空快照,把每份素材都當成「已移除」。"""
        _record([])
        before = snap.snapshot_path().read_bytes()
        for p in rs.ROOT.joinpath("vision").iterdir():
            p.unlink()
        lines = []
        assert _record(lines) is None
        assert snap.snapshot_path().read_bytes() == before
        assert any("沒有任何 Vision 全文" in l for l in lines)

    def test_nothing_changed_writes_nothing(self, corpus):
        _record([])
        lines = []
        _record(lines)
        assert list(snap.snapshot_path().parent.glob("employer_fields.*.json")) == []
        assert any("沒有變化" in l for l in lines)


class TestMismatchMessage:

    def _saved(self, corpus):
        _record([])
        return snap.load()

    def test_match_is_none(self, corpus):
        saved = self._saved(corpus)
        now = snap.current_fields(["33701"], extract=_parser())["33701"]
        assert snap.mismatch_message("33701", saved, now, "hash-A") is None

    def test_names_the_cell_and_both_values(self, corpus):
        saved = self._saved(corpus)
        now = snap.current_fields(["33701"], extract=_parser(電話="新電話"))["33701"]
        msg = snap.mismatch_message("33701", saved, now, "hash-A")
        assert "33701 電話" in msg and "電話@全文-33701" in msg and "新電話" in msg
        assert "雇主名稱_中" not in msg                     # 沒變的格不列

    def test_says_when_the_address_data_changed(self, corpus):
        saved = self._saved(corpus)
        now = snap.current_fields(["33701"], extract=_parser(郵遞區號="999"))["33701"]
        msg = snap.mismatch_message("33701", saved, now, "hash-B")
        assert "AllData.json" in msg

    def test_same_data_does_not_blame_the_data(self, corpus):
        saved = self._saved(corpus)
        now = snap.current_fields(["33701"], extract=_parser(郵遞區號="999"))["33701"]
        assert "AllData.json" not in snap.mismatch_message("33701", saved, now, "hash-A")

    def test_comparing_never_writes(self, corpus):
        saved = self._saved(corpus)
        before = snap.snapshot_path().read_bytes()
        now = snap.current_fields(["33701"], extract=_parser(電話="新電話"))["33701"]
        snap.mismatch_message("33701", saved, now, "hash-B")
        assert snap.snapshot_path().read_bytes() == before
        assert len(list(snap.snapshot_path().parent.iterdir())) == 1
