# 台灣電話的規則放在獨立的 leaf module `tw_phone`

台灣電話的知識分散在兩個彼此不 import 的 module:從一行文字抓出號碼(`PHONE`、括號區碼、
`+886` 三個 regex)在 `employer_extract`,轉成比對用的純數字(`_norm_phone`、
`PHONE_MIN_LEN`)在 `permit_lookup`,`+886` 的還原兩邊各寫一次。雇主電話挑選用 OCR
原字串計數,同一支號碼寫成 `0920-875-667` 與 `0920875667` 就算成兩支,「出現兩次者優先」
失效;O 欄也照 OCR 原樣輸出,寫法不一。**決定:另開只依賴標準函式庫的 `tw_phone.py`,
擁有「抓號碼」與「電話標準形」(CONTEXT.md),`employer_extract` 與 `permit_lookup` 都
import 它;O 欄、同號判斷、名冊反查一律用標準形(純數字、國內前導 0)。**

## 為什麼不照 ADR-0001 的先例

ADR-0001 否決過「另開第三個 leaf module 放台灣文字折疊規則」,理由是那個 module 沒有領域
身分,而折疊規則本來就是 `address_db` 已經在記錄的知識 —— 概念有天然的主人。電話不一樣:

- **沒有天然的主人。** 抓號碼的知識在雇主擷取側,正規化的知識在名冊側;放哪一邊,另一邊
  都得依賴一個不相干的重模組。
- **有領域身分。** 「台灣電話」有自己的規則(`+886`、括號區碼、9~10 碼、排除統編與地號),
  CONTEXT.md 有「電話標準形」這個詞,不是無名的工具函式。

## Considered Options

- **放在 `permit_lookup`,`employer_extract` import 它。** 否決:雇主擷取會依賴名冊模組,
  間接依賴 `address_db`,依賴方向與領域無關。
- **放在 `employer_extract`,`permit_lookup` import 它。** 否決:名冊反查會拖進 PIL、numpy
  與 Vision 那一整串,`permit_lookup` 至今只靠標準函式庫加 `address_db`。
- **標準形加連字號(`0919-889-969`、`037-985657`)。** 否決:台灣區碼長度不一(02、037、
  0826、0836…),要維護區碼表,放錯位置反而誤導。

## Consequences

- O 欄由 OCR 原樣改為標準形。Sheets 上的舊列不改(pipeline 對 Sheets 只寫不讀),新舊會混;
  要統一由人在 Sheets 上處理。
- **名冊的電話欄(G 欄)照抄原文,不轉標準形**:一格常塞兩支號碼或分機
  (`03-3253456/3253507`、`(03)-3554436#171`),轉了會丟官方資料。名冊這側的拆欄
  (`split_phone_field`)仍屬 `permit_lookup`,只是每段改呼叫 `tw_phone` 轉標準形。
- 核對值的電話照人看到的寫(多半有連字號),測試比對前先轉標準形 —— 真值檔不必改。
- D 欄反查理由裡的台仲電話(來自 OCR)也跟著變成標準形。
