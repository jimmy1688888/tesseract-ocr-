"""
prefilter.py
============
Docx 前置分類模組

根據 docx 內的圖片數量將文件分為：
  - "small"：圖片數 ≤ SMALL_DOCX_THRESHOLD（目前為 3 張）
             → 只掃 mol ROI；mol 無值時標記人工審查
  - "large"：圖片數 > SMALL_DOCX_THRESHOLD
             → 掃 mol + permit，並進行交叉比對

後續可在此模組加入更多篩選條件（影像品質、頁面類型⋯）。
"""

SMALL_DOCX_THRESHOLD = 3


def classify_by_count(image_count: int) -> str:
    """依圖片數量回傳分類標籤 'small' 或 'large'。"""
    return "small" if image_count <= SMALL_DOCX_THRESHOLD else "large"
