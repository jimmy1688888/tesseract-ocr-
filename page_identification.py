# -*- coding: utf-8 -*-
"""認頁:決定一份 docx 每張內嵌圖是哪一頁(CONTEXT「認頁 / 認頁結果」,#77)。

  PageIdentifier.identify(docx)   一批共用的認頁來源 → PageIdentification
  PageIdentification              認頁結果:契約頁、次頁、切結書頁都從它推出

認頁只看印刷體上緣、用 Tesseract(免費);讀欄位值才用 Vision,那是呼叫端的事。
雇主擷取、次頁台仲(反查救援)、切結書頁備援、錄契約頁核對值都收認頁結果,
不再自己開 docx 評分。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, NamedTuple

from docx_images import read_images
from pixels import crop, deink_red_stamp, open_upright
from tesseract_ocr import read_text

# 上緣掃描的範圍(圖高的比例):標題與雇主欄位標籤都在這一段。
TOP_FRACTION = 0.35
# 認定為契約頁的最低分。低於此值視為「頁面身分不明」,呼叫端各自決定退路。
MIN_CONTRACT_SCORE = 2
# 全零分時的退路:實測慣例上契約頁多在第 2 張(CONTEXT「圖序」)。
_CONTRACT_PAGE_BY_CONVENTION = "image2."
# 契約頁分數的權重:欄位標籤只出現在雇主資料表,樣板文字簽名頁也有(見 _FORM_LABELS 上方註解)。
FORM_LABEL_WEIGHT = 2
PROSE_MARKER_WEIGHT = 1
# 雇主姓名頁的候選數:切結書第一頁之後最多幾頁(見 pledge_name_pages)。
PLEDGE_NAME_PAGE_COUNT = 2

# 契約頁特徵字:用來從 docx 多張圖中辨識「哪一張是勞動契約頁」。
# 分兩類加權計分,以區辨「雇主資料表」與「簽名頁」:
#   FORM_LABELS(權重 2):只出現在雇主資料表的欄位標籤(Nama Majikan/
#     Perusahaan/Pemberi Kerja、Alamat、Nomor Telepon、MOL License)。
#     這正是我們要抽資料的那頁。
#   PROSE_MARKERS(權重 1):樣板文字,簽名頁的長段 prose 也密集出現
#     (PERJANJIAN KERJA、PIHAK PERTAMA、SELANJUTNYA、MAJIKAN DENGAN)。
# 只數總命中會讓 prose 密集的簽名頁壓過真正的資料表(32262 農業契約教訓:
# image3 簽名頁 prose 命中 4 > image2 資料表 2 → 選錯頁、名稱抽到樣板字)。
# 加權後資料表(欄位標籤×2)穩定勝出;標題被紅章毀時仍有 FORM 標籤撐分。
# 機構類(養護機構/醫院)樣板的雇主名稱標籤是「Nama Panti Jompo/Rumah Sakit」,
# 而且電話標籤落在上緣 35% 之外 —— 不認得它,資料表只剩 1 個 FORM 標籤,輸給
# 仲介區塊也有 Alamat 的次頁(33703 教訓:image2 4 分敗給 image3 5 分,#27)。
# 錨定整個標籤、一個標籤只算一次:單獨比對「Rumah Sakit」(醫院)會讓住院、
# 醫療費用條款的條文頁也拿 FORM 加權(33703 的 image4 就因此多 2 分)。
# 修正後 33703 是 6 比 5,只贏 1 分 —— 那是真實差距,不要靠重複計分灌大。
#
# 只列印尼文/英文:上緣 OCR 用 ind+eng,沒有載 chi_tra,中文標籤讀不出來。
_FORM_LABELS = re.compile(
    r"Nama\s*Majikan|Nama\s*Perusahaan|Pemberi\s*Kerja|Nomor\s*Telepon"
    r"|Nama\s*Panti\s*Jompo(?:\s*/\s*Rumah\s*Sakit)?"
    r"|Alamat|MOL\s*License",
    re.I,
)
_PROSE_MARKERS = re.compile(
    r"PERJANJIAN\s*KERJA|MAJIKAN\s*DENGAN|PIHAK\s*PERTAMA|SELANJUTNYA",
    re.I,
)

# 切結書(Surat Pernyataan Biaya dan Gaji)第一頁的標題。用來找雇主姓名的第二個
# 來源:那個欄位實測 6/6 都在「切結書第一頁的下一頁」(見 ADR-0006)。
#
# 刻意**不**用 `Majikan R.O.C`(欄位本身的標籤)當錨:32538/32554 的那一頁,
# Tesseract 在 35%/55%/70% 任何裁切範圍都讀不出這串字,而 Vision 讀得到。認頁
# 只能靠 Tesseract 讀得穩的東西 —— 印刷體大標題讀得穩,夾在表格線與紅章之間的
# 欄位標籤讀不穩。認頁與取值因此分工:Tesseract 認頁(免費),Vision 取值。
_PLEDGE_FRONT = re.compile(r"Surat\s*Pernyataan", re.I)


# 一張圖 → 上緣讀到的字。這是認頁唯一的外部依賴;測試傳假的進來,不跑 Tesseract。
TopText = Callable[[bytes], str]


def tesseract_top_text(image_bytes: bytes) -> str:
    """預設的 TopText:裁上緣、轉正後送 Tesseract。"""
    return read_text(crop(open_upright(image_bytes), (0.0, 0.0, 1.0, TOP_FRACTION)))


class PageMarks(NamedTuple):
    contract_score: int
    is_pledge_front: bool


def page_marks(image_bytes: bytes, top_text: TopText = tesseract_top_text) -> PageMarks:
    """一次上緣掃描,同時得出契約頁分數(FORM ×2 + PROSE ×1)與「是不是切結書第一頁」。

    兩件事共用同一段文字:分開跑會讓每份 docx 的 Tesseract 次數翻倍,而它們要的輸入
    完全一樣。加權讓「雇主資料表」壓過只有 prose 的簽名頁(見 _FORM_LABELS 上方註解)。

    **先去紅章再掃**:資料表那頁的欄位標籤常被大紅章整片蓋住,原圖直送 Tesseract
    會一個 FORM 標籤都讀不到 → 該頁 0 分,輸給只有 prose 的次頁,契約頁選錯
    (32426/32427 教訓:image2 資料表 0 分敗給 image3 次頁 2 分,結果雇主欄位
    抽到合約條文,且因「契約頁被判成最後一張」連次頁台仲都完全沒掃)。
    去紅章後同樣兩張 image2 由 0 → 6/7 分穩定勝出。前處理失敗則退回原圖,
    不讓評分因此中斷。
    """
    try:
        image_bytes = deink_red_stamp(image_bytes)
    except Exception:
        pass
    text = top_text(image_bytes)
    score = (FORM_LABEL_WEIGHT * len(_FORM_LABELS.findall(text))
             + PROSE_MARKER_WEIGHT * len(_PROSE_MARKERS.findall(text)))
    return PageMarks(score, bool(_PLEDGE_FRONT.search(text)))


@dataclass(frozen=True)
class IdentifiedPage:
    name: str                      # 圖檔名(word/media 底下),談位置一律用它
    image_bytes: bytes
    contract_score: int
    is_pledge_front: bool


class ContractPage(NamedTuple):
    """選中的契約頁;found=False 是未達門檻的候選頁(呼叫端不送 Vision、只留截圖)。"""
    page: IdentifiedPage
    found: bool


@dataclass(frozen=True)
class PageIdentification:
    """一份 docx 的認頁結果:每張內嵌圖,依圖序。"""
    docx_path: str
    pages: tuple[IdentifiedPage, ...]

    def contract_page(self) -> ContractPage | None:
        """最高分頁(同分取圖序在前);未達門檻仍回它當候選;全零分退 image2,
        沒有 image2 就用第一張。沒有圖回 None。"""
        if not self.pages:
            return None
        best = max(self.pages, key=lambda p: p.contract_score)
        found = best.contract_score >= MIN_CONTRACT_SCORE
        if best.contract_score == 0:
            best = next((p for p in self.pages
                         if p.name.startswith(_CONTRACT_PAGE_BY_CONVENTION)), best)
        return ContractPage(best, found)

    def next_page(self) -> IdentifiedPage | None:
        """契約頁的下一張(台仲區塊)。契約頁找不到就不猜 —— 次頁是哪張也無從談起;
        契約頁是最後一張也沒有。"""
        contract = self.contract_page()
        if contract is None or not contract.found:
            return None
        i = self.pages.index(contract.page) + 1
        return self.pages[i] if i < len(self.pages) else None

    def pledge_name_pages(self) -> tuple[IdentifiedPage, ...]:
        """雇主姓名可能所在的頁:切結書第一頁之**後**最多 PLEDGE_NAME_PAGE_COUNT 頁。

        實測 6 份(32503/32510/32538/32554/32572/32574)全部命中第一候選頁,所以實際
        只會花一次 Vision。留第二頁是因為切結書長度 2~3 頁不定(ADR-0005),而雇主欄
        與許可證**不一定同頁** —— 32538/32554 的許可證在 image6、雇主欄在 image5,
        所以不能拿許可證命中的圖來當這一頁(這條假設一度成立過 4/4,是巧合)。
        """
        for i, page in enumerate(self.pages):
            if page.is_pledge_front:
                return self.pages[i + 1:i + 1 + PLEDGE_NAME_PAGE_COUNT]
        return ()


class PageIdentifier:
    """一批共用的認頁來源:同一份 docx 只評一次分(ADR-0009「認頁結果的備忘」)。

    評分含 Tesseract(每份 docx 數秒),而反查救援讀次頁、雇主擷取讀契約頁都要同一份
    認頁結果;救援很常見,各自重評太貴。只記分數不記 bytes:一份 docx 的圖動輒數 MB,
    整批留著會吃掉大量記憶體,而重解 zip 遠比 Tesseract 便宜。壽命是一批,由 main 建。

    一批之內不偵測 docx 被換掉(以完整路徑為鍵):一批跑的是啟動時那組檔案,換檔要重跑。
    """

    def __init__(self, top_text: TopText = tesseract_top_text):
        self._top_text = top_text
        self._marks: dict[str, dict[str, PageMarks]] = {}

    def identify(self, docx_path: str | Path) -> PageIdentification:
        """讀不開的 docx 照常拋例外,由呼叫端當成那一份的失敗。"""
        images = read_images(docx_path)
        key = str(Path(docx_path).resolve())
        marks = self._marks.get(key)
        if marks is None:
            marks = {name: page_marks(raw, self._top_text) for name, raw in images}
            self._marks[key] = marks
        return PageIdentification(str(docx_path), tuple(
            IdentifiedPage(name, raw, *marks[name]) for name, raw in images))
