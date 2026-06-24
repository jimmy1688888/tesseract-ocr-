# -*- coding: utf-8 -*-
"""aggregate_small_docx 行為測試。

small docx 走不同的決策路徑(不經 decide_result):
  1. 整份 docx 所有圖片都沒命中 → 全列標 SMALL_NO_HIT + 送人工審查
  2. 至少一張命中 → 選**信心最高者**作 winner,所有命中列都標 vision_review=True

語意上 small 沒有 cross_match 機制可確認,所以即使有 winner 也必須 Vision 二次確認。
"""

import pytest

from pipeline import (
    aggregate_small_docx,
    _empty_result,
    ScanResult,
    ResultStatus,
)


def make_small_row(*, mol: str = "", id: str = "",
                   mol_conf: float = 0.0, id_conf: float = 0.0,
                   image_name: str = "x.jpeg") -> ScanResult:
    r = _empty_result("small.docx", image_name, "small")
    r.mol = mol
    r.id = id
    r.mol_conf = mol_conf
    r.id_conf = id_conf
    return r


class TestAllNoHits:
    """全部圖片都沒命中時,所有列被標為 SMALL_NO_HIT 且要送人工審查。"""

    def test_all_no_hits_marks_status(self):
        rows = [
            make_small_row(image_name="1.jpeg"),
            make_small_row(image_name="2.jpeg"),
            make_small_row(image_name="3.jpeg"),
        ]
        result = aggregate_small_docx(rows)
        assert len(result) == 3
        for r in result:
            assert r.status == ResultStatus.SMALL_NO_HIT
            assert r.vision_review is True
            assert r.note == "small:無命中"

    def test_all_no_hits_returns_all_rows(self):
        """全無命中時應該回傳所有列(不是只回傳 hits)"""
        rows = [make_small_row(image_name=f"{i}.jpeg") for i in range(5)]
        assert len(aggregate_small_docx(rows)) == 5


class TestWithHits:
    """至少一張命中時的 winner 選擇邏輯:**信心最高者勝**。"""

    def test_single_hit_becomes_winner(self):
        """只有一張命中時,該張就是 winner"""
        rows = [
            make_small_row(image_name="1.jpeg"),  # 無命中
            make_small_row(mol="1234", mol_conf=80, image_name="2.jpeg"),
        ]
        result = aggregate_small_docx(rows)
        # 只回傳 hits
        assert len(result) == 1
        assert result[0].final_value == "1234"
        assert result[0].final_conf == 80.0

    def test_multiple_hits_highest_conf_wins(self):
        """多張命中時,以 max(mol_conf, id_conf) 最高者為 winner"""
        rows = [
            make_small_row(mol="1111", mol_conf=60, image_name="1.jpeg"),
            make_small_row(mol="2222", mol_conf=85, image_name="2.jpeg"),  # winner
            make_small_row(mol="3333", mol_conf=70, image_name="3.jpeg"),
        ]
        result = aggregate_small_docx(rows)
        assert len(result) == 3
        # 所有 hit 列的 final_value 都被覆寫成 winner 值
        for r in result:
            assert r.final_value == "2222"
            assert r.final_conf == 85.0

    def test_id_field_eligible_as_winner_source(self):
        """winner 可由 id 提供(不限定 mol)"""
        rows = [
            make_small_row(id="9999", id_conf=90, image_name="1.jpeg"),
        ]
        result = aggregate_small_docx(rows)
        assert len(result) == 1
        assert result[0].final_value == "9999"
        assert result[0].final_conf == 90.0

    def test_best_conf_uses_max_of_mol_and_id(self):
        """同一列若 mol 與 id 都有值,以兩者較高的 conf 排序"""
        rows = [
            # row1: mol_conf=70, id_conf=50 → best=70
            make_small_row(mol="1111", mol_conf=70, id="1111", id_conf=50,
                           image_name="1.jpeg"),
            # row2: mol_conf=60, id_conf=85 → best=85 (winner)
            make_small_row(mol="2222", mol_conf=60, id="2222", id_conf=85,
                           image_name="2.jpeg"),
        ]
        result = aggregate_small_docx(rows)
        # row2 應為 winner
        for r in result:
            assert r.final_value == "2222"

    def test_all_hits_marked_for_vision_review(self):
        """有 winner 也要 vision_review=True(small 沒有 cross_match 機制)"""
        rows = [
            make_small_row(mol="1234", mol_conf=99, image_name="1.jpeg"),
        ]
        result = aggregate_small_docx(rows)
        assert result[0].vision_review is True

    def test_non_hit_rows_not_returned_when_some_hit(self):
        """有命中時,沒命中的列不會被回傳"""
        rows = [
            make_small_row(image_name="miss1.jpeg"),
            make_small_row(mol="1234", mol_conf=80, image_name="hit.jpeg"),
            make_small_row(image_name="miss2.jpeg"),
        ]
        result = aggregate_small_docx(rows)
        assert len(result) == 1
        assert result[0].image_name == "hit.jpeg"
