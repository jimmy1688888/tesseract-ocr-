# -*- coding: utf-8 -*-
"""名字回到主人身上(#59):流程模組不轉手匯出別人的名字,呼叫端直接 import 主人。

以前 pipeline 轉匯出約 30 個 permit_reading/送審決策/Vision 驗證的名字,`P.ScanResult`
這種寫法讓依賴方向看不見,送審快照之類的工具也得為一個函式載入整條流程。
"""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PIPELINE = ROOT / "pipeline.py"


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _project_modules() -> set[str]:
    return {p.stem for p in ROOT.glob("*.py")}


def _imported_from_project(tree: ast.Module) -> set[str]:
    """pipeline 從專案內其他 module import 進來的名字,含 import 的 module 本身。"""
    project = _project_modules()
    names: set[str] = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom) and n.module and n.module.split(".")[0] in project:
            names |= {a.asname or a.name for a in n.names}
        elif isinstance(n, ast.Import):
            names |= {a.asname or a.name for a in n.names if a.name in project}
    return names


def _bound_names(tree: ast.Module) -> set[str]:
    """pipeline 頂層 import 進來的所有名字(專案內外都算)。"""
    out: set[str] = set()
    for n in tree.body:
        if isinstance(n, ast.Import):
            out |= {(a.asname or a.name).split(".")[0] for a in n.names}
        elif isinstance(n, ast.ImportFrom):
            out |= {a.asname or a.name for a in n.names}
    return out


def test_pipeline_uses_every_name_it_imports():
    """沒有只為了讓別人經 pipeline 取用而 import 的名字。"""
    tree = _tree(PIPELINE)
    used = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    used |= {n.value.id for n in ast.walk(tree)
             if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)}
    # 字串型別標註("PermitLookup | None" 之類)裡的名字也算用到
    for n in ast.walk(tree):
        if isinstance(n, ast.Constant) and isinstance(n.value, str):
            used |= set(n.value.replace("|", " ").replace("[", " ").replace("]", " ").split())
    assert sorted(_bound_names(tree) - used) == []


def _pipeline_aliases(tree: ast.Module) -> set[str]:
    out = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            out |= {a.asname or a.name for a in n.names if a.name == "pipeline"}
    return out


def test_nobody_takes_other_modules_names_through_pipeline():
    borrowed = _imported_from_project(_tree(PIPELINE))
    found = []
    for path in sorted(ROOT.glob("*.py")):
        if path == PIPELINE:
            continue
        tree = _tree(path)
        aliases = _pipeline_aliases(tree)
        for n in ast.walk(tree):
            if isinstance(n, ast.ImportFrom) and n.module == "pipeline":
                found += [f"{path.name}: from pipeline import {a.name}"
                          for a in n.names if a.name in borrowed]
            elif (isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)
                  and n.value.id in aliases and n.attr in borrowed):
                found.append(f"{path.name}:{n.lineno} {n.value.id}.{n.attr}")
    # monkeypatch.setattr(P, "run_vision_items", …) 不算:那是替換 pipeline 自己用的綁定
    assert found == []
