#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""23_rerun_03_current_version.py —— 用**当前版本**的 03 重跑一遍，产物落到 out/03b/

## 为什么需要这一步

`out/03_重启一致性_D2880_anchor2880.json` 生成于 **06:22:29Z**，而
`03_restart_consistency.py` 的定稿时刻是 **06:22:37Z** —— 那份产物跑在**已经不存在的
脚本版本**上。它给出的「候选日志差异 6 个、连续 47 / 重建 49」与另外三处互相印证的
数字（15 号 51/51、19 号 51、生产 `history_candidates_discarded=51`）冲突，且逐币比对
有 10 个币的候选计数对不上（例如 1000PEPEUSDT：03 说 0 个，15/16/17 都说 1 个）。

所以必须用**当前磁盘上的** 03 重跑一次，看它是否收敛到 51。这是「差异是否还能复现」
的最后一块拼图。

## 怎么保证不覆盖旧证据

不复制、不改写 03 的逻辑，只把公共件 `00_common.OUT` 指到 `out/03b/` 子目录，
再调用它的 `main()`。旧产物 `out/03_重启一致性_D2880_anchor2880.json` **原样保留**。

## 口径锁定

`--upto 2026-10-25T04:59:59Z` 显式钉住，避免「现在」漂移（行情库已前进，默认值会变成
更新的截止时刻，那样两次运行就不可比）。
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
R1 = HERE.parent
C06 = R1.parent / "SIGNAL-CLOSURE-06"
sys.path.insert(0, str(C06 / "code"))

cm = importlib.import_module("00_common")
out_dir = R1 / "out" / "03b"
out_dir.mkdir(parents=True, exist_ok=True)
cm.OUT = out_dir
print(f"[cfg] 03 输出目录已重定向到 {out_dir}")

mod = importlib.import_module("03_restart_consistency")
sys.argv = ["03_restart_consistency.py"] + sys.argv[1:]
rc = mod.main()

print(f"[out] {out_dir}")
for f in sorted(out_dir.iterdir()):
    print(f"   {f.name}  {f.stat().st_size} B")
sys.exit(rc)
