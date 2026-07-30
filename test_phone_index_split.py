# -*- coding: utf-8 -*-
"""名冊「電話」欄一格塞多支號碼／分機時的索引拆分。

官方名冊有 24 筆(其中有效機構 12 筆)把兩支號碼或分機擠在同一格,
如 '03-3253456/3253507'、'(03)-3554436#171'、'02-29819295*13'。
整欄去非數字會串成一長串('0332534563253507'),精確反查必然落空,只有主號恰為
前綴時才靠前綴反查勉強救回,第二支號碼則完全查不到。更糟的是那支號碼常另屬他家
(2515 的主號 03-3554436 也是 1344 的登記電話),反查會「唯一命中」到別家、
填出看似高信心的錯誤許可證。

拆分後:每支號碼各建一把 key,撞號的情況自然變成「同號多家」,
由呼叫端判定模糊、列出供人工擇一 —— 寧可交人工,不要錯填。
"""
import pytest

from permit_lookup import PHONE_MIN_LEN, build_phone_index, split_phone_field


class TestSplitPhoneField:

    # ---- 單一號碼:行為與拆分前完全一致 ----
    @pytest.mark.parametrize("raw,expect", [
        ("02-27276999", ["0227276999"]),
        ("(04)23263526", ["0423263526"]),
        ("073110421", ["073110421"]),
        ("02-8773-8880", ["0287738880"]),          # 雙連字號
        ("886-933-921201", ["0933921201"]),        # 國際格式仍還原前導 0
    ])
    def test_single_number_unchanged(self, raw, expect):
        assert split_phone_field(raw) == expect

    # ---- 分機:切掉,只留主號 ----
    @pytest.mark.parametrize("raw,expect", [
        ("(03)-3554436#171", ["033554436"]),
        ("02-29819295*13", ["0229819295"]),        # 少數用 * 接分機
        ("(02)8770-5181-#7101", ["0287705181"]),   # 分機前多一個連字號
        ("02-27965959#6134", ["0227965959"]),
    ])
    def test_extension_dropped(self, raw, expect):
        assert split_phone_field(raw) == expect

    # ---- 兩支號碼:各建一把 key ----
    @pytest.mark.parametrize("raw,expect", [
        ("072114387、0925510286", ["072114387", "0925510286"]),
        ("034588687/0911894441", ["034588687", "0911894441"]),
        # '.' 分隔:第二支 3907750 缺區碼、不足 8 碼 → 丟棄
        ("07-3922319.3907750", ["073922319"]),
    ])
    def test_two_numbers_split(self, raw, expect):
        assert split_phone_field(raw) == expect

    def test_second_number_without_area_code_dropped(self):
        """缺區碼的第二支(3253507)太短 → 丟棄,不留只能靠完全相等命中的殘段。"""
        assert split_phone_field("03-3253456/3253507") == ["033253456"]

    # ---- 邊界 ----
    def test_empty_and_none(self):
        assert split_phone_field("") == []
        assert split_phone_field(None) == []

    def test_too_short_dropped(self):
        assert split_phone_field("12345") == []

    def test_min_len_boundary(self):
        assert len(split_phone_field("0" * PHONE_MIN_LEN)) == 1
        assert split_phone_field("0" * (PHONE_MIN_LEN - 1)) == []

    def test_duplicates_deduped_keeping_order(self):
        assert split_phone_field("02-27276999/0227276999") == ["0227276999"]


def _rec(permit, phone, revoked="", closed=""):
    return {"許可證": permit, "機構名稱": f"機構{permit}", "機構地址": "地址",
            "電話": phone, "廢止許可日期": revoked, "終止營業日期": closed}


class TestBuildPhoneIndex:

    def test_one_record_can_own_two_keys(self):
        idx = build_phone_index([_rec("3647", "034588687/0911894441")])
        assert set(idx) == {"034588687", "0911894441"}
        assert [a["許可證"] for a in idx["0911894441"]] == ["3647"]

    def test_shared_number_becomes_ambiguous_not_wrong(self):
        """2515/1344 實況:拆出主號後與他家撞號 → 兩家並列,呼叫端才判得出模糊。

        拆分前 2515 的 key 是 '033554436171',查 033554436 會「唯一命中」1344,
        錯填別家的許可證;拆分後變成 2 家,走人工擇一。
        """
        idx = build_phone_index([_rec("1344", "03-3554436"),
                                 _rec("2515", "(03)-3554436#171")])
        assert {a["許可證"] for a in idx["033554436"]} == {"1344", "2515"}

    def test_inactive_records_still_excluded(self):
        idx = build_phone_index([_rec("0671", "04-7785333/7516792", closed="20200101"),
                                 _rec("2108", "037-616161#11", revoked="20200101")])
        assert idx == {}

    def test_no_key_for_blank_phone(self):
        assert build_phone_index([_rec("1965-1", "")]) == {}
