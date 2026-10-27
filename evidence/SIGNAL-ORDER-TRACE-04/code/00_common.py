# -*- coding: utf-8 -*-
"""SIGNAL-ORDER-TRACE-04｜公共定义。

纪律（写死在代码里，不只写在报告里）：
  · **不另建引擎**：信号一律走既有
    `框架/backend/unified_runtime.py::RealSignalEngine` /
    `策略/WyckoffMaster/event_trend_v0_1/state_machine.py`；
    执行一律走既有 `框架/backend/paper_engine.py`。
    本目录只做「驱动 + 观测 + 记录 + 投影」，不重新实现任何条件或撮合规则。
  · **不改冻结口径**：`EventTrendConfig` 一个字段都不动。
  · **不污染生产**：所有写入都在 `evidence/SIGNAL-ORDER-TRACE-04/` 内；
    生产账本 `数据/paper/` 与行情库 `数据/市场数据/db/market_v2.sqlite3` **只读**。
    隔离靠既有环境变量（`PAPER_LEDGER_DIR` / `MARKET_DB` / `PAPER_GATE_FILE` /
    `LIVE_DIR`），不改任何被测代码。
  · **不伪造身份**：运行内 ID、跨运行键、引擎订单号、交易所订单号四类**分开写**；
    不存在的一律 `None` + 原因，绝不用行号/顺序/近似时间顶替。
"""
from __future__ import annotations

import bisect
import datetime as dt
import hashlib
import json
import os
import sqlite3
import sys
from pathlib import Path

BASE = Path("/Users/boos/Desktop/威科夫因子共振趋势")
ME = BASE / "evidence/SIGNAL-ORDER-TRACE-04"
OUT = ME / "out"
CODE = ME / "code"
BACKEND = BASE / "框架/backend"

for _p in (str(BACKEND), str(BASE), str(BASE / "策略")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

UTC = dt.timezone.utc
M15 = 900_000
H1 = 3_600_000
H4 = 4 * H1
DAY = 24 * H1

EXCHANGE = "binance"
MARKET = "um"

#: 上一单（SIGNAL-HISTORY-FIX-02）用既有路由建成的 180 天隔离行情库（只读复用，不重建）
ISO_DB_180D = BASE / "evidence/SIGNAL-HISTORY-FIX-02/out/180d_market.sqlite3"

#: 本单隔离行情库（在 180 天库基础上补 15m —— 纸面成交只用「已闭合 15m bar」）
ISO_MARKET_DB = OUT / "iso_market.sqlite3"
#: 本单隔离账本目录（**不得**指向 `数据/paper`）
ISO_LEDGER_DIR = OUT / "iso_ledger"
#: 本单隔离闸门文件（fail-closed；默认关，用于「有效拒绝」用例）
ISO_GATE_FILE = OUT / "iso_gate.json"
#: 本单隔离 live 库（live_signal 等）
ISO_LIVE_DIR = OUT / "iso_live"

#: 生产账本目录（只读）
PROD_LEDGER_DIR = BASE / "数据/paper"
PROD_ARM = "3a"


# ══════════════════════════════════════════════════════════════════════
# 基础工具
# ══════════════════════════════════════════════════════════════════════
def iso(ms) -> str | None:
    if ms is None:
        return None
    return dt.datetime.fromtimestamp(int(ms) / 1000, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(s: str) -> int:
    return int(dt.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ")
               .replace(tzinfo=UTC).timestamp() * 1000)


def utc_now() -> str:
    return dt.datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_file(p) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def sha256_text(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def write_json(name: str, obj) -> Path:
    OUT.mkdir(parents=True, exist_ok=True)
    p = OUT / name
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return p


def read_json(p) -> dict:
    return json.loads(Path(p).read_text(encoding="utf-8"))


def market_conn(db=None):
    """只读行情库连接。`db` 缺省 = 当前 `MARKET_DB`（可能是隔离库）。"""
    import live_store as L
    target = Path(db) if db is not None else Path(L.MARKET_DB)
    con = sqlite3.connect(f"file:{target}?mode=ro", uri=True)
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


# ══════════════════════════════════════════════════════════════════════
# 隔离环境（只设环境变量，不改被测代码）
# ══════════════════════════════════════════════════════════════════════
ISO_ENV_KEYS = ("MARKET_DB", "PAPER_LEDGER_DIR", "PAPER_GATE_FILE",
                "PAPER_LEDGER_ALLOW_INIT", "LIVE_DIR", "LIVE_DB")


def iso_env(market_db=None, gate_allow: bool | None = None, ledger_sub: str | None = None) -> dict:
    """构造隔离环境变量并**就地注入** `os.environ`。

    `gate_allow`：
      · `None`  ⇒ 不写闸门文件（沿用已有内容；文件不存在 ⇒ fail-closed 拒绝）
      · `True`  ⇒ 写 `{"allow_new_entries": true}`（正常执行用例）
      · `False` ⇒ 写 `{"allow_new_entries": false}`（有效拒绝用例）
    `ledger_sub`：账本子目录名（每个用例一套独立账本，互不干扰）。
    """
    OUT.mkdir(parents=True, exist_ok=True)
    ledger = ISO_LEDGER_DIR / ledger_sub if ledger_sub else ISO_LEDGER_DIR
    ledger.mkdir(parents=True, exist_ok=True)
    ISO_LIVE_DIR.mkdir(parents=True, exist_ok=True)
    env = {
        "MARKET_DB": str(Path(market_db) if market_db else ISO_MARKET_DB),
        "PAPER_LEDGER_DIR": str(ledger),
        "PAPER_GATE_FILE": str(ISO_GATE_FILE),
        "PAPER_LEDGER_ALLOW_INIT": "1",
        "LIVE_DIR": str(ISO_LIVE_DIR),
        "LIVE_DB": str(ISO_LIVE_DIR / f"live_signals_{ledger_sub or 'base'}.sqlite3"),
    }
    if gate_allow is not None:
        ISO_GATE_FILE.write_text(
            json.dumps({"allow_new_entries": bool(gate_allow),
                        "note": "SIGNAL-ORDER-TRACE-04 隔离闸门（生产闸门不受影响）"},
                       ensure_ascii=False, indent=2), encoding="utf-8")
    os.environ.update(env)
    return env


def assert_isolated() -> dict:
    """**隔离断言**：任一写路径落回生产 ⇒ 直接抛错，不继续跑。"""
    import live_store as L
    got = {
        "MARKET_DB": str(Path(L.MARKET_DB).resolve()),
        "LEDGER_DIR": str(Path(L.LEDGER_DIR).resolve()),
        "LIVE_DIR": str(Path(L.LIVE_DIR).resolve()),
        "gate_file": str(Path(os.environ.get("PAPER_GATE_FILE")
                              or (BACKEND / "live" / "paper_new_entry_gate.json")).resolve()),
    }
    me = str(ME.resolve())
    bad = [k for k, v in got.items() if not v.startswith(me)]
    if bad:
        raise SystemExit(f"隔离失败：{bad} 未落在 {me} 内 ⇒ 拒绝继续（{got}）")
    if got["LEDGER_DIR"] == str(PROD_LEDGER_DIR.resolve()):
        raise SystemExit("隔离失败：账本目录指向生产账本 ⇒ 拒绝继续")
    return got


# ══════════════════════════════════════════════════════════════════════
# 行情读取
# ══════════════════════════════════════════════════════════════════════
def load_bars(con, symbol: str, tf: str, upto_ms: int | None = None) -> list[dict]:
    """读某标的某周期**已收盘** bar，按 open_time 升序、按 open_time 去重。

    与生产 `get_closed_bars` 同口径：`is_closed=1` 且 `close_time <= upto`。
    """
    sql = ("SELECT open_time, close_time, open, high, low, close, volume, quote_volume "
           "FROM candle_versions WHERE symbol=? AND timeframe=? AND is_closed=1")
    args: list = [symbol, tf]
    if upto_ms is not None:
        sql += " AND close_time <= ?"
        args.append(int(upto_ms))
    sql += " ORDER BY open_time"
    out, seen = [], set()
    for r in con.execute(sql, args):
        ot = int(r["open_time"])
        if ot in seen:
            continue
        seen.add(ot)
        out.append({"open_time": ot, "close_time": int(r["close_time"]),
                    "open": float(r["open"]), "high": float(r["high"]),
                    "low": float(r["low"]), "close": float(r["close"]),
                    "volume": float(r["volume"] or 0.0),
                    "quote_volume": float(r["quote_volume"] or 0.0)})
    return out


class FastMemWatcher:
    """`unified_runtime._MemWatcher` 的**语义等价**实现（bisect 定位，O(log n + k)）。

    为什么要它：原生 `_MemWatcher.get_closed_bars` 每次调用都全表线性扫描，
    180 天 × 526 标的会退化成 ~24 亿次迭代（实测无法在可接受时间内完成）。
    本类**只改数据源实现，不改引擎**；等价性由 `02_candidates.py` 的
    `equivalence_check()` 在真实标的上逐位验证（候选集与状态计数必须完全相同）。
    """

    __slots__ = ("b4", "b1", "_o4", "_o1")

    def __init__(self, b4: list[dict], b1: list[dict]):
        self.b4, self.b1 = b4, b1
        self._o4 = [int(b["open_time"]) for b in b4]
        self._o1 = [int(b["open_time"]) for b in b1]

    def get_closed_bars(self, timeframe: str, start_open_ms: int, end_open_ms: int):
        src, idx = ((self.b4, self._o4) if timeframe == "4h" else (self.b1, self._o1))
        lo = bisect.bisect_left(idx, int(start_open_ms))
        out = []
        for b in src[lo:]:
            if int(b["close_time"]) > int(end_open_ms):
                break
            out.append(b)
        return out


# ══════════════════════════════════════════════════════════════════════
# 身份口径（本单第 1 条的核心）
# ══════════════════════════════════════════════════════════════════════
#: 候选快照里**参与跨运行键**的字段（都是策略语义量，不含任何计数器/时间戳噪声）
CROSS_RUN_FIELDS = (
    "symbol", "available_at_ms", "snapshot_time_ms",
    "box_high_h", "box_low_l", "box_atr_a", "box_er",
    "sweep_low_s", "retest_low_lr", "breakout_close",
    "breakout_volume", "v20_median_volume",
    "initial_stop_price", "execution_earliest_ms",
)


def cross_run_key(c) -> str:
    """**跨运行匹配键**：候选语义量的规范化哈希。

    为什么需要它（而不是直接用 `candidate_id`）：
      `candidate_id = cand_{event_id}_{breakout_close_ms}`，而
      `event_id = ev_{strategy}_{venue}_{symbol}_{box_formation_close_ms}`。
      两者都**确定性**，但都依赖「箱体建档时刻」——换窗口起点会让建档时刻平移，
      `candidate_id` 随之变化，**同一个市场形态会得到不同的 ID**。
      本键只取「形态本身的量」，与窗口/运行无关 ⇒ 换窗口也能对上同一形态。
    """
    payload = {k: getattr(c, k, None) for k in CROSS_RUN_FIELDS}
    return sha256_text(json.dumps(payload, sort_keys=True, default=str))


def candidate_row(c) -> dict:
    """把 `CandidateSnapshot` 展开成「四类身份分开写」的一行。"""
    return {
        # ① 运行内 ID（含 event_id；确定性但**随窗口起点变化**）
        "candidate_id": c.candidate_id,
        "event_id": c.event_id,
        # ② 跨运行匹配键（形态哈希；与窗口/运行无关）
        "cross_run_key": cross_run_key(c),
        # ③ 引擎订单号 / ④ 交易所订单号：候选阶段**尚不存在**，显式 None
        "engine_order_id": None,
        "exchange_order_id": None,
        "order_absent_reason": "候选阶段尚未下单（本字段由执行链写入）",
        # 语义量
        "symbol": c.symbol,
        "venue": c.venue,
        "strategy_version": c.strategy_version,
        "snapshot_time_ms": int(c.snapshot_time_ms),
        "snapshot_time_utc": iso(c.snapshot_time_ms),
        "available_at_ms": int(c.available_at_ms),
        "available_at_utc": iso(c.available_at_ms),
        "execution_earliest_ms": int(c.execution_earliest_ms),
        "execution_earliest_utc": iso(c.execution_earliest_ms),
        "box_high_h": c.box_high_h,
        "box_low_l": c.box_low_l,
        "box_atr_a": c.box_atr_a,
        "box_er": c.box_er,
        "sweep_low_s": c.sweep_low_s,
        "retest_low_lr": c.retest_low_lr,
        "breakout_close": c.breakout_close,
        "breakout_volume": c.breakout_volume,
        "v20_median_volume": c.v20_median_volume,
        "volume_ratio": c.volume_ratio,
        "oi_breakout": c.oi_breakout,
        "oi_6h_ago": c.oi_6h_ago,
        "oi_growth_6h": c.oi_growth_6h,
        "initial_stop_price": c.initial_stop_price,
        "initial_risk_r": c.initial_risk_r,
        "max_entry_price": c.max_entry_price,
        "min_entry_price": c.min_entry_price,
        "snapshot_hash": c.snapshot_hash,
        "pit_proven": c.pit_proven,
    }


def engine_identity() -> dict:
    """被测代码身份（文件 SHA-256；版本只认文件哈希）。"""
    import inspect
    import unified_runtime as UR
    import paper_engine as PE
    import live_store as L
    from 策略.WyckoffMaster.event_trend_v0_1 import config as cfgmod
    from 策略.WyckoffMaster.event_trend_v0_1 import state_machine as smmod
    from 策略.WyckoffMaster.event_trend_v0_1 import contracts as ctmod
    out = {}
    for tag, mod in (("unified_runtime", UR), ("paper_engine", PE),
                     ("live_store", L), ("event_trend_config", cfgmod),
                     ("event_trend_state_machine", smmod),
                     ("event_trend_contracts", ctmod)):
        p = Path(inspect.getfile(mod))
        out[tag] = {"path": str(p), "sha256": sha256_file(p),
                    "size": p.stat().st_size}
    c = config()
    out["frozen_params"] = {
        "box_bars_4h": c.box_bars_4h,
        "atr_period_4h": c.atr_period_4h,
        "atr_warmup_bars": c.atr_warmup_bars,
        "er_threshold": c.er_threshold,
        "box_min_width_atr": c.box_min_width_atr,
        "box_max_width_atr": c.box_max_width_atr,
        "cooling_hours": c.cooling_hours,
        "breakout_max_total_hours": c.breakout_max_total_hours,
    }
    return out
