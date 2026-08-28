# -*- coding: utf-8 -*-
"""名冊補充檔(data/custom_agencies.json)。

官方名冊每次重新下載會**整份覆蓋**,直接手改 agency_roster.json 的列會靜靜消失
——而且是下次跑才發現那家又查不到。故比照 address_db 的 custom_roads.json,
把自訂資料放獨立檔、進版控、跨機同步。
"""
import json

import pytest

import permit_lookup as pl

_OFFICIAL = [
    {"許可證": "0001", "機構名稱": "官方甲", "機構地址": "臺北市A路1號",
     "電話": "02-11111111", "終止營業日期": "", "廢止許可日期": ""},
    {"許可證": "2340-2", "機構名稱": "官方乙分公司", "機構地址": "臺中市B路2號",
     "電話": "04-22222222", "終止營業日期": "", "廢止許可日期": ""},
]


def _write(tmp_path, data):
    p = tmp_path / "custom_agencies.json"
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return p


class TestMerge:

    def test_new_agency_is_appended(self, tmp_path):
        p = _write(tmp_path, [{"許可證": "9001", "機構名稱": "自訂丙",
                               "機構地址": "高雄市C路3號", "電話": "07-33333333"}])
        out = pl.merge_custom_agencies(_OFFICIAL, p)
        assert [r["許可證"] for r in out] == ["0001", "2340-2", "9001"]

    def test_custom_wins_on_same_permit(self, tmp_path):
        """會手動加一筆,正是因為官方那筆不合用(名稱/電話已變更而名冊未更新)。"""
        p = _write(tmp_path, [{"許可證": "0001", "機構名稱": "自訂覆蓋",
                               "機構地址": "臺北市A路1號", "電話": "02-99999999"}])
        out = pl.merge_custom_agencies(_OFFICIAL, p)
        assert len(out) == 2
        assert [r["機構名稱"] for r in out] == ["官方乙分公司", "自訂覆蓋"]

    def test_permit_comparison_is_normalised(self, tmp_path):
        """自訂寫 "1" 也要蓋得掉官方的 "0001"——前導零是名冊的寫法,不是識別。"""
        p = _write(tmp_path, [{"許可證": "1", "機構名稱": "自訂覆蓋",
                               "機構地址": "x", "電話": "02-99999999"}])
        out = pl.merge_custom_agencies(_OFFICIAL, p)
        assert [r["機構名稱"] for r in out] == ["官方乙分公司", "自訂覆蓋"]

    def test_branch_suffix_does_not_collide_with_parent(self, tmp_path):
        """自訂 2340 是母公司,不該蓋掉官方的 2340-2 分公司。"""
        p = _write(tmp_path, [{"許可證": "2340", "機構名稱": "自訂母公司",
                               "機構地址": "x", "電話": "04-99999999"}])
        out = pl.merge_custom_agencies(_OFFICIAL, p)
        assert len(out) == 3
        assert [r["許可證"] for r in out] == ["0001", "2340-2", "2340"]


class TestDefaultsAndFailures:

    def test_missing_status_fields_mean_active(self, tmp_path):
        """省略終止/廢止日期即為有效——反查只採用有效的機構,這是最容易踩的坑。"""
        p = _write(tmp_path, [{"許可證": "9001", "機構名稱": "自訂丙",
                               "機構地址": "高雄市C路3號", "電話": "07-33333333"}])
        out = pl.merge_custom_agencies(_OFFICIAL, p)
        assert pl._is_active(out[-1])

    def test_explicitly_terminated_custom_entry_is_inactive(self, tmp_path):
        p = _write(tmp_path, [{"許可證": "9001", "機構名稱": "自訂丙",
                               "機構地址": "x", "電話": "07-33333333",
                               "終止營業日期": "20250101"}])
        out = pl.merge_custom_agencies(_OFFICIAL, p)
        assert not pl._is_active(out[-1])

    @pytest.mark.parametrize("content", ['{"不是": "清單"}', "壞掉的 json", "[]"])
    def test_bad_or_empty_file_leaves_roster_untouched(self, tmp_path, content):
        """補充檔是可有可無的補丁,不該讓它擋掉整份官方名冊。"""
        p = tmp_path / "custom_agencies.json"
        p.write_text(content, encoding="utf-8")
        assert pl.merge_custom_agencies(_OFFICIAL, p) == _OFFICIAL

    def test_missing_file_leaves_roster_untouched(self, tmp_path):
        assert pl.merge_custom_agencies(
            _OFFICIAL, tmp_path / "不存在.json") == _OFFICIAL

    def test_input_list_is_not_mutated(self, tmp_path):
        p = _write(tmp_path, [{"許可證": "0001", "機構名稱": "自訂覆蓋",
                               "機構地址": "x", "電話": "02-99999999"}])
        before = json.dumps(_OFFICIAL, ensure_ascii=False)
        pl.merge_custom_agencies(_OFFICIAL, p)
        assert json.dumps(_OFFICIAL, ensure_ascii=False) == before


class TestReachesLookup:
    """合併後的資料要真的能被反查用到,不是只躺在清單裡。"""

    def test_custom_agency_is_indexed_and_looked_up(self, tmp_path):
        p = _write(tmp_path, [{"許可證": "9001", "機構名稱": "自訂丙",
                               "機構地址": "高雄市C路3號", "電話": "07-33333333"}])
        lookup = pl.PermitLookup(pl.merge_custom_agencies(_OFFICIAL, p))
        assert lookup.lookup("9001")["機構名稱"] == "自訂丙"

    def test_custom_agency_is_reachable_by_phone(self, tmp_path):
        """電話反查救援也要吃得到——那是全無命中件唯一的退路。"""
        p = _write(tmp_path, [{"許可證": "9001", "機構名稱": "自訂丙",
                               "機構地址": "高雄市C路3號", "電話": "07-33333333"}])
        lookup = pl.PermitLookup(pl.merge_custom_agencies(_OFFICIAL, p))
        assert lookup.lookup_by_phone("07-33333333")["許可證"] == "9001"


class TestShippedFileIsValid:
    """版控裡那份 data/custom_agencies.json 必須永遠是合法的空清單或記錄清單。

    它會被每一次 fetch_roster() 讀到,格式壞掉雖然會被安靜跳過,但那等於補充
    功能默默失效——沒有人會發現。
    """

    def test_file_parses_as_a_list(self):
        data = json.loads(pl.CUSTOM_ROSTER_PATH.read_text(encoding="utf-8"))
        assert isinstance(data, list)

    def test_every_entry_has_the_required_fields(self):
        data = json.loads(pl.CUSTOM_ROSTER_PATH.read_text(encoding="utf-8"))
        for e in data:
            for k in ("許可證", "機構名稱", "機構地址", "電話"):
                assert e.get(k), f"{e.get('許可證')!r} 缺 {k}"
            assert pl._parse_permit(str(e["許可證"])), f"許可證格式無法解析:{e!r}"
