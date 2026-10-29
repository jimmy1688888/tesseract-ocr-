# 超過行數上限的 module 改成同名 package,`__init__` 只轉發原本的公開名字

CLAUDE.md 的 Monolith Prevention 把上限定在單檔 300 行、函式 50 行,而且回溯套用到既有檔
(2026-10-29,當時超標 6 檔:pipeline、employer_extract、permit_reading、address_db、snapshots、
permit_lookup)。照行數切很容易造出 shallow module —— 為了湊行數把函式搬到別處,呼叫端反而
要多認識幾個檔。**決定:超標的 module 改成同名 package,子檔沿 CONTEXT.md 已有名字的 seam 切;
`__init__` 只轉發原本就屬於這個 module 的公開名字,呼叫端的 `import X` / `from X import …`
一律不改。** 第一個照做的是 permit_reading(AM)與從 pipeline 搬出的許可證掃描(AK-1,
`permit_scan.py`)。

## 拆之前要先處理的測試

拆檔本身不該改行為,但有兩種測試會在搬檔後**悄悄變弱、不會變紅**,所以拆之前先處理:

- **monkeypatch 模組私有名的測試。** `monkeypatch.setattr(模組, "_私有名", …)` 在函式搬進子模組
  後只換掉 `__init__` 上的綁定,子模組裡的呼叫照舊走原本的函式。這類測試先改成注入參數,
  才能拆 —— 所以 employer_extract(AL)要等 X、AG 清掉 patch。
- **讀原始碼的架構測試。** `inspect.getsource(模組)`、`模組.__file__`、`ROOT.glob("*.py")` 在
  package 上只看得到 `__init__.py`,或根本看不到這個 package。一律改走 conftest 的
  `module_sources`(單檔回傳那一檔、package 回傳底下所有 .py),並在搬檔前用一次變異
  (在子檔塞一行被禁止的 import)確認它會紅。

## 搬檔與改寫分開 commit

先純搬移:快照(掃描快照、送審快照、H~O 快照)要一格不差;再改寫(拆超過 50 行的函式、
合併重複)。兩者混在一起,快照紅了分不出是搬壞還是改壞。

## Considered Options

- **照行數切成平鋪的新頂層檔(`permit_reading_votes.py` 之類)。** 否決:根目錄多出一堆無主的
  檔,呼叫端要改 import,依賴方向也看不見。
- **`__init__` 轉發子檔裡的一切(`from .image import *`)。** 否決:私有名與別人的名字(例如
  permit_patterns 的 `find_permits`)會被轉手出去,#59「名字回到主人身上」又被打破。
- **放寬上限、不拆既有檔。** 否決:只管新檔的話規則形同虛設 —— #60 #61 就是往 625 行的
  permit_reading 裡加。上限已依實測調成 300 / 50,單一高聚合實體(matches_csv、
  submission_decision)自然落在 250~280,不必拆。

## Consequences

- package 內的子檔可以互相 import,但依賴要單向(permit_reading:docx → tiers → image → ocr)。
- `__init__` 的轉發清單就是這個 module 的 interface;要加名字到清單上,等於擴大 interface。
- 不在轉發清單上的名字(例如 `permit_reading.image.find_permits`),測試直接指向子檔,
  不為了測試加轉發。
