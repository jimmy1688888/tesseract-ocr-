"""許可證判讀結果(CONTEXT.md):一批送審決策走完去向 → Sheets A~D(#71,候選 AC)。

  settle(decisions, batch, vision, roster) -> PermitOutcomes

package 的兩塊(ADR-0014;依賴單向 settle → rescue):
  settle  依去向分派、Vision 驗證、依寫入順序交出列與摘要
  rescue  反查救援:讀次頁台仲區塊、名冊挑一家、接回種子列
送審決策(submission_decision)、Vision 驗證(vision_verification)、決策梯
(permit_lookup.pick_agency)各在主人那裡;這裡只轉發自己的名字。
"""
from .settle import PermitOutcomes, settle
