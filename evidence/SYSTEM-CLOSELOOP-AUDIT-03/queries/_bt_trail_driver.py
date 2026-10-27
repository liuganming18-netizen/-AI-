# -*- coding: utf-8 -*-
"""回测侧驱动：用**回测策略自己的** `custom_stoploss` 跑一遍 4H 跟踪止损轨迹。

为什么单独一个文件、单独一个解释器：
  · 回测侧实现是 `evidence/FT-LOW4H-GRID-01/user_data/strategies/WyckoffLow4hGridBase.py`
    （58,289 字节，144 组网格的基类），它 `from freqtrade.strategy import IStrategy`
    ⇒ 只能在装了 freqtrade 的解释器里跑（evidence/FT-BASELINE-01/.venv，python 3.11）；
  · 模拟侧 paper_engine 在默认 3.13 解释器里跑。
  ⇒ 两侧各用**自己的真实实现**，通过 JSON 交接，绝不在同一进程里"重写一遍规则"。

**不重写规则**：本脚本只负责造 trade / candidate / 4h 数据帧，然后调用
`WyckoffLow4hGridBase.custom_stoploss(...)` 原函数，读它自己维护的
`state["current_effective_stop"]` 作为轨迹。规则一行都不在这里实现。

用法：
  <freqtrade-venv-python> _bt_trail_driver.py <bars.json> <out.json>
"""
from __future__ import annotations

import importlib.util
import json
import sys
import types
from datetime import datetime, timezone

import pandas as pd

BASE_DIR = "/Users/boos/Desktop/威科夫因子共振趋势"
STRAT = BASE_DIR + "/evidence/FT-LOW4H-GRID-01/user_data/strategies/WyckoffLow4hGridBase.py"
PAIR = "BTCUSDT"


def load_base():
    spec = importlib.util.spec_from_file_location("wyckoff_low4h_base", STRAT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.WyckoffLow4hGridBase


def main() -> None:
    bars_path, out_path = sys.argv[1], sys.argv[2]
    spec = json.loads(open(bars_path, encoding="utf-8").read())
    bars = spec["bars"]                 # [{open_ms, close_ms, low, close}, ...]
    entry_price = float(spec["entry_price"])
    init_stop = float(spec["init_stop"])
    stoploss_s = float(spec.get("stoploss_s", 0.15))
    trail_d = float(spec.get("trail_d", 0.05))

    Base = load_base()
    obj = Base.__new__(Base)            # 跳过 IStrategy.__init__（不需要配置/钱包）

    # 4H 数据帧：列名必须与策略 L828 一致（close_ms/close/low/l10）
    df_4h = pd.DataFrame({
        "close_ms": [int(b["close_ms"]) for b in bars],
        "close": [float(b["close"]) for b in bars],
        "low": [float(b["low"]) for b in bars],
        "l10": [0.0] * len(bars),
    })
    obj.pair_4h_df = {PAIR: df_4h}
    obj.pair_candidates = {PAIR: [types.SimpleNamespace(
        candidate_id="audit04", initial_stop_price=init_stop)]}
    obj.active_trade_stop_state = {}
    obj.stoploss_s = stoploss_s
    obj.trail_d = trail_d

    trade = types.SimpleNamespace(
        id=1, enter_tag="audit04", open_rate=entry_price,
        open_date_utc=datetime.fromtimestamp(int(bars[0]["open_ms"]) / 1000, tz=timezone.utc))

    # 预置状态：把初始止损对齐到与模拟侧相同的值（隔离"初始止损推导"这一段差异，
    # 只比"跟踪段"）。策略源码 L1151 只在 state is None 时才自建，所以预置有效。
    obj.active_trade_stop_state[trade.id] = {
        "cand_id": "audit04", "initial_stop": init_stop,
        "current_effective_stop": init_stop, "last_bar_close_ms": 0,
        "entry_time_ms": int(bars[0]["open_ms"]),
    }

    traj = []
    for i, b in enumerate(bars):
        t_ms = int(b["close_ms"])
        cur_dt = datetime.fromtimestamp(t_ms / 1000, tz=timezone.utc)
        # ① 用收盘价推进（不触发退出），读策略自己维护的生效止损
        obj.custom_stoploss(PAIR, trade, cur_dt, float(b["close"]), 0.0, False)
        eff = float(obj.active_trade_stop_state[trade.id]["current_effective_stop"])
        last = int(obj.active_trade_stop_state[trade.id]["last_bar_close_ms"])
        # ② 用本 bar 最低价再问一次：返回 0.0 表示"止损落在当前价上"⇒ 该 bar 内被击穿
        r_low = float(obj.custom_stoploss(PAIR, trade, cur_dt, float(b["low"]), 0.0, False))
        traj.append({
            "i": i, "bar_open_ms": int(b["open_ms"]), "bar_close_ms": t_ms,
            "low": float(b["low"]), "close": float(b["close"]),
            "candidate_stop": round(float(b["low"]) * (1.0 - trail_d), 10),
            "effective_stop": eff,
            "state_last_bar_close_ms": last,
            "exit_by_low": bool(r_low == 0.0 and float(b["low"]) <= eff),
        })

    out = {
        "impl": "evidence/FT-LOW4H-GRID-01/user_data/strategies/WyckoffLow4hGridBase.py::custom_stoploss",
        "stoploss_s": stoploss_s, "trail_d": trail_d,
        "entry_price": entry_price, "init_stop": init_stop,
        "traj": traj,
    }
    open(out_path, "w", encoding="utf-8").write(
        json.dumps(out, ensure_ascii=False, indent=1))
    print(json.dumps({"ok": True, "n": len(traj), "final_stop": traj[-1]["effective_stop"]},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
