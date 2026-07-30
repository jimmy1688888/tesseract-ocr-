# -*- coding: utf-8 -*-
"""雇主區塊的上下界(32417 教訓)。

32417 契約頁選對、Vision 也讀得完整,五個雇主欄位卻全空:
甲方那行被紅章毀成「用戶名稱(以下統為甲方): 林志明」——「甲方名稱」與
「以下簡稱」兩個詞都不成立,而乙方那行「勞工姓名(以下簡稱乙方)」印得乾淨,
舊的 NAME_LABEL 含裸的「以下簡稱」就靜默錨到乙方,把整個雇主區塊切在起點之前。

修法有兩段:
  上界 - NAME_LABEL 的「以下簡稱」限定為甲方;甲方標籤全毀時退印尼文標籤
        Nama Majikan(實測 7 份契約它一律完好),並往回一行納入中文姓名。
  下界 - 以 PARTY_END 切在甲方段落結束處,乙方資料不進統計。

不再退「MAJIKAN DENGAN」:那是標題字,Vision 對兩欄版面逐欄序列化,
標題屬右欄、雇主資料屬左欄,輸出順序與版面上下無關(32417 標題 y=104 排在
第 15 行,雇主 y=166 反而排在第 10 行),拿它當起點等於賭 Vision 怎麼切欄。
"""
from employer_extract import extract_employer_fields

# 32417 次頁 ROI 的實際 OCR(節錄):仲介框在前,甲方標籤已毀,乙方標籤完好
_LINES_32417 = [
    "台件中文/英名稱: 大和管理顧問有限公",
    "DA HER MANAGEMENT",
    "地址電話/演員:高雄市廣暢箱金田街55",
    "電話:093766)傳真:07-2692232",          # 仲介電話+傳真:絕不能當雇主電話
    "Aac 01 Lie pse Number: 3824",
    "用戶名稱(以下統為甲方): 林志明",        # ← 真正的甲方,標籤被毀
    "Nama Majikan:",
    "LIN ZHI MING",
    "勞動契約 監護工",
    "MAJIKAN DENGAN",
    "地址:",
    "高雄市左營區華夏路1728號13樓",
    "Alamat:",
    "電話:",
    "13F., No.1728, Huaxia Rd., Zuoying Dist., Kaohsiung City, Taiwan R.O.C.",
    "Nomor Telepon:",
    "0932237611",
    "0932237611",
    "SELANJUTNYA DISEBUT PIHAK PERTAMA",
    "勞工姓名(以下簡稱乙方):",               # ← 舊邏輯會錨到這裡
    "Nama Pekerja:",
    "NOVITASARI",
    "在印尼住址:",
    "DUSUN KRAJAN RT 001 RW 014 DESA KARANGANYAR KEC AMBULU",
]

# 一般件:甲方標籤完好(32444~32449 的形態)
_LINES_NORMAL = [
    "台中中文/英名稱: 苙與人力仲介有限公司",
    "電話:03-5310852,傳真:03-5277128",
    "Agency's MOL License Number: 3812",
    "甲方名稱 (以下簡稱為甲方): 李宜蓁",
    "Nama Majikan: LI, Yi-JHEN",
    "地址:",
    "新竹市北區磐石路29號7樓",
    "Alamat : 7F., No.29, Panshih Rd., North Dist., Hsinchu City 300068, Taiwan (R.O.C.)",
    "電話:",
    "Nomor Telepon :",
    "0989346899",
    "0989346899",
    "SELANJUTNYA DISEBUT PIHAK PERTAMA",
    "勞工姓名(以下簡稱乙方):",
    "NOVITASARI",
]


def _fields(lines):
    return extract_employer_fields("\n".join(lines))


class TestDamagedPartyALabel:
    """甲方標籤被毀 → 退 Nama Majikan,五欄都要抽得到(32417 實況)。"""

    def test_chinese_name_recovered(self):
        # 往回一行才納得到中文姓名(它排在 Nama Majikan 之前)
        assert _fields(_LINES_32417)["雇主名稱_中"] == "林志明"

    def test_english_name_recovered(self):
        assert _fields(_LINES_32417)["雇主名稱_英"] == "LIN ZHI MING"

    def test_address_recovered(self):
        f = _fields(_LINES_32417)
        assert f["地址_中"] == "高雄市左營區華夏路1728號13樓"
        assert f["地址_英"].startswith("13F., No.1728, Huaxia Rd.")

    def test_phone_is_employer_not_agency(self):
        """雇主電話,不是仲介框裡的 03/傳真 07-2692232(32098 教訓)。"""
        assert _fields(_LINES_32417)["電話"] == "0932237611"

    def test_not_anchored_to_party_b(self):
        """舊邏輯錨到「勞工姓名(以下簡稱乙方)」→ 五欄全空;現在不該再發生。"""
        assert any(_fields(_LINES_32417)[k] for k in
                   ("雇主名稱_中", "地址_中", "電話"))


class TestNormalContractUnaffected:
    """甲方標籤完好時,一切照舊(32444~32449 六份回歸零變動)。"""

    def test_all_fields(self):
        f = _fields(_LINES_NORMAL)
        assert f["雇主名稱_中"] == "李宜蓁"
        assert f["雇主名稱_英"] == "LI, Yi-JHEN"
        assert f["地址_中"] == "新竹市北區磐石路29號7樓"
        assert f["電話"] == "0989346899"

    def test_agency_phone_excluded(self):
        assert _fields(_LINES_NORMAL)["電話"] != "03-5310852"


class TestPartyBBoundary:
    """下界:乙方資料不得進入雇主欄位統計。"""

    def test_labor_indonesian_address_not_taken(self):
        f = _fields(_LINES_32417)
        assert "DUSUN KRAJAN" not in f["地址_英"]
        assert "NOVITASARI" not in f["雇主名稱_英"]

    def test_party_b_phone_not_counted(self):
        """乙方區塊的電話出現兩次也不能勝出——它在 end 之後,根本不進統計。"""
        lines = _LINES_NORMAL + ["電話:", "081249756761", "081249756761"]
        assert _fields(lines)["電話"] == "0989346899"


class TestAnchorFailureReturnsEmpty:
    """甲方標籤與 Nama Majikan 都錨不到 → 回空並註記,不退回「從第 0 行開始」。

    實測 7 份契約若退回 0,有 6 份會把仲介框的名稱/地址/電話整組當成雇主填進去
    (32447、32417 甚至抓到仲介的傳真號)。那是看起來完全合理的錯誤資料,
    人工審核看不出破綻,比留空危險得多。
    """

    NO_ANCHOR = [
        "台中中文/英名稱: 苙與人力仲介有限公司",
        "地址/電話/傳真: 新竹市東區東門街122-1號2樓",
        "電話:03-5310852,傳真:03-5277128",
        "Agency's MOL License Number: 3812",
        "XX(標籤全毀): 李宜蓁",
        "地址:",
        "新竹市北區磐石路29號7樓",
        "0989346899",
    ]

    def test_all_fields_empty(self):
        f = _fields(self.NO_ANCHOR)
        for k in ("雇主名稱_中", "雇主名稱_英", "地址_中", "地址_英", "電話"):
            assert f[k] == "", k

    def test_note_explains_why(self):
        assert "錨定失敗" in _fields(self.NO_ANCHOR)["_note"]

    def test_agency_data_not_leaked(self):
        """關鍵:不能把仲介的名稱/地址/電話當成雇主填出去。"""
        f = _fields(self.NO_ANCHOR)
        assert "仲介" not in f["雇主名稱_中"]
        assert f["電話"] not in ("03-5310852", "03-5277128")

    def test_normal_path_has_blank_note(self):
        assert _fields(_LINES_NORMAL)["_note"] == ""


class TestFaxExcludedFromEmployerPhone:
    """雇主電話統計也要切掉同行的傳真後段(原本只有台仲區塊那條路徑有做)。"""

    def test_damaged_phone_does_not_fall_back_to_fax(self):
        """電話號碼被 OCR 毀掉、傳真完好時,不切傳真就會拿傳真號充數(32417 實例)。

        『電話:093766)傳真:07-2692232』整行是電話標籤行,傳真號因此靠
        labeled 優先勝出。切掉後段 → 只剩讀壞的 093766(不合格式)→ 留空。
        """
        lines = [
            "甲方名稱 (以下簡稱為甲方): 王小明",
            "電話:093766)傳真:07-2692232",
            "Nomor Telepon:",
            "SELANJUTNYA DISEBUT PIHAK PERTAMA",
        ]
        assert _fields(lines)["電話"] == ""

    def test_phone_before_fax_still_taken(self):
        """同行有完好電話時照抽,不能因為切除而連電話一起丟掉。"""
        lines = [
            "甲方名稱 (以下簡稱為甲方): 王小明",
            "電話:03-5310852,傳真:03-5277128",
            "SELANJUTNYA DISEBUT PIHAK PERTAMA",
        ]
        assert _fields(lines)["電話"] == "03-5310852"

    def test_fax_keyword_case_insensitive(self):
        lines = [
            "甲方名稱 (以下簡稱為甲方): 王小明",
            "TEL: 03-5310852 FAX: 03-5277128",
            "SELANJUTNYA DISEBUT PIHAK PERTAMA",
        ]
        assert _fields(lines)["電話"] == "03-5310852"
