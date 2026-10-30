# -*- coding: utf-8 -*-
"""許可證判讀:一張圖的 image bytes → 一次判讀(CONTEXT.md「ROI 掃描」「多數票」「分層掃描」)。

不碰硬碟、不碰網路、不改全域:OCR 由參數注入(預設 tesseract),裁切圖只回傳
「要寫什麼」(Crop),寫出由 permit_scan.run_scan 做(#18)。所以多數票、交叉比對、
擴掃決策都能用假 OCR 直接測(#19);以前只能靠 monkeypatch 模組屬性。

  scan_image_large / scan_image_mol_only   一張圖 → Reading(ScanResult, crops)
  read_large_docx                           一份 large docx 的分層掃描(ADR-0005)
  scan_docx                                 一份 docx 的圖 → 它在 matches.csv 的列(#57)

讀值不在這裡判斷:門檻、small 的 winner、要不要送 Vision 只在送審決策(ADR-0013)。

一張圖的讀值整理成 matches.csv 的一列(ScanResult,在 matches_csv);
送審決策(一份 docx 一個決定)在 submission_decision。

package 的四塊(ADR-0014;依賴單向 docx → tiers → image → ocr):
  ocr     Tesseract 設定與呼叫(Ocr 與預設的 tesseract)
  image   一張圖 → 一次判讀(ROI、多數票、裁切圖)
  tiers   分層掃描
  docx    一份 docx → 它的列(small / large)
這裡只轉發四塊的公開名字;別人的名字(ScanResult、find_permits …)到主人那裡取。
"""
from .docx import (
    NO_HIT_REVIEW_LARGE, NO_HIT_REVIEW_SMALL, SMALL_DOCX_THRESHOLD, DocxScan,
    classify_by_count, scan_docx, unscannable)
from .image import (
    ROI_REGIONS, Crop, Reading, Vote, majority_vote, scan_image_large, scan_image_mol_only)
from .ocr import (
    FALLBACK_CONFIGS, PERMIT_VOTE_CONFIGS, SCAN_CONFIGS, TESS_LANG, WHITELIST_LATIN, Ocr,
    build_tess_config, ocr_with_conf, tesseract)
from .tiers import (
    TIER1_IMAGES, LargeDocxReading, ReadImage, TierOutcome, has_permit_vote, read_large_docx,
    split_tiers)
