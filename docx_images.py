# -*- coding: utf-8 -*-
"""docx 內嵌圖的讀取與圖序(見 CONTEXT.md「圖序」)。

許可證掃描(pipeline)與雇主擷取(employer_extract)都依賴這裡,兩側不互相
import。以前兩側各有一份,副檔名集合已經分歧:.tif/.bmp 只有許可證那側收,
於是同一份文件 B 欄正常、雇主擷取卻看不到契約頁,改選次高分的頁讀出錯的
H~O,沒有任何地方報錯(#5)。這種錯只能靠「只有一份」擋住。

本模組是 leaf:只依賴標準函式庫。
"""
from __future__ import annotations

import re
import zipfile
from pathlib import Path

# 含 .gif:部分 docx 把契約頁存成 GIF(image2.gif/image3.gif),漏收會讓
# 整份只剩護照 JPEG、契約頁被靜默略過(32254-32262 教訓)。PIL 原生可讀,
# 後續 .convert("RGB") 會處理調色盤/灰階,Tesseract 與 Vision 都吃得下。
IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".tif", ".gif"})


def image_sort_key(name: str) -> tuple[int, int, str]:
    """圖序的排序鍵:image1 < image2 < ... < image10。

    docx 內的圖通常命名為 image{N}.{ext}。預設的 lexical sort 會把
    image10.jpeg 排在 image2.jpeg 之前(因為字串 '1' < '2'),導致
    可能先掃到第 10 頁的舊資料而非第 2 頁的當前資料。

    從檔名抽出第一個數字當排序鍵:
      - 有數字 → (0, 數字值, 原名)
      - 無數字 → (1, 0, 原名) (排在所有有數字的後面)
    """
    m = re.search(r"\d+", name)
    if m:
        return (0, int(m.group()), name)
    return (1, 0, name)


def image_index(name: str) -> int:
    """imageN.jpeg → N(圖序的絕對序號)。檔名沒有數字回傳 0,不屬於任何一個序號位置。"""
    key = image_sort_key(name)
    return key[1] if key[0] == 0 else 0


def read_images(docx_path: Path | str) -> list[tuple[str, bytes]]:
    """docx 的所有內嵌圖 (檔名, bytes),**依圖序回傳**。

    只讀 word/media/ 底下副檔名在 IMAGE_EXTENSIONS 的項目;不依賴 zip 內的
    存放順序。
    """
    with zipfile.ZipFile(docx_path, "r") as z:
        images = [(Path(n).name, z.read(n)) for n in z.namelist()
                  if n.startswith("word/media/") and Path(n).suffix.lower() in IMAGE_EXTENSIONS]
    images.sort(key=lambda x: image_sort_key(x[0]))
    return images
