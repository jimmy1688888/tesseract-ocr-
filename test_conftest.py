# -*- coding: utf-8 -*-
"""conftest 的路徑設定。

conftest 曾把 `parent.parent`（repo 的上一層）插進 sys.path，兩個多月沒人發現
—— 因為平常跑測試的 `python -m pytest` 會把 cwd 塞進 sys.path[0]，替它把事做了。
只有 `pytest --import-mode=importlib`（用執行檔）才看得到它壞掉。

這支測試存在，是因為「只有冷門指令才抓得到」的 bug 等於沒人抓得到。它在平常的
`python -m pytest` 下就會紅。
"""
from pathlib import Path

import conftest
import pipeline


def test_conftest_puts_the_repo_root_on_sys_path():
    """插進 sys.path 的必須是 pipeline.py 所在的那一層，不是它的上一層。"""
    assert conftest.ROOT == Path(pipeline.__file__).resolve().parent
