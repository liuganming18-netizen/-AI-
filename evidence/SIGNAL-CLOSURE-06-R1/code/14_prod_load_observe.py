# -*- coding: utf-8 -*-
"""SIGNAL-CLOSURE-06-R1｜14 受控加载修复到生产模拟环境，并跨两根新增 1h 线观察。

**为什么要跑这个**：`unified_runtime.py` 的 mtime 是 `2026-10-25T06:00:47Z`，
而在跑的运行时 pid 79149 启动于 `04:05:05Z` ⇒ **进程加载的是旧代码，修复未生效**，
`live_run.state_anchor_ms` 也确实是空的（本单实测）。工单第 3 条要求按受控流程加载。

本脚本不重写逻辑：`importlib` 载入 06 的 `07_prod_load_observe.py`，只改输出目录。
它做的事（与 06 完全一致）：
  1. 记录加载前账本/代码 SHA/进程身份/1h 水位；
  2. `service_manager.py restart --enable-paper --arm 3a --symbol all`（唯一允许的加载方式，
     采集器按防误停策略不动）；
  3. 等 **两根新增 1h 收盘**，每根采一次：逐币水位、逐币阶段变化、漏斗、候选、订单处理；
  4. 记录加载后账本身份，逐项对比。

判据（任一不成立即 FAIL）：
  · 运行时 status=running 且 pid 活着，`bootstrap.ready` = 全池标的数；
  · `replay_mode = absolute_anchor` 且锚点非空、**重启后不前移**；
  · 跨两根新增 1h：水位逐根前进、`lag_1h_max <= 1`；
  · 候选/拒单有明确计数（允许 0，但必须是有依据的 0）；
  · 生产账本持仓/成交/已实现盈亏未被本脚本改动；
  · 采集器仍在。

用法：python code/14_prod_load_observe.py [--max-wait-min 150]
产出：out/14_生产加载观察.json、out/14_逐根水位.csv、out/14_逐币阶段变化.csv
"""
from __future__ import annotations

import importlib
import json
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
R1 = HERE.parent
C06 = R1.parent / "SIGNAL-CLOSURE-06"
OUT = R1 / "out"
OUT.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(C06 / "code"))
cm = importlib.import_module("00_common")
cm.OUT = OUT


def main() -> int:
    mod = importlib.import_module("07_prod_load_observe")
    mod.cm = cm
    sys.argv = ["07_prod_load_observe.py"] + sys.argv[1:]
    rc = mod.main()

    src = OUT / "07_生产加载观察.json"
    if src.exists():
        d = json.loads(src.read_text(encoding="utf-8"))
        d["r1_why"] = ("生产 pid 79149 启动于 04:05:05Z，而 unified_runtime.py 定稿于 "
                       "06:00:47Z ⇒ 加载前跑的是旧代码、state_anchor_ms 为空。"
                       "本文件是受控加载后的观察。")
        (OUT / "14_生产加载观察.json").write_text(
            json.dumps(d, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        src.unlink()
        print(f"[out] 14_生产加载观察.json  verdict={d.get('verdict')}")
    for a, b in (("07_逐根水位.csv", "14_逐根水位.csv"),
                 ("07_逐币阶段变化.csv", "14_逐币阶段变化.csv")):
        if (OUT / a).exists():
            shutil.move(str(OUT / a), str(OUT / b))
    return rc


if __name__ == "__main__":
    sys.exit(main())
