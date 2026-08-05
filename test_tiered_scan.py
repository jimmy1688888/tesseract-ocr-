# -*- coding: utf-8 -*-
"""許可證掃圖的分層掃描(ADR-0005)。

21 份實測:許可證命中 19/19 都在 image5 或 image6,mol 命中全在 image2,而
image1 與 image7 從來沒讀出過任何東西。掃全部 134 張要 2h51m,其中 99.7% 的
時間花在許可證掃描。

分層的安全性建立在一件事上:**第一層猜錯不會錯值,只會慢** —— 拿不到 permit
多數票就把其餘圖掃完,回到與全掃相同的輸入。本組測試釘的就是這個「拿不到就
一定會擴掃」的保證,以及兩個會讓它失效的坑。
"""
import csv

import pytest

import pipeline as P


def _img(n: int) -> tuple[str, bytes]:
    return (f"image{n}.jpeg", f"bytes{n}".encode())


def _vote_row(docx: str, img: str, value: str = "1234",
              conf: float = 70.0) -> P.ScanResult:
    """一張讀到 permit 多數票的圖(足以停止擴掃)。"""
    r = P.ScanResult(source_docx=docx, image_name=img, docx_class="large")
    r.id, r.id_conf, r.id_from_vote = value, conf, True
    r.final_value, r.final_conf = value, conf
    return r


@pytest.fixture
def scan_env(tmp_path, monkeypatch):
    """把 run_scan 的檔案輸出導到 tmp,並攔下實際 OCR,回傳被掃過的圖檔名。

    decide_result 一併停掉:本組測的是「掃哪幾張」的控制流,不是決策規則。
    """
    monkeypatch.setattr(P, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(P, "NO_HIT_REVIEW_DIR", tmp_path / "no_hit_review")
    monkeypatch.setattr(P, "decide_result", lambda r: None)

    scanned = {"large": [], "small": []}

    def make(images, large_result=lambda docx, name: None):
        monkeypatch.setattr(P, "extract_images_from_docx", lambda p: list(images))

        def _large(docx, name, data, roi_filter=""):
            scanned["large"].append(name)
            return large_result(docx, name)

        def _small(docx, name, data):
            scanned["small"].append(name)
            return P.ScanResult(source_docx=docx, image_name=name,
                                docx_class="small",
                                status=P.ResultStatus.SMALL_NO_HIT)

        monkeypatch.setattr(P, "scan_image_large", _large)
        monkeypatch.setattr(P, "scan_image_mol_only", _small)
        monkeypatch.setattr(P, "aggregate_small_docx", lambda rows: list(rows))
        return scanned

    return make


def _rows(tmp_path):
    with open(tmp_path / "matches.csv", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


class TestInputListing:
    """Word 的鎖定檔不得進入處理清單。

    Word 開著某份文件時會在同目錄產生 `~$檔名.docx`(約 162 bytes,不是 zip)。
    它符合 *.docx,而 extract_images_from_docx 會在它上面拋 BadZipFile 讓整批
    當場掛掉——觸發條件只是「有人正開著那份檔案在看」。
    """

    def test_word_lock_files_are_skipped(self, tmp_path):
        (tmp_path / "32572.docx").write_bytes(b"x")
        (tmp_path / "~$32572.docx").write_bytes(b"lock")
        assert [p.name for p in P.list_input_docx(tmp_path)] == ["32572.docx"]

    def test_order_is_deterministic(self, tmp_path):
        for n in ("32574.docx", "32503.docx", "~$32503.docx"):
            (tmp_path / n).write_bytes(b"x")
        assert [p.name for p in P.list_input_docx(tmp_path)] == ["32503.docx",
                                                                 "32574.docx"]


class TestSplitTiers:
    """第一層是 {2,5,6},其餘全歸第二層,兩者聯集恆等於輸入。"""

    def test_partition_is_lossless(self):
        images = [_img(n) for n in range(1, 8)]
        t1, rest = P.split_tiers(images)
        assert [n for n, _ in t1] == ["image2.jpeg", "image5.jpeg", "image6.jpeg"]
        assert [n for n, _ in rest] == ["image1.jpeg", "image3.jpeg",
                                        "image4.jpeg", "image7.jpeg"]
        assert sorted(t1 + rest) == sorted(images)

    def test_unnumbered_names_go_to_second_tier(self):
        """檔名不是 imageN 的圖身分不明,放進第一層只會白花時間。"""
        images = [("cover.png", b"x"), _img(5)]
        t1, rest = P.split_tiers(images)
        assert [n for n, _ in t1] == ["image5.jpeg"]
        assert [n for n, _ in rest] == ["cover.png"]

    def test_first_tier_may_be_empty(self):
        t1, rest = P.split_tiers([_img(1), _img(7)])
        assert t1 == []
        assert len(rest) == 2


class TestHasPermitVote:
    """只有「可信的 permit 多數票」能停止擴掃。這是兩個坑的正面表述。"""

    def test_real_vote_stops_expansion(self):
        assert P.has_permit_vote([_vote_row("a.docx", "image5.jpeg")])

    def test_mol_hit_is_not_enough(self):
        """mol 只證明這是契約頁,不是許可證的讀值。

        32523 與 32529 的 mol 信心是 37 與 28 —— 拿這種值擋掉擴掃,等於讓
        雜訊決定要不要去找真正的許可證。
        """
        r = P.ScanResult(source_docx="a.docx", image_name="image2.jpeg",
                         docx_class="large")
        r.mol, r.mol_conf, r.mol_from_vote = "0669", 37.0, True
        r.final_value, r.final_conf = "0669", 37.0
        assert not P.has_permit_vote([r])

    def test_permit_partial_is_not_enough(self):
        """幾個設定各讀出不同的值、湊不出多數 —— r.id 有值但不算讀到。

        若讓它擋掉擴掃,process_large_vs 的規則A2 不會採用這個值,反而掉進
        規則B 並以 no_vote_rescue 把整份推去走台仲反查,而真正的多數票可能
        就在還沒掃的那幾張圖上。這是分層最危險的一條路徑。
        """
        r = P.ScanResult(source_docx="a.docx", image_name="image5.jpeg",
                         docx_class="large")
        r.id, r.id_conf = "1284", 40.0          # id 有值,但 id_from_vote 為 False
        r.status = P.ResultStatus.PERMIT_PARTIAL
        assert not P.has_permit_vote([r])

    def test_low_confidence_vote_is_not_enough(self):
        assert not P.has_permit_vote(
            [_vote_row("a.docx", "image5.jpeg", conf=P.CONF_VOTE_MIN)]
        )


class TestExpansion:
    """第一層拿不到多數票就一定擴掃,拿到就不擴掃。"""

    def test_no_vote_expands_to_remaining_images(self, scan_env, tmp_path):
        scanned = scan_env([_img(n) for n in range(1, 8)])
        P.run_scan([tmp_path / "32512.docx"])
        assert scanned["large"] == ["image2.jpeg", "image5.jpeg", "image6.jpeg",
                                    "image1.jpeg", "image3.jpeg",
                                    "image4.jpeg", "image7.jpeg"]

    def test_second_tier_never_rescans_the_first(self, scan_env, tmp_path):
        scanned = scan_env([_img(n) for n in range(1, 8)])
        P.run_scan([tmp_path / "32512.docx"])
        assert len(scanned["large"]) == len(set(scanned["large"]))

    def test_vote_in_first_tier_skips_the_rest(self, scan_env, tmp_path):
        scanned = scan_env(
            [_img(n) for n in range(1, 8)],
            large_result=lambda docx, name: (
                _vote_row(docx, name) if name == "image5.jpeg" else None),
        )
        P.run_scan([tmp_path / "32508.docx"])
        assert scanned["large"] == ["image2.jpeg", "image5.jpeg", "image6.jpeg"]

    def test_partial_hit_does_not_block_expansion(self, scan_env, tmp_path):
        """image5 讀到值但湊不出多數 → 仍要擴掃,真正的多數票可能在 image4。"""
        def partial(docx, name):
            if name != "image5.jpeg":
                return None
            r = P.ScanResult(source_docx=docx, image_name=name, docx_class="large")
            r.id, r.id_conf = "1284", 40.0
            r.status = P.ResultStatus.PERMIT_PARTIAL
            return r

        scanned = scan_env([_img(n) for n in range(1, 8)], large_result=partial)
        P.run_scan([tmp_path / "32512.docx"])
        assert "image4.jpeg" in scanned["large"]

    def test_empty_first_tier_still_scans_everything(self, scan_env, tmp_path):
        """圖檔編號與 {2,5,6} 毫無交集 → 第一層是空的,不能因此什麼都不掃。"""
        scanned = scan_env([_img(1), _img(3), _img(4), _img(7)])
        P.run_scan([tmp_path / "odd.docx"])
        assert scanned["large"] == ["image1.jpeg", "image3.jpeg",
                                    "image4.jpeg", "image7.jpeg"]


class TestClassifyUsesOriginalCount:
    """docx_class 一律用原始張數判定,在任何過濾之前算。

    過濾後的張數丟進 classify_by_count,5 張圖的 docx 剩 3 張就會被判成 small,
    整份改走 mol-only 的另一條路 —— 掃的 ROI 不同、決策規則不同、輸出欄位不同。
    """

    def test_image_filter_does_not_turn_large_into_small(self, scan_env, tmp_path):
        scanned = scan_env([_img(n) for n in range(1, 8)])
        P.run_scan([tmp_path / "32509.docx"], image_filter="image5.jpeg")
        assert scanned["large"] == ["image5.jpeg"]
        assert scanned["small"] == []

    def test_narrow_first_tier_does_not_turn_large_into_small(self, scan_env,
                                                              tmp_path):
        """4 張圖的 docx 第一層只有 image2 一張,仍是 large。"""
        scanned = scan_env([_img(n) for n in range(1, 5)])
        P.run_scan([tmp_path / "small4.docx"])
        assert scanned["small"] == []
        assert scanned["large"][0] == "image2.jpeg"


class TestSmallIsNotTiered:
    """small 不套分層:最多 3 張圖、只掃一個佔頁面 7% 的 mol ROI。

    套下去會把它掃成零張 —— 1~2 張圖的 docx 與 {2,5,6} 可能毫無交集,而產出的
    「全無命中」與真的讀不到長得一模一樣,從結果上分不出來。
    """

    def test_all_images_scanned(self, scan_env, tmp_path):
        scanned = scan_env([_img(1), _img(2), _img(3)])
        P.run_scan([tmp_path / "tiny.docx"])
        assert scanned["small"] == ["image1.jpeg", "image2.jpeg", "image3.jpeg"]

    def test_single_image_not_filtered_away(self, scan_env, tmp_path):
        scanned = scan_env([_img(1)])
        P.run_scan([tmp_path / "one.docx"])
        assert scanned["small"] == ["image1.jpeg"]


class TestFullScanFlag:
    """--full-scan 關掉分層,行為等同分層前的現況。"""

    def test_every_image_scanned_in_one_pass(self, scan_env, tmp_path):
        scanned = scan_env(
            [_img(n) for n in range(1, 8)],
            large_result=lambda docx, name: (
                _vote_row(docx, name) if name == "image5.jpeg" else None),
        )
        P.run_scan([tmp_path / "32508.docx"], full_scan=True)
        assert scanned["large"] == [f"image{n}.jpeg" for n in range(1, 8)]

    def test_flag_exists_and_defaults_off(self, monkeypatch):
        monkeypatch.setattr("sys.argv", ["pipeline.py"])
        assert P._parse_args().full_scan is False
        monkeypatch.setattr("sys.argv", ["pipeline.py", "--full-scan"])
        assert P._parse_args().full_scan is True


class TestScanTierIsRecorded:
    """scan_tier 寫進 CSV,讓「第一層命中率崩掉」看得見。

    分層錯了只會慢不會錯值,所以它會安靜地失效:換一批版面不同的文件,每份都
    擴掃,總時間比全掃更長,而結果欄位一格都沒變。這欄是唯一的體檢報告。
    """

    def test_tier_recorded_per_row(self, scan_env, tmp_path):
        """第一層只讀到 mol(不足以停止擴掃)、真正的多數票在第二層的 image4。"""
        def result(docx, name):
            if name == "image2.jpeg":
                r = P.ScanResult(source_docx=docx, image_name=name,
                                 docx_class="large")
                r.mol, r.mol_conf, r.mol_from_vote = "0669", 37.0, True
                return r
            return _vote_row(docx, name) if name == "image4.jpeg" else None

        scan_env([_img(n) for n in range(1, 8)], large_result=result)
        P.run_scan([tmp_path / "32538.docx"])
        tiers = {r["image_name"]: r["scan_tier"] for r in _rows(tmp_path)}
        assert tiers["image2.jpeg"] == "1"
        assert tiers["image4.jpeg"] == "2"

    def test_no_hit_placeholder_records_that_it_expanded(self, scan_env, tmp_path):
        """全無命中的份沒有任何命中列,佔位列就是它在 CSV 裡的唯一代表。

        實跑 21 份時這裡失真過:32512 與 32545 擴掃完仍沒命中,佔位列卻標成
        第一層 —— 而「大量擴掃且仍沒命中」正是分層規則過期最典型的表現,
        偏偏就是這一格看不出來。
        """
        scan_env([_img(n) for n in range(1, 8)])
        P.run_scan([tmp_path / "32512.docx"])
        rows = _rows(tmp_path)
        assert [r["status"] for r in rows] == ["large_no_hit"]
        assert rows[0]["scan_tier"] == "2"

    def test_no_hit_placeholder_stays_first_tier_when_nothing_to_expand(
            self, scan_env, tmp_path):
        """--full-scan 沒有第二層可擴,佔位列不該假裝擴掃過。"""
        scan_env([_img(n) for n in range(1, 8)])
        P.run_scan([tmp_path / "32512.docx"], full_scan=True)
        assert _rows(tmp_path)[0]["scan_tier"] == "1"

    def test_column_survives_csv_round_trip(self):
        r = P.ScanResult(source_docx="a.docx", image_name="image4.jpeg",
                         docx_class="large", scan_tier=2)
        assert P.ScanResult.from_csv_row(r.to_csv_row()).scan_tier == 2

    def test_old_csv_without_the_column_reads_as_first_tier(self):
        """舊 matches.csv 沒有這欄,不能讓缺欄變成無意義的 0。"""
        assert P.ScanResult.from_csv_row({"source_docx": "a.docx"}).scan_tier == 1
