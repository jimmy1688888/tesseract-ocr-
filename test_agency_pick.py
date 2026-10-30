# -*- coding: utf-8 -*-
"""反查救援:次頁台仲區塊 → 名冊挑一家(#13)。

四級優先序(CONTEXT.md「反查救援」):電話精確唯一 → 電話前綴唯一 → 名稱／地址相似度
取高者 → 電話多家列出供人工擇一。以前這條決策梯寫在救援迴圈裡、結果靠就地改寫列表達,
只能用真 docx + 真 Vision 測;現在挑一家是純函式,餵台仲區塊與名冊就能測。

名冊與台仲區塊全部捏造。
"""
import pytest

from pipeline import AgencyPick, pick_agency, rescue_manual_review_via_agency_phone
from sheet_row import SheetRow, SheetStatus


def _agency(permit, name):
    return {"許可證": permit, "機構名稱": f"{name}仲介", "機構地址": f"{name}地址",
            "電話": f"02{permit * 2}"}


AGENCIES = {p: _agency(p, n) for p, n in
            [("1111", "甲"), ("2222", "乙"), ("3333", "丙"), ("4444", "丁"), ("5555", "戊")]}


class Roster:
    """名冊替身:每種查法查什麼回什麼,由各測試指定。"""

    def __init__(self, phone=None, prefix=None, name=None, address=None):
        self.phone, self.prefix = phone or {}, prefix or {}
        self.name, self.address = name or {}, address or {}

    def agencies_by_phone(self, ph):
        return [AGENCIES[p] for p in self.phone.get(ph, [])]

    def agencies_by_phone_prefix(self, ph):
        return [AGENCIES[p] for p in self.prefix.get(ph, [])]

    @staticmethod
    def _best(table, key):
        hit = table.get(key)
        return {**AGENCIES[hit[0]], "_ratio": hit[1]} if hit else None

    def best_by_name(self, name):
        return self._best(self.name, name)

    def best_by_address(self, address):
        return self._best(self.address, address)


def _block(phones=(), name="", address=""):
    return {"phones": list(phones), "name": name, "address": address}


class TestPhoneExact:
    """① 電話精確唯一命中:最可靠。"""

    def test_unique_phone_hit(self):
        pick = pick_agency(_block(["0211111111"]), Roster(phone={"0211111111": ["1111"]}))
        assert pick == AgencyPick(
            "1111", "[電話反查救回] 次頁台仲電話 0211111111 → 許可證 1111／甲仲介(仍請人工核對)")

    def test_first_phone_unknown_second_unique(self):
        pick = pick_agency(_block(["0299999999", "0211111111"]),
                           Roster(phone={"0211111111": ["1111"]}))
        assert pick.permit == "1111"

    def test_later_unique_beats_earlier_ambiguous(self):
        pick = pick_agency(_block(["0244444444", "0211111111"]),
                           Roster(phone={"0244444444": ["4444", "5555"], "0211111111": ["1111"]}))
        assert pick.permit == "1111"

    def test_exact_beats_prefix_and_name(self):
        pick = pick_agency(_block(["0211111111"], name="丙仲介"),
                           Roster(phone={"0211111111": ["1111"]},
                                  prefix={"0211111111": ["2222"]},
                                  name={"丙仲介": ("3333", 0.99)}))
        assert pick.permit == "1111"


class TestPhonePrefix:
    """② 電話前綴唯一命中(OCR 少碼):比通用的名稱可靠,排在名稱前。"""

    def test_unique_prefix_hit(self):
        pick = pick_agency(_block(["022222222"]), Roster(prefix={"022222222": ["2222"]}))
        assert pick == AgencyPick(
            "2222", "[電話前綴反查救回·電話OCR可能少碼] 次頁台仲電話 022222222 "
                    "前綴唯一命中 → 許可證 2222／乙仲介(仍請人工核對)")

    def test_prefix_beats_name(self):
        pick = pick_agency(_block(["022222222"], name="丙仲介"),
                           Roster(prefix={"022222222": ["2222"]}, name={"丙仲介": ("3333", 0.99)}))
        assert pick.permit == "2222"

    def test_ambiguous_prefix_is_skipped(self):
        pick = pick_agency(_block(["0222"]), Roster(prefix={"0222": ["2222", "3333"]}))
        assert pick == AgencyPick()


class TestNameOrAddress:
    """③ 電話救不回:名稱、地址各比名冊相似度,取高者。"""

    def test_name(self):
        pick = pick_agency(_block(name="丙仲介"), Roster(name={"丙仲介": ("3333", 0.9)}))
        assert pick == AgencyPick(
            "3333", "[名稱反查救回·電話無唯一命中] 次頁台仲名稱 → 許可證 3333／丙仲介"
                    "(名冊相似度 0.9,仍請人工核對)")

    def test_address(self):
        pick = pick_agency(_block(address="丙地址"), Roster(address={"丙地址": ("3333", 0.8)}))
        assert pick.permit == "3333"
        assert pick.note.startswith("[地址反查救回·電話無唯一命中] 次頁台仲地址 →")

    def test_higher_ratio_wins(self):
        roster = Roster(name={"甲仲介": ("1111", 0.8)}, address={"丙地址": ("3333", 0.9)})
        assert pick_agency(_block(name="甲仲介", address="丙地址"), roster).permit == "3333"
        roster = Roster(name={"甲仲介": ("1111", 0.9)}, address={"丙地址": ("3333", 0.8)})
        assert pick_agency(_block(name="甲仲介", address="丙地址"), roster).permit == "1111"

    def test_name_beats_ambiguous_phone(self):
        pick = pick_agency(_block(["0244444444"], name="丙仲介"),
                           Roster(phone={"0244444444": ["4444", "5555"]},
                                  name={"丙仲介": ("3333", 0.9)}))
        assert pick.permit == "3333"


class TestAmbiguousPhone:
    """④ 電話多家,前綴/名稱/地址都救不回:不填許可證,列出各家供人工擇一。"""

    def test_lists_every_agency(self):
        pick = pick_agency(_block(["0244444444"]), Roster(phone={"0244444444": ["4444", "5555"]}))
        assert pick == AgencyPick(
            "", "[電話反查·需人工擇一] 台仲電話 0244444444 命中2 家:4444／丁仲介、5555／戊仲介")

    def test_first_ambiguous_phone_is_listed(self):
        pick = pick_agency(_block(["0244444444", "0255555555"]),
                           Roster(phone={"0244444444": ["4444", "5555"],
                                         "0255555555": ["1111", "2222"]}))
        assert "0244444444" in pick.note and "0255555555" not in pick.note


class TestNothing:
    """救不回:沒有許可證、也沒有要接在理由後面的話。"""

    def test_empty_block(self):
        assert pick_agency(_block(), Roster()) == AgencyPick()

    def test_nothing_in_roster(self):
        assert pick_agency(_block(["0299999999"], name="查無", address="查無"), Roster()) == AgencyPick()


NOT_DONE = "；反查救援未完成:次頁 Vision 呼叫失敗"


class TestRescueReturnsNewRows:
    """救援:列進、列出,不改傳入的列。"""

    @staticmethod
    def _run(monkeypatch, tmp_path, blocks, seeds, with_reads=False):
        import employer_extract
        import google_clients
        import pipeline
        from batch import Batch

        for s in seeds:
            (tmp_path / s.source_docx).write_bytes(b"x")
        roster = Roster(phone={"0211111111": ["1111"], "0244444444": ["4444", "5555"]})
        monkeypatch.setattr(google_clients, "vision_client", lambda: object())
        reads = []

        def next_page(path, vision):
            name = path.replace("\\", "/").rsplit("/", 1)[-1]
            reads.append(name)
            if isinstance(blocks[name], Exception):
                raise blocks[name]
            return blocks[name]
        monkeypatch.setattr(employer_extract, "agency_block_from_next_page", next_page)
        got = rescue_manual_review_via_agency_phone(Batch.from_dir(tmp_path), seeds, roster)
        return (got, reads) if with_reads else got

    def test_rescued_and_not_rescued(self, monkeypatch, tmp_path):
        seeds = [SheetRow("a.docx", "", SheetStatus.MANUAL_REVIEW, "large:全無命中"),
                 SheetRow("b.docx", "9999", SheetStatus.MANUAL_REVIEW, "單一判讀低信心")]
        before = list(seeds)
        got = self._run(monkeypatch, tmp_path,
                        {"a.docx": _block(["0211111111"]), "b.docx": _block()}, seeds)

        assert seeds == before                                   # 傳入的列沒動
        assert got[0] == SheetRow(
            "a.docx", "1111", SheetStatus.MANUAL_REVIEW,
            "large:全無命中；[電話反查救回] 次頁台仲電話 0211111111 → 許可證 1111／甲仲介(仍請人工核對)")
        assert got[1] is seeds[1]                                # 救不回:原列、種子值留著

    def test_ambiguous_keeps_the_seed_value(self, monkeypatch, tmp_path):
        """單一判讀低信心的種子列 B 欄是候選值:電話多家要人工擇一時,候選值留著、理由接上各家。"""
        seed = SheetRow("c.docx", "9999", SheetStatus.MANUAL_REVIEW, "單一判讀低信心")
        [got] = self._run(monkeypatch, tmp_path, {"c.docx": _block(["0244444444"])}, [seed])

        assert got.permit == "9999"
        assert got.reason == ("單一判讀低信心；[電話反查·需人工擇一] 台仲電話 0244444444 "
                              "命中2 家:4444／丁仲介、5555／戊仲介")


class TestNextPageVisionCallFailed:
    """沒救 ≠ 救不回(#67、ADR-0015):次頁沒問到要寫在 D 欄;真的救不回仍不註明。"""

    _run = staticmethod(TestRescueReturnsNewRows._run)

    def test_states_it_on_the_row_and_keeps_the_seed(self, monkeypatch, tmp_path):
        from vision_reader import VisionCallFailed
        seeds = [SheetRow("a.docx", "", SheetStatus.MANUAL_REVIEW, "large:全無命中"),
                 SheetRow("b.docx", "9999", SheetStatus.MANUAL_REVIEW, "單一判讀低信心")]
        got = self._run(monkeypatch, tmp_path, {"a.docx": VisionCallFailed("503"),
                                                "b.docx": VisionCallFailed("503")}, seeds)
        assert got == [SheetRow("a.docx", "", SheetStatus.MANUAL_REVIEW, "large:全無命中" + NOT_DONE),
                       SheetRow("b.docx", "9999", SheetStatus.MANUAL_REVIEW, "單一判讀低信心" + NOT_DONE)]

    def test_nothing_found_is_still_not_stated(self, monkeypatch, tmp_path):
        seed = SheetRow("a.docx", "", SheetStatus.MANUAL_REVIEW, "large:全無命中")
        [got] = self._run(monkeypatch, tmp_path, {"a.docx": _block()}, [seed])
        assert got is seed

    def test_one_docx_is_read_once_and_every_row_is_stated(self, monkeypatch, tmp_path):
        from vision_reader import VisionCallFailed
        seeds = [SheetRow("a.docx", "", SheetStatus.MANUAL_REVIEW, "x"),
                 SheetRow("a.docx", "", SheetStatus.MANUAL_REVIEW, "y")]
        got, reads = self._run(monkeypatch, tmp_path, {"a.docx": VisionCallFailed("503")},
                               seeds, with_reads=True)
        assert reads == ["a.docx"]
        assert [r.reason for r in got] == ["x" + NOT_DONE, "y" + NOT_DONE]

    def test_others_are_still_rescued(self, monkeypatch, tmp_path):
        from vision_reader import VisionCallFailed
        seeds = [SheetRow("a.docx", "", SheetStatus.MANUAL_REVIEW, "large:全無命中"),
                 SheetRow("b.docx", "", SheetStatus.MANUAL_REVIEW, "large:全無命中")]
        got = self._run(monkeypatch, tmp_path, {"a.docx": VisionCallFailed("503"),
                                                "b.docx": _block(["0211111111"])}, seeds)
        assert got[0].reason.endswith(NOT_DONE)
        assert got[1].permit == "1111"

    def test_identity_refused_aborts_the_run(self, monkeypatch, tmp_path):
        from google.api_core.exceptions import PermissionDenied
        seed = SheetRow("a.docx", "", SheetStatus.MANUAL_REVIEW, "large:全無命中")
        with pytest.raises(PermissionDenied):
            self._run(monkeypatch, tmp_path, {"a.docx": PermissionDenied("403")}, [seed])
