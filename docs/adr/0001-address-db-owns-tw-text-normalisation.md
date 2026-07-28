# address_db 擁有台灣文字正規化,permit_lookup 呼叫它

台灣同一個地址有多種合法寫法(臺/台、鍾/鐘、全形/半形、段號國字/阿拉伯),而本專案的
三個資料來源各用一套:官方門牌庫與勞動部名冊的機構地址慣用正體「臺」與阿拉伯段號,
契約 OCR 印的則是「台」與國字段。過去 `address_db.fold_variants`(原私有 `_norm`)與
`permit_lookup._norm_addr` **各自實作了一套「地址正規化」且行為不一致** —— 後者不折疊
臺/台 也不折疊段號,偏偏它才是反查救援拿來比相似度的那一套。現決定:
**「異體折疊」這個概念由 `address_db` 單獨擁有並以 `fold_variants` 公開,
`permit_lookup` 頂層 import 它,`_norm_addr` 與 `_norm_name` 都先過這一關再去標點。**

## Considered Options

- **另開第三個 leaf module(如 `tw_text.py`)只放折疊規則,兩邊都依賴它。**
  否決:那是一個沒有領域身分的 module,而且 seam 兩側沒有任何東西會變動 ——
  單一實作的 seam 只是多一層轉接。折疊規則的來由(異體字、官方庫段號體例)本來就是
  `address_db` 已經在記錄的知識,概念放在知識所在之處。
- **不建立依賴,把規則複製進 `permit_lookup`。**
  否決:這正是原本的問題,只是把「行為不一致」換成「行為一致但要記得改兩處」。
- **`address_db` 直接提供比對用的完整正規化(含去標點、轉大寫)。**
  否決:去標點與轉大寫是「相似度比對」的政策,屬於 `permit_lookup`;
  `address_db` 自己並不做機構名稱／地址的相似度比對。

## Consequences

- `permit_lookup` 從零內部依賴變成依賴 `address_db`。可接受:`address_db` 只用標準
  函式庫,且 `_load()` 只在函式內被呼叫,**import 時零 I/O** —— 即使
  `data/AllData.json`(2.4MB、不進版控)缺席,`import address_db` 仍然安全,
  `permit_lookup` 的查表與反查完全不受影響。
- `fold_variants` 現在是公開介面,行為改動會同時影響地址庫比對與名冊反查。
  它的行為在此次變更中**一行未改**,只是換名字。
- `permit_lookup._norm_addr` 原本自行處理全形數字的 `str.maketrans` 已刪除 ——
  `fold_variants` 的 NFKC 已涵蓋。
- **`fold_variants` 的產出不可輸出**:它會把「臺」寫成「台」,填進 Sheets 標準地址欄
  就不符官方寫法。要輸出標準地址一律用 `normalize_address()`。
  `test_address_norm.py::TestNotForOutput` 釘住這條界線。
