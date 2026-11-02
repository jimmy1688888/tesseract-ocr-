"""一批的結果(CONTEXT.md):一批跑到最後是哪一種收場,入口把它轉成行程的結束碼。

只有跑完與被擋下兩種值;第三種「中途出錯」是往上拋的例外,Python 自己給結束碼 1、
traceback 留在畫面上給管理者 —— 不接住、不包裝。執行.bat 依結束碼印收尾那句話(#75)。
"""
from __future__ import annotations

from enum import Enum

# 2 與 argparse 參數打錯時的結束碼相同:同樣是開跑前就停、什麼都沒動。
_FINISHED_EXIT_CODE = 0
_BLOCKED_EXIT_CODE = 2


class BatchResult(Enum):
    FINISHED = "跑完"     # 處理完並寫入 Sheets(含待人工審查列);只做 preflight 且通過也算
    BLOCKED = "被擋下"    # 開跑前就停、Sheets 沒動:閘門沒過,或指令參數對不到

    @property
    def exit_code(self) -> int:
        return _EXIT_CODES[self]


# 新增結果卻漏了對應時 KeyError,不默默歸成被擋下。
_EXIT_CODES = {BatchResult.FINISHED: _FINISHED_EXIT_CODE, BatchResult.BLOCKED: _BLOCKED_EXIT_CODE}
