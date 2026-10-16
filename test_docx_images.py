# -*- coding: utf-8 -*-
"""docx 內嵌圖與圖序(docx_images,#5)。

涵蓋:
  TestImageSortKey             — image_sort_key 的排序語意
  TestReadImagesNaturalSort    — read_images 端到端順序
  TestBothSidesSeeSameImages   — 許可證掃描與雇主擷取看到同一組圖(真實素材)
  TestTifContractPage          — 契約頁存成 .tif 時雇主擷取仍認得到(真實素材)

業務動機:同一份 docx 內的 image1, image2, ... image10 應該嚴格依數字序處理。
預設的 lexical sort 會把 image10 排在 image2 之前(因為字串 '1' < '2'),
可能導致先掃到後面頁的舊資料。
"""

import zipfile
import tempfile
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

import employer_extract as ee
from docx_images import IMAGE_EXTENSIONS, image_index, image_sort_key, read_images
from real_samples import REAL_DOCX, needs_samples, sample


# ═══════════════════════════════════════════════════════════════════════════
# image_sort_key:natural sort 工具
# ═══════════════════════════════════════════════════════════════════════════

class TestImageSortKey:
    """natural sort 工具:讓 image1 < image2 < ... < image10。"""

    def test_image1_sorts_before_image2(self):
        assert image_sort_key("image1.jpeg") < image_sort_key("image2.jpeg")

    def test_image2_sorts_before_image10(self):
        """**核心 fix**:lexical sort 下 'image10' < 'image2',要被本工具修正"""
        assert image_sort_key("image2.jpeg") < image_sort_key("image10.jpeg")

    def test_image10_sorts_before_image11(self):
        assert image_sort_key("image10.jpeg") < image_sort_key("image11.jpeg")

    def test_sorting_full_list_matches_numeric_order(self):
        """整批排序結果符合 image1, image2, ..., image10, image11"""
        names = ["image10.jpeg", "image2.jpeg", "image11.jpeg",
                 "image1.jpeg", "image3.jpeg"]
        sorted_names = sorted(names, key=image_sort_key)
        assert sorted_names == [
            "image1.jpeg", "image2.jpeg", "image3.jpeg",
            "image10.jpeg", "image11.jpeg",
        ]

    def test_non_numeric_names_sort_after_numeric(self):
        """無數字的檔名排在所有含數字的後面"""
        names = ["image2.jpeg", "cover.png", "image1.jpeg"]
        sorted_names = sorted(names, key=image_sort_key)
        assert sorted_names == ["image1.jpeg", "image2.jpeg", "cover.png"]

    def test_extracts_first_number_when_multiple(self):
        """檔名含多個數字 → 用第一個做排序鍵"""
        # 'a3_b1.jpeg' 用 3,'a2_b5.jpeg' 用 2 → a2 在前
        assert image_sort_key("a2_b5.jpeg") < image_sort_key("a3_b1.jpeg")


# ═══════════════════════════════════════════════════════════════════════════
# read_images 端到端:確認從 zip 解出後是 natural sort
# ═══════════════════════════════════════════════════════════════════════════

class TestReadImagesNaturalSort:
    """端到端測試:模擬一個 docx(實際上是 zip)裡含多張圖,
    驗證 read_images 用 natural sort 而非 lexical sort 回傳。
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
            images = read_images(docx)
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
            images = read_images(docx)
            names = [name for name, _ in images]
            assert names == ["image1.jpeg", "image2.png"]


    def test_every_supported_extension_is_read(self, tmp_path):
        """副檔名集合只有一份;七種都讀得到(.tif/.bmp 曾經只有一側收)。"""
        docx = tmp_path / "all.docx"
        with zipfile.ZipFile(docx, "w") as z:
            for i, ext in enumerate(sorted(IMAGE_EXTENSIONS), start=1):
                z.writestr(f"word/media/image{i}{ext}", b"x")
        got = {Path(n).suffix for n, _ in read_images(docx)}
        assert got == set(IMAGE_EXTENSIONS)
        assert {".tif", ".tiff", ".bmp", ".gif"} <= got


class TestBothSidesSeeSameImagesSynthetic:
    """「兩側看到同一組圖」的合成版:沒有真實素材的機器上也會跑,不會 skip。

    七種副檔名各一張、故意打亂存放順序;雇主擷取看到的圖與圖序要和
    許可證掃描(docx_images.read_images)一模一樣。
    """

    def test_every_extension_reaches_employer_side(self, tmp_path):
        docx = tmp_path / "mixed.docx"
        exts = sorted(IMAGE_EXTENSIONS)
        with zipfile.ZipFile(docx, "w") as z:
            for i, ext in reversed(list(enumerate(exts, start=1))):
                z.writestr(f"word/media/image{i}{ext}", b"x")
        pipeline_side = [n for n, _ in read_images(docx)]
        assert len(pipeline_side) == len(exts)
        assert [n for n, _ in ee._sorted_images(str(docx))] == pipeline_side


class TestImageIndex:
    """圖序的絕對序號(ADR-0005 的第一層 {2,5,6} 靠它挑圖)。"""

    def test_numbered(self):
        assert image_index("image5.jpeg") == 5
        assert image_index("image10.tif") == 10

    def test_unnumbered_is_zero(self):
        assert image_index("cover.png") == 0


# ═══════════════════════════════════════════════════════════════════════════
# 真實素材:samples/docs/(使用者提供,含個資,不進版控;不在時 skip)
# ═══════════════════════════════════════════════════════════════════════════



def _as_tif(src: Path, dst: Path, media_name: str) -> Path:
    """複製一份真實 docx,把其中一張內嵌圖轉成 TIFF 重新打包(只寫 dst)。

    圖片內容仍是真實的,只換格式;rels 與 [Content_Types].xml 一併改,
    做出來的是一份 Word 開得起來的 docx,不是只有 word/media 的空殼。
    """
    with zipfile.ZipFile(src) as z:
        if f"word/media/{media_name}" not in z.namelist():
            raise ValueError(f"{src.name} 裡沒有 {media_name},做不出 .tif 變體")
    stem = Path(media_name).stem
    tif_name = f"{stem}.tif"
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            name = item.filename
            if name == f"word/media/{media_name}":
                buf = BytesIO()
                Image.open(BytesIO(data)).save(buf, format="TIFF")
                data, name = buf.getvalue(), f"word/media/{tif_name}"
            elif name == "word/_rels/document.xml.rels":
                data = data.replace(f"media/{media_name}".encode(), f"media/{tif_name}".encode())
            elif name == "[Content_Types].xml" and b'Extension="tif"' not in data:
                data = data.replace(
                    b"<Default ", b'<Default Extension="tif" ContentType="image/tiff"/><Default ', 1)
            zout.writestr(name, data)
    return dst


@needs_samples
class TestBothSidesSeeSameImages:
    """許可證掃描(pipeline)與雇主擷取(employer_extract)看到同一組圖、同一個圖序。

    這才是擋住下一次分歧的東西:兩側曾各有一份副檔名集合,.tif/.bmp 只有一側收,
    於是同一份文件 B 欄正常、H~O 卻讀錯頁或留空,沒有任何地方報錯(#5)。
    """

    @staticmethod
    def _names(images):
        return [n for n, _ in images]

    @pytest.mark.parametrize("docx", REAL_DOCX, ids=lambda p: p.name)
    def test_real_docx(self, docx):
        assert self._names(ee._sorted_images(str(docx))) == self._names(read_images(docx))

    def test_tif_variant(self, tmp_path):
        with zipfile.ZipFile(REAL_DOCX[0]) as z:      # 一定要關:Windows 上會鎖住素材
            names = z.namelist()
        media = "image2.png" if "word/media/image2.png" in names else "image2.jpeg"
        tif = _as_tif(REAL_DOCX[0], tmp_path / "tif.docx", media)
        pipeline_side = self._names(read_images(tif))
        assert "image2.tif" in pipeline_side
        assert self._names(ee._sorted_images(str(tif))) == pipeline_side


@needs_samples
class TestTifContractPage:
    """契約頁存成 .tif 時,雇主擷取仍要選中它當契約頁(真的跑 Tesseract,約 10 秒)。

    修好前雇主擷取看不到 .tif,會改選次高分的頁 —— 不是 H~O 留空,而是從錯的頁
    讀出錯的雇主資料。33701 的契約頁是 image2.png(10 分,次高 image3 4 分),
    差距最大、判定最穩,所以用它。
    """

    pytestmark = pytest.mark.usefixtures("real_ocr")

    def test_tif_contract_page_is_picked(self, tmp_path):
        tif = _as_tif(sample("33701.docx"), tmp_path / "33701.docx", "image2.png")
        best = ee._best_scored_page(ee._scored_pages(str(tif)))
        assert best is not None
        assert best[1] == "image2.tif"
        assert best[0] >= ee.MIN_CONTRACT_SCORE
