# -*- coding: utf-8 -*-
"""許可證掃描:一批 docx → matches.csv、裁切圖、全無命中的原圖(CONTEXT.md「許可證掃描」)。

判讀(每張圖讀到什麼、要寫哪些列與裁切圖)在 permit_reading,不碰檔案系統;這裡只做
寫檔那一層:讀 docx 的圖、寫裁切圖與原圖、寫 matches.csv,並計數與 log(#18、#61)。

  run_scan           一批 → matches.csv(回傳路徑)
  read_docx_images   一份 docx 的內嵌圖;打不開回傳 None(#47)

Tesseract 執行檔路徑不在這裡設(仍由 pipeline import 時設定,候選 AG)。
"""
from __future__ import annotations

import logging
import time
import zipfile
import zlib
from pathlib import Path
from typing import Callable

# docx 內嵌圖與圖序只有一份,與 employer_extract 共用(#5)。
import docx_images
import matches_csv
from batch import Batch
from matches_csv import DocxClass, Roi
from permit_reading import (
    TIER1_IMAGES, Crop, Reading, TierOutcome, scan_docx, scan_image_large, scan_image_mol_only)
# 像素前處理(轉正、裁切)也只有一份,與 employer_extract 共用(#16)。
from pixels import crop, open_upright

logger = logging.getLogger(__name__)


def read_docx_images(docx_path: Path) -> list[tuple[str, bytes]] | None:
    """docx 的內嵌圖;打不開回傳 None,不拋。

    一份壞檔不能讓整批中斷(#47):它照樣要在 matches.csv 與 Sheets 各有一列。
    「打不開」= 不是有效的 zip(BadZipFile,含 CRC 錯),或目錄完好、壓縮資料損壞
    (zlib.error,讀到那張圖才炸)。檔案被鎖、權限不足不算 —— 那不是「不是有效的
    docx 檔」,照舊拋出。
    """
    try:
        return docx_images.read_images(docx_path)
    except (zipfile.BadZipFile, zlib.error):
        return None


def _save_review_images(review_dir: Path, docx_path: Path,
                        images: list[tuple[str, bytes]]) -> list[str]:
    """把原圖存到人工比對資料夾，回傳已存檔名。

    用於「全無命中」的 docx：留下關鍵頁原圖，人工可直接開來對照缺漏原因。
    存哪幾張由 permit_reading.scan_docx 決定;存整張原圖（非 ROI 裁切）。
    """
    review_dir.mkdir(parents=True, exist_ok=True)
    saved: list[str] = []
    for name, data in images:
        dst = review_dir / f"{docx_path.stem}_{name}"
        dst.write_bytes(data)
        saved.append(dst.name)
    return saved


def _write_crops(out_dir: Path, image_bytes: bytes, crops: list[Crop]) -> None:
    """把一張圖判讀出的裁切圖寫到這一批的輸出目錄(路徑掃描端已填進列,#61)。

    掃描只決定要寫什麼;寫檔(與它需要的解碼、轉正)只在這裡(#18)。
    """
    if crops:
        upright = open_upright(image_bytes)
        for c in crops:
            path = out_dir / c.path
            path.parent.mkdir(parents=True, exist_ok=True)
            crop(upright, c.roi).save(path)


def run_scan(batch: Batch, image_filter: str = "",
             roi_filter: str = "", full_scan: bool = False, *,
             read_large: Callable[..., Reading | None] = scan_image_large,
             read_small: Callable[..., Reading] = scan_image_mol_only) -> Path:
    """掃這一批的 docx，輸出 matches.csv，回傳 csv 路徑。

    每份 docx 只讀一次;它要寫哪些列由 permit_reading.scan_docx 決定,這裡只做
    I/O(寫裁切圖、比對原圖、matches.csv)、計數與 log。
    所有產出（matches.csv、裁切圖、無命中複查圖）都寫在 batch.output_dir 底下。

    image_filter：若非空，配合單一 docx 只處理該檔名的圖（對應 --image）。
                  對不到要在呼叫前擋下(main 的 _image_filter_miss);這裡遇到就拋。
    roi_filter  ：若非空，只掃指定的 ROI（對應 --roi）。
    full_scan   ：True 則關掉分層，每張圖都掃（對應 --full-scan）。
    read_large / read_small：一張圖 → 判讀,預設 permit_reading 的 Tesseract 版本。
                  測試傳假的進來,就不必跑 Tesseract。
    """
    out_dir = batch.output_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = batch.matches_csv

    total = hits = upper_hits = lower_hits = 0
    large_docs = tier1_docs = expanded_docs = 0
    t0 = time.time()

    with matches_csv.writer(csv_path) as writer:
        for docx_path in batch.docx_files:
            docx_name = docx_path.name
            images = read_docx_images(docx_path)
            scan = scan_docx(
                docx_name, images,
                read_large=lambda name, data: read_large(docx_name, name, data,
                                                         roi_filter=roi_filter),
                read_small=lambda name, data: read_small(docx_name, name, data),
                full_scan=full_scan, image_filter=image_filter)

            image_bytes = dict(images or [])
            for result, crops in scan.rows:
                _write_crops(out_dir, image_bytes.get(result.image_name, b""), crops)
                writer.write(result)
                hits += result.hit
                upper_hits += result.hit_roi is Roi.PERMIT_UPPER
                lower_hits += result.hit_roi is Roi.PERMIT_LOWER

            total += scan.scanned
            if scan.docx_class is DocxClass.LARGE:
                large_docs += 1
                tier1_docs += scan.tier is TierOutcome.第一層命中
                expanded_docs += scan.tier is TierOutcome.擴掃

            if scan.review_images:
                saved = _save_review_images(batch.no_hit_review_dir, docx_path,
                                            scan.review_images)
                logger.info(
                    f"  ⚠ {docx_name} {scan.docx_class}全無命中"
                    f"，存原圖供比對 {saved}"
                )

    elapsed = round(time.time() - t0, 1)
    logger.info(f"掃描完成：{total} 張圖，命中 {hits} 張，耗時 {elapsed}s")
    if upper_hits + lower_hits > 0:
        logger.info(f"  permit 上半命中：{upper_hits} 張 / 下半命中：{lower_hits} 張")
    if large_docs:
        # 這行是分層規則的體檢報告：命中率一旦崩掉,代表文件版面變了、第一層
        # 押錯位置,此時每份都會擴掃、總時間反而比全掃更長(ADR-0005)。
        mode = "全掃模式" if full_scan else f"第一層{sorted(TIER1_IMAGES)}"
        logger.info(
            f"  {mode}：{tier1_docs}/{large_docs} 份一次命中，"
            f"擴掃 {expanded_docs} 份"
        )
    return csv_path
