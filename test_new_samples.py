# -*- coding: utf-8 -*-
"""新素材進來:錄製指令補 Vision 全文與契約頁核對值(#33)。

有 docx、沒有全文的素材:用正式流程的規則選契約頁 → 列清單請人確認 → 確認 Vision
可用 → 每份打一次 Vision 存全文。人確認過的契約頁才記成核對值;程式不自動升格。
這裡 Vision、人的回答、頁面評分都換成假的,不花錢。
"""
import json

import pytest

import employer_extract as ee
import real_samples as rs
import snapshots as snap

M = ee.MIN_CONTRACT_SCORE

PAGES = {
    "33711": [(1, "image1.png", b"p1"), (M + 3, "image2.png", b"p2"), (M - 1, "image3.png", b"p3")],
    "33712": [(0, "image1.png", b"q1"), (1, "image2.jpeg", b"q2")],      # 未達門檻
}


@pytest.fixture
def corpus(tmp_path, monkeypatch):
    monkeypatch.setattr(rs, "ROOT", tmp_path / "samples")
    docs = tmp_path / "samples" / "docs"
    docs.mkdir(parents=True)
    for stem in ("33710", *PAGES):
        (docs / f"{stem}.docx").write_bytes(b"")
    rs.save_vision_text("33710", "image2.png", "0.35", "舊全文")      # 已有全文
    return tmp_path / "samples"


class Fake:
    """記錄 Vision 呼叫與印出內容;回答依序取用。"""

    def __init__(self, answers, vision_ok=True):
        self.answers = list(answers)
        self.lines, self.sent, self.checks = [], [], 0
        self._ok = vision_ok

    def ask(self, prompt):
        self.lines.append(prompt)
        return self.answers.pop(0)

    def vision_ok(self):
        self.checks += 1
        return (self._ok, "" if self._ok else "Reauthentication is needed")

    def fetch_text(self, content):
        self.sent.append(content)
        return f"全文<{content.decode()}>"

    def run(self):
        return snap.fill_missing_vision(
            ask=self.ask, out=self.lines.append, vision_ok=self.vision_ok,
            fetch_text=self.fetch_text,
            score_pages=lambda p: PAGES[p.stem], prepare=lambda raw: raw)


def test_confirmed_page_becomes_checked_value_and_is_sent(corpus):
    f = Fake(["y", "", "y"])                          # 33711 對、33712 跳過、開打
    assert f.run() == ["33711"]
    assert rs.checked_contract_page("33711.docx") == "image2.png"
    assert f.sent == [b"p2"]
    saved = rs.vision_sample("33711")
    assert (saved["image"], saved["roi"]) == ("image2.png", str(ee.EMPLOYER_ROI[3]))
    assert rs.employer_text("33711") == "全文<p2>"


def test_naming_another_page_records_it_and_sends_that_page(corpus):
    f = Fake(["image3.png", "", "y"])
    f.run()
    assert rs.checked_contract_page("33711.docx") == "image3.png"
    assert f.sent == [b"p3"]
    assert rs.vision_sample("33711")["image"] == "image3.png"


def test_send_without_looking_records_no_checked_value(corpus):
    f = Fake(["g", "", "y"])
    f.run()
    assert rs.checked_contract_page("33711.docx") is None
    assert f.sent == [b"p2"]


def test_send_without_looking_refuses_a_page_below_threshold(corpus):
    """未達門檻的候選頁,正式流程也不送 Vision;人沒指定哪張就不打。"""
    f = Fake(["", "g", "image2.jpeg", "y"])           # 33712:先 g 被拒,再指定
    f.run()
    assert f.sent == [b"q2"]
    assert rs.checked_contract_page("33712.docx") == "image2.jpeg"


def test_page_list_shows_pick_scores_count_and_image_to_open(corpus):
    f = Fake(["y", "y", "n"])
    f.run()
    text = "\n".join(f.lines)
    assert f"33711.docx → image2.png(分數 {M + 3},次高 {M - 1})" in text
    assert "未達門檻" in text                          # 33712
    assert "共 2 次 Vision" in text
    review = corpus / "review" / "33711_image2.png"
    assert review.read_bytes() == b"p2" and str(review) in text


def test_already_saved_text_is_never_fetched_again(corpus):
    f = Fake(["y", "y", "y"])
    f.run()
    assert "33710" not in "\n".join(f.lines)
    assert rs.employer_text("33710") == "舊全文"
    assert len(f.sent) == 2


def test_declining_the_final_prompt_sends_nothing(corpus):
    f = Fake(["y", "y", "n"])
    assert f.run() == []
    assert f.sent == [] and f.checks == 0
    assert rs.vision_stems() == ["33710"]


def test_vision_unavailable_stops_before_sending(corpus):
    f = Fake(["y", "", "y"], vision_ok=False)
    assert f.run() == []
    assert f.sent == []
    assert any("Reauthentication" in l for l in f.lines)


def test_nothing_to_fetch_does_not_even_check_vision(corpus):
    f = Fake(["", "", "y"])
    assert f.run() == []
    assert f.checks == 0


def test_no_new_samples_asks_nothing(corpus):
    for stem in PAGES:
        rs.save_vision_text(stem, "image2.png", "0.35", "x")
    f = Fake([])
    assert f.run() == []
    assert f.checks == 0
