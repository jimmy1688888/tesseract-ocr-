# -*- coding: utf-8 -*-
"""多數票:幾組 Tesseract 設定各讀一次,同一個值至少兩票才算數(CONTEXT.md「多數票」)。

挑贏家與「得票平均信心」是同一件事:以前三個呼叫端各自挑完贏家、再各寫一次
round(sum(winning_confs) / len(winning_confs), 1)(#17)。
"""
import permit_reading as R


class TestMajorityVote:
    def test_winner_and_its_average_confidence(self):
        v = R.majority_vote([("1234", 70.0), ("1284", 90.0), ("1234", 61.0)])
        assert (v.value, v.conf, v.count, v.total) == ("1234", 65.5, 2, 3)

    def test_average_rounds_to_one_decimal(self):
        v = R.majority_vote([("1234", 70.0), ("1234", 61.0), ("1234", 60.0)])
        assert v.conf == 63.7

    def test_losing_votes_do_not_count_toward_confidence(self):
        v = R.majority_vote([("1234", 50.0), ("1234", 50.0), ("9999", 99.0)])
        assert v.conf == 50.0

    def test_single_vote_is_not_a_majority(self):
        v = R.majority_vote([("1234", 95.0)])
        assert not v
        assert (v.value, v.conf, v.count, v.total) == ("", 0.0, 0, 1)

    def test_empty_input(self):
        v = R.majority_vote([])
        assert not v
        assert (v.value, v.conf, v.count, v.total) == ("", 0.0, 0, 0)

    def test_tie_goes_to_the_value_read_first(self):
        """兩個值票數相同時,先被讀到的那個贏(Counter 的順序,與以前相同)。"""
        v = R.majority_vote([("1284", 99.0), ("1234", 10.0),
                             ("1234", 10.0), ("1284", 99.0)])
        assert v.value == "1284"
        v = R.majority_vote([("1234", 10.0), ("1284", 99.0),
                             ("1284", 99.0), ("1234", 10.0)])
        assert v.value == "1234"

    def test_min_count_is_inclusive(self):
        assert R.majority_vote([("1234", 70.0), ("1234", 70.0)], min_count=2)
        assert not R.majority_vote([("1234", 70.0), ("1234", 70.0)], min_count=3)

    def test_winner_is_truthy(self):
        assert R.majority_vote([("1234", 70.0), ("1234", 70.0)])
