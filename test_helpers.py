# -*- coding: utf-8 -*-
"""掃描順序測試:natural sort 工具與 extract_images_from_docx 端到端順序。

涵蓋:
  TestImageSortKey             — _image_sort_key 工具的排序語意
  TestExtractImagesNaturalSort — extract_images_from_docx 端到端順序

業務動機:同一份 docx 內的 image1, image2, ... image10 應該嚴格依數字序處理。
預設的 lexical sort 會把 image10 排在 image2 之前(因為字串 '1' < '2'),
可能導致先掃到後面頁的舊資料。
"""

import zipfile
import tempfile
from pathlib import Path

from pipeline import (
    _image_sort_key,
    extract_images_from_docx,
)


# ═══════════════════════════════════════════════════════════════════════════
# _image_sort_key:natural sort 工具
# ═══════════════════════════════════════════════════════════════════════════

class TestImageSortKey:
    """natural sort 工具:讓 image1 < image2 < ... < image10。"""

    def test_image1_sorts_before_image2(self):
        assert _image_sort_key("image1.jpeg") < _image_sort_key("image2.jpeg")

    def test_image2_sorts_before_image10(self):
        """**核心 fix**:lexical sort 下 'image10' < 'image2',要被本工具修正"""
        assert _image_sort_key("image2.jpeg") < _image_sort_key("image10.jpeg")

    def test_image10_sorts_before_image11(self):
        assert _image_sort_key("image10.jpeg") < _image_sort_key("image11.jpeg")

    def test_sorting_full_list_matches_numeric_order(self):
        """整批排序結果符合 image1, image2, ..., image10, image11"""
        names = ["image10.jpeg", "image2.jpeg", "image11.jpeg",
                 "image1.jpeg", "image3.jpeg"]
        sorted_names = sorted(names, key=_image_sort_key)
        assert sorted_names == [
            "image1.jpeg", "image2.jpeg", "image3.jpeg",
            "image10.jpeg", "image11.jpeg",
        ]

    def test_non_numeric_names_sort_after_numeric(self):
        """無數字的檔名排在所有含數字的後面"""
        names = ["image2.jpeg", "cover.png", "image1.jpeg"]
        sorted_names = sorted(names, key=_image_sort_key)
        assert sorted_names == ["image1.jpeg", "image2.jpeg", "cover.png"]

    def test_extracts_first_number_when_multiple(self):
        """檔名含多個數字 → 用第一個做排序鍵"""
        # 'a3_b1.jpeg' 用 3,'a2_b5.jpeg' 用 2 → a2 在前
        assert _image_sort_key("a2_b5.jpeg") < _image_sort_key("a3_b1.jpeg")


# ═══════════════════════════════════════════════════════════════════════════
# extract_images_from_docx 端到端:確認從 zip 解出後是 natural sort
# ═══════════════════════════════════════════════════════════════════════════

class TestExtractImagesNaturalSort:
    """端到端測試:模擬一個 docx(實際上是 zip)裡含多張圖,
    驗證 extract_images_from_docx 用 natural sort 而非 lexical sort 回傳。
    """

    def _make_fake_docx(self, tmp_path: Path, image_names: list[str]) -> Path:
        """製造一個簡易 docx(zip),內含指定圖片名(content 為佔位 bytes)"""
        docx = tmp_path / "fake.docx"
        with zipfile.ZipFile(docx, "w") as z:
            # docx 內部結構慣例:圖片放在 word/media/
            # 故意以「lexical 錯亂」順序寫入,確認 extract 不依賴 zip 內順序
            for name in image_names:
                z.writestr(f"word/media/{name}", b"fake-image-bytes-" + name.encode())
        return docx

    def test_extract_returns_natural_sort_order(self):
        """docx 含 image1, image10, image2 → 取出順序為 image1, image2, image10"""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            # 故意以 lexical 反序寫入,測試 sort key 真的在作用
            docx = self._make_fake_docx(tmp_path, [
                "image10.jpeg", "image2.jpeg", "image1.jpeg", "image11.jpeg",
            ])
            images = extract_images_from_docx(docx)
            names = [name for name, _ in images]
            assert names == ["image1.jpeg", "image2.jpeg", "image10.jpeg", "image11.jpeg"]

    def test_extract_skips_non_image_files(self):
        """zip 內非圖檔(word/document.xml 等)應被忽略"""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            docx = tmp_path / "fake.docx"
            with zipfile.ZipFile(docx, "w") as z:
                z.writestr("word/document.xml", b"<xml/>")
                z.writestr("word/media/image1.jpeg", b"img1")
                z.writestr("word/media/image2.png", b"img2")
                z.writestr("[Content_Types].xml", b"<types/>")
            images = extract_images_from_docx(docx)
            names = [name for name, _ in images]
            assert names == ["image1.jpeg", "image2.png"]
