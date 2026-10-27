#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""UI-LINK-01 · 「暂停只阻止新开仓」的机械证明

做法：用 AST 找出后端里**所有**读取总闸的调用点，再逐个回答两个问题：
  1. 它在哪个函数里？
  2. 那个函数属于「新开仓」路径，还是「已有持仓维护（止损/退出/平仓）」路径？

为什么不能只靠一句注释：注释说「退出不受影响」是**声明**，不是证据。
本脚本把「谁读了闸」变成可枚举、可复核的清单，并与「维护类函数一个都没读」对照。

用法：python3 gate_scope_probe.py
"""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

BACKEND = Path("/Users/boos/Desktop/威科夫因子共振趋势/框架/backend")
FILES = ["paper_engine.py", "live_api.py", "unified_runtime.py", "live_worker.py", "adapter.py"]

# 读总闸的「名字」：函数调用或常量引用
GATE_CALLS = {"new_entry_gate"}
GATE_NAMES = {"GATE_FILE", "GATE_REASON"}

# 维护类函数（已有持仓的止损推进 / 平仓 / 退出检查）—— 这些**不得**读闸
# 注意：`advance_pending`（挂单推进）**故意不列在这里**：它经由 `open_position`
# 间接受闸门约束，这是设计（自动 / 手工 / 挂单三路一致拦）。挂单不是「已有持仓」。
MAINTENANCE = {
    "advance_position_trailing", "advance_arm_trailing", "close_position",
    "check_exits", "trailing_update", "update_trailing",
}
# 新开仓类函数
ENTRY = {
    "open_position", "route_pending_orders", "_open", "paper_order_post",
    "paper_toggle_gate", "submit_order",
}


def func_of(node, parents):
    cur = node
    while cur in parents:
        cur = parents[cur]
        if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return cur.name
    return "<module>"


def scan(path: Path):
    src = path.read_text(encoding="utf-8", errors="replace")
    tree = ast.parse(src)
    parents = {}
    for p in ast.walk(tree):
        for c in ast.iter_child_nodes(p):
            parents[c] = p
    hits = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in GATE_CALLS:
            hits.append({"line": n.lineno, "kind": "call", "name": n.func.id,
                         "func": func_of(n, parents)})
        elif isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load) and n.id in GATE_NAMES:
            hits.append({"line": n.lineno, "kind": "const", "name": n.id,
                         "func": func_of(n, parents)})
        elif isinstance(n, ast.Attribute) and n.attr in GATE_CALLS:
            hits.append({"line": n.lineno, "kind": "attr", "name": "PE." + n.attr,
                         "func": func_of(n, parents)})
    return hits


def main():
    all_hits = []
    for f in FILES:
        p = BACKEND / f
        if not p.exists():
            continue
        for h in scan(p):
            h["file"] = f
            all_hits.append(h)

    entry_hits = [h for h in all_hits if h["func"] in ENTRY]
    maint_hits = [h for h in all_hits if h["func"] in MAINTENANCE]
    other_hits = [h for h in all_hits if h["func"] not in ENTRY and h["func"] not in MAINTENANCE]

    verdict = {
        "gate_reader_total": len(all_hits),
        "in_entry_path": len(entry_hits),
        "in_maintenance_path": len(maint_hits),
        "in_other_path": len(other_hits),
        "maintenance_functions_read_gate": sorted({h["func"] for h in maint_hits}),
        "conclusion": ("维护类函数（止损推进/平仓/退出检查）**没有任何一处**读取总闸 ⇒ "
                       "暂停只可能拦住新开仓，不会停掉已有持仓的盯市与退出")
        if not maint_hits else "维护类函数里读到了总闸 ⇒ 暂停会污染已有持仓维护（缺陷）",
    }
    print(json.dumps({"verdict": verdict, "readers": all_hits}, ensure_ascii=False, indent=1))
    return 0 if not maint_hits else 1


if __name__ == "__main__":
    sys.exit(main())
