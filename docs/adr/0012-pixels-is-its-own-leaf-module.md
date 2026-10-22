# 像素前處理放在獨立的 leaf module `pixels`,去紅章的差異用設定表達

像素前處理(轉正、相對比例裁切、通道/放大/對比/去雜、去紅章)原本有三份:許可證掃描的
`preprocess` 與 `crop_roi` 在 `pipeline`;契約頁的 `deink_red_stamp`、`crop_fraction` 與
契約頁評分的上緣裁切在 `employer_extract`;次頁的 `_deink_next_page_png` 則從 `pipeline`
**延遲 import** `preprocess`。兩側都不在 module level import 對方,所以沒有靜態循環,只有
一個沒有主人的約定:任一側改成 eager 就真的循環了,而沒有測試釘住它。更要緊的是三份去紅章
的差異會直接改變輸出(ADR-0006:同一格換個前處理讀出滄/濃/澹三個字),卻沒有任何地方寫下
三者的關係。**決定:另開只依賴標準函式庫、PIL、numpy 的 `pixels.py`,擁有全部像素處理;
`pipeline` 與 `employer_extract` 都 import 它。三個呼叫端共用同一個 `preprocess`,差異只在
設定值,並把理由寫在設定旁(#16)。**

| 呼叫端 | 設定 | 為什麼不一樣 |
|---|---|---|
| 許可證掃描(Tesseract) | `SCAN_CONFIGS` / `FALLBACK_CONFIGS` | 多組設定投票,見 ADR-0005 |
| 契約頁(Vision) | `CONTRACT_PAGE_DEINK`:紅通道 + 抹白紅章 | 雇主名稱/地址常被大紅章蓋住,只靠紅通道仍留灰影;Vision 自己會二值化,不放大不去雜 |
| 契約次頁(Vision) | `NEXT_PAGE_DEINK` = 掃描第一組「紅通道_2x_中值3」去掉 psm | 台仲字小、與章交錯,要放大+中值;章多壓在字上,抹白會吃筆畫 |

## 為什麼不照 ADR-0001 的先例

ADR-0001 否決過「另開第三個 leaf module」,理由是它沒有領域身分、而概念已有天然的主人。
像素處理兩個條件都不成立(比照 ADR-0011):

- **沒有天然的主人。** 放 `pipeline`,`employer_extract` 就得 import 2,000 行的流程模組
  (今天的延遲 import 正是這樣來的);放 `employer_extract`,許可證掃描就依賴雇主擷取。
- **有領域身分。** 「去紅章」是 CONTEXT.md 的詞,三組設定各有業務理由,不是無名工具函式。

## Considered Options

- **維持現狀,加測試釘住「兩側都延遲 import」的約定。** 否決:釘住的是一個繞路,三份去紅章
  的關係仍然寫不下來。
- **`employer_extract` 改成 eager import `pipeline`。** 否決:`pipeline` 對 `employer_extract`
  也有延遲 import,會造成真的循環;而且雇主擷取不該依賴流程模組。
- **三份去紅章合成一組設定。** 否決:會改變送 Vision 與 Tesseract 的像素,輸出跟著變;這是
  結構票,差異有理由就保留,只換表達方式。

## Consequences

- 重構前後像素逐點相同(378 組輸出雜湊,含 EXIF 轉向);對比拉伸統一用 float64。numpy 2 下
  舊的 float32 寫法實際上也被提升成 float64,所以沒有差異;numpy 1.x 下可能有個別像素差 1(未實測)。
- `test_pixels.py` 釘住:`pixels` 只 import 標準函式庫/PIL/numpy;只有契約頁抹白;次頁設定
  等於 `SCAN_CONFIGS[0]` 去掉 name 與 psm。改任何一組設定前要先看這張表。
- 拿不到憑證時三處各自怎麼走(許可證判讀、雇主欄位整批中止;次頁反查救回 0 筆)由
  `test_no_credentials.py` 釘住;使用者 2026-10-22 決定維持「整批中止」,改成降級屬行為票。
