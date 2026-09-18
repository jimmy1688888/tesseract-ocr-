# -*- coding: utf-8 -*-
"""preflight 的閘門語意：什麼該擋下整批、什麼只該留空。

這支檔案存在的理由是一個 bug：仲介名冊載入失敗本來被寫成 preflight 的失敗項，
於是 run_preflight 回傳 False、main 直接 return —— 整批不跑。而
`_permit_lookup` 的註解、preflight 自己印出來的那句「不影響許可證判讀」、
以及名冊查表本身的降級路徑（`_agency_cols` 查無 → 三個空字串）三處都承諾
「留空但不中斷」。承諾與行為互相矛盾，而矛盾的那一側使用者看不到。

所以這裡釘的不是實作細節，是那句承諾：**名冊是降級來源，不是閘門。**
"""
import logging

import pytest

import pipeline


@pytest.fixture
def docx_dir(tmp_path):
    """一個有東西可處理的輸入資料夾。

    內容不必是真的 docx —— 本檔測的是 preflight 要不要放行，不是解壓。
    """
    (tmp_path / "32417.docx").write_bytes(b"placeholder")
    return tmp_path


@pytest.fixture
def roster_unavailable(monkeypatch):
    """名冊載不起來的狀態。

    `False` 是 `_permit_lookup` 的降級標記（載入失敗後不再每列重試），
    它會讓 `_permit_lookup()` 回傳 None。直接設這個標記而不是去弄壞資料檔，
    測的才是「名冊不可用時怎麼辦」而不是「資料檔長怎樣」。
    """
    monkeypatch.setattr(pipeline, "_PERMIT_LOOKUP", False)


@pytest.fixture
def google_checks_pass(monkeypatch):
    """把打到 Google 的那幾項換成通過，只留下本檔關心的輸入／名冊。"""
    def ok(name):
        return pipeline.CheckResult(name, True, "（測試替身）")

    monkeypatch.setattr(pipeline, "_check_tesseract", lambda: ok("Tesseract 執行檔"))
    monkeypatch.setattr(pipeline, "_check_adc", lambda: (ok("ADC 憑證"), object()))
    monkeypatch.setattr(pipeline, "_check_impersonation", lambda: ok("模擬身分"))
    monkeypatch.setattr(pipeline, "_check_token", lambda: ok("取得權杖"))
    monkeypatch.setattr(pipeline, "_check_vision_client", lambda: ok("Vision client"))
    monkeypatch.setattr(pipeline, "_check_sheets", lambda: ok("Google Sheets 存取"))


class TestRosterIsNotAGate:
    """名冊載不起來 → E~G 留空，整批照跑。"""

    def test_check_dirs_passes_when_only_roster_unavailable(
        self, docx_dir, roster_unavailable
    ):
        assert pipeline._check_dirs(docx_dir).ok

    def test_run_preflight_passes_when_only_roster_unavailable(
        self, monkeypatch, docx_dir, roster_unavailable, google_checks_pass
    ):
        monkeypatch.setattr(pipeline, "INPUT_DIR", docx_dir)
        # run_preflight 回傳 True 就是「main 不會提早 return」——
        # main 對 preflight 的唯一反應是 `if not run_preflight(...): return`，
        # 所以這一行釘住的就是「整批照跑」。
        assert pipeline.run_preflight(require_input=True) is True

    def test_roster_failure_is_still_said_out_loud(
        self, monkeypatch, caplog, docx_dir, roster_unavailable, google_checks_pass
    ):
        """降級不等於沉默：不擋人，但要讓人知道 E~G 會是空的。"""
        monkeypatch.setattr(pipeline, "INPUT_DIR", docx_dir)
        with caplog.at_level(logging.WARNING, logger=pipeline.logger.name):
            pipeline.run_preflight(require_input=True)
        assert any("名冊" in r.message for r in caplog.records)

    def test_roster_failure_is_not_counted_as_a_failed_check(
        self, monkeypatch, caplog, docx_dir, roster_unavailable, google_checks_pass
    ):
        """一個降級不該印成「preflight 未通過（N 項）」。"""
        monkeypatch.setattr(pipeline, "INPUT_DIR", docx_dir)
        with caplog.at_level(logging.ERROR, logger=pipeline.logger.name):
            pipeline.run_preflight(require_input=True)
        assert not [r for r in caplog.records if r.levelno >= logging.ERROR]

    def test_agency_cols_are_blank_when_roster_unavailable(self, roster_unavailable):
        """E~G 的降級路徑：查不到就是三個空字串，不是拋錯。"""
        assert pipeline._agency_cols("1234") == ["", "", ""]


class TestInputStillGates:
    """真正該擋的仍然擋 —— 放行名冊不等於放行一切。"""

    def test_check_dirs_fails_when_input_dir_missing(self, tmp_path):
        missing = tmp_path / "不存在"
        result = pipeline._check_dirs(missing)
        assert not result.ok
        assert "不存在" in result.detail

    def test_check_dirs_fails_when_no_docx(self, tmp_path):
        result = pipeline._check_dirs(tmp_path)
        assert not result.ok
        assert ".docx" in result.detail

    def test_check_dirs_ignores_word_lock_files(self, tmp_path, roster_unavailable):
        """`~$` 開頭的 Word 鎖定檔不算「有東西可處理」。"""
        (tmp_path / "~$32417.docx").write_bytes(b"lock")
        assert not pipeline._check_dirs(tmp_path).ok

    def test_run_preflight_fails_when_input_dir_is_empty(
        self, monkeypatch, tmp_path, roster_unavailable, google_checks_pass
    ):
        monkeypatch.setattr(pipeline, "INPUT_DIR", tmp_path)
        assert pipeline.run_preflight(require_input=True) is False
