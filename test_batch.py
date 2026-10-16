# -*- coding: utf-8 -*-
"""一批:一次啟動要處理的那組 docx(見 CONTEXT.md「一批」、ADR-0009)。

這支檔案存在的理由是一個 bug:docx 穿過 matches.csv 時只剩檔名,反查救援
再用 `./docs / 檔名` 拼回去 —— `--file` 指到別處時,救援讀錯檔或靜默跳過。
所以一批把每份 docx 記成**完整路徑**,下游要開檔時跟它查,不自己拼。
"""
import argparse
import logging
from pathlib import Path

import pytest

import employer_extract
import google_clients
import pipeline
from batch import Batch


class TestFromDir:
    """Word 的鎖定檔不得進入處理清單。

    Word 開著某份文件時會在同目錄產生 `~$檔名.docx`(約 162 bytes,不是 zip)。
    它符合 *.docx,而 docx_images.read_images 會在它上面拋 BadZipFile 讓整批
    當場掛掉——觸發條件只是「有人正開著那份檔案在看」。
    """

    def test_word_lock_files_are_skipped(self, tmp_path):
        (tmp_path / "32572.docx").write_bytes(b"x")
        (tmp_path / "~$32572.docx").write_bytes(b"lock")
        assert [p.name for p in Batch.from_dir(tmp_path).docx_files] == ["32572.docx"]

    def test_order_is_deterministic(self, tmp_path):
        for n in ("32574.docx", "32503.docx", "~$32503.docx"):
            (tmp_path / n).write_bytes(b"x")
        assert [p.name for p in Batch.from_dir(tmp_path).docx_files] == [
            "32503.docx", "32574.docx"]


class TestProblem:
    """有沒有東西可處理。preflight 的輸入閘門與 main 共用這一個判斷。

    建立一批本身不會失敗(ADR-0009),所以「沒東西可處理」只會從這裡說出來。
    """

    def test_dir_with_docx_has_no_problem(self, tmp_path):
        (tmp_path / "32417.docx").write_bytes(b"x")
        assert Batch.from_dir(tmp_path).problem() is None

    def test_missing_dir(self, tmp_path):
        problem = Batch.from_dir(tmp_path / "不存在").problem()
        assert "輸入資料夾不存在" in problem

    def test_dir_without_docx(self, tmp_path):
        assert ".docx" in Batch.from_dir(tmp_path).problem()

    def test_dir_with_only_word_lock_files(self, tmp_path):
        """`~$` 開頭的 Word 鎖定檔不算「有東西可處理」。"""
        (tmp_path / "~$32417.docx").write_bytes(b"lock")
        assert Batch.from_dir(tmp_path).problem() is not None

    def test_existing_file_has_no_problem(self, tmp_path):
        f = tmp_path / "32417.docx"
        f.write_bytes(b"x")
        assert Batch.from_file(f).problem() is None

    def test_missing_file(self, tmp_path):
        problem = Batch.from_file(tmp_path / "32417.docx").problem()
        assert "找不到檔案" in problem
        assert "32417.docx" in problem

    def test_file_mode_ignores_what_is_in_docs(self, monkeypatch, tmp_path):
        """`--file` 時不看 ./docs:./docs 是空的也照樣有東西可處理。"""
        monkeypatch.chdir(tmp_path)
        f = tmp_path / "D" / "32417.docx"
        f.parent.mkdir()
        f.write_bytes(b"x")
        assert Batch.from_file(f).problem() is None


class TestOutputPaths:
    """往哪寫。預設值用字面值釘住 —— 搬進一批之前,這些路徑是模組層常數,
    一批之後的預設產出要落在一模一樣的位置(#24 的「預設參數下產出一致」)。"""

    def test_default_output_paths_are_unchanged(self, tmp_path):
        b = Batch.from_dir(tmp_path)
        assert b.output_dir == Path("scan_results")
        assert b.matches_csv == Path("scan_results/matches.csv")
        assert b.upload_log == Path("scan_results/upload_log.csv")
        assert b.employer_crop_dir == Path("scan_results/employer_crops")
        assert b.employer_texts_json == Path("scan_results/employer_texts.json")
        assert b.doubt_report_csv == Path("scan_results/address_doubts.csv")
        assert b.no_hit_review_dir == Path("scan_results/no_hit_review")

    def test_single_file_mode_writes_to_the_same_place(self, tmp_path):
        """--file 只改從哪讀,不改往哪寫。"""
        assert Batch.from_file(tmp_path / "x.docx").output_dir == Path("scan_results")

    def test_derived_paths_follow_output_dir(self, tmp_path):
        b = Batch.from_dir(tmp_path, output_dir=tmp_path / "out")
        for p in (b.matches_csv, b.upload_log, b.employer_crop_dir,
                  b.employer_texts_json, b.doubt_report_csv, b.no_hit_review_dir):
            assert p.parent == tmp_path / "out"


class _OneAgencyRoster:
    """名冊替身:任何電話都唯一命中同一家。"""

    INFO = {"許可證": "2340", "機構名稱": "測試仲介"}

    def agencies_by_phone(self, phone):
        return [self.INFO]


class TestRescueOpensTheBatchFile:
    """反查救援要開的是一批裡記的那份檔案,不是 `./docs/同檔名`。"""

    @pytest.fixture
    def opened(self, monkeypatch):
        """記下救援真正送去讀次頁的路徑。替身只放在外部邊界:名冊、Vision、次頁擷取。"""
        paths: list[Path] = []

        def fake_next_page(path, client=None):
            paths.append(Path(path))
            return {"phones": ["02-1234-5678"], "name": "", "address": ""}

        monkeypatch.setattr(pipeline, "_permit_lookup", lambda: _OneAgencyRoster())
        monkeypatch.setattr(google_clients, "vision_client", lambda: object())
        monkeypatch.setattr(employer_extract, "agency_block_from_next_page", fake_next_page)
        return paths

    @pytest.fixture
    def elsewhere(self, monkeypatch, tmp_path):
        """`--file` 指到的那份,在 ./docs 以外。cwd 底下的 ./docs 放一份同名的另一份檔案。"""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "32417.docx").write_bytes(b"decoy")
        (tmp_path / "D").mkdir()
        real = tmp_path / "D" / "32417.docx"
        real.write_bytes(b"real")
        return real

    def _items(self):
        return [{"source_docx": "32417.docx", "final_value": "", "reason": "全無命中"}]

    def test_opens_the_file_given_by_path(self, opened, elsewhere):
        items = self._items()
        n = pipeline.rescue_manual_review_via_agency_phone(
            Batch.from_file(elsewhere), items)
        assert opened == [elsewhere]
        assert n == 1
        assert items[0]["final_value"] == "2340"

    def test_rescues_even_when_docs_has_no_such_file(self, opened, elsewhere):
        """修好前:./docs 沒有同名檔就靜默跳過。"""
        (elsewhere.parent.parent / "docs" / "32417.docx").unlink()
        items = self._items()
        assert pipeline.rescue_manual_review_via_agency_phone(
            Batch.from_file(elsewhere), items) == 1
        assert opened == [elsewhere]


class TestMainStopsWhenNothingToProcess:
    """--skip-preflight 時沒有閘門,main 仍以同一個判斷(Batch.problem)擋下。"""

    @staticmethod
    def _opts(**kw):
        base = dict(file="", image="", roi="", full_scan=False,
                    preflight_only=False, skip_preflight=True)
        base.update(kw)
        return argparse.Namespace(**base)

    @pytest.fixture
    def scanned(self, monkeypatch):
        calls = []

        def fake_run_scan(batch, **kw):
            calls.append(list(batch.docx_files))
            raise _Stop      # 本檔只關心「有沒有開跑、跑哪幾份」,後面的步驟不走

        monkeypatch.setattr(pipeline, "run_scan", fake_run_scan)
        monkeypatch.setattr(pipeline.docx_images, "read_images", lambda p: [])
        return calls

    def test_missing_file_is_not_scanned(self, caplog, scanned, tmp_path):
        with caplog.at_level(logging.ERROR, logger=pipeline.logger.name):
            pipeline.main(self._opts(file=str(tmp_path / "32417.docx")))
        assert scanned == []
        assert any("找不到檔案" in r.message for r in caplog.records)

    def test_empty_docs_is_not_scanned(self, caplog, monkeypatch, scanned, tmp_path):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "docs").mkdir()
        with caplog.at_level(logging.ERROR, logger=pipeline.logger.name):
            pipeline.main(self._opts())
        assert scanned == []
        assert any(".docx" in r.message for r in caplog.records)

    def test_missing_docs_is_not_scanned(self, caplog, monkeypatch, scanned, tmp_path):
        monkeypatch.chdir(tmp_path)          # 底下沒有 ./docs
        with caplog.at_level(logging.ERROR, logger=pipeline.logger.name):
            pipeline.main(self._opts())
        assert scanned == []
        assert any("輸入資料夾不存在" in r.message for r in caplog.records)

    def test_existing_file_is_scanned_by_its_full_path(self, scanned, monkeypatch, tmp_path):
        # 單檔模式下 main 會把 root logger 調成 DEBUG;讓 monkeypatch 在測試後
        # 還原,否則後面的測試全都跑在 DEBUG 底下。
        monkeypatch.setattr(logging.getLogger(), "level", logging.getLogger().level)
        f = tmp_path / "D" / "32417.docx"
        f.parent.mkdir()
        f.write_bytes(b"x")
        with pytest.raises(_Stop):
            pipeline.main(self._opts(file=str(f)))
        assert scanned == [[f]]


class _Stop(Exception):
    pass
