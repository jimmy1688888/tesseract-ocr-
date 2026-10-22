# -*- coding: utf-8 -*-
"""preflight 的閘門語意：什麼該擋下整批、什麼只該留空。

這支檔案存在的理由是一個 bug：仲介名冊載入失敗本來被寫成 preflight 的失敗項，
於是 run_preflight 回傳 False、main 直接 return —— 整批不跑。而
`_permit_lookup` 的註解、preflight 自己印出來的那句「不影響許可證判讀」、
以及名冊查表本身的降級路徑（`_agency_cols` 查無 → 三個空字串）三處都承諾
「留空但不中斷」。承諾與行為互相矛盾，而矛盾的那一側使用者看不到。

所以這裡釘的不是實作細節，是那句承諾：**名冊是降級來源，不是閘門。**
整條承諾由三段測試接力釘住，少一段就只是看著門沒進屋：

  1. 壞掉的名冊檔真的會讓 `_permit_lookup()` 降級成 None（而不是炸掉）
  2. 降級之後 preflight 放行（閘門會開）
  3. 放行之後寫出去的那一列，B 欄有值、E~G 留空（屋裡的燈還亮著）
"""
import logging

import pytest

import permit_lookup
import pipeline
from batch import Batch


@pytest.fixture
def docx_dir(tmp_path):
    """一個有東西可處理的輸入資料夾。

    內容不必是真的 docx —— 本檔測的是 preflight 要不要放行，不是解壓。
    """
    (tmp_path / "32417.docx").write_bytes(b"placeholder")
    return tmp_path


@pytest.fixture
def empty_dir(tmp_path):
    """沒有 .docx 的輸入資料夾。"""
    return tmp_path


@pytest.fixture
def roster_unavailable(monkeypatch):
    """名冊已經降級的狀態。

    `False` 是 `_permit_lookup` 的降級標記（載入失敗後不再每列重試），
    它會讓 `_permit_lookup()` 回傳 None。

    這個 fixture 假設「標記已經掛上去了」，所以**不足以**證明壞檔案會讓標記掛
    上去 —— 那一段由 TestRosterFailureDegrades 用真的壞檔案走一次。
    """
    monkeypatch.setattr(pipeline, "_PERMIT_LOOKUP", False)


@pytest.fixture
def gchecks():
    """preflight 的檢查結果與渲染都來自共用套件；沒裝的機器上乾淨略過。"""
    return pytest.importorskip("gcp_identity.checks")


@pytest.fixture
def google_checks_pass(monkeypatch, gchecks):
    """把外部邊界換成通過，只留下本檔關心的輸入／名冊。

    換的是共用套件的 check_adc／check_token、Tesseract、Sheets —— 不換 pipeline
    怎麼串它們，所以「上游失敗時下游標略過」那段邏輯照樣真的跑。
    """
    def ok(name):
        return gchecks.CheckResult(name, True, "（測試替身）")

    monkeypatch.setattr(pipeline, "_check_tesseract", lambda: ok("Tesseract 執行檔"))
    monkeypatch.setattr(gchecks, "check_adc", lambda: (ok("Google 認證(ADC)"), object()))
    monkeypatch.setattr(gchecks, "check_token", lambda ident: ok("模擬服務帳號"))
    monkeypatch.setattr(pipeline, "_check_sheets", lambda: ok("Google Sheets 存取"))
    return gchecks


class TestRosterFailureDegrades:
    """接力第一段：名冊檔真的壞了，降級標記要自己掛上去，不能讓例外跑出來。

    這裡刻意不用 roster_unavailable fixture —— 那個 fixture 直接把標記設好，
    等於跳過 `_permit_lookup` 裡真正把例外轉成標記的那段 try/except。
    """

    @staticmethod
    def _loads_from(path):
        """一個會真的去讀 path 的 PermitLookup 替身。

        讀檔與解析都走 permit_lookup 真正的 fetch_roster，所以拋出來的是真的
        那個例外，不是測試自己捏的。
        """
        class RosterFromPath:
            def __init__(self):
                permit_lookup.fetch_roster(path)
        return RosterFromPath

    def test_broken_roster_file_raises_at_source(self, tmp_path):
        """先確認壞檔案真的會炸 —— 否則下面兩支測的是空氣。"""
        broken = tmp_path / "agency_roster.json"
        broken.write_text("{ 這不是合法的 JSON", encoding="utf-8")
        with pytest.raises(Exception):
            permit_lookup.fetch_roster(broken)

    def test_broken_roster_file_degrades_to_none(self, monkeypatch, tmp_path):
        broken = tmp_path / "agency_roster.json"
        broken.write_text("{ 這不是合法的 JSON", encoding="utf-8")
        monkeypatch.setattr(pipeline, "PermitLookup", self._loads_from(broken))
        monkeypatch.setattr(pipeline, "_PERMIT_LOOKUP", None)   # 清快取，逼它重載

        assert pipeline._permit_lookup() is None
        # False 而不是 None：降級標記要留下，否則每一列都會重試一次壞檔案
        assert pipeline._PERMIT_LOOKUP is False

    def test_missing_roster_file_degrades_to_none(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            pipeline, "PermitLookup", self._loads_from(tmp_path / "不存在.json"))
        monkeypatch.setattr(pipeline, "_PERMIT_LOOKUP", None)

        assert pipeline._permit_lookup() is None


class TestRosterIsNotAGate:
    """接力第二段：降級之後 preflight 放行。"""

    def test_check_input_passes_when_only_roster_unavailable(
        self, gchecks, docx_dir, roster_unavailable
    ):
        assert pipeline._check_input(Batch.from_dir(docx_dir)).ok

    def test_run_preflight_passes_when_only_roster_unavailable(
        self, docx_dir, roster_unavailable, google_checks_pass
    ):
        # run_preflight 回傳 True 就是「main 不會提早 return」——
        # main 對 preflight 的唯一反應是 `if not run_preflight(...): return`。
        assert pipeline.run_preflight(Batch.from_dir(docx_dir)) is True

    def test_roster_failure_is_still_said_out_loud(
        self, caplog, docx_dir, roster_unavailable, google_checks_pass
    ):
        """降級不等於沉默：不擋人，但要讓人知道 E~G 會是空的。"""
        with caplog.at_level(logging.WARNING, logger=pipeline.logger.name):
            pipeline.run_preflight(Batch.from_dir(docx_dir))
        assert any("名冊" in r.message for r in caplog.records)

    def test_roster_failure_is_not_counted_as_a_failed_check(
        self, caplog, docx_dir, roster_unavailable, google_checks_pass
    ):
        """一個降級不該印成「preflight 未通過（N 項）」。"""
        with caplog.at_level(logging.ERROR, logger=pipeline.logger.name):
            pipeline.run_preflight(Batch.from_dir(docx_dir))
        assert not [r for r in caplog.records if r.levelno >= logging.ERROR]

    def test_single_file_mode_also_warns_about_roster(
        self, caplog, monkeypatch, empty_dir, roster_unavailable, google_checks_pass
    ):
        """`--file` 指定單一檔案時也要印這句警告。

        這是一個刻意的取捨，不是順手：單檔模式常是「補跑漏掉的那一份」，E~G
        一樣會留空，最不該跑完才發現仲介欄空白。代價是連 --preflight-only 都會
        提早載 2.2MB 名冊——已確認接受。

        ./docs 是空的：證明這個模式不看 ./docs（還是回 True），卻仍然印出名冊警告。
        """
        monkeypatch.chdir(empty_dir)
        target = empty_dir / "別處" / "32417.docx"
        target.parent.mkdir()
        target.write_bytes(b"placeholder")
        with caplog.at_level(logging.WARNING, logger=pipeline.logger.name):
            assert pipeline.run_preflight(Batch.from_file(target)) is True
        assert any("名冊" in r.message for r in caplog.records)


class TestSheetRowSurvivesRosterFailure:
    """接力第三段：放行之後，寫出去的那一列長什麼樣。

    這是整張票真正答應的事 —— 前兩段只證明「門開了」，這一段才進屋看燈。
    B 欄（許可證）由 OCR 判讀而來，完全不經過名冊；E~G 才是查名冊補的。
    """

    PERMIT = "2340"

    @pytest.mark.parametrize("status", list(pipeline.SheetStatus))
    def test_row_keeps_permit_and_blanks_agency_cols(self, roster_unavailable, status):
        row = pipeline._row_to_sheet_values(
            pipeline.SheetRow("32417.docx", self.PERMIT, status, "多數票"), {})
        assert row[1] == self.PERMIT          # B 欄：許可證照常有值
        assert row[4:7] == ["", "", ""]       # E~G：機構名稱／地址／電話留空

    def test_agency_cols_are_blank_when_roster_unavailable(self, roster_unavailable):
        """E~G 的降級路徑本身：查不到就是三個空字串，不是拋錯。"""
        assert pipeline._agency_cols(self.PERMIT) == ["", "", ""]


@pytest.mark.usefixtures("gchecks")
class TestInputStillGates:
    """真正該擋的仍然擋 —— 放行名冊不等於放行一切。"""


    def test_check_input_fails_when_input_dir_missing(self, tmp_path):
        result = pipeline._check_input(Batch.from_dir(tmp_path / "不存在"))
        assert not result.ok
        assert "不存在" in result.detail

    def test_check_input_fails_when_no_docx(self, empty_dir):
        result = pipeline._check_input(Batch.from_dir(empty_dir))
        assert not result.ok
        assert ".docx" in result.detail

    def test_check_input_ignores_word_lock_files(self, empty_dir):
        """`~$` 開頭的 Word 鎖定檔不算「有東西可處理」。"""
        (empty_dir / "~$32417.docx").write_bytes(b"lock")
        assert not pipeline._check_input(Batch.from_dir(empty_dir)).ok

    def test_check_input_counts_the_dir_it_reports(self, docx_dir, roster_unavailable):
        """回報的份數來自實際掃過的那個資料夾。

        報出另一個資料夾的份數時，兩個數字都「看起來很合理」，沒人會發現。
        所以這裡釘的是份數，不只是通過與否。
        """
        (docx_dir / "32508.docx").write_bytes(b"placeholder")
        result = pipeline._check_input(Batch.from_dir(docx_dir))
        assert result.ok
        assert "2 份" in result.detail

    def test_run_preflight_fails_when_input_dir_is_empty(
        self, empty_dir, roster_unavailable, google_checks_pass
    ):
        assert pipeline.run_preflight(Batch.from_dir(empty_dir)) is False

    def test_run_preflight_fails_when_single_file_is_missing(
        self, capsys, tmp_path, roster_unavailable, google_checks_pass
    ):
        """`--file` 指到不存在的檔案：由 preflight 的輸入閘門擋下，附修復方式。

        以前 --file 時 preflight 不看輸入，Google 檢查全跑完才由 main 說找不到。
        """
        missing = tmp_path / "32417.docx"
        assert pipeline.run_preflight(Batch.from_file(missing)) is False
        text = capsys.readouterr().out
        assert "找不到檔案" in text
        assert "--file" in text           # 修復方式指向 --file，不是叫人去建 ./docs


class TestOneRootCauseOneLine:
    """一個根因印一行，不是五個 ✘(#4)。

    憑證檢查有順序相依：ADC 不可用就談不上換權杖，換不到權杖就談不上 Sheets。
    下游那幾項是「略過」，不是失敗 —— 算成失敗的話，「preflight 未通過（5 項）」
    會把真正的根因埋在一串衍生錯誤裡。

    替身只放在外部邊界：共用套件的 check_adc／check_token、Tesseract、Sheets。
    """

    @pytest.fixture
    def checks(self, roster_unavailable, google_checks_pass):
        """預設全通過；各測試只把要壞掉的那一項換掉。"""
        return google_checks_pass

    @staticmethod
    def _must_not_run(*a, **kw):
        raise AssertionError("上游已失敗，這一項不該真的去打 Google")

    def test_adc_failure_is_the_only_failure(self, capsys, monkeypatch, checks, docx_dir):
        monkeypatch.setattr(checks, "check_adc", lambda: (
            checks.CheckResult("Google 認證(ADC)", False, "找不到 ADC", "重跑 重新登入.bat"),
            None))
        monkeypatch.setattr(checks, "check_token", self._must_not_run)
        monkeypatch.setattr(pipeline, "_check_sheets", self._must_not_run)

        assert pipeline.run_preflight(Batch.from_dir(docx_dir)) is False
        out = capsys.readouterr().out
        assert "你可以自己處理（1 項）" in out
        assert "管理者處理" not in out
        assert out.count("⏭") == 2            # 換權杖、Sheets 標為略過

    def test_token_failure_skips_sheets(self, capsys, monkeypatch, checks, docx_dir):
        monkeypatch.setattr(checks, "check_token", lambda ident: checks.CheckResult(
            "模擬服務帳號", False, "PERMISSION_DENIED", "請管理者授予 TokenCreator",
            blame=checks.ADMIN))
        monkeypatch.setattr(pipeline, "_check_sheets", self._must_not_run)

        assert pipeline.run_preflight(Batch.from_dir(docx_dir)) is False
        out = capsys.readouterr().out
        # blame 原樣保留：需要管理者處理的，單獨列在管理者那一區
        assert "需要 GCP 專案管理者處理（1 項）" in out
        assert "你可以自己處理" not in out
        assert out.count("⏭") == 1

    def test_all_pass(self, capsys, checks, docx_dir):
        assert pipeline.run_preflight(Batch.from_dir(docx_dir)) is True
        assert "⏭" not in capsys.readouterr().out
