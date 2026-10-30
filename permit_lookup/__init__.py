"""許可證 → 機構資料(名稱／地址／電話)對照查表。

資料來源:勞動部「跨國人力仲介公司許可名冊」(data.gov.tw dataset 6682)的官方 JSON。
刻意用 JSON 而非 Excel 匯出的 CSV:Excel 會把分支許可證(如 1-73)自動判讀成日期
(→ Jan-73),並吃掉前導零(0234 → 234);JSON 原樣保留 "0001"、"0002-1" 等格式。

資料檔:只讀本地檔 data/agency_roster.json,不自動連網。更新方式為手動——
    用瀏覽器開啟 ROSTER_JSON_URL 下載 JSON,覆蓋該檔即可。
    (mol.gov.tw 憑證常被 Python SSL 擋下,故不在程式內連網。)

用法:
    from permit_lookup import load_roster
    roster = load_roster()              # 讀本地 data/agency_roster.json;載不起來回 None
    info = roster.lookup("2340")        # → {'機構名稱': ..., '機構地址': ..., '電話': ...}
    if info is None:                    # 查無 → 交人工補
        ...

package 的四塊(ADR-0014;依賴單向 rescue_pick → lookup → matching → roster):
  roster       名冊讀檔、併入自建補充仲介、許可證索引
  matching     電話欄拆號碼與電話索引、機構名稱/地址的相似度比對
  lookup       查表器 PermitLookup 與一批載一次的 load_roster
  rescue_pick  反查救援的決策梯:台仲區塊 → 名冊挑一家(pick_agency)
這裡只轉發四塊的公開名字;別人的名字(PHONE_MIN_LEN 在 tw_phone)到主人那裡取。
"""
from .lookup import PermitLookup, load_roster
from .matching import ADDR_MATCH_MIN, NAME_MATCH_MIN, build_phone_index, split_phone_field
from .rescue_pick import AgencyPick, pick_agency
from .roster import (CUSTOM_ROSTER_PATH, ROSTER_JSON_URL, ROSTER_PATH, ROSTER_STALE_DAYS,
                     build_indices, fetch_roster, merge_custom_agencies)
