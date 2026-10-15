# -*- coding: utf-8 -*-
"""conftest 的路徑設定。

為什麼寫錯了平常看不出來、要用哪個指令才看得到，見 conftest.py「路徑設定」那段。
這支測試存在，是因為「只有冷門指令才抓得到」的 bug 等於沒人抓得到——它在平常的
`python -m pytest` 下就會紅。

只檢查 ROOT 的值，不檢查它有沒有真的進 sys.path：後者在 `python -m pytest` 下
永遠成立（cwd 本來就在 sys.path 裡），刪掉 insert 那行也照樣綠，是空斷言。
"""
from pathlib import Path

import conftest
import pipeline


def test_conftest_root_is_the_repo_root():
    """ROOT 必須是 pipeline.py 所在的那一層，不是它的上一層。"""
    assert conftest.ROOT == Path(pipeline.__file__).resolve().parent
