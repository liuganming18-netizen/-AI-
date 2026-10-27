# -*- coding: utf-8 -*-
"""SIGNAL-HISTORY-FIX-02｜公共定义（沿用 FIX-01 口径，只改证据目录）。

纪律（全单统一，写死在代码里而不是只写在报告里）：
  · **只读**行情库 `market_v2.sqlite3`；不下载、不联网、不写行情库。
  · **不另建策略引擎**：所有信号计算一律走既有
    `框架/backend/unified_runtime.py`（`MultiSymbolSignalEngine` / `RealSignalEngine`）
    → `策略/WyckoffMaster/event_trend_v0_1/state_machine.py`（`EventStateMachine`）。
    本目录只做「驱动 + 观测 + 记录」，不做「重新实现条件」。
  · **不改冻结口径**：`EventTrendConfig` 一个字段都不动；`box_bars_4h=42`、
    `er_threshold=0.30`、`2A~8A`、风险与执行规则全部沿用。
  · 本单所有写入都在 `evidence/SIGNAL-HISTORY-FIX-02/` 内。
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import sys
from pathlib import Path

BASE = Path("/Users/boos/Desktop/威科夫因子共振趋势")
ME = BASE / "evidence/SIGNAL-HISTORY-FIX-02"
OUT = ME / "out"
BACKEND = BASE / "框架/backend"

for _p in (str(BACKEND), str(BASE), str(BASE / "策略")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

UTC = dt.timezone.utc
H1 = 3_600_000
M15 = 900_000
H4 = 4 * H1
DAY = 24 * H1

EXCHANGE = "binance"
MARKET = "um"

#: 工单要求的历史读取目标（不等于策略窗口）
TARGET_DAYS = 180


def iso(ms) -> str | None:
    if ms is None:
        return None
    return dt.datetime.fromtimestamp(int(ms) / 1000, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def utc_now() -> str:
    return dt.datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def market_conn():
    """只读行情库连接（统一 PRAGMA，与 backend 的 _connect_ro 同口径）。"""
    import sqlite3
    import live_store as L
    con = sqlite3.connect(f"file:{L.MARKET_DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA cache_size=-262144")
    con.execute("PRAGMA temp_store=MEMORY")
    return con


def universe():
    """当前币池（唯一事实源 `live_pool.scope_symbols()`）。"""
    import live_pool as LP
    syms, meta = LP.scope_symbols()
    return list(syms), meta


def config():
    """冻结配置（**不做任何覆盖**，与生产同一份）。"""
    from 策略.WyckoffMaster.event_trend_v0_1.config import EventTrendConfig
    return EventTrendConfig()


def write_json(name: str, obj) -> Path:
    OUT.mkdir(parents=True, exist_ok=True)
    p = OUT / name
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return p


def new_engine(symbols, history_floor_ms=0, enable_oi_filter=False):
    """构造既有引擎（不包装、不继承、不改）。"""
    import unified_runtime as UR
    eng = UR.MultiSymbolSignalEngine(list(symbols), enable_oi_filter=enable_oi_filter)
    eng.history_floor_ms = int(history_floor_ms)
    return eng


def config_identity() -> dict:
    """冻结口径身份（供报告与复算引用）。"""
    import inspect
    from 策略.WyckoffMaster.event_trend_v0_1 import config as cfgmod
    from 策略.WyckoffMaster.event_trend_v0_1 import state_machine as smmod
    import unified_runtime as UR
    c = config()
    return {
        "config_file": str(Path(cfgmod.__file__)),
        "config_file_sha256": sha256_file(Path(cfgmod.__file__)),
        "state_machine_file": str(Path(smmod.__file__)),
        "state_machine_sha256": sha256_file(Path(smmod.__file__)),
        "unified_runtime_file": str(Path(UR.__file__)),
        "unified_runtime_sha256": sha256_file(Path(UR.__file__)),
        "frozen_params": {
            "box_bars_4h": c.box_bars_4h,
            "atr_period_4h": c.atr_period_4h,
            "atr_warmup_bars": c.atr_warmup_bars,
            "er_threshold": c.er_threshold,
            "box_min_width_atr": c.box_min_width_atr,
            "box_max_width_atr": c.box_max_width_atr,
            "sweep_max_hours": c.sweep_max_hours,
            "sweep_max_overshoot_atr": c.sweep_max_overshoot_atr,
            "sweep_min_recovery_atr": c.sweep_min_recovery_atr,
            "retest_max_hours": c.retest_max_hours,
            "retest_max_zone_atr": c.retest_max_zone_atr,
            "retest_vol_multiplier": c.retest_vol_multiplier,
            "retest_fail_buffer_atr": c.retest_fail_buffer_atr,
            "breakout_buffer_atr": c.breakout_buffer_atr,
            "breakout_vol_lookback": c.breakout_vol_lookback,
            "breakout_vol_multiplier": c.breakout_vol_multiplier,
            "breakout_max_hours_after_retest": c.breakout_max_hours_after_retest,
            "breakout_max_total_hours": c.breakout_max_total_hours,
            "cooling_hours": c.cooling_hours,
            "oi_min_growth_pct": c.oi_min_growth_pct,
            "oi_lookback_hours": c.oi_lookback_hours,
        },
    }


#: 状态机可能到达的状态（含终态），用于逐币漏斗列名
ALL_STATES = ("BOX", "SWEEP", "RETEST", "CANDIDATE",
              "VOLUME_REJECTED", "OI_UNAVAILABLE", "OI_REJECTED",
              "INVALIDATED", "EXPIRED", "MISSED_WITHOUT_RETEST")

#: 终态（事件结束）
TERMINAL_STATES = ("CANDIDATE", "VOLUME_REJECTED", "OI_UNAVAILABLE", "OI_REJECTED",
                   "INVALIDATED", "EXPIRED", "MISSED_WITHOUT_RETEST")
