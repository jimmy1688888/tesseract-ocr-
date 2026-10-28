# matches.csv 只存讀值,判斷只在送審決策

許可證掃描原本在掃描當下就用送審決策的門檻判一次:`decide_result` 逐張寫 `final_value`、
`final_conf`、`vision_review`、`note`,`aggregate_small_docx` 再替 small docx 挑一個 winner;
送審決策讀這些旗標、又挑一次 winner。門檻因此在兩個時間點各用一次,測試 fixture 也得抄上游
規則才造得出合理的輸入。**決定:掃描只留下讀值(值、信心、是否多數票、兩 ROI 是否互證、
命中狀態)與掃描觀測(`note` 只記掃描當下的事,例如佔位列);`final_value`、`final_conf`、
`vision_review` 三欄從 matches.csv 刪除,所有判斷(含 small 的 winner、規則 C 的理由措辭)
只在送審決策(候選 P)。**

## Consequences

- **打開 matches.csv 看不到結論。** 要知道一份 docx 的去向,看送審決策(Sheets D 欄、步驟 3
  的 log、送審快照),不是看 CSV。這是刻意放棄的便利,別把三欄加回來 —— 加回來就是兩份門檻。
- **舊檔照讀。** 本決定之前寫的 matches.csv(真實素材的某日掃描、`matches.21份.backup.csv`)
  帶著這三欄;讀取時忽略,送審決策從同一份讀值重算,結果與舊程式逐字相同(送審快照含理由守著)。
- 停止擴掃(ADR-0005)本來就只看 `is_permit_vote`,不受影響。

## 補充(2026-10-28,候選 W+C+AE+AD grill)

同一個理由延伸到掃描端剩下的兩個「半判斷」:`manual_review`(掃描當下標「要人工」,
送審決策只看 `status`、沒人讀)與 `low_conf`(信心 < `CONF_KEY_IN` 另存一張低信心截圖,
沒人讀、也沒人看那個資料夾;門檻還與送審決策的 `<=` 不一致)。**兩欄刪除、低信心截圖
不再存**;「mol 無值」這類掃描當下的事實寫進 `note`。舊檔帶這兩欄照讀、忽略。
之後 permit_reading 只為停止擴掃 import 多數票謂詞(ADR-0005),不再碰任何送審門檻。
