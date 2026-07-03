"""從勞動契約圖片擷取「雇主資料」(名稱中/英、地址中/英、電話)。

用 Google Vision DOCUMENT_TEXT_DETECTION 取全文後,以內容啟發式解析。
與許可證流程(run_google_vision 抓 4 碼號碼)分開,互不影響。

難點與對策:
- 版面是中印尼雙語兩欄表,Vision 逐欄序列化常錯行 → 不依賴嚴格「標籤緊接值」,
  改用內容特徵(中日韓字元、地址關鍵字、電話重複)判斷。
- 仲介公司 block 在標題「勞動契約」之上,會混入公司名/地址 → 從雇主標籤處才開始解析。
- 紅章常蓋在名稱上 → 名稱信心較低,呼叫端應標人工複核。
"""
from __future__ import annotations

import re

CJK        = re.compile(r"[一-鿿]")
# 台灣電話:0 開頭,可含一個 '-'。前後不接其他數字,避免抓到統編等長數字。
PHONE      = re.compile(r"(?<!\d)(0\d{1,3}-?\d{6,8})(?!\d)")
CN_ADDR    = re.compile(r"[縣市].{0,20}?[路街道段巷弄號樓]")
EN_ADDR_KW = re.compile(
    r"\b(No\.|Rd\.|St\.|Dist\.|Lane|Alley|Sec\.|Village|Township|County|City|Taiwan)\b",
    re.I,
)
NAME_LABEL  = re.compile(r"(甲方名稱|雇主名[稱称]|以下簡稱)")
ID_LABEL    = re.compile(r"Nama\s*(Majikan|Perusahaan)", re.I)
ADDR_LABEL  = re.compile(r"(地址|Alamat)")
PHONE_LABEL = re.compile(r"(電話|Nomor\s*Telepon|傳真)", re.I)
LABEL_WORDS = re.compile(r"(甲方名稱|雇主名[稱称]|以下簡稱為?甲方|地址|電話|傳真)")
# 表格樣板/浮水印字,絕非雇主名稱
BOILERPLATE = re.compile(
    r"SEKTOR|INFORMAL|FORMAL|SELANJUTNYA|DISEBUT|PIHAK|PERTAMA|PERJANJIAN"
    r"|MAJIKAN|DENGAN|PEKERJA|ANTARA|Perawat|Care\s*Giver|Agency"
    r"|Nama|Perusahaan|Alamat|Nomor|Telepon",
    re.I,
)


def _has_cjk(s: str) -> bool:
    return bool(CJK.search(s))


def extract_employer_fields(text: str) -> dict:
    """從 Vision 全文擷取雇主欄位。回傳 dict(缺項為空字串)。"""
    lines = [l.strip() for l in text.splitlines() if l.strip()]

    # ── 電話:出現兩次者為雇主(電話 + Nomor Telepon 各一);否則取最常見 ──
    counts: dict[str, int] = {}
    for l in lines:
        for m in PHONE.findall(l):
            counts[m] = counts.get(m, 0) + 1
    phone = ""
    dup = [p for p, c in counts.items() if c >= 2]
    if dup:
        phone = max(dup, key=lambda p: counts[p])
    elif counts:
        phone = max(counts, key=lambda p: counts[p])

    # ── 定位雇主區塊起點(跳過標題之上的仲介 block)──
    start = 0
    for i, l in enumerate(lines):
        if NAME_LABEL.search(l):
            start = i
            break
    else:
        for i, l in enumerate(lines):
            if "MAJIKAN DENGAN" in l.upper():
                start = i + 1
                break
    seg = lines[start:]

    # ── 地址:中文靠行政區+街道 pattern;英文靠地址關鍵字 ──
    addr_cn = addr_en = ""
    for idx, l in enumerate(seg):
        if not addr_cn and _has_cjk(l) and CN_ADDR.search(l):
            addr_cn = LABEL_WORDS.sub("", l).lstrip(":： ").strip()
        if not addr_en and not _has_cjk(l) and EN_ADDR_KW.search(l) and len(l) > 12:
            en = l
            # 併入下一行的 R.O.C. 之類接續
            if idx + 1 < len(seg) and re.fullmatch(r"[A-Za-z.()\s]{2,8}", seg[idx + 1]):
                en = en + " " + seg[idx + 1]
            addr_en = re.sub(r"^Alamat\s*[:：]?\s*", "", en, flags=re.I).strip()

    # ── 名稱:雇主區塊前段,排除地址/標籤,依中日韓/拉丁分中英 ──
    name_cn = name_en = ""
    for l in seg[:9]:
        if ADDR_LABEL.search(l) or PHONE_LABEL.search(l) or l in (addr_cn, addr_en):
            continue
        # 英文名:優先取 Nama 標籤後的值(同行),否則取整行(去冒號前綴)
        if not name_en:
            cand_en = re.sub(r"^.*Nama\s*(Majikan|Perusahaan)\s*[:：]?", "", l, flags=re.I)
            if cand_en == l:                       # 該行無 Nama 標籤
                cand_en = re.sub(r".*[:：]", "", l)
            cand_en = cand_en.strip()
            if (cand_en and not _has_cjk(cand_en) and re.search(r"[A-Za-z]", cand_en)
                    and not BOILERPLATE.search(cand_en) and not EN_ADDR_KW.search(cand_en)
                    and len(cand_en) > 2):
                name_en = cand_en
        # 中文名:有中日韓字、非地址、去標籤字
        if not name_cn:
            core = re.sub(r".*[:：]", "", l).strip()
            if _has_cjk(core) and not CN_ADDR.search(core):
                cand = LABEL_WORDS.sub("", core)
                cand = re.sub(r"Nama.*", "", cand, flags=re.I).strip(" ()（）:：")
                if cand and _has_cjk(cand):
                    name_cn = cand

    return {
        "雇主名稱_中": name_cn,
        "雇主名稱_英": name_en,
        "地址_中": addr_cn,
        "地址_英": addr_en,
        "電話": phone,
    }


def extract_employer(img_path: str, client=None) -> dict:
    """對單張圖跑 Vision DOCUMENT_TEXT_DETECTION,再擷取雇主欄位。"""
    from google.cloud import vision as gvision

    if client is None:
        from pipeline import get_vision_client
        client = get_vision_client()
    with open(img_path, "rb") as f:
        content = f.read()
    resp = client.document_text_detection(image=gvision.Image(content=content))
    if resp.error.message:
        raise RuntimeError(f"Vision API 錯誤: {resp.error.message}")
    text = resp.full_text_annotation.text if resp.full_text_annotation else ""
    return extract_employer_fields(text)
