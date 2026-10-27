# -*- coding: utf-8 -*-
"""SYSTEM-RUNTIME-01｜真实信号到自动执行统一运行环境与调度入口。

具备职责：
  1. 完整运行链闭环：行情 -> 信号生成 -> 信号持久化 -> 风控与订单 -> 执行撮合 -> 持仓守护与退出；
  2. 真实信号引擎驱动：复用可运行的确定性状态机（EventStateMachine），严禁直接调用开仓函数；
  3. 单实例独占锁：同机严格单一进程执行（fcntl.flock），异常退出清理，杜绝多进程竞争；
  4. 断点恢复与热重启：持仓中重启后无缝接管既有持仓，不重复建仓、不丢失跟踪止损与退出防线；
  5. 状态全景如实暴露：细分 market_data, signal_engine, order_processing, execution_daemon 状态，
     不以 API 存活代替系统健康；
  6. 行情与参数服务端可信来源：严格从 SQLite 行情库读取闭合 K 线，禁止日常注入或配置篡改。
"""
from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import hashlib
import json
import logging
import os
import signal
import sqlite3
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import numpy as np

HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parents[1].resolve() if len(HERE.parents) >= 2 else HERE.parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import live_store as L
import live_pool as LP
import paper_engine as PE
import strategy_contracts as SC
from strategies.wx_execution import apply_order_rounding
from 策略.WyckoffMaster.event_trend_v0_1.config import EventTrendConfig
from 策略.WyckoffMaster.event_trend_v0_1.contracts import CandidateSnapshot

try:
    from telegram_notifier import get_notifier
except Exception:
    get_notifier = None
from 策略.WyckoffMaster.event_trend_v0_1.features import (compute_efficiency_ratio,
                                                          compute_wilder_atr_series)
from 策略.WyckoffMaster.event_trend_v0_1.state_machine import EventStateMachine

logger = logging.getLogger("unified_runtime")

M15 = 900_000
H1 = 3_600_000
H4 = 4 * H1
DAY = 24 * H1

# ── 信号身份口径（**唯一常量**，读写两侧共用，禁止各处硬编码）──────────────────
# 缺陷背景（本窗口核实）：`route_pending_orders` 的扫描只按 `strategy_key` 过滤，
# 既没限定交易所/市场，也没校验事件角色 ⇒ 一旦库内混入其它交易所（okx/swap）或
# 结构提示行，会被当成可执行信号路由去开仓（用错行情/错合约参数），且**全库扫描**。
# 现在：交易所/市场/周期 + 事件角色 四项一起限定；角色口径与界面
# （`SignalListPanel.isCurrentSignal`）和后端当前区（`live_api` scope=current）**完全一致**。
EXCHANGE = "binance"
MARKET = "um"
TIMEFRAME = "1h"
STRATEGY_KEY = "wyckoff_v0_1"      # 真实策略键（此前误用 arm 名占位）
ARM_ENTRY_ROLE = "enterable_arm_entry"
# 纸面账本里**永不可成交**的终态拒单 ⇒ 只有这些才可写 `paper_processed`（永久拉黑）；
# 其余拒单（仓位已满 / 参数缺失 / 行情库不可用 / 闸门暂停）按 paper_engine 设计**可重试**。
TERMINAL_REJECT_REASONS = ("missed_fill_moment_no_backfill", "before_paper_epoch_no_backfill")


# 指数 / 合成标的：不参与「可交易币」口径（观察 528 → 允许 526）
UNIVERSE_EXCLUDE = ("ALLUSDT", "BTCDOMUSDT")


def _default_universe() -> List[str]:
    """全池标的（526 个）。

    来源优先级：
      1. `live_pool.scope_symbols()` 的 `scope_crypto_v1`（唯一事实源，526）；
      2. 该事实源文件已随旧工作区删除时，**退回行情库实际覆盖的标的**
         （`candle_heads` 里真实有 1h 数据的 528 个，排除 ALLUSDT / BTCDOMUSDT ⇒ 526）；
      3. 都取不到才退化为单标的，并由 `bootstrap` 统计如实暴露（不静默）。
    """
    try:
        syms, meta = LP.scope_symbols()
        if syms and meta.get("pool_source") == "scope_crypto_v1":
            return list(syms)
    except Exception:                                    # noqa: BLE001
        pass
    try:
        con = L.market_conn()
        try:
            rows = con.execute(
                "SELECT DISTINCT symbol FROM candle_heads "
                "WHERE exchange=? AND market=? AND timeframe=? ORDER BY symbol",
                (EXCHANGE, MARKET, TIMEFRAME)).fetchall()
        finally:
            con.close()
        syms = [r[0] for r in rows if r[0] and r[0] not in UNIVERSE_EXCLUDE]
        if syms:
            return syms
    except Exception:                                    # noqa: BLE001
        pass
    return ["BTCUSDT"]


def _iso_ms(ms) -> Optional[str]:
    """毫秒时间戳 → UTC ISO（只用于展示/证据，不参与任何比较）。"""
    if ms is None:
        return None
    return dt.datetime.fromtimestamp(int(ms) / 1000, dt.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def _slim_history(st4: dict, st1: dict) -> dict:
    """把 `history_loader` 的统计压成「能放进每 2 秒写一次的状态文件」的大小。

    逐币覆盖表（526 行）只进证据 JSON，**不进状态文件**；否则状态文件会被撑到几百 KB，
    每轮写盘/前端拉取都会变慢。
    """
    out: dict = {}
    for tf, st in (("4h", st4), ("1h", st1)):
        per = (st or {}).get("per_timeframe", {}).get(tf, {})
        out[tf] = {
            "cold_rows_scanned": per.get("cold_rows_scanned"),
            "hot_rows_scanned": per.get("hot_rows_scanned"),
            "overlap_rows": per.get("overlap_rows"),
            "overlap_value_mismatch": per.get("overlap_value_mismatch"),
            "rows_materialized": per.get("rows_materialized"),
            "symbols_with_data": per.get("symbols_with_data"),
            "symbols_full_window": per.get("symbols_full_window"),
            "missing_bars_total": per.get("missing_bars_total"),
            "bars_min": per.get("bars_min"), "bars_max": per.get("bars_max"),
            "listed_late_n": per.get("listed_late_n"),
            "listed_late": (per.get("listed_late") or [])[:30],
            "cold_max_open_utc": per.get("cold_max_open_utc"),
            "source_split": per.get("source_split"),
        }
    out["read_window"] = (st4 or {}).get("read_window")
    out["archive_state"] = (st4 or {}).get("archive_state")
    return out


def get_lock_dir() -> Path:
    if os.environ.get("PAPER_LOCK_DIR"):
        return Path(os.environ["PAPER_LOCK_DIR"])
    if os.environ.get("PAPER_LEDGER_DIR"):
        return Path(os.environ["PAPER_LEDGER_DIR"]).parent / "locks"
    return HERE / "locks"


def status_file_path(arm: str) -> Path:
    lock_d = get_lock_dir()
    return lock_d / f"paper_runtime_{arm}_status.json"


# ═══════════════════════════════════════════════════════════════════════
# 1. 行情监听与时钟推进器 (MarketDataWatcher)
# ═══════════════════════════════════════════════════════════════════════
class MarketDataWatcher:
    """服务端可信行情读取与因果边界防护。"""

    def __init__(self, symbol: str = "BTCUSDT"):
        self.symbol = symbol

    def get_closed_bars(self, timeframe: str, start_open_ms: int, end_open_ms: int) -> List[dict]:
        """按时间顺序获取指定周期内所有已闭合 K 线。"""
        con = L.market_conn()
        try:
            cur = con.execute(
                "SELECT open_time, close_time, open, high, low, close, volume, quote_volume "
                "FROM candles WHERE exchange='binance' AND market='um' AND is_closed=1 AND symbol=? AND timeframe=? AND open_time>=? AND close_time<=? "
                "ORDER BY open_time ASC",
                (self.symbol, timeframe, start_open_ms, end_open_ms)
            )
            return [dict(r) for r in cur.fetchall()]
        finally:
            con.close()

    def get_latest_closed_bar(self, timeframe: str, upto_ms: int) -> Optional[dict]:
        """获取截至指定挂钟时点最新一根已闭合 K 线。"""
        con = L.market_conn()
        try:
            cur = con.execute(
                "SELECT open_time, close_time, open, high, low, close, volume, quote_volume "
                "FROM candles WHERE exchange='binance' AND market='um' AND is_closed=1 AND symbol=? AND timeframe=? AND close_time<=? "
                "ORDER BY close_time DESC LIMIT 1",
                (self.symbol, timeframe, upto_ms)
            )
            r = cur.fetchone()
            return dict(r) if r else None
        finally:
            con.close()

    def seed_dataset(self, candles: List[dict]):
        """向独立行情源注入版本化测试数据集，确保因果边界完整。"""
        mdb = os.environ.get("MARKET_DB") or str(L.MARKET_DB)
        con = sqlite3.connect(mdb)
        try:
            con.execute("""
            CREATE TABLE IF NOT EXISTS candle_dataset (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                exchange TEXT NOT NULL,
                market TEXT NOT NULL,
                symbol TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                open_time INTEGER NOT NULL,
                close_time INTEGER NOT NULL,
                open TEXT NOT NULL,
                high TEXT NOT NULL,
                low TEXT NOT NULL,
                close TEXT NOT NULL,
                volume TEXT NOT NULL,
                quote_volume TEXT NOT NULL,
                is_activated INTEGER NOT NULL DEFAULT 0
            )
            """)
            for c in candles:
                con.execute(
                    "INSERT INTO candle_dataset(exchange, market, symbol, timeframe, open_time, close_time, open, high, low, close, volume, quote_volume, is_activated) "
                    "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)",
                    (
                        c.get("exchange", "binance"),
                        c.get("market", "um"),
                        c["symbol"],
                        c["timeframe"],
                        int(c["open_time"]),
                        int(c["close_time"]),
                        f"{float(c['open']):.2f}",
                        f"{float(c['high']):.2f}",
                        f"{float(c['low']):.2f}",
                        f"{float(c['close']):.2f}",
                        f"{float(c.get('volume', 1000.0)):.2f}",
                        f"{float(c.get('quote_volume', 100000.0)):.2f}"
                    )
                )
            con.commit()
        finally:
            con.close()

    def sync_dataset_bars_upto(self, upto_ms: int):
        """将版本化测试数据集中**本标的** close_time <= upto_ms 的 K 线激活到 candles 视图。

        限定交易所 / 市场 / 标的：原实现只按 `close_time` 扫全表，数据集里一旦有多个
        标的，任意一个标的推进时钟就会把**别的标的**的 K 线一起激活（串数据 + 全表扫描）。
        """
        mdb = os.environ.get("MARKET_DB") or str(L.MARKET_DB)
        con = sqlite3.connect(mdb)
        con.row_factory = sqlite3.Row
        try:
            cur = con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='candle_dataset'")
            if not cur.fetchone():
                return
            rows = con.execute(
                "SELECT * FROM candle_dataset WHERE exchange=? AND market=? AND symbol=? "
                "AND close_time <= ? AND is_activated=0 ORDER BY open_time ASC",
                (EXCHANGE, MARKET, self.symbol, int(upto_ms))
            ).fetchall()
            for r in rows:
                rd = dict(r)
                con.execute(
                    "INSERT OR REPLACE INTO candle_versions(exchange,market,symbol,timeframe,open_time,close_time,open,high,low,close,volume,quote_volume,trade_count,taker_buy_volume,taker_buy_quote_volume,source_id,available_at,is_closed) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,10,500,50000,1,?,1)",
                    (rd["exchange"], rd["market"], rd["symbol"], rd["timeframe"], rd["open_time"], rd["close_time"],
                     rd["open"], rd["high"], rd["low"], rd["close"], rd["volume"], rd["quote_volume"], rd["close_time"])
                )
                con.execute(
                    "INSERT OR REPLACE INTO candle_heads(exchange,market,symbol,timeframe,open_time,source_id) "
                    "VALUES(?,?,?,?,?,1)",
                    (rd["exchange"], rd["market"], rd["symbol"], rd["timeframe"], rd["open_time"])
                )
                con.execute(
                    "UPDATE candle_dataset SET is_activated=1 WHERE id=?",
                    (rd["id"],)
                )
            con.commit()
        finally:
            con.close()


# ═══════════════════════════════════════════════════════════════════════
# 2. 真实信号引擎驱动器 (RealSignalEngine)
# ═══════════════════════════════════════════════════════════════════════
class _WilderATR:
    """`features.compute_wilder_atr_series` 的**增量等价实现**（同一递推、逐位相同）。

    为什么需要它（SIGNAL-HISTORY-FIX-02 实测缺陷）：实时路径原来**每根 4h 只取最近
    `box_bars_4h=42` 根重算 ATR**，而冻结规则第 17 行要求「4h Wilder ATR(14)，前 14 个
    TR 的算术平均作初值随后递推……**至少预热 200 根完整 4h K 线**」，回测
    (`runner.py:225-230`) 用的是**全历史连续序列**。42 根重算 = 每根都把 Wilder 递推
    重置一次，与规范/回测都不是同一个数（实测中位差 0.96%、最大 87.7%）。

    为什么不在实时路径直接调现成函数：现成函数吃整段数组，若每根 4h 都重算全前缀，
    bootstrap 526 标的 × 268 根 ≈ 1,900 万次 Python 循环，会把 21 s 的预热拖成分钟级。
    本类逐根推进，只保留 (prev_close, n, 前 period 根 TR, atr)，结果与
    `compute_wilder_atr_series(全前缀)[-1]` 完全相同（`02_atr_parity.py` 逐根实测）。
    """

    __slots__ = ("period", "n", "prev_close", "_seed", "atr")

    def __init__(self, period: int):
        self.period = int(period)
        self.n = 0
        self.prev_close: Optional[float] = None
        self._seed: List[float] = []
        self.atr: Optional[float] = None

    def update(self, high: float, low: float, close: float) -> Optional[float]:
        if self.prev_close is None:
            tr = high - low                      # 首根没有前收盘 ⇒ TR = high - low
        else:
            pc = self.prev_close
            tr = max(high - low, abs(high - pc), abs(low - pc))
        self.prev_close = close
        self.n += 1
        if self.n < self.period:
            self._seed.append(tr)
            self.atr = None
        elif self.n == self.period:
            self._seed.append(tr)
            self.atr = sum(self._seed) / float(self.period)   # 前 period 个 TR 的算术平均
        else:
            self.atr = (self.atr * (self.period - 1) + tr) / float(self.period)
        return self.atr


class RealSignalEngine:
    """复用确定性事件状态机（EventStateMachine），杜绝任何伪造信号。"""

    #: 连续多少根「零成交平价」1h 才判为交易所占位行（见 `_is_zero_flat`）
    INVALID_RUN_MIN = 3

    def __init__(self, symbol: str = "BTCUSDT", enable_oi_filter: bool = False,
                 config: Optional[EventTrendConfig] = None,
                 box_gate_mode: str = "atr_warmup"):
        self.symbol = symbol
        self.config = config or EventTrendConfig()
        self.enable_oi_filter = enable_oi_filter
        # ── 建档闸门口径（SIGNAL-ZERO-CAUSE-05）──────────────────────────────
        # `atr_warmup`（默认，规范口径）：ATR 预热满 `atr_warmup_bars` 就允许建档。
        #   规范第 17 行只约束 ATR「至少预热 200 根完整 4h K 线」；第 32 行的建档条件
        #   只有「最近 42 根 + 宽度 2~8A + ER<=0.30 + 数据连续 + ATR 有效」，
        #   没有「窗口内再等 200 根」。回测 `runner.py:349-363` 也是够 42 根就建档。
        # `window_bars`（旧口径，仅诊断/回归对照，**不投入生产**）：要求
        #   `box_gate_bars`（声明回放窗口内处理过的 4h 根数）>= 200 才建档。
        #   它的锚点是「窗口起点」⇒ 每次重启窗口前移，开闸时刻跟着前移，
        #   窗口移动 60h 会改变 32/526 标的的箱体与阶段（03 脚本实测）。
        if box_gate_mode not in ("atr_warmup", "window_bars"):
            raise ValueError(f"box_gate_mode 非法：{box_gate_mode}")
        self.box_gate_mode = box_gate_mode
        self.sm = EventStateMachine(
            config=self.config,
            symbol=self.symbol,
            enable_oi_filter=self.enable_oi_filter
        )
        self.processed_4h_closes: Set[int] = set()
        # `processed_4h_opens` 与 `processed_4h_closes` **同一批 bar 的两种口径**：
        #   · closes 用于去重（原有语义，不动）；
        #   · opens 用于「与库内 open_time 比较水位」和「回取最近 42 根做箱体」。
        # 缺陷（SIGNAL-HISTORY-FIX-03）：`watermark_consistency()` 原来拿 closes（收盘）
        # 直接和 `db_4h_max`（开盘）相减 ⇒ 恒得 -1 根，`aligned_4h` 恒 False。
        # **修的是口径，不是断言**：比较两侧必须是同一个时间口径。
        self.processed_4h_opens: Set[int] = set()
        self.processed_1h_opens: Set[int] = set()
        self.signals_generated: List[CandidateSnapshot] = []
        self.last_cycle_ms: Optional[int] = None
        self.history_floor_ms = 0
        # ── 连续 Wilder ATR（SIGNAL-HISTORY-FIX-02）─────────────────────────
        # 以「本引擎装载窗口的第一根 4h」为原点逐根递推，与回测全历史连续序列同一条递推。
        # `atr_bars_seen` 同时是「已预热 4h 根数」，用于执行规范要求的 ≥atr_warmup_bars 预热。
        self.atr = _WilderATR(self.config.atr_period_4h)
        self.atr_bars_seen = 0
        #: **只统计「声明回放窗口内」处理过的 4h 根数** —— 旧闸门口径的诊断量。
        # 为什么与 `atr_bars_seen` 分开（SIGNAL-HISTORY-FIX-03）：
        #   接入 180 天历史后，`atr_bars_seen` 会包含窗口之前的预热根数（≈790）。
        #   FIX-03 当时担心「拿它当建档闸门会让建档起点从窗口第 200 根前移到窗口第 42 根」，
        #   于是保留窗口内计数、把问题留给裁决。
        # **SIGNAL-ZERO-CAUSE-05 已裁决**：那个前移正是规范要求的（ATR 预热一次就够），
        #   保留窗口内计数才是偏差 —— 它让有效事件窗口从 41.3 天缩到 15 天，
        #   而完整事件链最长需要 24 天 ⇒ 数学上走不完，全池零候选。
        #   现在 `box_gate_mode="atr_warmup"`（默认）用 `atr_bars_seen` 判闸门，
        #   本计数仅保留为诊断量（`atr_caliber.box_gate_bars_*`），不再参与决策。
        self.box_gate_bars = 0
        self.last_atr: Optional[float] = None
        # 诊断：最近一次因预热不足而**跳过**建档的 4h 收盘时刻（如实暴露，不静默）
        self.last_box_skipped_warmup_ms: Optional[int] = None
        # ── 数据质量闸门（SIGNAL-HISTORY-FIX-03）─────────────────────────────
        # 已结算合约（`status=SETTLING`）在交易所侧仍会持续返回「零成交 + 平价」占位 K 线
        # （实测 `1000000BOBUSDT` / `PROMPTUSDT` / `PUMPBTCUSDT`，官方 REST 与官方月度归档
        # 两个独立来源都是如此）。这类行**不是我们采集坏了**，不能删、不能改价；但**不能
        # 用来做决策**：连续平价会让 ATR→0、并可能把「回踩区」判成成立。
        # 纪律：只跳过**决策**，不删行、不改价；连续 `INVALID_RUN_MIN` 根才判无效，
        # 避免把某个真实清淡时段误判成占位。
        self.data_quality: Dict[str, Any] = {
            "invalid": False, "reason": None, "zero_flat_run": 0,
            "since_ms": None, "skipped_bars": 0, "box_skipped_invalid": 0,
            "last_bar_ms": None,
        }

    def export_state(self) -> dict:
        """导出信号引擎断点恢复状态。"""
        return {
            "symbol": self.symbol,
            "processed_4h_closes": sorted(list(self.processed_4h_closes)),
            "processed_1h_opens": sorted(list(self.processed_1h_opens)),
            "signals_count": len(self.signals_generated),
            "atr_bars_seen": self.atr_bars_seen,
            "last_atr": self.last_atr,
            "sm_state": self.sm.export_state()
        }


    def restore_state(self, st: dict):
        """从状态字典恢复。"""
        if not st:
            return
        self.processed_4h_closes = set(st.get("processed_4h_closes", []))
        self.processed_1h_opens = set(st.get("processed_1h_opens", []))
        if "sm_state" in st and st["sm_state"]:
            self.sm = EventStateMachine.restore_state(st["sm_state"], self.config)

    @staticmethod
    def _is_zero_flat(bar: dict) -> bool:
        """交易所「零成交平价占位行」签名：`volume==0` 且 `O=H=L=C`。

        依据（SIGNAL-HISTORY-FIX-03 三源核对）：已结算合约在官方 REST
        `/fapi/v1/klines` 与官方月度归档 zip 里**都**返回这种行，价格冻结在最后成交价。
        """
        try:
            return (float(bar["volume"]) == 0.0
                    and float(bar["high"]) == float(bar["low"])
                    and float(bar["open"]) == float(bar["close"]))
        except Exception:                                    # noqa: BLE001
            return False

    def _note_bar_quality(self, bar: dict) -> bool:
        """更新数据质量状态，返回「这根 bar 是否允许参与决策」。"""
        dq = self.data_quality
        dq["last_bar_ms"] = int(bar["open_time"])
        if self._is_zero_flat(bar):
            dq["zero_flat_run"] = int(dq.get("zero_flat_run") or 0) + 1
            if dq["zero_flat_run"] >= self.INVALID_RUN_MIN and not dq["invalid"]:
                dq["invalid"] = True
                dq["reason"] = f"zero_volume_flat_run({dq['zero_flat_run']})"
                dq["since_ms"] = int(bar["open_time"])
        else:
            dq["zero_flat_run"] = 0
            if dq["invalid"]:
                dq["invalid"] = False
                dq["reason"] = None
                dq["since_ms"] = None
        return not dq["invalid"]

    def process_market(self, upto_ms: int, watcher: MarketDataWatcher) -> List[CandidateSnapshot]:
        """按时序驱动 4h 箱体蓄势与 1h 扫低/突破推进，返回新产生的信号候选。"""
        self.last_cycle_ms = upto_ms
        new_candidates = []

        # 4h 环境和 1h 事件按收盘时刻合并推进，不能先读完未来4h再处理历史1h。
        floor = self.history_floor_ms
        n = self.config.box_bars_4h
        # ── 4h 取数窗口（SIGNAL-HISTORY-FIX-03 / SIGNAL-ZERO-CAUSE-05）─────────
        # 原实现每次 `process_market` 都从 `floor` 重扫整个窗口（≈290 根 × 每根一次
        # ⇒ O(n²)）。接入 180 天历史后窗口更长，O(n²) 会把 bootstrap 拖到分钟级。
        # 箱体只需要「最近 n=42 根」+「尚未处理的」，故下界取
        # `最后已处理 open + H4 - (n-1)*H4`（= 倒数第 41 根），**语义不变**：
        # 未处理的第一根在列表里的下标恰为 n-1，`seg=bars_4h[i+1-n:i+1]` 仍是它往前 42 根。
        #
        # 再往前留 `(n-1)` 根**上下文**（SIGNAL-ZERO-CAUSE-05）：窗口第一根 4h 的
        # 「最近 42 根」必须能取到窗口起点之前的 bar，否则窗口最前面 41 根永远建不了档
        # （回测 `runner.py:298-300` 取的是 `idx_start_4h - 220`，正是这个意思）。
        # 这些上下文 bar **只参与 42 根计算，不产生事件**（下面按 `open_time >= floor` 过滤）。
        ctx_floor = int(floor) - (n - 1) * H4
        if self.processed_4h_opens:
            start_4h = max(int(x) for x in self.processed_4h_opens) + H4 - (n - 1) * H4
            start_4h = max(start_4h, ctx_floor)
        else:
            start_4h = ctx_floor
        bars_4h = watcher.get_closed_bars("4h", start_4h, upto_ms)
        start_1h = max(self.processed_1h_opens) + H1 if self.processed_1h_opens else floor
        bars_1h = watcher.get_closed_bars("1h", start_1h, upto_ms)
        events = [(int(b["close_time"]), 0, i, b) for i, b in enumerate(bars_4h)
                  if int(b["close_time"]) not in self.processed_4h_closes
                  and int(b["open_time"]) >= int(floor)]
        events += [(int(b["close_time"]), 1, i, b) for i, b in enumerate(bars_1h)
                   if int(b["open_time"]) not in self.processed_1h_opens]
        for cl_ms, kind, i, bar in sorted(events, key=lambda e: (e[0], e[1])):
            if cl_ms > upto_ms:
                continue
            if kind == 0:
                # ── 连续 Wilder ATR（规范：4h Wilder ATR(14)，至少预热 200 根完整 4h）──
                # 缺陷（SIGNAL-HISTORY-FIX-02）：原来每根只取最近 42 根重算 ⇒ Wilder 递推
                # 每根被重置，既不符合冻结规则第 17 行，也与回测（全历史连续序列）不同源。
                # 现在按装载窗口逐根递推；`atr_bars_seen` 不足 `atr_warmup_bars` 时
                # **不建档**（规范要求的预热），并如实记下被跳过的 4h 收盘时刻。
                self.atr_bars_seen += 1
                self.box_gate_bars += 1
                value = self.atr.update(float(bar["high"]), float(bar["low"]),
                                        float(bar["close"]))
                self.last_atr = value
                # 4h 侧只看**这一根自己**是否占位：连续计数只在 1h 序列上维护
                # （否则同一段行情会被 4h 与 1h 各计一次，把「2 根」放大成「3 根」）。
                usable = not self._is_zero_flat(bar)
                # 规范：ATR 预热满 `atr_warmup_bars` 即可建档（`atr_warmup` 口径）。
                # 旧口径（`window_bars`）要求窗口内也满 200 根，**仅诊断用**：
                # 它的锚点是窗口起点 ⇒ 重启即改变开闸时刻（SIGNAL-ZERO-CAUSE-05 已判为偏差）。
                gate_bars = (self.atr_bars_seen if self.box_gate_mode == "atr_warmup"
                             else self.box_gate_bars)
                if i + 1 >= n:
                    seg = bars_4h[i + 1 - n:i + 1]
                    h = np.array([float(x["high"]) for x in seg])
                    l = np.array([float(x["low"]) for x in seg])
                    c = np.array([float(x["close"]) for x in seg])
                    if gate_bars < int(self.config.atr_warmup_bars):
                        self.last_box_skipped_warmup_ms = cl_ms
                    elif not usable:
                        # 占位行（零成交平价）不许建档：宽度/ER 在平价序列上没有意义
                        self.data_quality["box_skipped_invalid"] = int(
                            self.data_quality.get("box_skipped_invalid") or 0) + 1
                    elif value is not None and np.isfinite(value) and value > 0:
                        self.sm.check_and_form_box(cl_ms, h, l, c, float(value))
                self.processed_4h_closes.add(cl_ms)
                self.processed_4h_opens.add(int(bar["open_time"]))
                continue
            # 1h：占位行只推进水位，**不喂状态机**（否则会基于无效行情做决策）
            usable = self._note_bar_quality(bar)
            self.processed_1h_opens.add(int(bar["open_time"]))
            if not usable:
                self.data_quality["skipped_bars"] = int(
                    self.data_quality.get("skipped_bars") or 0) + 1
                continue
            # OI禁用时不伪造观测；开启过滤但缺数据时由状态机拒绝。
            cand = self.sm.process_1h_bar(
                open_time_ms=int(bar["open_time"]), open_p=float(bar["open"]),
                high_p=float(bar["high"]), low_p=float(bar["low"]),
                close_p=float(bar["close"]), volume_v=float(bar["volume"]),
                oi_timestamps=np.array([], dtype=np.int64), oi_values=np.array([], dtype=np.float64))
            if cand is not None:
                new_candidates.append(cand)
                self.signals_generated.append(cand)

        return new_candidates


# ═══════════════════════════════════════════════════════════════════════
# 2b. 全池信号引擎（多标的）
# ═══════════════════════════════════════════════════════════════════════
class PoolMarketWatcher:
    """全池行情读取：轻量增量探测 + 单标的闭合 bar 读取。

    为什么不做「每轮遍历 528 个标的各查两次」：那会让 2 秒轮询变成每分钟上千次查询。
    这里先用 `candle_heads` 的索引做 O(1) 的全局水位探测，水位没动就整轮跳过。
    """

    def __init__(self, symbols: Optional[List[str]] = None):
        self.symbols = set(symbols) if symbols else None

    def global_last_open(self, timeframe: str) -> Optional[int]:
        con = L.market_conn()
        try:
            r = con.execute(
                "SELECT MAX(open_time) FROM candle_heads "
                "WHERE exchange=? AND market=? AND timeframe=?",
                (EXCHANGE, MARKET, timeframe)).fetchone()
            return int(r[0]) if r and r[0] is not None else None
        finally:
            con.close()

    def latest_open_by_symbol(self, timeframe: str) -> Dict[str, int]:
        con = L.market_conn()
        try:
            rows = con.execute(
                "SELECT symbol, MAX(open_time) FROM candle_heads "
                "WHERE exchange=? AND market=? AND timeframe=? GROUP BY symbol",
                (EXCHANGE, MARKET, timeframe)).fetchall()
            out: Dict[str, int] = {}
            for sym, mx in rows:
                if mx is None:
                    continue
                if self.symbols is not None and sym not in self.symbols:
                    continue
                out[sym] = int(mx)
            return out
        finally:
            con.close()

    def get_closed_bars(self, symbol: str, timeframe: str,
                        start_open_ms: int, end_open_ms: int) -> List[dict]:
        con = L.market_conn()
        try:
            cur = con.execute(
                "SELECT open_time, close_time, open, high, low, close, volume, quote_volume "
                "FROM candles WHERE exchange=? AND market=? AND is_closed=1 AND symbol=? "
                "AND timeframe=? AND open_time>=? AND close_time<=? ORDER BY open_time ASC",
                (EXCHANGE, MARKET, symbol, timeframe,
                 int(start_open_ms), int(end_open_ms)))
            return [dict(r) for r in cur.fetchall()]
        finally:
            con.close()


class _SymbolWatcher:
    """把全池 watcher 收窄成单标的视图（`process_market` 需要的两参数签名）。"""

    def __init__(self, pool: PoolMarketWatcher, symbol: str):
        self.pool = pool
        self.symbol = symbol

    def get_closed_bars(self, timeframe: str, start_open_ms: int, end_open_ms: int) -> List[dict]:
        return self.pool.get_closed_bars(self.symbol, timeframe, start_open_ms, end_open_ms)


class _MemWatcher:
    """内存行情源（bootstrap 用）：一次载入，逐根回放，不再碰库。"""

    def __init__(self, b4: List[dict], b1: List[dict]):
        self.b4, self.b1 = b4, b1

    def get_closed_bars(self, timeframe: str, start_open_ms: int, end_open_ms: int) -> List[dict]:
        src = self.b4 if timeframe == "4h" else self.b1
        return [b for b in src
                if int(b["open_time"]) >= int(start_open_ms)
                and int(b["close_time"]) <= int(end_open_ms)]


class MultiSymbolSignalEngine:
    """全池信号引擎：每个标的一台独立状态机，增量推进。

    纪律：
      · **bootstrap 只重建状态，不落信号**：预热窗口回放产生的历史候选一律丢弃
        （不补开历史信号），只保留各标的的箱体/扫低进度；
      · **step 只处理有新 bar 的标的**：全局水位没动就整轮空转，不动库；
      · 每个标的的身份、周期、交易所与本运行时其余环节**同一口径**（EXCHANGE/MARKET/TIMEFRAME）。
    """

    def __init__(self, symbols: List[str], enable_oi_filter: bool = False,
                 config: Optional[EventTrendConfig] = None,
                 history_provider=None, box_gate_mode: str = "atr_warmup"):
        self.symbols = list(symbols)
        self.config = config or EventTrendConfig()
        self.enable_oi_filter = enable_oi_filter
        # 建档闸门口径（SIGNAL-ZERO-CAUSE-05）：默认规范口径，见 RealSignalEngine 注释
        self.box_gate_mode = box_gate_mode
        # ── 合并历史入口（SIGNAL-HISTORY-FIX-03）──────────────────────────────
        # 传 `history_loader.MergedHistory` 时，bootstrap 从「冷层+热库合并」读，
        # 否则退回原来的「只读热库」路径（测试/回放默认如此，保证旧行为可复现）。
        self.history_provider = history_provider
        self.history_stats: dict = {}
        self.watcher = PoolMarketWatcher(self.symbols)
        self.engines: Dict[str, RealSignalEngine] = {}
        self.last_1h_open: Dict[str, int] = {}
        self.last_4h_open: Dict[str, int] = {}
        self._global_1h: Optional[int] = None
        self._global_4h: Optional[int] = None
        self.last_cycle_ms: Optional[int] = None
        self.history_floor_ms = 0
        self.bootstrap_stats: dict = {}
        self.skipped_reasons: Dict[str, str] = {}
        self.scan_errors: Dict[str, dict] = {}
        # 声明窗口（与 run_forever/run_replay 原公式一致，提出来做单一来源）
        c = self.config
        self.warmup_ms = int((c.atr_warmup_bars * 4 + c.breakout_max_total_hours
                              + c.cooling_hours) * H1)
        self._last_catchup_at: float = 0.0
        self.catchup_interval_sec: float = 60.0
        self._wm_cache: Optional[dict] = None
        self._wm_cache_at: float = 0.0
        # ── 漏斗计数（SIGNAL-LINK-01）────────────────────────────────────────
        # 为什么要它：`signals_generated_total=0` 这一个数字**无法区分**
        #   (甲) 链路断了 与 (乙) 策略没机会。
        # 于是按状态机真实迁移逐关计数：箱体→扫低→回踩→候选，以及各条淘汰原因。
        # 纪律：**只统计 `step()` 期间新产生的迁移**（预热回放的历史迁移不计入
        # 「自启动以来」，否则会把历史当成当前）；预热候选单独由
        # `bootstrap_stats.history_candidates_discarded` 表达，不混在一起。
        self.transition_counts: Dict[str, int] = {}
        self._seen_transitions: Dict[str, int] = {}
        self.candidates_since_start = 0
        self.last_candidate_ms: Optional[int] = None
        # 预热回放期的漏斗（回答「为什么启动后是 0」：历史上到底走到过第几关）
        self.history_transition_counts: Dict[str, int] = {}
        # IDLE 分因诊断缓存（只读，60s TTL，避免每轮状态保存都扫库）
        self._idle_cache: Optional[dict] = None
        self._idle_cache_at: float = 0.0

    def _absorb_transitions(self, sym: str, eng: "RealSignalEngine") -> None:
        """把该标的**新产生**的状态迁移计入漏斗（幂等：按已计数条数推进）。"""
        try:
            all_tr = eng.sm.transitions
        except Exception:                                    # noqa: BLE001
            return
        n = len(all_tr)
        prev = self._seen_transitions.get(sym, 0)
        if n <= prev:
            return
        for t in all_tr[prev:]:
            st = getattr(t, "to_state", None)
            if st:
                self.transition_counts[st] = self.transition_counts.get(st, 0) + 1
        self._seen_transitions[sym] = n

    def funnel(self) -> dict:
        """漏斗快照（自启动以来 + 预热期历史，两者分开列）。"""
        c = dict(self.transition_counts)
        h = dict(self.history_transition_counts)
        keys = ("BOX", "SWEEP", "RETEST", "CANDIDATE",
                "VOLUME_REJECTED", "OI_UNAVAILABLE", "OI_REJECTED",
                "INVALIDATED", "EXPIRED", "MISSED_WITHOUT_RETEST")
        return {
            "since_start": {k: c.get(k, 0) for k in keys},
            "history": {k: h.get(k, 0) for k in keys},
            "candidates_since_start": self.candidates_since_start,
            "last_candidate_ms": self.last_candidate_ms,
            "last_candidate_utc": (
                dt.datetime.fromtimestamp(self.last_candidate_ms / 1000, dt.timezone.utc)
                .strftime("%Y-%m-%dT%H:%M:%SZ") if self.last_candidate_ms else None),
            "history_candidates_discarded":
                (self.bootstrap_stats or {}).get("history_candidates_discarded"),
            "bootstrap_window_hours": (
                round((self.bootstrap_stats or {}).get("_window_hours") or 0) or None),
            "note": ("since_start 只统计本次进程启动后的状态机迁移；history 是预热回放期"
                     "（≈48 天）走过的各关数量；history_candidates_discarded 是其中产生、"
                     "按纪律丢弃的历史候选。三者互不替代。"),
        }

    def _new_engine(self, sym: str) -> RealSignalEngine:
        e = RealSignalEngine(symbol=sym, enable_oi_filter=self.enable_oi_filter,
                             config=self.config, box_gate_mode=self.box_gate_mode)
        e.history_floor_ms = self.history_floor_ms
        return e

    def prepare_history(self, upto_ms: int, log=print) -> dict:
        """用**合并入口**把「半年历史」读进来（只读；不下载、不写生产库）。

        读取范围与用途**分开记录**（工单「二」）：
          · **读取**：最近 `WX_HISTORY_DAYS`（默认 180）天的 1h/4h 已收盘行情，
            冷层 parquet + 热库按既定来源规则合并（同 key 热层优先）；
          · **ATR**：用读到的**全部 4h**（180 天 ≈ 1079 根）连续递推预热
            —— 与回测 `runner.py` 的「全历史连续序列」同源，规范只要求「至少 200 根」；
          · **箱体 / ER / 突破量**：只用**声明窗口**
            `atr_warmup_bars*4 + breakout_max_total_hours + cooling_hours = 1160h`。
            依据：事件最长活 336h + 冷却 24h ⇒ 更早的状态不可能影响当前。
            本单用「180 天全量回放」与「1160h 回放」做过逐币对照（见证据），
            终态与候选一致 ⇒ 窗口取 1160h 不丢信息，且重启耗时可控。
        """
        if self.history_provider is None:
            return {}
        t0 = time.time()
        try:
            read_days = int(os.environ.get("WX_HISTORY_DAYS", "180"))
        except ValueError:
            read_days = 180
        read_start = int(upto_ms) - read_days * DAY
        win_start = int(upto_ms) - self.warmup_ms
        try:
            h4, st4 = self.history_provider.load(
                self.symbols, ("4h",), read_start, int(upto_ms), keep_from_ms=read_start)
            h1, st1 = self.history_provider.load(
                self.symbols, ("1h",), read_start, int(upto_ms), keep_from_ms=win_start)
        except Exception as exc:                             # noqa: BLE001
            log(f"[history] 合并入口失败，退回热库：{type(exc).__name__}: {exc}")
            self.history_stats = {"data_source": "hot_only_fallback",
                                  "error": f"{type(exc).__name__}: {exc}"}
            self._hist = None
            return self.history_stats
        self._hist = {"4h": {s: (v.get("4h") or []) for s, v in h4.items()},
                      "1h": {s: (v.get("1h") or []) for s, v in h1.items()}}
        self.history_stats = _slim_history(st4, st1)
        self.history_stats.update({
            "data_source": "merged_cold_hot",
            "read_days": read_days,
            "read_start_utc": _iso_ms(read_start),
            "read_end_utc": _iso_ms(int(upto_ms)),
            "replay_window_hours": round(self.warmup_ms / H1, 1),
            "replay_window_start_utc": _iso_ms(win_start),
            "elapsed_sec": round(time.time() - t0, 2),
        })
        log(f"[history] {self.history_stats}")
        return self.history_stats

    def bootstrap(self, upto_ms: int, log=print) -> dict:
        """一次性回放预热窗口，重建每个标的的状态（历史候选丢弃，不补开）。"""
        t0 = time.time()
        ready = skipped = discarded = 0
        self.skipped_reasons.clear()
        hist = getattr(self, "_hist", None)
        src = "merged_cold_hot" if hist else "hot_only"
        win_start = int(upto_ms) - self.warmup_ms
        used_4h: List[int] = []
        used_1h: List[int] = []
        pre_bars: List[int] = []
        for sym in self.symbols:
            if hist:
                b4_all = hist["4h"].get(sym) or []
                b1 = hist["1h"].get(sym) or []
                b4_win = [b for b in b4_all if int(b["close_time"]) <= int(upto_ms)]
                pre = [b for b in b4_win if int(b["open_time"]) < win_start]
                b4 = [b for b in b4_win if int(b["open_time"]) >= win_start]
            else:
                b4 = self.watcher.get_closed_bars(sym, "4h", self.history_floor_ms, upto_ms)
                b1 = self.watcher.get_closed_bars(sym, "1h", self.history_floor_ms, upto_ms)
                pre = []
            # 箱体需要 box_bars_4h 根 4h；1h 至少要能算 ATR 预热
            min_4h = self.config.box_bars_4h + 8
            if len(b4) < min_4h:
                skipped += 1
                self.skipped_reasons[sym] = f"4h_bars_insufficient({len(b4)}<{min_4h})"
                continue
            if len(b1) < 200:
                skipped += 1
                self.skipped_reasons[sym] = f"1h_bars_insufficient({len(b1)}<200)"
                continue
            eng = self._new_engine(sym)
            # ── ATR 预热的「窗口之前」部分（SIGNAL-HISTORY-FIX-03）────────────
            # 只推进 ATR 递推与计数，**不建档**：这样 `atr_bars_seen` 反映的是
            # 「真正读到的 4h 根数」，而箱体仍然只在声明窗口内评估。
            for b in pre:
                eng.atr_bars_seen += 1
                eng.last_atr = eng.atr.update(float(b["high"]), float(b["low"]),
                                              float(b["close"]))
            # ── 42 根上下文（SIGNAL-ZERO-CAUSE-05）───────────────────────────
            # 窗口最前面 (box_bars_4h-1) 根 4h 的「最近 42 根」必须能取到窗口起点
            # 之前的 bar，否则这 41 根永远建不了档。回测 `runner.py:298-300` 用
            # `idx_start_4h - 220` 正是这个意思 ⇒ 这里是「与回测同源」，不是新规则。
            # 上下文只供 42 根计算，不产生事件（`process_market` 按 `open_time>=floor` 过滤）。
            _n = int(self.config.box_bars_4h)
            _ctx_floor = win_start - (_n - 1) * H4
            ctx = [b for b in b4_all
                   if int(b["close_time"]) <= int(upto_ms)
                   and _ctx_floor <= int(b["open_time"]) < win_start]
            w = _MemWatcher(ctx + b4, b1)
            for bar in b4:
                discarded += len(eng.process_market(int(bar["close_time"]), w))
            self.engines[sym] = eng
            # ── 水位必须取「**真正喂进状态机的**那根」，不能取「装载到的最后一根」 ──
            # 缺陷（SIGNAL-HISTORY-FIX-01 实测）：回放只按 **4h 收盘时刻**驱动
            # `process_market`，而它内部按 `close_time <= 该 4h 收盘` 取 1h ⇒ 最后一根
            # 4h 收盘之后才开盘的那根 1h **从未被喂进状态机**。原实现却把它写成
            # `last_1h_open = b1[-1]["open_time"]`（装载到的最后一根）⇒ `step()` 的
            # `has_new` 判 `n1 > last_1h_open` 恒 False ⇒ 该根被推迟到**下一根 1h 到达**
            # 才处理；期间进程停止则永不处理。实测 28/28 标的存在该 1 根缺口，
            # 且其中 2 个标的的结构（BOX→SWEEP / RETEST→IDLE）就发生在这根上
            # ⇒ 状态接口展示的是**过期一个 1h 的阶段**。
            # 修法：水位取状态机**实际处理过的最大 1h open_time**；一根都没处理过时
            # 回退到「首根之前」以强制 `has_new=True`，让下一轮把所有 1h 补齐。
            self.last_1h_open[sym] = (
                max(eng.processed_1h_opens) if eng.processed_1h_opens
                else (int(b1[0]["open_time"]) - H1 if b1 else 0))
            self.last_4h_open[sym] = (
                max(eng.processed_4h_opens) if eng.processed_4h_opens
                else (int(b4[0]["open_time"]) - H4 if b4 else 0))
            used_4h.append(len(b4))
            used_1h.append(len(b1))
            pre_bars.append(len(pre))
            # 预热期的迁移全部「已看过」，不计入 since_start（只作为历史）
            self._seen_transitions[sym] = len(eng.sm.transitions)
            # 但历史漏斗要留下：它才是「为什么启动后 0 条」的可核对答案
            for _t in eng.sm.transitions:
                _st = getattr(_t, "to_state", None)
                if _st:
                    self.history_transition_counts[_st] = (
                        self.history_transition_counts.get(_st, 0) + 1)
            ready += 1
        # ── 全局水位必须以**预热截止时刻**为准，不能直接取库里的最大值 ─────────
        # 缺陷（SIGNAL-LINK-01 核实）：原实现 `self._global_1h = global_last_open("1h")`
        # 取的是**全库最大值**，与 `upto_ms` 无关。生产上 `upto_ms≈now`、两者相等所以看不出，
        # 但有两种情况会把它变成真故障：
        #   ① 库里混入**未来时间戳**的坏 bar（本项目历史上多次出现数据质量问题）⇒
        #      水位被顶到未来 ⇒ `step()` 里 `g1 == self._global_1h` 恒成立 ⇒ **引擎永久空转**，
        #      直到真实挂钟追到那个未来时刻，且没有任何报错；
        #   ② `run_replay(start_ms)` / 任何「预热到过去某刻」的调用 ⇒ 同样恒空转。
        # 修法：取「本次真正预热到的那批 bar 里最大的 open_time」作为水位（它必然 <= upto_ms）；
        # 只有当库最大值本身就 <= upto_ms 时两者相等 ⇒ **生产语义完全不变**。
        _db_g1 = self.watcher.global_last_open("1h")
        _db_g4 = self.watcher.global_last_open("4h")
        _warm_g1 = max(self.last_1h_open.values(), default=None)
        _warm_g4 = max(self.last_4h_open.values(), default=None)
        self._global_1h = (_db_g1 if (_db_g1 is not None and _db_g1 <= int(upto_ms))
                           else _warm_g1)
        self._global_4h = (_db_g4 if (_db_g4 is not None and _db_g4 <= int(upto_ms))
                           else _warm_g4)
        if _db_g1 is not None and _db_g1 > int(upto_ms):
            # 如实暴露：库里存在超出预热截止时刻的 bar（未来时间戳或回放起点早于数据末端）
            self.skipped_reasons.setdefault(
                "__watermark_clamped__",
                f"db_1h_max={_db_g1} > upto_ms={int(upto_ms)} ⇒ 水位按预热值 {_warm_g1}")
        _wm_clamped = (_db_g1 is not None and _db_g1 > int(upto_ms))
        self.bootstrap_stats = {
            "universe": len(self.symbols), "ready": ready, "skipped": skipped,
            "skipped_reasons": dict(self.skipped_reasons),
            "history_candidates_discarded": discarded,
            # 预热回放实际覆盖的时长（小时）——「历史 4 个候选」必须带上分母才成立
            "_window_hours": round((int(upto_ms) - int(self.history_floor_ms)) / H1, 1),
            # ── 历史入口：**读取量**与**实际使用量**分开写（SIGNAL-HISTORY-FIX-03）──
            # 混成一个「已接入 180 天」就会变成不可核对的口号：读进来 180 天，
            # ATR 用多少、箱体用多少，必须各自有数。
            "data_source": src,
            "history": self.history_stats or None,
            "used": {
                "replay_window_hours": round(self.warmup_ms / H1, 1),
                "replay_window_start_utc": _iso_ms(win_start),
                "atr_prewarm_bars_min": min(pre_bars) if pre_bars else None,
                "atr_prewarm_bars_max": max(pre_bars) if pre_bars else None,
                "bars_4h_min": min(used_4h) if used_4h else None,
                "bars_4h_max": max(used_4h) if used_4h else None,
                "bars_1h_min": min(used_1h) if used_1h else None,
                "bars_1h_max": max(used_1h) if used_1h else None,
                "box_bars": int(self.config.box_bars_4h),
                "er_bars": int(self.config.box_bars_4h),
                "breakout_vol_lookback": int(self.config.breakout_vol_lookback),
            },
            # 库内存在超出预热截止时刻的 bar（未来时间戳 / 回放起点早于数据末端）时如实暴露，
            # 并说明水位已按预热值收敛 —— 否则「引擎空转」会毫无痕迹。
            "watermark_clamped": _wm_clamped,
            "watermark_note": (
                f"db_1h_max={_db_g1} > upto_ms={int(upto_ms)} ⇒ 水位按预热值 {_warm_g1}"
                if _wm_clamped else None),
            "elapsed_sec": round(time.time() - t0, 1),
        }
        log(f"[bootstrap] {self.bootstrap_stats}")
        return self.bootstrap_stats

    def step(self, upto_ms: int, log=None) -> List[CandidateSnapshot]:
        """增量推进：只对出现新 1h/4h bar 的标的跑状态机。

        ── 1h 节奏（SIGNAL-HISTORY-FIX-03）─────────────────────────────────
        原实现用**全池全局水位**做闸门：`g1 == self._global_1h and g4 == ...` 就整轮
        返回。正常时全局水位每小时前进一次，看不出问题；但全局水位是 526 个标的的
        **最大值**，只要有一个标的的数据补齐/延迟到达、而它没推动全局最大值，
        这些标的就会被整轮跳过 —— 而且**没有任何报错**，表现为「某些币永远慢半拍」。

        现在：全局水位没动时，仍然做一次**逐币落后检查**（纯内存比较，不查库），
        只为「自己落后于全局水位」的标的推进；为了不把 2 秒轮询变成每轮 526 次查询，
        这个兜底每 `catchup_interval_sec`（默认 60s）最多跑一次。
        1h 事件仍然**按 1h 节奏**推进：每根 1h 收盘后的下一轮就会被处理，
        不需要等下一根 4h。
        """
        self.last_cycle_ms = upto_ms
        g1 = self.watcher.global_last_open("1h")
        g4 = self.watcher.global_last_open("4h")
        stale = (g1 == self._global_1h and g4 == self._global_4h)
        targets: Optional[List[str]] = None
        if stale:
            now_s = time.time()
            if now_s - self._last_catchup_at < self.catchup_interval_sec:
                return []
            self._last_catchup_at = now_s
            # 只比内存水位：`last_1h_open` 是「真正处理过的最大 open」，
            # `_global_1h` 是库内全池最大 open ⇒ 前者小于后者就是落后。
            targets = [s for s, v in self.last_1h_open.items()
                       if self._global_1h is not None and int(v) < int(self._global_1h)
                       and s in self.engines]
            if not targets:
                return []
            if log:
                log(f"[step] 全局水位未动，逐币兜底：{len(targets)} 个标的落后")
        else:
            self._global_1h, self._global_4h = g1, g4
        latest_1h = self.watcher.latest_open_by_symbol("1h")
        latest_4h = self.watcher.latest_open_by_symbol("4h")

        out: List[CandidateSnapshot] = []
        touched = 0
        items = ((s, self.engines[s]) for s in targets) if targets is not None \
            else self.engines.items()
        for sym, eng in items:
            n1 = latest_1h.get(sym)
            n4 = latest_4h.get(sym)
            has_new = ((n1 is not None and n1 > self.last_1h_open.get(sym, 0))
                       or (n4 is not None and n4 > self.last_4h_open.get(sym, 0)))
            if not has_new:
                continue
            touched += 1
            try:
                got = eng.process_market(upto_ms, _SymbolWatcher(self.watcher, sym))
            except Exception as exc:                    # noqa: BLE001
                err_str = f"{type(exc).__name__}: {exc}"
                self.scan_errors[sym] = {"time_ms": upto_ms, "error": err_str}
                if log:
                    log(f"[step] {sym} 推进失败：{err_str}")
                continue
            # ── 水位只认「真正处理过的」，不认「库里最新的」────────────────────
            # 缺陷（SIGNAL-HISTORY-FIX-03）：原来写 `last_1h_open = n1`（库内最大 open）。
            # 若某一轮因为 bar 尚未标记闭合/时钟边界而没处理掉那根，水位仍会跳到它上面
            # ⇒ 下一轮 `n1 > last_1h_open` 为 False ⇒ 那根被**永久跳过**。
            # 现在水位取引擎自己的 `processed_*`，与 bootstrap 完全同一口径。
            if eng.processed_1h_opens:
                self.last_1h_open[sym] = max(int(x) for x in eng.processed_1h_opens)
            if eng.processed_4h_opens:
                self.last_4h_open[sym] = max(int(x) for x in eng.processed_4h_opens)
            self._absorb_transitions(sym, eng)      # 漏斗：只计本次推进新产生的迁移
            if got:
                self.candidates_since_start += len(got)
                self.last_candidate_ms = max(
                    [self.last_candidate_ms or 0]
                    + [int(c.available_at_ms) for c in got])
            out.extend(got)
        if log and touched:
            log(f"[step] 有新 bar 的标的 {touched} 个，新候选 {len(out)}")
        return out

    # ── 诊断：水位是否等于「真正喂进状态机的那根」（FIX-02 建，FIX-03 统一口径+逐币）──
    def watermark_consistency(self, ttl_sec: float = 10.0) -> dict:
        """核对「最后处理水位」与「实际处理过的 K 线」是否同一根，并**逐币**列出落后。

        ── 为什么重写（SIGNAL-HISTORY-FIX-03）──────────────────────────────
        ① **口径必须统一**：原实现拿 `processed_4h_closes`（收盘时刻）直接减
           `db_4h_max`（开盘时刻）⇒ 恒得 `-1` 根、`aligned_4h` 恒 False。
           这不是「阈值问题」，是两侧不是同一个时间口径。现在所有比较都用
           **open_time**，同时把 close 口径原样附上（`*_close`），谁也不会再混。
        ② **必须逐币**：只报全池 max/min 会把「1 个标的落后 5 根」藏进一个
           「lag=0」里。这里给出逐币分布 + 落后清单（最多 30 个）。
        ③ **不许把阈值从 1 改成 4**：1h 的正常值就是 **≤1**（刚闭合那根等下一轮）。
           `lag_1h_bars > 1` 一律按漏处理如实报出，不做「结构性」豁免。

        `ttl_sec`：逐币部分要跑两次 `GROUP BY symbol`（约 500k 行），状态文件每 2 秒
        写一次，故做短 TTL 缓存；聚合部分（全池 max）每次都现算。
        """
        p1 = [max(e.processed_1h_opens) for e in self.engines.values() if e.processed_1h_opens]
        p4_open = [max(e.processed_4h_opens) for e in self.engines.values() if e.processed_4h_opens]
        p4_close = [max(e.processed_4h_closes) for e in self.engines.values() if e.processed_4h_closes]
        try:
            db1 = self.watcher.global_last_open("1h")
            db4 = self.watcher.global_last_open("4h")
        except Exception:                                    # noqa: BLE001
            db1 = db4 = None
        m1 = max(p1) if p1 else None
        m4o = max(p4_open) if p4_open else None
        m4c = max(p4_close) if p4_close else None

        now = time.time()
        if self._wm_cache is not None and now - self._wm_cache_at < ttl_sec:
            per = self._wm_cache
        else:
            per = self._per_symbol_lag()
            self._wm_cache, self._wm_cache_at = per, now

        return {
            # —— 1h（全部 open_time 口径）——
            "convention": "open_time（区间起点）；close 口径另列 `*_close`，不混用",
            "global_1h": self._global_1h,
            "processed_1h_max": m1,
            "processed_1h_min": min(p1) if p1 else None,
            "db_1h_max": db1,
            "aligned_1h": (m1 is not None and self._global_1h == m1),
            "lag_1h_bars": (int((db1 - m1) // H1) if (db1 is not None and m1 is not None) else None),
            # —— 4h（open 对 open；close 只作附注）——
            "global_4h": self._global_4h,
            "processed_4h_max_open": m4o,
            "processed_4h_max_close": m4c,
            "db_4h_max_open": db4,
            "aligned_4h": (m4o is not None and self._global_4h == m4o),
            "lag_4h_bars": (int((db4 - m4o) // H4) if (db4 is not None and m4o is not None) else None),
            # —— 逐币 ——
            "per_symbol": per,
            "n_engines_with_1h": len(p1),
            "n_engines_with_4h": len(p4_open),
            "n_engines": len(self.engines),
            "note": ("`lag_1h_bars` = 库内最新已闭合 1h 与「实际处理过的最大 1h open」之差，"
                     "正常 ≤1；>1 即漏处理（本单**不**把允许值放宽到 4）。"
                     "`per_symbol` 给逐币分布与落后清单，防止 1 个标的落后被全池 max 掩盖。"),
        }

    def _per_symbol_lag(self) -> dict:
        """逐币水位差（库内最新已闭合 open vs 引擎实际处理过的最大 open）。"""
        try:
            latest_1h = self.watcher.latest_open_by_symbol("1h")
            latest_4h = self.watcher.latest_open_by_symbol("4h")
        except Exception as exc:                             # noqa: BLE001
            return {"error": f"{type(exc).__name__}: {exc}"}
        rows = []
        hist: Dict[str, int] = {}
        for sym, eng in self.engines.items():
            p1 = max(eng.processed_1h_opens) if eng.processed_1h_opens else None
            p4 = max(eng.processed_4h_opens) if eng.processed_4h_opens else None
            d1 = latest_1h.get(sym)
            d4 = latest_4h.get(sym)
            l1 = int((d1 - p1) // H1) if (d1 is not None and p1 is not None) else None
            l4 = int((d4 - p4) // H4) if (d4 is not None and p4 is not None) else None
            if l1 is not None:
                hist[str(l1)] = hist.get(str(l1), 0) + 1
            rows.append({"symbol": sym, "lag_1h": l1, "lag_4h": l4,
                         "processed_1h_utc": _iso_ms(p1), "db_1h_utc": _iso_ms(d1),
                         "processed_4h_utc": _iso_ms(p4), "db_4h_utc": _iso_ms(d4)})
        lagging = [r for r in rows if (r["lag_1h"] or 0) > 1]
        lagging.sort(key=lambda r: -(r["lag_1h"] or 0))
        return {
            "n": len(rows),
            "lag_1h_histogram": dict(sorted(hist.items(), key=lambda kv: int(kv[0]))),
            "lag_1h_max": max((r["lag_1h"] or 0) for r in rows) if rows else None,
            "n_lag_1h_gt1": len(lagging),
            "lagging": lagging[:30],
            "note": ("`lag_1h>1` 的标的逐条列出（最多 30 条）。"
                     "`lag_1h_histogram` 是全部标的的分布，用于区分「个别落后」与「全池落后」。"),
        }

    def data_quality_stats(self) -> dict:
        """数据质量闸门：哪些标的正在**禁止决策**、为什么、跳过了多少根。"""
        blocked = {}
        for sym, eng in self.engines.items():
            dq = eng.data_quality or {}
            if dq.get("invalid"):
                blocked[sym] = {
                    "reason": dq.get("reason"),
                    "since_utc": _iso_ms(dq.get("since_ms")),
                    "zero_flat_run": dq.get("zero_flat_run"),
                    "skipped_bars": dq.get("skipped_bars"),
                    "box_skipped_invalid": dq.get("box_skipped_invalid"),
                }
        return {
            "rule": (f"连续 ≥{getattr(RealSignalEngine, 'INVALID_RUN_MIN', 3)} 根 1h 为"
                     "「volume==0 且 O=H=L=C」的交易所占位行 ⇒ 该标的**禁止决策**"
                     "（不建档、不推进状态机）；只跳过决策，不删行、不改价"),
            "blocked_n": len(blocked),
            "blocked": blocked,
            "note": ("已结算合约（SETTLING）在交易所侧仍持续返回占位 K 线，属官方行为；"
                     "删除或填价都属伪造，故只做「阻断决策」。"),
        }

    def state_counter(self) -> Dict[str, int]:
        """当前各状态标的数（供状态接口如实暴露进度）。"""
        cnt: Dict[str, int] = {}
        for eng in self.engines.values():
            st = (eng.sm.active_event.current_state.value
                  if eng.sm.active_event else "IDLE")
            cnt[st] = cnt.get(st, 0) + 1
        return cnt

    # ── 诊断：ATR 口径是否真的按规范在跑（SIGNAL-HISTORY-FIX-02）──────────────
    def atr_caliber_stats(self) -> dict:
        """把「ATR 用的是什么口径」变成可从 API 核对的数字，而不是只写在注释里。

        冻结规则第 17 行要求：4h Wilder ATR(14)，前 14 个 TR 的算术平均作初值随后递推，
        **至少预热 200 根完整 4h K 线**。修复前实时路径每根只取最近 42 根重算（Wilder
        递推每根被重置、且没有 200 根预热），与回测/规范都不是同一个数。

        本方法只读引擎状态，不改任何决策：
          · `warmup_bars` / `period`：生效门槛（来自 `EventTrendConfig`，不是另设常数）
          · `n_warmed` / `n_not_warmed`：预热是否已达标（不达标时**不会建档**）
          · `atr_bars_seen_min/max`：各标的实际喂进递推的 4h 根数
          · `last_atr_valid` / `last_atr_invalid`：最新 ATR 是否为正有限值（坏死数据会为 0）
          · `last_box_skipped_warmup_utc`：最近一次「因预热不足而跳过建档」的 4h 收盘
        """
        c = self.config
        seen: List[int] = []
        valid = invalid = 0
        skipped_ms: Optional[int] = None
        gate: List[int] = []
        for eng in self.engines.values():
            n = int(getattr(eng, "atr_bars_seen", 0) or 0)
            seen.append(n)
            gate.append(int(getattr(eng, "box_gate_bars", 0) or 0))
            a = getattr(eng, "last_atr", None)
            if a is not None and np.isfinite(a) and a > 0:
                valid += 1
            else:
                invalid += 1
            s = getattr(eng, "last_box_skipped_warmup_ms", None)
            if s is not None and (skipped_ms is None or s > skipped_ms):
                skipped_ms = s
        wb = int(c.atr_warmup_bars)
        gate_used = ([n for n in seen] if self.box_gate_mode == "atr_warmup" else gate)
        return {
            "caliber": "4h Wilder ATR(14)，连续递推 + ≥atr_warmup_bars 预热（冻结规则第 17 行）",
            "period": int(c.atr_period_4h),
            "warmup_bars": wb,
            "box_bars_4h": int(c.box_bars_4h),
            # 建档闸门口径（SIGNAL-ZERO-CAUSE-05）：
            #   atr_warmup = 规范口径，ATR 预热满 200 根即建档（默认，生产在用）
            #   window_bars = 旧口径，窗口内再数 200 根（仅诊断，**不投入生产**）
            "box_gate_mode": self.box_gate_mode,
            "n_engines": len(self.engines),
            # `atr_bars_seen` = 喂进 ATR 递推的 4h 总根数（含 180 天预热部分）
            "n_warmed": sum(1 for n in seen if n >= wb),
            "n_not_warmed": sum(1 for n in seen if n < wb),
            "atr_bars_seen_min": min(seen) if seen else None,
            "atr_bars_seen_max": max(seen) if seen else None,
            # `box_gate_bars` = **声明回放窗口内**处理过的 4h 根数（旧闸门口径的诊断量，
            # 现在**不参与决策**；保留它是为了让「旧口径会怎样」随时可复算）
            "n_box_gate_open": sum(1 for n in gate_used if n >= wb),
            "box_gate_bars_min": min(gate) if gate else None,
            "box_gate_bars_max": max(gate) if gate else None,
            "gate_metric": ("atr_bars_seen" if self.box_gate_mode == "atr_warmup"
                            else "box_gate_bars"),
            "last_atr_valid": valid,
            "last_atr_invalid": invalid,
            "last_box_skipped_warmup_ms": skipped_ms,
            "last_box_skipped_warmup_utc": (
                dt.datetime.fromtimestamp(skipped_ms / 1000, dt.timezone.utc)
                .strftime("%Y-%m-%dT%H:%M:%SZ") if skipped_ms else None),
            "note": ("修复前实时路径每根只取最近 42 根重算 ⇒ Wilder 递推每根被重置、"
                     "且没有 200 根预热。`atr_bars_seen` 含 180 天预热；"
                     "SIGNAL-ZERO-CAUSE-05 起建档闸门改用 `atr_bars_seen`（规范第 17 行"
                     "只要求 ATR 预热一次，第 32 行建档条件不含「窗口内再等 200 根」，"
                     "回测 runner.py:349-363 也是够 42 根即建档）。"
                     "`box_gate_bars` 仅作旧口径诊断量保留。"),
        }

    # ── 诊断：IDLE 分因（SIGNAL-HISTORY-FIX-01）─────────────────────────────
    def idle_breakdown(self, ttl_sec: float = 60.0) -> dict:
        """把 `state_counter()` 里的 IDLE 拆成**可核对的原因**，不压成一个数。

        为什么需要（工单「不得把以上全部压成一个信号0」）：生产状态里 `IDLE=130`
        一个数字同时代表
          · IDLE_COOLING    冷却期未过（`cooling_hours=24`）
          · IDLE_NO_4H_DATA 4h 根数不足 `box_bars_4h=42`，无法评估箱体
          · IDLE_DEAD_BARS  4h 全零（volume=0 且 high==low）⇒ ATR=0（**数据坏死**）
          · IDLE_ATR_INVALID ATR 非正（非全零导致的其它原因）
          · IDLE_WAIT_BOX   宽度不在 2A~8A 或 ER 超阈（**策略条件未达**）
          · IDLE_READY_BOX  条件全满足，等下一根 4h 收盘即可建档（**时间未到**）
        混成一个「IDLE」时，用户只能读到「没有信号」，无法区分数据问题/策略问题/时间问题。

        纪律：**只读**——不改任何状态、不参与决策；判据逐条取自 `EventTrendConfig`
        的同一批字段（与 `check_and_form_box` 一致），不新增门槛、不放宽门槛。
        """
        now = time.time()
        if self._idle_cache is not None and now - self._idle_cache_at < ttl_sec:
            return self._idle_cache
        c = self.config
        cnt: Dict[str, int] = {}
        detail: Dict[str, dict] = {}
        con = L.market_conn()
        try:
            for sym, eng in self.engines.items():
                sm = eng.sm
                if sm.active_event is not None:
                    continue
                last_close = (max(sm.processed_1h_bar_opens) + H1
                              if sm.processed_1h_bar_opens else None)
                if sm.cooling_until_ms and last_close and last_close < sm.cooling_until_ms:
                    k = "IDLE_COOLING"
                    info = {"actual": dt.datetime.fromtimestamp(
                                sm.cooling_until_ms / 1000, dt.timezone.utc
                            ).strftime("%Y-%m-%dT%H:%M:%SZ"),
                            "threshold": f"冷却 {c.cooling_hours}h",
                            "remaining_hours": round((sm.cooling_until_ms - last_close) / H1, 2)}
                else:
                    rows = con.execute(
                        "SELECT high, low, close, volume FROM candles "
                        "WHERE exchange=? AND market=? AND symbol=? AND timeframe='4h' "
                        "AND is_closed=1 ORDER BY open_time DESC LIMIT ?",
                        (EXCHANGE, MARKET, sym, int(c.box_bars_4h))).fetchall()
                    rows = list(reversed(rows))
                    if len(rows) < c.box_bars_4h:
                        k = "IDLE_NO_4H_DATA"
                        info = {"actual": f"{len(rows)} 根",
                                "threshold": f"{c.box_bars_4h} 根"}
                    else:
                        hi = np.array([float(r["high"]) for r in rows])
                        lo = np.array([float(r["low"]) for r in rows])
                        cl = np.array([float(r["close"]) for r in rows])
                        vl = np.array([float(r["volume"]) for r in rows])
                        # ATR 必须用**决策时同一个数**（连续递推、≥200 根预热），
                        # 不能用最近 42 根重算 —— 否则「展示的宽度」与「决策用的宽度」
                        # 不是同一个值（SIGNAL-ZERO-CAUSE-05 实测：42 根重算会让宽度
                        # 落在 2~8A 之外/之内的判断和实际决策不一致）。
                        atr = getattr(eng, "last_atr", None)
                        if atr is None or not np.isfinite(atr) or atr <= 0:
                            atr = float(compute_wilder_atr_series(
                                hi, lo, cl, period=c.atr_period_4h)[-1])
                        if not np.isfinite(atr) or atr <= 0:
                            dead = int(np.sum((vl == 0.0) & (hi == lo)))
                            k = "IDLE_DEAD_BARS" if dead else "IDLE_ATR_INVALID"
                            info = {"actual": f"ATR={atr}", "threshold": "ATR>0",
                                    "zero_range_bars": dead}
                        else:
                            w_atr = float(np.max(hi) - np.min(lo)) / atr
                            er = compute_efficiency_ratio(cl)
                            unmet = []
                            if not (c.box_min_width_atr <= w_atr <= c.box_max_width_atr):
                                unmet.append(
                                    f"宽度 {w_atr:.2f}A 不在 "
                                    f"{c.box_min_width_atr}~{c.box_max_width_atr}A")
                            if er is None or er > c.er_threshold:
                                unmet.append(
                                    f"ER={er if er is None else round(er, 4)} "
                                    f"超阈 {c.er_threshold}")
                            k = "IDLE_WAIT_BOX" if unmet else "IDLE_READY_BOX"
                            info = {
                                "actual": (f"宽度={w_atr:.2f}A, "
                                           f"ER={None if er is None else round(er, 4)}"),
                                "threshold": (f"{c.box_min_width_atr}~{c.box_max_width_atr}A, "
                                              f"ER<={c.er_threshold}"),
                                "unmet": unmet,
                            }
                cnt[k] = cnt.get(k, 0) + 1
                if len(detail) < 40:
                    detail[sym] = {"reason": k, **info}
        finally:
            con.close()
        out = {
            "counts": cnt,
            "idle_total": sum(cnt.values()),
            "sample": detail,
            "note": ("只读诊断：不改状态、不参与决策；判据取自 EventTrendConfig 同一批字段。"
                     "IDLE_READY_BOX=条件全满足，等下一根 4h 收盘即可建档；"
                     "IDLE_DEAD_BARS=4h 全零导致 ATR=0（数据坏死，不是策略没机会）。"
                     "宽度/ER 用的是决策时的连续递推 ATR（`eng.last_atr`），"
                     "不是最近 42 根重算值。"),
        }
        self._idle_cache, self._idle_cache_at = out, now
        return out

    def export_state(self) -> dict:
        """诊断快照（**不是**断点恢复机制）。

        缺陷背景（SIGNAL-HISTORY-FIX-01 实测）：本方法原名即 `export_state`，
        但只导出 `symbols/ready/bootstrap`，**不含任何逐标的状态机状态**，且类上
        没有对称的 `restore_state`。命名会让人以为「状态已保存、重启可恢复」，
        而 `UnifiedRuntime._recover_state_on_startup` 只数纸面账本行数、从不碰信号引擎
        ⇒ 生产重启**没有任何信号状态被恢复**，一律靠 `bootstrap()` 全量重放。

        为什么不补快照恢复：`EventStateMachine.export_state()` 含**不裁剪**的
        `h1_open_times/h1_opens/h1_highs/h1_lows/h1_closes/h1_volumes`（每标的约
        1.2k 根 × 6 数组），全池 526 标的序列化量级 ~10^2 MB，写盘/读盘成本远高于
        重放（实测 526 标的预热重放 21.4s），且重放与「不重启」的状态等价性已实测
        成立（`evidence/SIGNAL-HISTORY-FIX-01/out/05_三路衔接对比.json`）。
        ⇒ 结论：**重放是这里的正确恢复方式**；本方法只如实声明它是什么。
        """
        return {"symbols": self.symbols, "ready": sorted(self.engines.keys()),
                "bootstrap": self.bootstrap_stats,
                # 如实声明：快照里没有逐标的状态，也没有恢复入口
                "state_persisted": False,
                "restore_supported": False,
                "recovery_method": "bootstrap_full_replay",
                "recovery_note": ("重启后状态由 bootstrap() 按行情全量重放重建；"
                                  "预热期的历史候选按纪律丢弃，不补开历史订单。")}


# ═══════════════════════════════════════════════════════════════════════
# 3. 信号持久化与风控路由 (SignalRouter & Persistence)
# ═══════════════════════════════════════════════════════════════════════
class SignalPersistenceAndRouter:
    """将候选信号持久化到 live_signal，经契约与风控核验后转化为挂单。"""

    def __init__(self, arm: str = "3a"):
        self.arm = arm
        self.account_id = f"paper43_{arm}"

    def persist_candidates(self, candidates: List[CandidateSnapshot], origin: str = "live", now_ms: Optional[int] = None) -> dict:
        """将状态机产出的 CandidateSnapshot 幂等写入 live_signal。

        两条硬约束（本窗口修复）：
          · **事件角色必须落库**：`payload.role=enterable_arm_entry` 是界面当前信号区
            与后端 `scope=current` 的判据之一，缺它信号即使生成也会被隐藏；
          · **冲突时必须补写 payload**：原 `INSERT OR IGNORE` 在 `signal_uid` 已存在时
            整行丢弃 ⇒ 早先无 `role` 的旧行永远补不上角色，于是永久不可见。
            现改为 `ON CONFLICT(signal_uid) DO UPDATE`：只补 payload/可执行态/更新时间，
            **不改** `first_seen_at`、不改事件时间与价格事实。
        """
        stats = {"new": 0, "updated": 0, "total_written": 0}
        if not candidates:
            return stats
        now = int(now_ms or time.time() * 1000)
        con = L.live_conn()
        try:
            for c in candidates:
                payload = json.dumps(
                    {**c.to_dict(), "role": ARM_ENTRY_ROLE, "arm": self.arm},
                    ensure_ascii=False, default=str)
                sig_uid = c.candidate_id
                exists = con.execute(
                    "SELECT 1 FROM live_signal WHERE signal_uid=?", (sig_uid,)).fetchone()
                con.execute(
                    "INSERT INTO live_signal("
                    "signal_uid, source, origin, exchange, market, symbol, timeframe, "
                    "strategy_key, strategy_version, structural_kind, direction, event_time, "
                    "confirmed_at, available_at, bar_open_time, bar_close_time, detection_low, "
                    "detection_atr, display_state, executable_state, payload_json, first_seen_at, updated_at) "
                    "VALUES(?, 'wyckoff_v0_1', ?, ?, ?, ?, ?, "
                    "?, ?, 'breakout', 'long', ?, ?, ?, ?, ?, ?, ?, 'displayable', 'enterable', ?, ?, ?) "
                    "ON CONFLICT(signal_uid) DO UPDATE SET "
                    "payload_json=excluded.payload_json, "
                    "executable_state=excluded.executable_state, "
                    "display_state=excluded.display_state, "
                    "updated_at=excluded.updated_at",
                    (
                        sig_uid, origin, EXCHANGE, MARKET, c.symbol, TIMEFRAME,
                        STRATEGY_KEY, c.strategy_version, c.snapshot_time_ms,
                        c.snapshot_time_ms, c.available_at_ms, c.snapshot_time_ms - H1,
                        c.snapshot_time_ms, c.initial_stop_price, c.box_atr_a,
                        payload, now, now
                    )
                )
                if exists:
                    stats["updated"] += 1
                else:
                    stats["new"] += 1
            con.commit()
        finally:
            con.close()
        stats["total_written"] = stats["new"] + stats["updated"]
        return stats

    def _scan_actionable(self, con_live, origin: Optional[str] = None) -> List[dict]:
        """按**与界面同一口径**扫描本臂可执行信号。

        限定项（缺一不可）：交易所 / 市场 / 周期 / 事件角色 / 所属臂 / 时间字段齐备。
        不再只按 `strategy_key` 裸扫全库 —— 那会把其它交易所、其它市场、以及
        结构证据提示（`role != enterable_arm_entry`）一并当成可开仓信号。
        """
        where = [
            "executable_state='enterable'",
            "exchange=?", "market=?", "timeframe=?",
            "json_extract(payload_json,'$.role')=?",
            "json_extract(payload_json,'$.arm')=?",
            "confirmed_at IS NOT NULL AND available_at IS NOT NULL",
        ]
        args: List[Any] = [EXCHANGE, MARKET, TIMEFRAME, ARM_ENTRY_ROLE, self.arm]
        if origin:
            where.append("origin=?")
            args.append(origin)
        sql = ("SELECT * FROM live_signal WHERE " + " AND ".join(where)
               + " ORDER BY confirmed_at ASC")
        return [dict(r) for r in con_live.execute(sql, args).fetchall()]

    def route_pending_orders(self, now_ms: int, origin: Optional[str] = None) -> dict:
        """从 live_signal 扫描未处理的可执行信号，经契约核验与风控后登记挂单。

        闸门（`new_entry_gate`）暂停期间：**不写任何状态**（不写 `paper_processed`、
        不写 `paper_order`、不登记挂单）⇒ 恢复后同一批信号可被重新扫描并重试；
        同时如实返回 `waiting_gate` 条数，暂停不再表现为「本轮 0 条、什么都没有」。
        """
        con_live = L.live_conn()
        stats = {"processed": 0, "registered": 0, "rejected": 0,
                 "waiting_gate": 0, "terminal": 0, "errors": []}
        try:
            signals = self._scan_actionable(con_live, origin)
            if not signals:
                return stats

            gate = PE.new_entry_gate()
            if not gate["allow_new_entries"]:
                # 只数「真正待处理」的（已挂单/已持仓/已处理的不算），不落任何状态。
                # 账本不可读时如实给 None（不可统计），不伪造 0。
                waiting: Optional[int] = None
                try:
                    con_chk = L.paper_conn(self.arm)
                except Exception:                            # noqa: BLE001
                    con_chk = None
                if con_chk is not None:
                    try:
                        waiting = 0
                        for sig in signals:
                            suid = sig["signal_uid"]
                            hit = any(con_chk.execute(f"SELECT 1 FROM {t} WHERE signal_uid=?", (suid,)).fetchone()
                                      for t in ("paper_pending", "paper_processed", "paper_position"))
                            if not hit:
                                waiting += 1
                    finally:
                        con_chk.close()
                stats["waiting_gate"] = waiting
                return stats

            con_paper = L.paper_conn(self.arm)
            try:
                for sig in signals:
                    suid = sig["signal_uid"]
                    sym = sig["symbol"]

                    # 检查是否已在挂单、持仓或已处理名单中
                    chk_pen = con_paper.execute("SELECT 1 FROM paper_pending WHERE signal_uid=?", (suid,)).fetchone()
                    chk_pro = con_paper.execute("SELECT 1 FROM paper_processed WHERE signal_uid=?", (suid,)).fetchone()
                    chk_pos = con_paper.execute("SELECT 1 FROM paper_position WHERE signal_uid=?", (suid,)).fetchone()
                    if chk_pen or chk_pro or chk_pos:
                        continue

                    stats["processed"] += 1
                    s_event_time = int(sig.get("event_time") or now_ms)
                    s_avail_at = int(sig.get("available_at") or now_ms)

                    # 调用 paper_engine.open_position 统一进行契约验证、挂单登记与风控校验
                    reg_res = PE.open_position(
                        self.arm, sym, signal_uid=suid, direction="long",
                        now_ms=now_ms, event_time=s_event_time, available_at=s_avail_at,
                        requested_at=now_ms, origin=sig.get("origin", "live")
                    )

                    if reg_res.get("ok") or reg_res.get("reason") == "pending_future_fill":
                        stats["registered"] += 1
                    else:
                        stats["rejected"] += 1
                        reason = reg_res.get("reason")
                        # **只有终态拒单**（永不可成交）才落 `paper_processed` 永久拉黑；
                        # 其余（仓位已满 / 参数缺失 / 行情库不可用 / 闸门暂停 …）按
                        # paper_engine 的设计是**可重试**的，落库会把信号静默判死。
                        if reason in TERMINAL_REJECT_REASONS:
                            stats["terminal"] += 1
                            con_paper.execute(
                                "INSERT OR REPLACE INTO paper_processed(signal_uid, arm, action, detail, ts) "
                                "VALUES(?, ?, ?, ?, ?)",
                                (suid, self.arm, "rejected_terminal",
                                 json.dumps(reg_res, ensure_ascii=False, default=str), now_ms)
                            )
                            con_paper.commit()
            finally:
                con_paper.close()
        except Exception as exc:
            stats["errors"].append(str(exc))
        finally:
            con_live.close()

        return stats


# ═══════════════════════════════════════════════════════════════════════
# 4. 统一执行守护器 (UnifiedExecutionDaemon)
# ═══════════════════════════════════════════════════════════════════════
class UnifiedExecutionDaemon:
    """挂单自动撮合、跟踪止损推进与明细周期平仓退出。"""

    def __init__(self, arm: str = "3a"):
        self.arm = arm
        self.account_id = f"paper43_{arm}"

    def execute_cycle(self, now_ms: int) -> dict:
        res = {
            "pending_matched": 0,
            "trailing_updates": 0,
            "exits_triggered": 0,
            "errors": []
        }

        # 1. 推进挂单撮合成交
        try:
            match_res = PE.advance_pending(T=now_ms, log=lambda *args: None)
            arm_stat = match_res.get("by_arm", {}).get(self.arm, {})
            res["pending_matched"] = arm_stat.get("filled", 0)
        except Exception as exc:
            res["errors"].append(f"advance_pending_err: {exc}")

        # 2. 推进持仓 4h 跟踪止损更新与 15m 明细周期退出
        con_paper = L.paper_conn(self.arm, ro=True)
        open_positions = []
        try:
            cur = con_paper.execute(
                "SELECT position_id, symbol, stop_price, entry_price, entry_ts, earliest_fill_ms "
                "FROM paper_position WHERE account_id=? AND status='open' AND COALESCE(voided,0)=0",
                (self.account_id,)
            )
            open_positions = [dict(r) for r in cur.fetchall()]
        except Exception as exc:
            res["errors"].append(f"query_open_positions_err: {exc}")
        finally:
            con_paper.close()

        if open_positions:
            con_mkt = None
            try:
                con_mkt = L.market_conn()
                for pos in open_positions:
                    pos_id = pos["position_id"]
                    sym = pos["symbol"]

                    # 4h 跟踪止损单调上移
                    c4h = PE.last_closed_4h(con_mkt, sym, upto_ms=now_ms)
                    if c4h:
                        try:
                            adv = PE.advance_position_trailing(
                                self.arm, pos_id, c4h, now_ms=now_ms, decision_at=now_ms
                            )
                            if adv.get("ok") and adv.get("stop_updated"):
                                res["trailing_updates"] += 1
                                if get_notifier:
                                    try:
                                        get_notifier().notify_trailing_stop(
                                            sym,
                                            float(pos.get("stop_price") or 0.0),
                                            float(adv.get("new_stop_price") or 0.0)
                                        )
                                    except Exception:
                                        pass
                            if adv.get("exit_triggered"):
                                res["exits_triggered"] += 1
                                if get_notifier:
                                    try:
                                        get_notifier().notify_exit({
                                            "symbol": sym,
                                            "entry_price": pos.get("entry_price"),
                                            "exit_price": adv.get("exit_price"),
                                            "pnl": adv.get("realized_pnl", 0.0),
                                            "reason": "4h跟踪止损离场"
                                        })
                                    except Exception:
                                        pass
                                continue
                        except Exception as exc:
                            res["errors"].append(f"trailing_err_pos_{pos_id}: {exc}")

                    # 15m 明细退出检查
                    min_t = int(pos.get("earliest_fill_ms") or pos.get("entry_ts") or 0)
                    cur_b15 = None
                    try:
                        cur_b15 = con_mkt.execute(
                            "SELECT symbol, timeframe, open_time, close_time, open, high, low, close, available_at "
                            "FROM candles WHERE exchange='binance' AND market='um' AND symbol=? AND timeframe='15m' "
                            "AND is_closed=1 AND open_time>? AND close_time<=? "
                            "ORDER BY open_time DESC LIMIT 1",
                            (sym, min_t, now_ms)
                        ).fetchone()
                    except Exception:
                        try:
                            cur_b15 = con_mkt.execute(
                                "SELECT symbol, timeframe, open_time, close_time, open, high, low, close, available_at "
                                "FROM candle_versions WHERE exchange='binance' AND market='um' AND symbol=? AND timeframe='15m' "
                                "AND is_closed=1 AND open_time>? AND close_time<=? "
                                "ORDER BY open_time DESC LIMIT 1",
                                (sym, min_t, now_ms)
                            ).fetchone()
                        except Exception:
                            cur_b15 = None
                    if cur_b15:
                        b15 = dict(cur_b15)
                        try:
                            exit_chk = PE.check_position_exit_on_bar(
                                self.arm, pos_id, b15, now_ms=now_ms, decision_at=now_ms
                            )
                            if exit_chk.get("exit_triggered"):
                                res["exits_triggered"] += 1
                        except Exception as exc:
                            res["errors"].append(f"exit_chk_err_pos_{pos_id}: {exc}")
            finally:
                if con_mkt is not None:
                    try:
                        con_mkt.close()
                    except Exception:
                        pass

        return res


# ═══════════════════════════════════════════════════════════════════════
# 5. 统一运行时总协调器 (UnifiedRuntime)
# ═══════════════════════════════════════════════════════════════════════
class UnifiedRuntime:
    """SYSTEM-RUNTIME-01 统一运行时调度中心。具备独占锁、优雅退出、断点恢复与全状态暴露。"""

    def __init__(self, arm: str = "3a", symbols: Optional[List[str]] = None,
                 poll_interval_sec: float = 1.0,
                 lock_dir: Optional[Path] = None, enable_oi_filter: bool = False):
        self.arm = arm
        # 全池口径：默认用唯一事实源 `live_pool.scope_symbols()`（scope_crypto_v1，526 个），
        # 不再写死单标的。单标的调试仍可传 `symbols=["BTCUSDT"]`。
        self.symbols = list(symbols) if symbols else _default_universe()
        self.symbol = self.symbols[0] if len(self.symbols) == 1 else f"all({len(self.symbols)})"
        self.poll_interval_sec = poll_interval_sec
        self.lock_dir = lock_dir or get_lock_dir()
        self.lock_dir.mkdir(parents=True, exist_ok=True)
        self.lock_file_path = self.lock_dir / f"paper_runtime_{arm}.lock"
        self.lock_fd = None
        self.pid = os.getpid()
        self.started_at_ms = int(time.time() * 1000)

        # 核心子组件装配
        self.watcher = MarketDataWatcher(symbol=self.symbols[0])
        self.signal_engine = MultiSymbolSignalEngine(self.symbols,
                                                     enable_oi_filter=enable_oi_filter)
        self.router = SignalPersistenceAndRouter(arm=arm)
        self.execution = UnifiedExecutionDaemon(arm=arm)

        self._stop_event = threading.Event()
        self.cycle_count = 0
        self.stats = {
            "signals_generated_total": 0,
            "orders_routed_total": 0,
            "orders_rejected_total": 0,
            "orders_waiting_gate": 0,
            "pending_matched_total": 0,
            "trailing_updates_total": 0,
            "exits_triggered_total": 0,
            "last_error": None,
            "health": "initialized"
        }

        # 尝试从 SQLite 账本恢复历史断点
        self._recover_state_on_startup()
        self._save_status_file("initialized")

    def acquire_lock(self) -> bool:
        """获取独占文件锁，杜绝同机重复启动。"""
        try:
            self.lock_fd = open(self.lock_file_path, "w")
            fcntl.flock(self.lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.lock_fd.write(f"pid={self.pid} started_at={self.started_at_ms}\n")
            self.lock_fd.flush()
            return True
        except (IOError, BlockingIOError):
            if self.lock_fd:
                try:
                    self.lock_fd.close()
                except Exception:
                    pass
                self.lock_fd = None
            return False

    def release_lock(self):
        """释放独占锁。"""
        if self.lock_fd:
            try:
                fcntl.flock(self.lock_fd, fcntl.LOCK_UN)
                self.lock_fd.close()
            except Exception:
                pass
            self.lock_fd = None

    def _recover_state_on_startup(self):
        """从 SQLite 账本恢复已有持仓与已处理信号，实现热重启与断点接管。"""
        con_p = L.paper_conn(self.arm, ro=True)
        try:
            n_pos = con_p.execute(
                "SELECT COUNT(*) FROM paper_position WHERE account_id=? AND status='open'",
                (f"paper43_{self.arm}",)
            ).fetchone()[0]
            n_pen = con_p.execute(
                "SELECT COUNT(*) FROM paper_pending WHERE account_id=? AND status='pending'",
                (f"paper43_{self.arm}",)
            ).fetchone()[0]
            logger.info(f"[*] [Runtime-{self.arm}] 启动恢复：发现 {n_pos} 个在途持仓，{n_pen} 个待撮合挂单")
        except Exception:
            pass
        finally:
            con_p.close()

    def get_full_status(self) -> dict:
        """全景状态接口：如实报告各组件详细运行指标，绝不虚假上报 OK。"""
        last_15m = self.watcher.get_latest_closed_bar("15m", int(time.time() * 1000))
        last_1h = self.watcher.get_latest_closed_bar("1h", int(time.time() * 1000))
        last_4h = self.watcher.get_latest_closed_bar("4h", int(time.time() * 1000))

        con_p = L.paper_conn(self.arm, ro=True)
        open_pos_count = 0
        pending_orders_count = 0
        try:
            open_pos_count = con_p.execute(
                "SELECT COUNT(*) FROM paper_position WHERE account_id=? AND status='open'",
                (f"paper43_{self.arm}",)
            ).fetchone()[0]
            pending_orders_count = con_p.execute(
                "SELECT COUNT(*) FROM paper_pending WHERE account_id=? AND status='pending'",
                (f"paper43_{self.arm}",)
            ).fetchone()[0]
        except Exception:
            pass
        finally:
            con_p.close()

        health = self.stats.get("health", "running")
        if self._stop_event.is_set() or health in ("stopped", "idle"):
            overall = "stopped"
        elif not self._is_process_alive(self.pid):
            overall = "unhealthy" if health == "running" else "stopped"
        elif self.stats.get("last_error"):
            overall = "degraded"
        else:
            overall = "healthy"

        _sig_states = self.signal_engine.state_counter()
        _active_states = {k: v for k, v in _sig_states.items() if k != "IDLE"}

        return {
            "runtime_identity": {
                "arm": self.arm,
                "symbol": self.symbol,
                "pid": self.pid,
                "status": "stopped" if (self._stop_event.is_set() or health == "stopped") else "running",
                "started_at": self.started_at_ms,
                "cycle_count": self.cycle_count,
                "overall_health": overall
            },
            "market_data": {
                "status": "connected" if last_15m else "waiting_for_data",
                "symbol": self.symbol,
                "latest_15m_close_time": last_15m["close_time"] if last_15m else None,
                "latest_1h_close_time": last_1h["close_time"] if last_1h else None,
                "latest_4h_close_time": last_4h["close_time"] if last_4h else None
            },
            "signal_engine": {
                "status": "active",
                "universe": len(self.signal_engine.engines),
                "universe_declared": len(self.symbols),
                "watermark_1h": self.signal_engine._global_1h,
                "watermark_4h": self.signal_engine._global_4h,
                "bootstrap": self.signal_engine.bootstrap_stats,
                "skipped_reasons": self.signal_engine.skipped_reasons,
                "scan_errors": self.signal_engine.scan_errors,
                "scan_errors_total": len(self.signal_engine.scan_errors),
                # 全池进度：各状态各多少标的（IDLE/BOX/SWEEP/RETEST/BREAKOUT）
                "state_counter": _sig_states,
                # IDLE 分因：把「IDLE=130」拆成冷却/无数据/坏死行情/宽度不符/ER 超阈/等收盘
                # —— 否则用户只能读到「没有信号」，无法区分数据问题、策略问题还是时间问题。
                "idle_breakdown": self.signal_engine.idle_breakdown(),
                # 兼容旧字段：任一标的处于活跃形态即 true，主状态取占比最高的那个
                "active_box": bool(_active_states),
                "active_state": (max(_active_states.items(), key=lambda kv: kv[1])[0]
                                 if _active_states else "IDLE"),
                "signals_generated_total": self.stats["signals_generated_total"],
                # 漏斗：把「0 条」拆成「卡在第几关」——箱体/扫低/回踩各多少、
                # 各条淘汰原因各多少、历史候选几个、最近一次候选何时。
                # 没有它，「0」既可能是链路断，也可能是策略没机会，无法区分。
                "funnel": self.signal_engine.funnel(),
                # ATR 口径诊断（SIGNAL-HISTORY-FIX-02）：连续递推 + ≥200 根预热是否**真的**在跑。
                # 没有它，「ATR 已按规范修好」只能靠读代码相信，无法从 API / 用户入口核对。
                "atr_caliber": self.signal_engine.atr_caliber_stats(),
                # 水位一致性：水位是否就是「实际喂进状态机的那根」（FIX-01 的缺陷就是两者不一致）
                "watermark_check": self.signal_engine.watermark_consistency(),
                # 数据质量闸门（SIGNAL-HISTORY-FIX-03）：哪些标的因「交易所占位行」被禁止决策
                "data_quality": self.signal_engine.data_quality_stats(),
                "last_cycle_ms": self.signal_engine.last_cycle_ms
            },
            "order_processing": {
                "status": "ready" if self._gate_allow() else "paused_new_entry_gate",
                "orders_routed_total": self.stats["orders_routed_total"],
                "orders_rejected_total": self.stats["orders_rejected_total"],
                # 闸门暂停期间「已生成但尚未登记」的条数：恢复后会被重新扫描并重试，
                # 因此这里如实暴露积压量，而不是让暂停表现为「本轮 0 条」。
                "orders_waiting_gate": self.stats.get("orders_waiting_gate", 0),
                "new_entry_gate": PE.new_entry_gate(),
                "current_pending_in_db": pending_orders_count
            },
            "execution_daemon": {
                "status": "running" if not self._stop_event.is_set() else "stopped",
                "open_positions": open_pos_count,
                "pending_matched_total": self.stats["pending_matched_total"],
                "trailing_updates_total": self.stats["trailing_updates_total"],
                "exits_triggered_total": self.stats["exits_triggered_total"],
                "last_error": self.stats["last_error"]
            }
        }

    def _gate_allow(self) -> bool:
        """新开仓总闸是否放行（实时读盘；异常按 fail-closed 视为不放行）。"""
        try:
            return bool(PE.new_entry_gate()["allow_new_entries"])
        except Exception:                                    # noqa: BLE001
            return False

    def _is_process_alive(self, pid: int) -> bool:
        try:
            os.kill(pid, 0)
            return True
        except (OSError, ProcessLookupError):
            return False

    def _save_status_file(self, health_str: str):
        try:
            self.stats["health"] = health_str
            st_data = self.get_full_status()
            p = status_file_path(self.arm)
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix(".tmp")
            tmp.write_text(json.dumps(st_data, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(p)
        except Exception:
            pass

    def seed_dataset(self, candles: List[dict]):
        """向独立行情源注入版本化测试数据，供回放自主推进。"""
        self.watcher.seed_dataset(candles)

    def step_once(self, now_ms: Optional[int] = None, origin: str = "live") -> dict:
        """执行单次完整管道推进：行情扫描 -> 信号计算 -> 信号落盘 -> 路由挂单 -> 撮合成交 -> 止损与平仓。"""
        now = int(now_ms or time.time() * 1000)
        self.cycle_count += 1
        summary = {
            "now_ms": now,
            "new_signals": 0,
            "routed": 0,
            "waiting_gate": 0,
            "matched": 0,
            "trailing_updates": 0,
            "exits": 0,
            "errors": []
        }

        # 0. 版本化测试数据因果时序激活
        try:
            self.watcher.sync_dataset_bars_upto(now)
        except Exception as exc:
            summary["errors"].append(f"sync_dataset_failed: {exc}")

        # 1. 信号生成（全池：只对出现新 bar 的标的推进）
        try:
            new_cands = self.signal_engine.step(now, log=logger.info)
            if origin == "live":
                # 启动之前确认的历史事件一律不补开（水位线纪律）
                new_cands = [c for c in new_cands if c.available_at_ms >= self.started_at_ms]
            if new_cands:
                summary["new_signals"] = len(new_cands)
                self.stats["signals_generated_total"] += len(new_cands)
                # 2. 信号持久化
                self.router.persist_candidates(new_cands, origin=origin, now_ms=now)
                if origin == "live" and get_notifier:
                    tn = get_notifier()
                    for cand in new_cands:
                        try:
                            tn.notify_signal({
                                "symbol": cand.symbol,
                                "stage": getattr(cand, "stage", "BREAKOUT"),
                                "score": getattr(cand, "resonance_score", 0.0),
                                "price": getattr(cand, "price", "N/A"),
                                "direction": getattr(cand, "direction", "LONG"),
                            })
                        except Exception:
                            pass
        except Exception as exc:
            summary["errors"].append(f"signal_engine_failed: {exc}")

        # 3. 路由与风控挂单
        try:
            r_res = self.router.route_pending_orders(now, origin=origin)
            summary["routed"] = r_res["registered"]
            summary["waiting_gate"] = r_res.get("waiting_gate", 0)
            self.stats["orders_routed_total"] += r_res["registered"]
            self.stats["orders_rejected_total"] += r_res["rejected"]
            self.stats["orders_waiting_gate"] = r_res.get("waiting_gate", 0)
            if r_res["errors"]:
                summary["errors"].extend(r_res["errors"])
        except Exception as exc:
            summary["errors"].append(f"router_failed: {exc}")

        # 4. 执行守护与撮合/止损/退出
        try:
            e_res = self.execution.execute_cycle(now)
            summary["matched"] = e_res["pending_matched"]
            summary["trailing_updates"] = e_res["trailing_updates"]
            summary["exits"] = e_res["exits_triggered"]
            self.stats["pending_matched_total"] += e_res["pending_matched"]
            self.stats["trailing_updates_total"] += e_res["trailing_updates"]
            self.stats["exits_triggered_total"] += e_res["exits_triggered"]
            if e_res["errors"]:
                summary["errors"].extend(e_res["errors"])
        except Exception as exc:
            summary["errors"].append(f"execution_failed: {exc}")

        if summary["errors"]:
            self.stats["last_error"] = "; ".join(summary["errors"])
        else:
            self.stats["last_error"] = None

        self._save_status_file("running")
        return summary

    def run_replay(self, start_ms: int, end_ms: int, step_ms: int = M15) -> List[dict]:
        """加速回放模式：由隔离运行器自行推进时间时钟，测试脚本严禁调用交易执行方法！"""
        # 启动自检
        cfg = PE.approved_params()
        if not cfg.get("_config_valid", False):
            raise RuntimeError(f"startup_config_check_failed: {cfg.get('_config_error')}")

        if not self.acquire_lock():
            raise RuntimeError(f"Runtime lock occupied for arm {self.arm}")

        history = []
        try:
            # 回放同样先预热，否则第一根 bar 就没有箱体可判
            self.signal_engine.history_floor_ms = max(
                0, int(start_ms) - self.signal_engine.warmup_ms)
            self.watcher.sync_dataset_bars_upto(int(start_ms))
            self.signal_engine.bootstrap(int(start_ms), log=logger.info)
            cur_ms = start_ms
            while cur_ms <= end_ms:
                res = self.step_once(now_ms=cur_ms, origin="replay")
                history.append(res)
                cur_ms += step_ms
        finally:
            self.release_lock()
            self._save_status_file("stopped")
        return history

    def run_forever(self):
        """常驻实时模式。"""
        cfg = PE.approved_params()
        if not cfg.get("_config_valid", False):
            msg = f"startup_config_check_failed: {cfg.get('_config_error')}"
            self._save_status_file("failed_config_check")
            raise RuntimeError(msg)

        if not self.acquire_lock():
            msg = f"Runtime for arm {self.arm} lock occupied: {self.lock_file_path}"
            self._save_status_file("failed_lock_occupied")
            raise RuntimeError(msg)

        # 启动只用有限历史恢复状态；历史候选不作为实时新单补开。
        c = self.signal_engine.config
        warmup_ms = self.signal_engine.warmup_ms
        self.signal_engine.history_floor_ms = max(0, self.started_at_ms - warmup_ms)
        L.ensure_live_start_ms(self.started_at_ms)
        logger.info(f"[*] UnifiedRuntime starting: arm={self.arm} "
                    f"universe={len(self.symbols)} interval={self.poll_interval_sec}s")
        self._save_status_file("bootstrapping")
        # ── 合并历史入口（SIGNAL-HISTORY-FIX-03）─────────────────────────────
        # 冷层 parquet + 热库按 `market_repository` 的既定来源规则合并，作为**初始化**
        # 数据入口。只影响 bootstrap；`step()` 仍只读热库（新数据只可能落在热库）。
        # 失败时**不静默**：退回热库并如实写进 bootstrap.data_source。
        src_mode = (os.environ.get("WX_HISTORY_SOURCE") or "merged").strip().lower()
        if src_mode != "hot":
            try:
                import history_loader as HL
                import project_paths as PP
                self.signal_engine.history_provider = HL.MergedHistory(
                    L.MARKET_DB, PP.MARKET_ARCHIVE, log=logger.info)
                logger.info(f"[*] 历史入口 = 冷层+热库合并（archive={PP.MARKET_ARCHIVE}）")
            except Exception as exc:                         # noqa: BLE001
                logger.warning(f"[!] 合并历史入口不可用，退回热库：{type(exc).__name__}: {exc}")
                self.signal_engine.history_provider = None
        else:
            logger.info("[*] 历史入口 = 仅热库（WX_HISTORY_SOURCE=hot）")
        self.signal_engine.prepare_history(int(time.time() * 1000), log=logger.info)
        # 全池预热：逐标的回放预热窗口，重建箱体/扫低进度；产生的历史候选一律丢弃。
        self.signal_engine.bootstrap(int(time.time() * 1000), log=logger.info)
        # 预热用的历史序列（全池 180 天约 1 GB）用完即放，不长期占内存。
        try:
            self.signal_engine._hist = None
            if self.signal_engine.history_provider is not None:
                self.signal_engine.history_provider.release()
        except Exception:                                    # noqa: BLE001
            pass
        logger.info(f"[*] UnifiedRuntime ready: arm={self.arm}")
        self._save_status_file("running")

        try:
            while not self._stop_event.is_set():
                res = self.step_once()
                if res["new_signals"] or res["routed"] or res["matched"] or res["trailing_updates"] or res["exits"]:
                    print(f"[{time.strftime('%X')}] [arm={self.arm}] cycle summary: {res}", flush=True)
                self._stop_event.wait(self.poll_interval_sec)
        except Exception as exc:
            self.stats["last_error"] = str(exc)
            self._save_status_file("stopped_with_error")
        finally:
            self.release_lock()
            self._save_status_file("stopped")

    def stop(self):
        self._stop_event.set()
        self.release_lock()


def get_unified_runtime_status(arm: str = "3a") -> dict:
    """供外部 API 及审计程序读取的统一状态函数。

    除了「状态文件写了什么」，还额外回答一个关键问题：**这条记录属于当前活着的进程吗？**

    为什么必须回答：进程重启时，新实例要先 import 一堆重模块（实测约 20 秒）才写出
    第一份 `bootstrapping` 状态；这段窗口里读到的仍是**上一个实例的临终记录**
    （它 `finally` 里写过 `stopped`）。如果直接把这条记录当成事实，页面就会在每次
    重启后把「正在启动」误判成「运行时停了 ⇒ 故障」，训练人忽略红灯。

    因此这里不再把死 pid 的记录一律说成 `stopped`：
      · `record_pid_alive=False` ⇒ 这条记录来自已退出的进程，**当前实例状态未知**；
      · `record_age_ms` 给出记录写入距今多久，调用方据此区分「刚重启」与「真的没人接手」。
    """
    p = status_file_path(arm)
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            pid = data.get("runtime_identity", {}).get("pid")
            daemon_status = data.get("execution_daemon", {}).get("status")
            is_alive = False
            if pid:
                try:
                    os.kill(int(pid), 0)
                    is_alive = True
                except (OSError, ProcessLookupError):
                    is_alive = False

            try:
                _rec_ms = int(p.stat().st_mtime * 1000)
                _rec_age = max(0, int(time.time() * 1000) - _rec_ms)
            except OSError:
                _rec_ms, _rec_age = None, None
            _ri = data.setdefault("runtime_identity", {})
            _ri["record_pid_alive"] = is_alive
            _ri["record_written_ms"] = _rec_ms
            _ri["record_age_ms"] = _rec_age

            if daemon_status == "stopped" or not is_alive:
                if daemon_status == "stopped":
                    _ri["overall_health"] = "stopped"
                    _ri["status"] = "stopped"
                else:
                    _ri["overall_health"] = "unhealthy"
                    _ri["status"] = "crashed"
                    if "execution_daemon" in data:
                        data["execution_daemon"]["status"] = "crashed"
                # 记录来自已退出的进程 ⇒ 它描述的**不是**当前实例的状态。
                # 保留上面两个原始取值供追溯，另给一个明确语义的状态。
                if not is_alive:
                    _ri["record_status_raw"] = _ri["status"]
                    _ri["status"] = "stale_record"
            return data
        except Exception:
            pass
    return {
        "runtime_identity": {"arm": arm, "status": "not_started", "overall_health": "idle"},
        "market_data": {"status": "unknown"},
        "signal_engine": {"status": "unknown"},
        "order_processing": {"status": "unknown"},
        "execution_daemon": {"status": "unknown"}
    }


def main():
    parser = argparse.ArgumentParser(description="Unified System Runtime")
    parser.add_argument("--arm", default="3a", choices=["3a", "3b", "3c", "3d"], help="Paper account arm")
    parser.add_argument("--symbol", default="all",
                        help="标的：all（默认，全池 scope_crypto_v1）/ 单个 BTCUSDT / 逗号分隔多个")
    parser.add_argument("--interval", type=float, default=2.0, help="Poll interval seconds")
    parser.add_argument("--once", action="store_true", help="Run one cycle and exit")
    parser.add_argument("--status", action="store_true", help="Print current status and exit")
    args = parser.parse_args()

    if args.status:
        st = get_unified_runtime_status(args.arm)
        print(json.dumps(st, ensure_ascii=False, indent=2))
        return 0

    raw = (args.symbol or "all").strip()
    if raw.lower() in ("all", "*", "universe"):
        symbols = _default_universe()
    else:
        symbols = [s.strip().upper() for s in raw.split(",") if s.strip()]
    if not symbols:
        symbols = _default_universe()

    runtime = UnifiedRuntime(arm=args.arm, symbols=symbols, poll_interval_sec=args.interval)

    if args.once:
        # 单轮调试：先预热再推一轮，否则状态机没有箱体可判
        runtime.signal_engine.history_floor_ms = max(
            0, runtime.started_at_ms - runtime.signal_engine.warmup_ms)
        runtime.signal_engine.bootstrap(int(time.time() * 1000))
        res = runtime.step_once()
        print(f"[*] Step finished: {res}")
        return 0

    def sig_handler(sig, frame):
        print("\n[*] Stopping UnifiedRuntime gracefully...")
        runtime.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, sig_handler)
    signal.signal(signal.SIGTERM, sig_handler)

    runtime.run_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
