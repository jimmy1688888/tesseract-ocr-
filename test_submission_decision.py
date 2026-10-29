# -*- coding: utf-8 -*-
"""送審決策:一份 docx 的許可證掃描結果 → 一個決定(#40)。

CONTEXT.md「送審決策」:去向(直接 key-in／送 Vision／反查救援)、候選值、被選的那張
截圖、理由。以 docx 為單位 —— 一份 docx 在 Sheets 上只有一列。舊做法逐圖決定,規則 B
每張「部分命中無多數」各送一筆、規則 C 再各一筆,同一份 docx 會寫出多列。

多張圖都需送 Vision → 送信心最高的那張,同分取圖序最前;其他圖讀到的值寫進理由
(grill Q2:Vision 與被送那張一致就已是兩個獨立證據,其他圖的不同讀值不推翻它)。
資料全部捏造。
"""
import pytest

from matches_csv import ResultStatus, ScanResult
from submission_decision import (CONF_KEY_IN, CONF_MOL_VOTE_MIN, CONF_NOVOTE_RESCUE_MAX,
                                 CONF_VOTE_MIN, Destination, decide, is_permit_vote)
from sheet_row import SheetStatus
from vision_verification import run_vision_items


def _row(image="image5.jpeg", cls="large", **kw):
    kw.setdefault("permit_crop", f"crops/{image}.png")
    return ScanResult(source_docx="a.docx", image_name=image, docx_class=cls, **kw)


# fixture 只放掃描讀到的東西(值、信心、多數票、互證、命中狀態),不替送審決策先判。

def _permit(image, value, conf, *, vote=False, partial=False, roi="permit_lower", **kw):
    return _row(image, id=value, id_conf=conf, id_from_vote=vote, hit_roi=roi,
                status=ResultStatus.PERMIT_PARTIAL if partial else ResultStatus.OK, **kw)


def _mol(image, value, conf, *, vote=False, cross=False):
    return _row(image, mol=value, mol_conf=conf, mol_from_vote=vote, cross_match=cross,
                hit_roi="mol", mol_crop=f"crops/{image}_mol.png", permit_crop="")


class TestLarge:

    def test_all_no_hit_is_rescue_without_candidate(self):
        d = decide([_row("image5.jpeg", status=ResultStatus.LARGE_NO_HIT),
                    _row("image6.jpeg", status=ResultStatus.LARGE_NO_HIT)])
        assert d.destination is Destination.反查救援
        assert d.candidate_value == "" and "全 2 張圖" in d.reason

    def test_rule_a_cross_match_high_conf_is_keyin(self):
        d = decide([_mol("image2.jpeg", "1234", 70), _permit("image5.jpeg", "1234", 80)])
        assert d.destination is Destination.直接KEYIN
        assert (d.candidate_value, d.image_name) == ("1234", "image5.jpeg")

    def test_rule_a_cross_match_low_conf_is_vision(self):
        d = decide([_mol("image2.jpeg", "1234", 50), _permit("image5.jpeg", "1234", 52)])
        assert d.destination is Destination.送VISION and d.candidate_value == "1234"

    def test_rule_a_keyin_threshold_is_exclusive(self):
        d = decide([_mol("image2.jpeg", "1234", 50), _permit("image5.jpeg", "1234", CONF_KEY_IN)])
        assert d.destination is Destination.送VISION

    def test_crop_is_the_permit_crop_when_both_exist(self):
        r = _permit("image5.jpeg", "1234", 52)
        r.mol_crop = "crops/image5_mol.png"
        assert decide([r]).img_path == "crops/image5.jpeg.png"

    def test_crop_roi_follows_the_crop_actually_sent(self):
        """hit_roi 記的是「第一個」命中的 ROI:mol 先中、permit 後中時它停在 mol,
        但送出去的是 permit 的截圖 —— 取號 ROI 必須跟著截圖走,不能照抄 hit_roi。"""
        r = _permit("image5.jpeg", "1234", 60, partial=True, roi="mol")
        r.mol, r.mol_conf, r.mol_crop = "9999", 30.0, "crops/image5_mol.png"
        d = decide([r])
        assert d.img_path == "crops/image5.jpeg.png"
        assert d.hit_roi == "permit"                    # 哪一半不知道,至少知道是 permit

    def test_crop_roi_keeps_the_permit_half_when_known(self):
        assert decide([_permit("image5.jpeg", "1234", 60, partial=True,
                               roi="permit_upper")]).hit_roi == "permit_upper"

    def test_mol_crop_roi_is_mol(self):
        r = _mol("image2.jpeg", "1234", 50)
        r.hit_roi = ""                                  # 截圖是 mol 的,就是 mol
        assert decide([r]).hit_roi == "mol"

    def test_rule_a_conflict_prefers_earliest_permit(self):
        d = decide([_mol("image2.jpeg", "9999", 90), _permit("image6.jpeg", "1234", 60),
                    _permit("image5.jpeg", "1235", 40)])
        assert d.destination is Destination.送VISION
        assert (d.candidate_value, d.image_name) == ("1235", "image5.jpeg")

    def test_rule_a2_vote_is_keyin_earliest_image(self):
        d = decide([_permit("image8.jpeg", "1234", 60, vote=True),
                    _permit("image5.jpeg", "1234", 50, vote=True),
                    _permit("image6.jpeg", "5678", 30, partial=True)])
        assert d.destination is Destination.直接KEYIN
        assert d.image_name == "image5.jpeg"

    def test_rule_a2_earliest_regardless_of_row_order(self):
        d = decide([_permit("image5.jpeg", "1234", 50, vote=True),
                    _permit("image8.jpeg", "1234", 60, vote=True)])
        assert d.image_name == "image5.jpeg"

    def test_low_conf_review_row_that_is_not_partial_goes_to_vision(self):
        """反查救援只給「部分命中無多數」的低信心單一判讀;一般需覆核的讀值照樣送 Vision。"""
        d = decide([_permit("image5.jpeg", "1234", CONF_NOVOTE_RESCUE_MAX - 5)])
        assert d.destination is Destination.送VISION

    def test_single_partial_high_conf_is_vision(self):
        d = decide([_permit("image5.jpeg", "1234", 60, partial=True)])
        assert d.destination is Destination.送VISION
        assert d.img_path == "crops/image5.jpeg.png" and d.hit_roi == "permit_lower"

    def test_single_partial_low_conf_is_rescue_with_seed(self):
        d = decide([_permit("image5.jpeg", "1234", CONF_NOVOTE_RESCUE_MAX - 1, partial=True)])
        assert d.destination is Destination.反查救援 and d.candidate_value == "1234"

    def test_single_partial_low_conf_reason_says_it_skipped_vision(self):
        """D 欄理由(#50 之前由 Vision 這一步補前綴;措辭逐字保留)。"""
        d = decide([_permit("image5.jpeg", "1234", 49, partial=True)])
        assert d.reason == ("[單一判讀低信心 conf=49.0<50,略過 ROI Vision 改走次頁電話反查] "
                            "permit部分命中無多數")

    def test_high_conf_without_review_is_keyin(self):
        d = decide([_permit("image5.jpeg", "1234", 90)])
        assert d.destination is Destination.直接KEYIN


class TestLargeThresholds:
    """large 只有一種 ROI 讀到值時(整份只有 mol、或只有 permit;兩種都有走規則 A),
    每條門檻邊界的去向與理由。理由原樣進 Sheets D 欄 —— 真實素材沒有走到這些分支,
    措辭只有這裡守(#52:先在掃描當下判一次的舊程式上錄成特性測試,再把判斷搬進送審決策)。"""

    def _one(self, row):
        d = decide([row])
        return d.destination, d.candidate_value, d.reason

    @pytest.mark.parametrize("conf, dest, reason", [
        (50.0, Destination.送VISION, "僅mol，信心低"),
        (CONF_KEY_IN, Destination.送VISION, "僅mol，信心低"),
        (55.1, Destination.直接KEYIN, "高信心直接key-in conf=55.1"),
    ])
    def test_mol_single_read(self, conf, dest, reason):
        assert self._one(_mol("image2.jpeg", "1234", conf)) == (dest, "1234", reason)

    @pytest.mark.parametrize("conf, dest, reason", [
        (48.0, Destination.送VISION, "僅mol，多數票信心低"),
        (CONF_MOL_VOTE_MIN, Destination.送VISION, "僅mol，多數票信心低"),
        (50.1, Destination.直接KEYIN, "高信心直接key-in conf=50.1"),
    ])
    def test_mol_vote(self, conf, dest, reason):
        assert self._one(_mol("image2.jpeg", "1234", conf, vote=True)) == (dest, "1234", reason)

    @pytest.mark.parametrize("conf, vote, dest, reason", [
        (48.0, True, Destination.送VISION, "僅mol，多數票信心低 多數決信心低"),
        (53.0, True, Destination.送VISION, "多數決信心低"),
        (CONF_KEY_IN, True, Destination.送VISION, "多數決信心低"),
        (55.1, True, Destination.直接KEYIN, "高信心直接key-in conf=55.1"),
        (50.0, False, Destination.送VISION, "僅mol，信心低 多數決信心低"),
    ])
    def test_mol_cross_match(self, conf, vote, dest, reason):
        row = _mol("image2.jpeg", "1234", conf, vote=vote, cross=True)
        assert self._one(row) == (dest, "1234", reason)

    @pytest.mark.parametrize("conf, dest, reason", [
        (40.0, Destination.送VISION, "permit多數票，信心低"),
        (CONF_VOTE_MIN, Destination.送VISION, "permit多數票，信心低"),
        (45.1, Destination.直接KEYIN, "permit多數票_高信心_最早圖(image5.jpeg) conf=45.1"),
    ])
    def test_permit_vote(self, conf, dest, reason):
        row = _permit("image5.jpeg", "1234", conf, vote=True)
        assert self._one(row) == (dest, "1234", reason)

    @pytest.mark.parametrize("conf, dest, reason", [
        (40.0, Destination.送VISION, "僅permit，信心低"),
        (CONF_KEY_IN, Destination.送VISION, "僅permit，信心低"),
        (55.1, Destination.直接KEYIN, "高信心直接key-in conf=55.1"),
    ])
    def test_permit_single_read(self, conf, dest, reason):
        assert self._one(_permit("image5.jpeg", "1234", conf)) == (dest, "1234", reason)

    @pytest.mark.parametrize("conf, dest, reason", [
        (49.9, Destination.反查救援,
         "[單一判讀低信心 conf=49.9<50,略過 ROI Vision 改走次頁電話反查] permit部分命中無多數"),
        (CONF_NOVOTE_RESCUE_MAX, Destination.送VISION, "permit部分命中無多數"),
        (70.0, Destination.送VISION, "permit部分命中無多數"),
    ])
    def test_permit_partial(self, conf, dest, reason):
        row = _permit("image5.jpeg", "1234", conf, partial=True)
        assert self._one(row) == (dest, "1234", reason)

    def test_vision_candidates_mix_partial_and_low_conf_reads(self):
        d = decide([_permit("image5.jpeg", "1234", 60.0, partial=True),
                    _permit("image6.jpeg", "1284", 50.0)])
        assert (d.destination, d.candidate_value, d.image_name) == (
            Destination.送VISION, "1234", "image5.jpeg")
        assert d.reason == "permit部分命中無多數；其他圖讀值:image6.jpeg=1284(conf=50.0)"

    def test_low_conf_read_wins_with_its_own_reason(self):
        d = decide([_mol("image2.jpeg", "1234", 52.0), _mol("image3.jpeg", "1234", 50.0)])
        assert (d.destination, d.image_name) == (Destination.送VISION, "image2.jpeg")
        assert d.reason == "僅mol，信心低；其他圖讀值:image3.jpeg=1234(conf=50.0)"

    def test_high_conf_keyin_takes_the_most_confident_read(self):
        d = decide([_mol("image2.jpeg", "1234", 60.0), _mol("image3.jpeg", "5678", 70.0)])
        assert (d.destination, d.candidate_value, d.image_name, d.reason) == (
            Destination.直接KEYIN, "5678", "image3.jpeg", "高信心直接key-in conf=70.0")

    def test_threshold_compares_unrounded_but_reason_shows_one_decimal(self):
        """門檻比的是讀到的信心本身;理由裡的信心取到小數一位(正式流程讀 CSV,本來就一位)。"""
        assert self._one(_mol("image2.jpeg", "1234", 55.04)) == (
            Destination.直接KEYIN, "1234", "高信心直接key-in conf=55.0")


class TestNoUsableReading:
    """舊做法這兩種回空佇列 —— docx 安靜消失;現在走反查救援(全無命中同一條路)。"""

    def test_large_rows_without_any_value(self):
        d = decide([_row("image5.jpeg"), _row("image6.jpeg", status=ResultStatus.LARGE_NO_HIT)])
        assert d.destination is Destination.反查救援 and d.image_name == ""
        assert "皆無可用讀值" in d.reason

    def test_small_rows_without_hits_even_if_status_is_ok(self):
        d = decide([_row("image1.png", cls="small")])
        assert d.destination is Destination.反查救援 and d.candidate_value == ""


class TestOneDecisionPerDocx:
    """舊做法:兩張部分命中 + 一張 C 規則 → 三筆。現在一筆,選信心最高、同分取圖序前。"""

    def test_highest_conf_wins_others_in_reason(self):
        d = decide([_permit("image5.jpeg", "1234", 52, partial=True),
                    _permit("image6.jpeg", "1284", 60, partial=True),
                    _permit("image8.jpeg", "7777", 45)])
        assert (d.destination, d.candidate_value, d.image_name) == (
            Destination.送VISION, "1284", "image6.jpeg")
        assert "image5.jpeg=1234" in d.reason and "image8.jpeg=7777" in d.reason

    def test_tie_takes_earliest_image(self):
        d = decide([_permit("image6.jpeg", "1284", 60, partial=True),
                    _permit("image5.jpeg", "1234", 60, partial=True)])
        assert d.image_name == "image5.jpeg"

    def test_low_conf_partial_loses_to_vision_candidate(self):
        """最高信心那張決定去向:它夠高就送 Vision,不因另一張低信心而改走反查。"""
        d = decide([_permit("image5.jpeg", "1234", 40, partial=True),
                    _permit("image6.jpeg", "1284", 60, partial=True)])
        assert d.destination is Destination.送VISION and d.image_name == "image6.jpeg"


class TestSmall:
    """small:winner 是信心最高的讀值(同分取先出現者),門檻與理由。

    #53:先在掃描當下彙總一次的舊程式上錄成特性測試,再把 winner 只留在送審決策。"""

    def _small(self, image, value, conf, *, vote=False, id_conf=0.0):
        return _row(image, cls="small", mol=value, mol_conf=conf, mol_from_vote=vote,
                    id_conf=id_conf, hit_roi="mol",
                    mol_crop=f"crops/{image}_mol.png", permit_crop="")

    @pytest.mark.parametrize("conf, vote, dest, reason", [
        (float(CONF_MOL_VOTE_MIN), True, Destination.送VISION,
         "small:mol多數票但平均信心50.0未達直接key-in門檻50,需人工確認"),
        (50.1, True, Destination.直接KEYIN, "mol多數票_高信心 avg_conf=50.1"),
        (49.9, False, Destination.反查救援,
         "[單一判讀低信心 conf=49.9<50,略過 ROI Vision 改走次頁電話反查] "
         "small:mol非多數票(單一判讀 conf=49.9),需人工複核"),
        (CONF_NOVOTE_RESCUE_MAX, False, Destination.送VISION,
         "small:mol非多數票(單一判讀 conf=50.0),需人工複核"),
        (90.0, False, Destination.送VISION,
         "small:mol非多數票(單一判讀 conf=90.0),需人工複核"),
    ])
    def test_thresholds(self, conf, vote, dest, reason):
        d = decide([self._small("image1.png", "1234", conf, vote=vote)])
        assert (d.destination, d.candidate_value, d.reason) == (dest, "1234", reason)

    def test_rescue_threshold_compares_conf_to_one_decimal(self):
        """49.96 取到小數一位是 50.0 —— 送 Vision,不是反查救援(與 matches.csv 寫出的一致)。"""
        d = decide([self._small("image1.png", "1234", 49.96)])
        assert (d.destination, d.candidate_conf) == (Destination.送VISION, 50.0)
        assert d.reason == "small:mol非多數票(單一判讀 conf=50.0),需人工複核"

    def test_winner_is_the_most_confident_read_of_either_roi(self):
        d = decide([self._small("image1.png", "1111", 60.0),
                    self._small("image2.png", "2222", 55.0, id_conf=85.0),
                    self._small("image3.png", "3333", 70.0)])
        assert (d.candidate_value, d.image_name, d.candidate_conf) == ("2222", "image2.png", 85.0)

    def test_tie_takes_the_first_read(self):
        d = decide([self._small("image1.png", "1111", 60.0, vote=True),
                    self._small("image2.png", "2222", 60.0, vote=True)])
        assert (d.destination, d.candidate_value, d.image_name) == (
            Destination.直接KEYIN, "1111", "image1.png")

    def test_a_vote_that_is_not_the_winner_does_not_key_in(self):
        """多數票只算 winner 自己的;另一張低信心的多數票不會讓 winner 直接 key-in。"""
        d = decide([self._small("image1.png", "1111", 52.0, vote=True),
                    self._small("image2.png", "2222", 60.0)])
        assert (d.destination, d.candidate_value) == (Destination.送VISION, "2222")
        assert d.reason == "small:mol非多數票(單一判讀 conf=60.0),需人工複核"

    def test_permit_only_read_is_the_value(self):
        r = _row("image1.png", cls="small", id="9999", id_conf=70.0, hit_roi="mol")
        d = decide([r])
        assert (d.destination, d.candidate_value) == (Destination.送VISION, "9999")

    def test_mol_value_comes_before_permit_value(self):
        """同一列兩個 ROI 都有值時取 mol(舊彙總 winner.mol or winner.id 的順序)。"""
        r = self._small("image1.png", "1111", 60.0, id_conf=40.0)
        r.id = "2222"
        assert decide([r]).candidate_value == "1111"

    def test_no_hit_rows_beside_a_hit_do_not_matter(self):
        d = decide([_row("image1.png", cls="small"),
                    self._small("image2.png", "1234", 70.0, vote=True)])
        assert (d.destination, d.image_name) == (Destination.直接KEYIN, "image2.png")

    def test_all_no_hit_counts_every_image(self):
        d = decide([_row(f"image{i}.png", cls="small") for i in (1, 2, 3)])
        assert (d.destination, d.reason) == (
            Destination.反查救援, "small docx：mol ROI 全 3 張圖無命中")

    def test_all_no_hit_is_rescue(self):
        d = decide([_row("image1.png", cls="small", status=ResultStatus.SMALL_NO_HIT)])
        assert d.destination is Destination.反查救援 and d.candidate_value == ""

    def test_single_read_low_conf_is_rescue(self):
        d = decide([self._small("image1.png", "1234", 40)])
        assert d.destination is Destination.反查救援 and d.candidate_value == "1234"
        assert d.reason == ("[單一判讀低信心 conf=40.0<50,略過 ROI Vision 改走次頁電話反查] "
                            "small:mol非多數票(單一判讀 conf=40.0),需人工複核")

    def test_otherwise_vision_on_the_best_read_without_needing_flags(self):
        """不靠掃描端的旗標(舊的防禦性退路就是為了旗標漏設而存在)。"""
        d = decide([self._small("image1.png", "1234", 50),
                    self._small("image2.png", "1234", 52)])
        assert d.destination is Destination.送VISION and d.image_name == "image2.png"
        assert d.img_path == "crops/image2.png_mol.png"


class TestVotePredicate:
    """停止擴掃(ADR-0005)與規則 A2 用同一個多數票判斷。"""

    def test_threshold(self):
        assert is_permit_vote(_permit("image5.jpeg", "1234", CONF_VOTE_MIN + 1, vote=True))
        assert not is_permit_vote(_permit("image5.jpeg", "1234", CONF_VOTE_MIN, vote=True))
        assert not is_permit_vote(_permit("image5.jpeg", "1234", 90, partial=True))
        assert not is_permit_vote(_mol("image2.jpeg", "1234", 90, vote=True))

    def test_stop_expanding_uses_the_same_predicate(self):
        from permit_reading import has_permit_vote
        rows = [_permit("image5.jpeg", "1234", CONF_VOTE_MIN + 1, vote=True)]
        assert has_permit_vote(rows) is True
        assert decide(rows).destination is Destination.直接KEYIN
        rows = [_permit("image5.jpeg", "1234", CONF_VOTE_MIN, vote=True)]
        assert has_permit_vote(rows) is False
        assert decide(rows).destination is not Destination.直接KEYIN


def test_queue_has_one_item_per_docx(tmp_path):
    """matches.csv 一份 docx 兩列(兩張圖都需送 Vision)→ 佇列只有一筆。"""
    import matches_csv
    import pipeline
    p = tmp_path / "matches.csv"
    matches_csv.write(p, [_permit("image5.jpeg", "1234", 52, partial=True),
                          _permit("image6.jpeg", "1284", 60, partial=True)])
    vision, manual, keyin, _ = pipeline.build_vision_queue(p)
    assert [(i.source_docx, i.image_name) for i in vision + keyin] == [("a.docx", "image6.jpeg")]
    assert manual == []


def test_queue_items_carry_the_crop_roi(tmp_path):
    """送 Vision 的項目帶著截圖屬於哪個 ROI(#41 依此選取號 pattern)。"""
    import matches_csv
    import pipeline
    p = tmp_path / "matches.csv"
    matches_csv.write(p, [_permit("image5.jpeg", "1234", 60, partial=True)])
    vision, manual, keyin, _ = pipeline.build_vision_queue(p)
    assert [i.hit_roi for i in vision] == ["permit_lower"]


# ─── 讀一份 matches.csv 得出決策:截圖路徑以 CSV 所在資料夾解析(#61) ──────────

class TestCropPathFromCsv:
    """matches.csv 的截圖欄相對於它自己的資料夾;交給 Vision 的是開得了的路徑。"""

    def _decide_from(self, folder, rows):
        import matches_csv
        from submission_decision import read_decisions
        folder.mkdir(parents=True, exist_ok=True)
        matches_csv.write(folder / "matches.csv", rows)
        [d] = read_decisions(folder / "matches.csv")
        return d

    def test_relative_crop_resolves_against_the_csv_folder(self, tmp_path):
        d = self._decide_from(tmp_path / "2026-10-29", [_permit(
            "image5.jpeg", "1234", 60, partial=True, permit_crop="permit_crops/a_image5.png")])
        assert d.destination is Destination.送VISION
        assert d.img_path == str(tmp_path / "2026-10-29" / "permit_crops" / "a_image5.png")

    def test_relative_crop_is_what_vision_reads(self, tmp_path):
        folder = tmp_path / "2026-10-29"
        d = self._decide_from(folder, [_permit(
            "image5.jpeg", "1234", 60, partial=True, permit_crop="permit_crops/a_image5.png")])
        (folder / "permit_crops").mkdir()
        (folder / "permit_crops" / "a_image5.png").write_bytes(b"x")
        read = []
        run_vision_items([d], read_text=lambda p: read.append(p) or "No. Izin: 1234")
        assert read == [str(folder / "permit_crops" / "a_image5.png")]

    def test_no_crop_still_goes_to_manual_review(self, tmp_path):
        """拼上去會變成資料夾本身 —— exists() 為真,「沒截圖 → 人工審查」就被跳過。"""
        d = self._decide_from(tmp_path, [_permit(
            "image5.jpeg", "1234", 60, partial=True, permit_crop="")])
        assert d.img_path == ""
        read = []
        auto, review = run_vision_items([d], read_text=lambda p: read.append(p) or "")
        assert (read, auto) == ([], [])
        assert [r.status for r in review] == [SheetStatus.MANUAL_REVIEW]

    def test_old_files_with_absolute_paths_still_open(self, tmp_path):
        old = str(tmp_path / "elsewhere" / "permit_crops" / "a_image5.png")
        d = self._decide_from(tmp_path / "now", [_permit(
            "image5.jpeg", "1234", 60, partial=True, permit_crop=old)])
        assert d.img_path == old
