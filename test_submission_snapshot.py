# -*- coding: utf-8 -*-
"""送審快照(#38):真實素材的許可證掃描結果離線重放,釘住每份 docx 的送審決策。

送審決策(CONTEXT.md)散在多個函式、沒有直接測試,接下來要收成一個 module(#40)。
這張網記的是「現在每份 docx 的去向、候選值、被選的那張圖、理由(#51,原樣進 D 欄)」—— 機器輸出,不是真值
(同 H~O 快照):對不上代表行為變了,刻意的改動用 `python snapshots.py record` 重錄、
diff 交人看過。pytest 只比對、絕不寫入,也不打任何 API。

素材:舊 21 份的 matches.csv(samples/legacy/)與新素材在本機掃出的 matches.csv
(samples/permit_scan/<日期>/ 最新的某日掃描)。都不進版控,不在就 skip。
"""
import csv

import pytest

import real_samples as rs
import snapshots as snap


# ─── 真實素材 ─────────────────────────────────────────────────────────────

def test_submission_decisions_matches_snapshot():
    if not rs.permit_scans():
        pytest.skip("沒有任何許可證掃描結果(samples/legacy、samples/permit_scan)")
    saved = snap.load_decisions()
    if saved is None:
        pytest.skip("沒有送審快照:執行 python snapshots.py record 錄製(會先印出內容給你看)")
    msg = snap.decisions_mismatch_message(saved, snap.current_decisions())
    assert msg is None, msg


# ─── 錄製與比對本身(tmp 底下的假 corpus、假送審) ─────────────────────────

@pytest.fixture
def corpus(tmp_path, monkeypatch):
    monkeypatch.setattr(rs, "ROOT", tmp_path / "samples")
    p = tmp_path / "samples" / "legacy" / "matches.21份.backup.csv"
    p.parent.mkdir(parents=True)
    p.write_text("source_docx\n", encoding="utf-8-sig")
    return tmp_path / "samples"


def _decide(**override):
    """假送審:每份 docx 一個決定;override 可換掉某份的決定。"""
    base = {
        "甲.docx": [{"去向": "直接key-in", "候選值": "1234", "圖": "image5.jpeg"}],
        "乙.docx": [{"去向": "反查救援", "候選值": "", "圖": ""}],
    }
    base.update(override)
    return lambda csv_path: base


def _record(lines, **kw):
    kw.setdefault("decide", _decide())
    kw.setdefault("today", "2026-10-20")
    return snap.record_decisions(out=lines.append, **kw)


class TestRecordDecisions:

    def test_first_record_prints_every_docx(self, corpus):
        lines = []
        _record(lines)
        text = "\n".join(lines)
        assert "甲.docx" in text and "乙.docx" in text
        assert snap.load_decisions()["decisions"]["甲.docx"][0]["候選值"] == "1234"

    def test_rerecord_prints_only_changed_docx(self, corpus):
        _record([])
        lines = []
        _record(lines, decide=_decide(**{"甲.docx": [
            {"去向": "送Vision", "候選值": "1234", "圖": "image5.jpeg"}]}))
        changed = [l for l in lines if "→" in l]
        assert len(changed) == 1 and "甲.docx" in changed[0] and "送Vision" in changed[0]

    def test_old_snapshot_is_kept_with_a_date(self, corpus):
        _record([])
        before = snap.decisions_snapshot_path().read_text(encoding="utf-8")
        _record([], decide=_decide(**{"乙.docx": []}))
        kept = snap.decisions_snapshot_path().with_name("submission_decisions.2026-10-20.json")
        assert kept.read_text(encoding="utf-8") == before

    def test_nothing_changed_writes_nothing(self, corpus):
        _record([])
        lines = []
        _record(lines)
        assert list(snap.decisions_snapshot_path().parent.glob("submission_decisions.*.json")) == []
        assert any("沒有變化" in l for l in lines)

    def test_no_scan_results_refuses_to_record(self, corpus):
        for p in (rs.ROOT / "legacy").iterdir():
            p.unlink()
        lines = []
        assert _record(lines) is None
        assert not snap.decisions_snapshot_path().exists()
        assert any("沒有任何許可證掃描結果" in l for l in lines)


class TestDecisionsMismatch:

    def test_match_is_none(self, corpus):
        _record([])
        now = snap.current_decisions(decide=_decide())
        assert snap.decisions_mismatch_message(snap.load_decisions(), now) is None

    def test_names_the_docx_and_both_decisions(self, corpus):
        _record([])
        now = snap.current_decisions(decide=_decide(**{"甲.docx": [
            {"去向": "送Vision", "候選值": "1234", "圖": "image5.jpeg"}]}))
        msg = snap.decisions_mismatch_message(snap.load_decisions(), now)
        assert "甲.docx" in msg and "直接key-in" in msg and "送Vision" in msg
        assert "乙.docx" not in msg

    def test_comparing_never_writes(self, corpus):
        _record([])
        before = snap.decisions_snapshot_path().read_bytes()
        now = snap.current_decisions(decide=_decide(**{"乙.docx": []}))
        snap.decisions_mismatch_message(snap.load_decisions(), now)
        assert snap.decisions_snapshot_path().read_bytes() == before


from pipeline import ResultStatus  # noqa: E402

LARGE_NO_HIT = ResultStatus.LARGE_NO_HIT
PERMIT_PARTIAL = ResultStatus.PERMIT_PARTIAL


class TestDefaultDecisionsAdapter:
    """現行送審程式 → 快照的形狀(去向／候選值／圖／理由)。#40 重構時只換這一層。

    理由會原樣進 Sheets D 欄(#51):候選 P 要搬產生理由的規則,得先有網。"""

    def _csv(self, tmp_path, rows):
        """經 ScanResult 的正式序列化寫出 matches.csv(旗標的字面值不自己猜)。"""
        import pipeline
        p = tmp_path / "matches.csv"
        with open(p, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=pipeline.CSV_FIELDS)
            w.writeheader()
            for r in rows:
                w.writerow(pipeline.ScanResult(**r).to_csv_row())
        return p

    def test_three_destinations(self, tmp_path):
        p = self._csv(tmp_path, [
            # 高信心多數票 → 直接 key-in
            {"source_docx": "a.docx", "image_name": "image5.jpeg", "docx_class": "large",
             "id": "1234", "id_conf": 80.0, "id_from_vote": True,
             "final_value": "1234", "final_conf": 80.0},
            # 全無命中 → 反查救援
            {"source_docx": "b.docx", "image_name": "image5.jpeg", "docx_class": "large",
             "status": LARGE_NO_HIT},
            # mol 單一判讀、信心不足以 key-in → 送 Vision
            {"source_docx": "c.docx", "image_name": "image2.jpeg", "docx_class": "large",
             "mol": "5678", "mol_conf": 53.0, "final_value": "5678", "final_conf": 53.0,
             "vision_review": True},
        ])
        got = snap._default_decisions(p)
        assert got["a.docx"] == [{"去向": "直接key-in", "候選值": "1234", "圖": "image5.jpeg",
                                  "理由": "permit多數票_高信心_最早圖(image5.jpeg) conf=80.0"}]
        assert got["b.docx"] == [{"去向": "反查救援", "候選值": "", "圖": "",
                                  "理由": "large docx：全 1 張圖掃描無許可證命中"}]
        assert got["c.docx"] == [{"去向": "送Vision", "候選值": "5678", "圖": "image2.jpeg",
                                  "理由": "large:讀值信心不足"}]

    def test_low_conf_single_read_is_rescue(self, tmp_path):
        p = self._csv(tmp_path, [
            {"source_docx": "d.docx", "image_name": "image5.jpeg", "docx_class": "large",
             "id": "1234", "id_conf": 40.0, "final_value": "1234", "final_conf": 40.0,
             "vision_review": True, "status": PERMIT_PARTIAL},
        ])
        assert snap._default_decisions(p)["d.docx"] == [
            {"去向": "反查救援", "候選值": "1234", "圖": "image5.jpeg",
             "理由": "[單一判讀低信心 conf=40.0<50,略過 ROI Vision 改走次頁電話反查] "
                     "permit部分命中無多數"}]
