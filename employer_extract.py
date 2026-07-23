"""從勞動契約圖片擷取「雇主資料」(名稱中/英、地址中/英、電話)。

用 Google Vision DOCUMENT_TEXT_DETECTION 取全文後,以內容啟發式解析。
與許可證流程(run_google_vision 抓 4 碼號碼)分開,互不影響。

難點與對策:
- 版面是中印尼雙語兩欄表,Vision 逐欄序列化常錯行 → 不依賴嚴格「標籤緊接值」,
  改用內容特徵(中日韓字元、地址關鍵字、電話重複)判斷。
- 仲介公司 block 在標題「勞動契約」之上,會混入公司名/地址 → 從雇主標籤處才開始解析。
- 紅章常蓋在名稱上 → 名稱信心較低,呼叫端應標人工複核。
- **去紅章前處理(選項 A)**:送 Vision 前先 deink_red_stamp() 抽紅色通道並抹白
  明顯紅印章像素,讓黑字保留、印章變淡,提升被章尾污染的名稱/地址辨識率。
"""
from __future__ import annotations

import re
from io import BytesIO

import numpy as np
from PIL import Image, ImageOps

CJK        = re.compile(r"[一-鿿]")
# 台灣電話:0 開頭,可含一個 '-'。前後不接其他數字,避免抓到統編等長數字。
PHONE      = re.compile(r"(?<!\d)(0\d{1,3}-?\d{6,8})(?!\d)")
# 括號區碼格式:(04)7775-863、(02) 2371-7608。統一正規化成「區碼-號碼」。
PHONE_PAREN = re.compile(r"\((0\d{1,3})\)\s*(\d{3,4})\s*-?\s*(\d{3,4})(?!\d)")
# 國際格式 +886 / 886:國碼後省略市話/手機的前導 0(+886 2 2727 6999 = 02-27276999、
# +886 912 345 678 = 0912345678),抓國碼後的數字段(容分隔),交呼叫端補 0 還原國內號。
PHONE_INTL = re.compile(r"(?<!\d)\+?886[\s.-]*([\d][\d\s.-]{6,12}\d)")
# 電話標籤行(不含「傳真」,避免傳真號被當電話):優先取此類行上的號碼。
PHONE_LINE_LABEL = re.compile(r"電話|Nomor\s*Telepon", re.I)


def _phones_in(line: str) -> list[str]:
    """抓一行內的電話候選。括號區碼正規化為「04-7775863」;
    +886 國際格式還原為國內「0…」格式(去國碼、補前導 0);一般格式原樣。"""
    out = list(PHONE.findall(line))
    for area, a, b in PHONE_PAREN.findall(line):
        out.append(f"{area}-{a}{b}")
    for m in PHONE_INTL.finditer(line):
        digits = re.sub(r"\D", "", m.group(1)).lstrip("0")  # 去 886 後分隔與冗餘前導 0
        if 8 <= len(digits) <= 9:                  # 市話8 / 手機9(皆已省前導 0)
            out.append("0" + digits)               # 補回國內前導 0 → 與名冊格式一致
    return out
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


# 雇主 block ROI(相對比例):上緣 → Nomor Telepon 一帶,涵蓋整個雇主區(標題、
# 甲方名稱、地址中/英、電話/Nomor Telepon),下緣停在乙方標籤前,避免抓到勞工資料。
EMPLOYER_ROI = (0.0, 0.0, 1.0, 0.35)

# 契約頁特徵字:用來從 docx 多張圖中辨識「哪一張是勞動契約頁」。
# 分兩類加權計分,以區辨「雇主資料表」與「簽名頁」:
#   FORM_LABELS(權重 2):只出現在雇主資料表的欄位標籤(雇主/甲方名稱、
#     Nama Majikan/Perusahaan/Pemberi Kerja、Alamat、Nomor Telepon、
#     招募許可函號、MOL License)。這正是我們要抽資料的那頁。
#   PROSE_MARKERS(權重 1):樣板文字,簽名頁的長段 prose 也密集出現
#     (PERJANJIAN KERJA、PIHAK PERTAMA、SELANJUTNYA、MAJIKAN DENGAN、勞動契約)。
# 只數總命中會讓 prose 密集的簽名頁壓過真正的資料表(32262 農業契約教訓:
# image3 簽名頁 prose 命中 4 > image2 資料表 2 → 選錯頁、名稱抽到樣板字)。
# 加權後資料表(欄位標籤×2)穩定勝出;標題被紅章毀時仍有 FORM 標籤撐分。
_FORM_LABELS = re.compile(
    r"Nama\s*Majikan|Nama\s*Perusahaan|Pemberi\s*Kerja|Nomor\s*Telepon"
    r"|Alamat|MOL\s*License|招募許可|甲方名稱|雇主名[稱称]",
    re.I,
)
_PROSE_MARKERS = re.compile(
    r"PERJANJIAN\s*KERJA|MAJIKAN\s*DENGAN|PIHAK\s*PERTAMA|SELANJUTNYA|勞動契約",
    re.I,
)
# 向後相容:保留舊名(外部若引用),等同兩類聯集。
CONTRACT_MARKERS = re.compile(
    _FORM_LABELS.pattern + "|" + _PROSE_MARKERS.pattern, re.I)


def _has_cjk(s: str) -> bool:
    return bool(CJK.search(s))


def _natural_key(name: str):
    """image2 < image10 的自然排序鍵(避免字典序把 image10 排到 image2 前)。"""
    m = re.search(r"(\d+)", name)
    return (int(m.group(1)) if m else 1 << 30, name)


def crop_fraction(image_bytes: bytes, roi: tuple = EMPLOYER_ROI) -> bytes:
    """依相對比例 roi=(x1,y1,x2,y2) 裁切影像,回傳 PNG bytes。先做 EXIF 方向校正。"""
    img = ImageOps.exif_transpose(Image.open(BytesIO(image_bytes)).convert("RGB"))
    w, h = img.size
    x1, y1, x2, y2 = roi
    crop = img.crop((int(x1 * w), int(y1 * h), int(x2 * w), int(y2 * h)))
    buf = BytesIO()
    crop.save(buf, format="PNG")
    return buf.getvalue()


def _ensure_tesseract() -> None:
    """確保 pytesseract 指向 tesseract.exe(與 pipeline 相同路徑),未設則補上。"""
    import os
    import pytesseract
    cmd = pytesseract.pytesseract.tesseract_cmd
    if os.path.basename(cmd) == cmd:              # 尚未設定絕對路徑
        default = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
        if os.path.exists(default):
            pytesseract.pytesseract.tesseract_cmd = default


def _tesseract_top_text(image_bytes: bytes, top_frac: float = 0.35) -> str:
    """對影像上緣區塊跑輕量 Tesseract(供契約頁辨識用,不呼叫 Vision 省成本)。"""
    import pytesseract
    _ensure_tesseract()
    img = ImageOps.exif_transpose(Image.open(BytesIO(image_bytes)).convert("RGB"))
    w, h = img.size
    top = img.crop((0, 0, w, int(h * top_frac)))
    return pytesseract.image_to_string(top, lang="ind+eng", config="--psm 3")


def score_contract_page(image_bytes: bytes) -> int:
    """一張圖的契約頁分數:欄位標籤(FORM)×2 + 樣板(PROSE)×1。
    加權讓「雇主資料表」壓過只有 prose 的簽名頁(見 CONTRACT_MARKERS 註解)。"""
    text = _tesseract_top_text(image_bytes)
    return 2 * len(_FORM_LABELS.findall(text)) + len(_PROSE_MARKERS.findall(text))


def find_contract_image(images: list[tuple[str, bytes]],
                        min_score: int = 2) -> tuple[str, bytes] | None:
    """從 (檔名, bytes) 清單挑出契約頁:取特徵字命中數最高者,需 ≥ min_score。

    以 Tesseract 掃各圖上緣、計 CONTRACT_MARKERS 命中數;同分時取自然排序在前者。
    找不到(全都 < min_score)回傳 None。
    """
    best: tuple[str, bytes] | None = None
    best_score = 0
    for name, b in sorted(images, key=lambda x: _natural_key(x[0])):
        s = score_contract_page(b)
        if s > best_score:
            best_score, best = s, (name, b)
    return best if best_score >= min_score else None


def deink_red_stamp(image_bytes: bytes, *, whiten_thresh: int = 45,
                    min_red: int = 120, contrast: tuple[int, int] = (2, 98)) -> bytes:
    """去紅章前處理:抽紅色通道 + 抹白明顯紅印章像素,回傳 PNG bytes 供 Vision 使用。

    原理(紅印章 R 高、G/B 低;黑字三通道皆低;白紙三通道皆高):
      1. 以紅色通道為底 → 紅印章與白紙皆偏亮、黑字維持暗,印章自然變淡。
      2. 抹白遮罩:同時滿足「明顯偏紅(R - max(G,B) > whiten_thresh)」與
         「夠亮(R > min_red)」的像素設為 255。只清掉蓋在白底上的印章與章尾,
         不動壓在黑字上的暗紅像素(那類 R 偏低,避免把字筆畫一起抹掉)。
      3. 百分位對比拉伸 → 黑字更黑、背景更白,利於 Vision 二值化。

    參數皆可調;whiten_thresh 越小抹得越積極,min_red 越高越保護暗處筆畫。
    """
    img = Image.open(BytesIO(image_bytes)).convert("RGB")
    img = ImageOps.exif_transpose(img)          # 與 pipeline 一致的方向校正
    rgb = np.array(img).astype(np.int16)
    R, G, B = rgb[:, :, 0], rgb[:, :, 1], rgb[:, :, 2]

    gray = R.astype(np.uint8).copy()            # 紅通道為底
    red_mask = ((R - np.maximum(G, B)) > whiten_thresh) & (R > min_red)
    gray[red_mask] = 255                        # 白底上的紅印章/章尾抹白

    lo, hi = contrast
    low, high = np.percentile(gray, lo), np.percentile(gray, hi)
    if high > low:
        gray = np.clip((gray.astype(np.float32) - low) * 255.0 / (high - low),
                       0, 255).astype(np.uint8)

    buf = BytesIO()
    Image.fromarray(gray).save(buf, format="PNG")
    return buf.getvalue()


def extract_employer_fields(text: str) -> dict:
    """從 Vision 全文擷取雇主欄位。回傳 dict(缺項為空字串)。"""
    lines = [l.strip() for l in text.splitlines() if l.strip()]

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

    # ── 電話:只統計雇主區塊內。仲介框在區塊上方,其電話絕不能混入——
    #    雇主電話被格式/紅章毀掉時,全文統計的 fallback 會把仲介電話
    #    誤當雇主電話(32098 實例),故寧可留空也不取區塊外的號碼。
    #    優先「電話/Nomor Telepon 標籤行」上的號碼:農業契約地址含地號
    #    (0075-0010…)會被 PHONE regex 誤當電話,標籤行優先可避開(32262 教訓);
    #    標籤行取不到才退回全區塊。出現兩次者(電話+Nomor Telepon 各一)優先。
    labeled_counts: dict[str, int] = {}
    all_counts: dict[str, int] = {}
    for l in seg:
        on_label = bool(PHONE_LINE_LABEL.search(l))
        for m in _phones_in(l):
            all_counts[m] = all_counts.get(m, 0) + 1
            if on_label:
                labeled_counts[m] = labeled_counts.get(m, 0) + 1
    pool = labeled_counts or all_counts
    phone = ""
    dup = [p for p, c in pool.items() if c >= 2]
    if dup:
        phone = max(dup, key=lambda p: pool[p])
    elif pool:
        phone = max(pool, key=lambda p: pool[p])

    # ── 地址:中文靠行政區+街道 pattern;英文靠地址關鍵字 ──
    addr_cn = addr_en = en_hint = ""
    for idx, l in enumerate(seg):
        if not addr_cn and _has_cjk(l) and CN_ADDR.search(l):
            addr_cn = LABEL_WORDS.sub("", l).lstrip(":： ").strip()
        if not addr_en and not _has_cjk(l) and EN_ADDR_KW.search(l) and len(l) > 12:
            en = l
            # 併入下一行的 R.O.C. 之類接續
            if idx + 1 < len(seg) and re.fullmatch(r"[A-Za-z.()\s]{2,8}", seg[idx + 1]):
                en = en + " " + seg[idx + 1]
            addr_en = re.sub(r"^Alamat\s*[:：]?\s*", "", en, flags=re.I).strip()
        # 錨定線索:紅章殘影常把中文字黏進英文地址行(「司賜聼12.Ln.18…」),
        # 這種行不能當 地址_英 輸出,但行尾的英文縣市/區仍完好,
        # 留給 address_db 的英文錨定反查用(不寫入任何輸出欄位)。
        if not en_hint and _has_cjk(l) and EN_ADDR_KW.search(l) and len(l) > 12:
            en_hint = l

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
        # 中文名:有中日韓字、非地址(中式或英式)、去標籤字。
        # EN_ADDR_KW 檢查:紅章殘影字有時會黏在英文地址行首(「司賜聼12.Ln.18…」),
        # 該行含 CJK 又無中文縣市字,若只擋 CN_ADDR 會被誤收為中文名。
        if not name_cn:
            core = re.sub(r".*[:：]", "", l).strip()
            if _has_cjk(core) and not CN_ADDR.search(core) and not EN_ADDR_KW.search(core):
                cand = LABEL_WORDS.sub("", core)
                cand = re.sub(r"Nama.*", "", cand, flags=re.I).strip(" ()（）:：")
                if cand and _has_cjk(cand):
                    name_cn = cand

    fields = {
        "雇主名稱_中": name_cn,
        "雇主名稱_英": name_en,
        "地址_中": addr_cn,
        "地址_英": addr_en,
        "電話": phone,
    }
    _standardize_address(fields, addr_cn or "", en_hint=en_hint)
    return fields


def _standardize_address(fields: dict, raw_cn: str, en_hint: str = "") -> None:
    """用官方地址庫(address_db)校正地址,非破壞式補上標準欄位。

    - 新增:地址_中_標準、地址_英_標準、郵遞區號、地址_比對(是否命中)。
    - 回填:原 OCR 地址_英 為空時,以官方英文譯名補上。
    - 保守:縣市+行政區都命中才回填標準中文;僅命中縣市時只補郵遞/英文,
      不覆寫地址_中,避免把「有 OCR 原文」誤換成「資訊較少的標準串」。
    - 英文錨定:OCR 英文地址行一併傳入 normalize_address(en_text=…);
      印章蓋掉中文行首縣市時,以行尾倖存的英文縣市/區反查糾錯。
    庫或資料缺失時安靜跳過(try/except),絕不影響既有流程。
    """
    fields.setdefault("地址_中_標準", "")
    fields.setdefault("地址_英_標準", "")
    fields.setdefault("郵遞區號", "")
    fields.setdefault("地址_比對", False)
    if not raw_cn.strip():
        return
    try:
        from address_db import normalize_address
        r = normalize_address(
            raw_cn, en_text=fields.get("地址_英", "") or en_hint)
    except Exception:
        return
    if not r.get("matched"):
        return
    fields["地址_比對"] = True
    fields["郵遞區號"] = r["zip"]
    fields["地址_英_標準"] = r["address_en"]
    if r["district"] and r["road"]:        # 縣市+區+路都命中才給標準中文地址;
        fields["地址_中_標準"] = r["address_cn"]  # 無路名的鄉村地址易失真,留空以 OCR 原文為準
    if not fields["地址_英"] and r["address_en"]:   # 原英文空 → 用官方英文回填
        fields["地址_英"] = r["address_en"]


def vision_full_text(image_bytes: bytes, client=None) -> str:
    """對影像 bytes 跑 Vision DOCUMENT_TEXT_DETECTION,回傳全文(缺則空字串)。"""
    from google.cloud import vision as gvision

    if client is None:
        from pipeline import get_vision_client
        client = get_vision_client()
    resp = client.document_text_detection(image=gvision.Image(content=image_bytes))
    if resp.error.message:
        raise RuntimeError(f"Vision API 錯誤: {resp.error.message}")
    return resp.full_text_annotation.text if resp.full_text_annotation else ""


def extract_employer(img_path: str, client=None, deink: bool = True,
                     roi: tuple | None = EMPLOYER_ROI) -> dict:
    """對單張圖跑 Vision DOCUMENT_TEXT_DETECTION,再擷取雇主欄位。

    處理順序:讀檔 → (可選)ROI 裁切 → (可選)去紅章 → Vision → 解析。
    roi  :預設 EMPLOYER_ROI(上緣→Nomor Telepon);傳 None 用整張圖。
    deink:預設 True,送 Vision 前先去紅章前處理(選項 A)。
    """
    with open(img_path, "rb") as f:
        content = f.read()
    if roi:
        content = crop_fraction(content, roi)
    if deink:
        content = deink_red_stamp(content)
    text = vision_full_text(content, client=client)
    return extract_employer_fields(text)


def _docx_images(docx_path: str) -> list[tuple[str, bytes]]:
    """讀 docx 內嵌圖(word/media/*),回傳 (檔名, bytes) 清單。"""
    import zipfile
    from pathlib import Path
    # 含 .gif:契約頁有時存成 GIF(image2.gif),漏收會只剩護照 JPEG(32254 教訓)。
    exts = {".jpg", ".jpeg", ".png", ".gif"}
    with zipfile.ZipFile(docx_path) as z:
        return [(Path(n).name, z.read(n)) for n in z.namelist()
                if n.startswith("word/media/") and Path(n).suffix.lower() in exts]


# 台灣/印尼仲介區塊標記(次頁左半)。台印電話都以 0 開頭、格式會重疊,無法靠
# 純格式區分,故改「標記錨定」:只取『台灣仲介』標記之後、遇『印尼仲介/P3MI』
# 標記即停的區塊,結構性排除右側/下方的印尼仲介電話。
_TW_AGENCY_LABEL = re.compile(r"[台臺]灣仲介|Agen(?:cy|si)\s*Taiwan", re.I)
_ID_AGENCY_LABEL = re.compile(r"印尼仲介|P3MI|Agen(?:cy|si)\s*Indonesia|Perwakilan", re.I)


def agency_phones_from_next_page(docx_path: str, client=None) -> list[str]:
    """全無命中救援:取「契約頁的次一頁」左半邊『台灣仲介公司』區塊的電話候選。

    雙重隔離只取台灣仲介(不誤取右側/下方印尼 P3MI):
      ① 幾何:只裁左半邊。
      ② 語意:錨定『台灣仲介』標記,遇『印尼仲介/P3MI』標記即停。
    回傳台灣電話候選(排傳真、去重保序);找不到契約頁/無次頁 → 空清單。
    """
    imgs = _docx_images(docx_path)
    if not imgs:
        return []
    ordered = sorted(imgs, key=lambda x: _natural_key(x[0]))
    page = find_contract_image(imgs)
    if not page:
        return []
    names = [n for n, _ in ordered]
    ci = names.index(page[0])
    if ci + 1 >= len(ordered):
        return []                          # 契約頁是最後一張,無次頁
    img = ImageOps.exif_transpose(
        Image.open(BytesIO(ordered[ci + 1][1])).convert("RGB"))
    w, h = img.size
    left = img.crop((0, 0, int(w * 0.5), h))
    buf = BytesIO()
    left.save(buf, format="PNG")
    lines = [l.strip() for l in vision_full_text(buf.getvalue(), client=client)
             .splitlines() if l.strip()]

    # 錨定台灣仲介區塊:標記之後 → 遇印尼仲介標記(或結尾)為止。
    start = next((i for i, l in enumerate(lines) if _TW_AGENCY_LABEL.search(l)), None)
    if start is None:
        block = lines                      # 無標記 → 退回整個左半(仍主要是台仲)
    else:
        end = next((i for i in range(start + 1, len(lines))
                    if _ID_AGENCY_LABEL.search(lines[i])), len(lines))
        block = lines[start:end]

    # 電話:區塊內切掉「傳真/Fax」後段,抽台灣電話;去重保序。
    seen: set[str] = set()
    phones: list[str] = []
    for l in block:
        head = re.split(r"傳真|Fax", l, maxsplit=1)[0]
        for ph in _phones_in(head):
            if ph not in seen:
                seen.add(ph)
                phones.append(ph)
    return phones


def extract_employer_from_docx(docx_path: str, client=None, deink: bool = True,
                               roi: tuple | None = EMPLOYER_ROI,
                               crop_dir: str = "") -> dict:
    """端到端:從 docx 找出契約頁 → ROI 裁切 → 去紅章 → Vision → 擷取雇主欄位。

    crop_dir:非空時,把「去紅章前」的 ROI 裁切圖存到該資料夾
      ({docx主檔名}_{圖檔名}),保留紅章原貌供人工複查 H~O 欄位值。
      **每份 docx 都會存**:找不到契約頁時存「最高分候選頁」(全零分則退
      image2 慣例位置),欄位留空但截圖必在,人工審核不會沒圖可看。

    回傳除五個雇主欄位外,另含:
      _image:實際採用(或候選)的圖檔名
      _note :流程備註(例如「找不到契約頁」)
      _crop :已存檔的 ROI 截圖檔名(未存檔則為空)
    """
    from pathlib import Path

    empty = {"雇主名稱_中": "", "雇主名稱_英": "", "地址_中": "",
             "地址_英": "", "電話": ""}
    images = _docx_images(docx_path)
    if not images:
        return {**empty, "_image": "", "_note": "docx 無內嵌圖", "_crop": ""}

    # 一次算完各圖分數(自然排序,同分取前者=max 的預設行為)
    scored = [(score_contract_page(b), n, b)
              for n, b in sorted(images, key=lambda x: _natural_key(x[0]))]
    best_s, name, raw = max(scored, key=lambda t: t[0])
    found = best_s >= 2
    if not found and best_s == 0:
        # 全零分:退 image2(樣本慣例上契約頁多在第 2 張),沒有再用第一張
        name, raw = next(((n, b) for _s, n, b in scored
                          if n.startswith("image2.")), (name, raw))

    content = raw
    if roi:
        content = crop_fraction(content, roi)
    crop_name = ""
    if crop_dir:
        d = Path(crop_dir)
        d.mkdir(parents=True, exist_ok=True)
        crop_name = f"{Path(docx_path).stem}_{name}"
        (d / crop_name).write_bytes(content)

    if not found:
        # 特徵不足不送 Vision(頁面身分不明,抽出的欄位不可信),只留截圖
        return {**empty, "_image": name,
                "_note": f"找不到契約頁(特徵字命中不足,最高分 {best_s});已存候選頁截圖",
                "_crop": crop_name}

    if deink:
        content = deink_red_stamp(content)
    fields = extract_employer_fields(vision_full_text(content, client=client))
    return {**fields, "_image": name, "_note": "", "_crop": crop_name}


# ═══════════════════════════════════════════════════════════════════════════
# A/B 測試小工具:比較「去紅章前 vs 後」的擷取結果,並可另存 deink 影像供目視
# ═══════════════════════════════════════════════════════════════════════════

def _ab_compare(img_path: str, dump: str = "", roi: tuple | None = EMPLOYER_ROI) -> None:
    """單張圖:在同一個 ROI 內比較「去紅章前 vs 後」的雇主欄位差異。"""
    from pipeline import get_vision_client
    client = get_vision_client()

    with open(img_path, "rb") as f:
        raw = f.read()
    base = crop_fraction(raw, roi) if roi else raw   # 兩邊都套 ROI,單獨看 deink 效果

    if dump:
        with open(dump, "wb") as f:
            f.write(deink_red_stamp(base))
        print(f"已輸出 ROI+去紅章影像:{dump}")

    before = extract_employer_fields(vision_full_text(base, client))
    after  = extract_employer_fields(vision_full_text(deink_red_stamp(base), client))

    print(f"\n== {img_path}  (ROI={roi}) ==")
    for k in before:
        b, a = before[k], after[k]
        flag = "  ← 變更" if b != a else ""
        print(f"[{k}]\n  去紅章前: {b!r}\n  去紅章後: {a!r}{flag}")


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="雇主資料擷取(契約頁偵測 + ROI + 去紅章)")
    ap.add_argument("path", help="契約影像(jpg/png)或 .docx 檔路徑")
    ap.add_argument("--dump", default="", help="另存 ROI+去紅章後影像到此路徑(供目視)")
    ap.add_argument("--no-ab", action="store_true", help="單圖時只跑正式流程,不做 A/B")
    ap.add_argument("--full", action="store_true", help="不裁 ROI,對整張圖處理")
    args = ap.parse_args()

    roi = None if args.full else EMPLOYER_ROI

    if args.path.lower().endswith(".docx"):
        # docx:自動找契約頁 → ROI → 去紅章 → Vision
        res = extract_employer_from_docx(args.path, deink=True, roi=roi)
        print(f"採用契約頁:{res.get('_image') or '(無)'}  {res.get('_note','')}")
        for k in ("雇主名稱_中", "雇主名稱_英", "地址_中", "地址_英", "電話",
                  "地址_中_標準", "地址_英_標準", "郵遞區號"):
            print(f"  {k}: {res.get(k)!r}")
    elif args.no_ab:
        print(extract_employer(args.path, deink=True, roi=roi))
    else:
        _ab_compare(args.path, dump=args.dump, roi=roi)
