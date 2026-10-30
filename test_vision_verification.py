# -*- coding: utf-8 -*-
"""Vision 驗證:一個「送 Vision」的決定 → 一列 Sheets 結果,絕不回空(#41)。

Vision client 只負責「截圖 → 全文」,放在 seam 後(測試換假的)。取號與 Tesseract 共用
同一個函式、依截圖所屬的 ROI 選 pattern —— 舊做法 Vision 另寫一段、不吃 permit_lower
的那套(冒號可省略),同一張下半部截圖 Tesseract 讀得出、Vision 讀到一樣的字卻取不出,
被判「Vision 無值」進人工審查:兩個引擎用了兩把尺。資料全部捏造。
"""
import pytest

import permit_patterns
from sheet_row import SheetStatus
from submission_decision import Decision, Destination
from permit_patterns import permit_in_text
from vision_verification import run_vision_items


class TestPermitInText:

    def test_lower_roi_accepts_missing_colon(self):
        assert permit_in_text("VI. Pihak\nNo. Izin 1234\n", "permit_lower") == "1234"

    def test_upper_roi_keeps_the_strict_pattern(self):
        assert permit_in_text("No. Izin 1234", "permit_upper") == ""
        assert permit_in_text("No. Izin: 1234", "permit_upper") == "1234"

    def test_permit_of_unknown_half_uses_the_wider_lower_patterns(self):
        assert permit_in_text("No. Izin 1234", "permit") == "1234"

    def test_tesseract_and_vision_choose_patterns_in_one_place(self):
        """ROI → pattern 的選擇只一處:掃描(first-hit 與多數票)與 Vision 取號都呼叫
        permit_id_patterns,許可證判讀裡沒有任何一處直接拿 pattern 清單。"""
        import permit_reading
        from conftest import module_sources
        sources = "\n".join(p.read_text(encoding="utf-8") for p in module_sources(permit_reading))
        assert "permit_id_patterns(" in sources
        assert "RE_PERMIT_ID_LIST" not in sources
        assert permit_patterns.permit_id_patterns("permit_lower") is \
            permit_patterns.RE_PERMIT_ID_LIST_LOWER
        assert permit_patterns.permit_id_patterns("permit_upper") is \
            permit_patterns.RE_PERMIT_ID_LIST

    def test_mol_roi_reads_the_license_number(self):
        assert permit_in_text("Agency's MOL License Number: 5678-2", "mol") == "5678-2"

    def test_mol_roi_does_not_use_permit_patterns(self):
        assert permit_in_text("No. Izin: 1234", "mol") == ""

    def test_unknown_roi_tries_permit_then_mol(self):
        assert permit_in_text("No. Izin: 1234", "") == "1234"
        assert permit_in_text("Agency's MOL License Number: 5678", "") == "5678"

    def test_same_patterns_as_the_tesseract_scan(self):
        """Tesseract 掃描與 Vision 取號用的是同一份 pattern 與同一個函式。"""
        import permit_reading
        assert permit_reading.find_permits is permit_patterns.find_permits


def _item(tmp_path, *, value="1234", roi="permit_lower", crop=True, **kw):
    path = ""
    if crop:
        path = tmp_path / f"{value}-{roi}.png"
        path.write_bytes(b"x")
    return Decision(source_docx="a.docx", destination=Destination.送VISION,
                    image_name="image5.jpeg", img_path=str(path),
                    candidate_value=value, candidate_conf=60.0,
                    reason="permit部分命中無多數", hit_roi=roi, **kw)


class TestOneRowPerItem:

    @pytest.mark.parametrize("vision_text, where, b", [
        ("No. Izin 1234", "auto", "1234"),            # 一致(且 lower 省略冒號也取得出)
        ("No. Izin 1284", "review", ""),              # 差 1 字元 → 不填值
        ("No. Izin 9876", "review", "9876"),          # 不一致
        ("什麼都沒有", "review", "1234"),               # Vision 無值 → 留候選值
    ])
    def test_each_verification_level_lands_in_one_place(self, tmp_path, vision_text, where, b):
        auto, review = run_vision_items(
            [_item(tmp_path)], read_text=lambda p: vision_text)
        rows = {"auto": auto, "review": review}[where]
        assert len(rows) == 1 and len(auto) + len(review) == 1
        assert rows[0].permit == b
        assert rows[0].status is {"auto": SheetStatus.KEYED_IN,
                                  "review": SheetStatus.MANUAL_REVIEW}[where]

    def test_vision_only_when_tesseract_had_nothing(self, tmp_path):
        auto, review = run_vision_items(
            [_item(tmp_path, value="")],
            read_text=lambda p: "No. Izin 1234")
        assert auto == [] and review[0].permit == "1234"

    def test_every_item_gets_exactly_one_row(self, tmp_path):
        items = [_item(tmp_path, value="1234"),
                 _item(tmp_path, value="2222", crop=False),
                 _item(tmp_path, value="3333"),
                 _item(tmp_path, value="4444")]
        auto, review = run_vision_items(
            items, read_text=lambda p: "No. Izin 1234")
        assert len(auto) + len(review) == len(items)
        assert sorted(r.source_docx for r in auto + review) == ["a.docx"] * 4

    def test_roi_decides_the_pattern(self, tmp_path):
        """同一段 Vision 文字,下半部截圖取得出、上半部截圖取不出(依 ROI 選 pattern)。"""
        lower, _ = run_vision_items([_item(tmp_path, roi="permit_lower")],
                                    read_text=lambda p: "No. Izin 1234")
        upper, _ = run_vision_items([_item(tmp_path, roi="permit_upper")],
                                    read_text=lambda p: "No. Izin 1234")
        assert len(lower) == 1 and upper == []


class TestRealVisionReplay:
    """真實 Vision 全文離線重放(#42):30768/30770 的截圖寫 `No ijin`,Vision 讀成 `No jin`;
    兩個引擎讀到同一個號碼,就該自動 key-in,值等於許可證核對值。素材不在就 skip。"""

    def test_replayed_vision_texts_key_in_the_checked_permit(self):
        from pathlib import Path

        import pipeline
        import real_samples as rs
        texts = rs.permit_vision_texts()
        vision_decisions, _, _, _ = pipeline.build_vision_queue(rs.permit_scan_csv())
        items = [d for d in vision_decisions if Path(d.img_path).name in texts]
        # 存過 Vision 全文的截圖,就是當初送 Vision 的那幾張:不在佇列裡是送審決策變了,不是缺素材。
        assert sorted(Path(i.img_path).name for i in items) == sorted(texts)
        if not all(Path(i.img_path).exists() for i in items):
            pytest.skip("送 Vision 的截圖不在最新的某日掃描 samples/permit_scan/<日期>/permit_crops")

        auto, review = run_vision_items(
            items, read_text=lambda p: texts[Path(p).name])

        assert review == []
        got = {Path(r.source_docx).stem: r.permit for r in auto}
        assert got == {stem: rs.checked_permit(stem) for stem in got}
        assert len(got) == len(texts)
