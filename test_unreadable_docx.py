# -*- coding: utf-8 -*-
"""一份 docx 一列:讀不到圖的 docx 不再從 Sheets 消失(#47,候選 Q)。

CONTEXT.md「送審決策」:一批裡的每份 docx 都有一列,連讀不到圖的也有。以前 0 張可讀
內嵌圖的 docx(word/media 沒東西,或只有 .emf)被判成 small、沒有任何掃描列,
matches.csv 與 Sheets 上都沒有它;打不開的 docx(不是 zip)讓整批在 prefilter 那步中斷。

現在由掃描那一層保證:這兩種 docx 在 matches.csv 寫一列佔位列,送審決策去向是
「直接人工審查」—— 沒有圖,Vision 與反查救援都無從做起。

docx 全部捏造,用真的 zip 檔走真的 docx_images.read_images:「哪些副檔名讀得到」
這件事要真的過一次,不替身。
"""
import argparse
import csv
import io
import logging
import zipfile
from pathlib import Path

import pytest
from PIL import Image

import google_clients
import pipeline as P
from batch import Batch
from employer_result import DOUBT_KEY, EMPLOYER_COLUMNS
from sheet_row import SheetRow, SheetStatus
from submission_decision import Destination, decide

NO_IMAGES_REASON = "docx 無可讀的內嵌圖(word/media 底下沒有 png/jpg/bmp/tif/gif)"
UNREADABLE_REASON = "docx 無法開啟(不是有效的 docx 檔)"


# ─── 捏造 docx ────────────────────────────────────────────────────────────

def _png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), "white").save(buf, "PNG")
    return buf.getvalue()


def _docx(path: Path, media: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("word/document.xml", "<w:document/>")
        for name, data in media.items():
            z.writestr(f"word/media/{name}", data)
    return path


def no_media(d: Path) -> Path:
    """只有 document.xml,沒有 word/media。"""
    return _docx(d / "no_media.docx", {})


def emf_only(d: Path) -> Path:
    """掃描檔在 Word 裡存成 .emf:有圖,但沒有一張讀得到。"""
    return _docx(d / "emf_only.docx", {"image1.emf": b"\x01\x00\x00\x00", "image2.emf": b"x"})


def not_zip(d: Path) -> Path:
    p = d / "not_zip.docx"
    p.write_bytes(b"this is not a zip file")
    return p


def corrupt_zip(d: Path) -> Path:
    """zip 目錄完好、壓縮資料壞掉:打得開,讀圖那一刻才炸(zlib.error,不是 BadZipFile)。"""
    p = d / "corrupt.docx"
    with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("word/document.xml", "<w:document/>")
        z.writestr("word/media/image1.png", _png() * 50)
    raw = bytearray(p.read_bytes())
    start = raw.index(b"word/media/image1.png") + len("word/media/image1.png")
    for i in range(start + 10, start + 60):
        raw[i] ^= 0xFF
    p.write_bytes(bytes(raw))
    return p


def good_small(d: Path) -> Path:
    return _docx(d / "good.docx", {"image1.png": _png(), "image2.png": _png()})


BAD = [
    pytest.param(no_media, NO_IMAGES_REASON, P.ResultStatus.NO_IMAGES, id="no_media"),
    pytest.param(emf_only, NO_IMAGES_REASON, P.ResultStatus.NO_IMAGES, id="emf_only"),
    pytest.param(not_zip, UNREADABLE_REASON, P.ResultStatus.UNREADABLE, id="not_zip"),
    pytest.param(corrupt_zip, UNREADABLE_REASON, P.ResultStatus.UNREADABLE, id="corrupt_zip"),
]


@pytest.fixture
def mol_hit():
    """好的 small docx:每張圖都讀到同一個 mol 多數票(不跑 Tesseract)。注入 run_scan 用。"""
    def fake(docx, name, data):
        return P.Reading(P.ScanResult(source_docx=docx, image_name=name, docx_class="small",
                                      mol="1234", mol_conf=80.0, mol_from_vote=True,
                                      hit_roi="mol"), [])
    return fake


def _csv_rows(path):
    with open(path, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


# ─── 掃描層:佔位列,壞檔不中斷 ──────────────────────────────────────────

class TestScanWritesPlaceholder:

    @pytest.mark.parametrize("make, reason, status", BAD)
    def test_placeholder_row_in_matches_csv(self, make, reason, status, tmp_path):
        p = make(tmp_path)
        P.run_scan(Batch((p,), source=tmp_path, output_dir=tmp_path / "out"))

        [row] = _csv_rows(tmp_path / "out" / "matches.csv")
        r = P.ScanResult.from_csv_row(row)
        assert r.source_docx == p.name
        assert r.status is status
        assert r.manual_review is True
        assert r.image_name == ""

    def test_bad_file_does_not_stop_the_batch(self, mol_hit, tmp_path):
        """壞檔排在前面:後面的好 docx 照常產出。"""
        bad, good = not_zip(tmp_path), good_small(tmp_path)
        P.run_scan(Batch((bad, good), source=tmp_path, output_dir=tmp_path / "out"),
                   read_small=mol_hit)

        rows = _csv_rows(tmp_path / "out" / "matches.csv")
        assert {r["source_docx"] for r in rows} == {bad.name, good.name}
        assert [r["mol"] for r in rows if r["source_docx"] == good.name] == ["1234", "1234"]


# ─── 送審決策:第四種去向 ────────────────────────────────────────────────

class TestDirectManualReview:

    @pytest.mark.parametrize("make, reason, status", BAD)
    def test_decision(self, make, reason, status, tmp_path):
        p = make(tmp_path)
        P.run_scan(Batch((p,), source=tmp_path, output_dir=tmp_path))
        rows = [P.ScanResult.from_csv_row(r) for r in _csv_rows(tmp_path / "matches.csv")]

        d = decide(rows)
        assert d.destination is Destination.直接人工審查
        assert d.reason == reason
        assert (d.candidate_value, d.image_name, d.img_path) == ("", "", "")

    @pytest.mark.parametrize("make, reason, status", BAD)
    def test_queue_keeps_it_out_of_vision_and_rescue(self, make, reason, status, tmp_path):
        p = make(tmp_path)
        csv_path = P.run_scan(Batch((p,), source=tmp_path, output_dir=tmp_path))

        q = P.build_vision_queue(csv_path)
        assert (q.vision, q.rescue, q.keyin) == ([], [], [])
        assert q.direct_review == [SheetRow(p.name, "", SheetStatus.MANUAL_REVIEW, reason)]


# ─── 不變式:一批 N 份 → matches.csv 恰好 N 份、送審決策恰好 N 個 ─────────

def test_every_docx_in_the_batch_is_accounted_for(mol_hit, tmp_path):
    large = _docx(tmp_path / "large.docx", {f"image{n}.png": _png() for n in range(1, 8)})
    docs = (no_media(tmp_path), emf_only(tmp_path), not_zip(tmp_path),
            corrupt_zip(tmp_path), good_small(tmp_path), large)

    csv_path = P.run_scan(Batch(docs, source=tmp_path, output_dir=tmp_path / "out"),
                          read_small=mol_hit,
                          read_large=lambda *a, **k: None)   # large 全無命中

    names = sorted(p.name for p in docs)
    assert sorted({r["source_docx"] for r in _csv_rows(csv_path)}) == names
    q = P.build_vision_queue(csv_path)
    decided = [r.source_docx for r in q.vision + q.keyin + q.rescue + q.direct_review]
    assert sorted(decided) == names


# ─── 整條主流程:Sheets 一列,不送 Vision、不跑反查救援 ───────────────────

def _opts(**kw):
    base = dict(file="", image="", roi="", full_scan=False,
                preflight_only=False, skip_preflight=True)
    base.update(kw)
    return argparse.Namespace(**base)


@pytest.fixture
def world(monkeypatch, tmp_path):
    """外部邊界全替身:Vision、反查救援、名冊、Sheets。雇主擷取走真的(這幾份都不會送 Vision)。

    cwd 換到 tmp:main 的輸入 ./docs 與輸出 ./scan_results 都是相對路徑。
    """
    monkeypatch.chdir(tmp_path)
    (tmp_path / "docs").mkdir()
    calls = {"vision": [], "rescue": [], "sheets": [], "employer": 0}

    def fake_vision(items, read_text):
        calls["vision"].extend(items)
        return [], [], []

    def fake_rescue(batch, rows):
        calls["rescue"].extend(rows)
        return rows

    real_collect = P.collect_employer_fields

    def counting_collect(batch):
        calls["employer"] += 1
        return real_collect(batch)

    monkeypatch.setattr(P, "run_vision_items", fake_vision)
    monkeypatch.setattr(P, "rescue_manual_review_via_agency_phone", fake_rescue)
    monkeypatch.setattr(P, "collect_employer_fields", counting_collect)
    monkeypatch.setattr(P, "_permit_lookup", lambda: None)
    monkeypatch.setattr(P, "_sheets_append_atomic", lambda values: calls["sheets"].extend(values))
    monkeypatch.setattr(google_clients, "vision_client", lambda: object())
    return calls


_L = 7 + [k for _, k in EMPLOYER_COLUMNS].index(DOUBT_KEY)


class TestMainWritesOneRowEach:

    def test_sheets_rows(self, world, tmp_path):
        for make in (no_media, emf_only, not_zip):
            make(tmp_path / "docs")

        P.main(_opts())

        rows = {r[0]: r for r in world["sheets"]}
        assert sorted(rows) == ["emf_only.docx", "no_media.docx", "not_zip.docx"]
        for name, reason, l_text in [
            ("no_media.docx", NO_IMAGES_REASON, "docx 無內嵌圖,未擷取"),
            ("emf_only.docx", NO_IMAGES_REASON, "docx 無內嵌圖,未擷取"),
            ("not_zip.docx", UNREADABLE_REASON, "雇主擷取失敗(例外),未擷取"),
        ]:
            r = rows[name]
            assert r[1:4] == ["", "manual_review", reason], name
            assert r[4:7] == ["", "", ""], name              # E~G 空
            assert l_text in r[_L], name

    def test_no_vision_no_rescue(self, world, tmp_path):
        for make in (no_media, emf_only, not_zip):
            make(tmp_path / "docs")

        P.main(_opts())

        assert world["vision"] == []
        assert world["rescue"] == []


# ─── --image 對不到:直接結束 ────────────────────────────────────────────

class TestImageFilterMiss:

    @pytest.fixture(autouse=True)
    def _restore_root_level(self, monkeypatch):
        # 單檔模式下 main 會把 root logger 調成 DEBUG;讓 monkeypatch 在測試後還原
        monkeypatch.setattr(logging.getLogger(), "level", logging.getLogger().level)

    def _run(self, caplog, f, image):
        with caplog.at_level(logging.ERROR, logger=P.logger.name):
            P.main(_opts(file=str(f), image=image))
        return " ".join(r.message for r in caplog.records if r.levelno >= logging.ERROR)

    def test_stops_before_employer_and_sheets(self, world, caplog, tmp_path):
        msg = self._run(caplog, good_small(tmp_path), "image9.png")

        assert world["employer"] == 0
        assert world["sheets"] == []
        assert "image9.png" in msg
        assert "image1.png" in msg and "image2.png" in msg

    @pytest.mark.parametrize("make, says", [
        (not_zip, "無法開啟"),
        (emf_only, "沒有可讀的內嵌圖"),
    ])
    def test_docx_without_readable_images_says_so(self, make, says, world, caplog, tmp_path):
        msg = self._run(caplog, make(tmp_path), "image1.png")

        assert world["employer"] == 0
        assert world["sheets"] == []
        assert says in msg

    def test_image_needs_file(self, monkeypatch):
        """--image 只在 --file 單檔除錯用;整批帶 --image 是指令打錯,不讓它開跑。"""
        monkeypatch.setattr("sys.argv", ["pipeline.py", "--image", "image2.png"])
        with pytest.raises(SystemExit):
            P._parse_args()
