# -*- coding: utf-8 -*-
"""WX_EVENT_TREND_V0_1 真实数据回测与多维对账执行器 (Runner)。

具备：
1. 支持加载 BTCUSDT / ETHUSDT / SOLUSDT 真实多周期 Parquet 数据；
2. 驱动确定性事件状态机生成事件档案与候选快照；
3. 执行四大逐字段对账：
   - 重复全量运行 (Repeatability)
   - 逐条流式 vs 分块批量 (Streaming vs Chunked Batch)
   - 注入重复 Bar 抗扰 (Duplicate Injection)
   - 状态持久化与断点恢复 (Crash-Recovery)
4. 运行 Variant A (完整含OI)、Variant B (关OI消融)、Variant C (成本压力)；
5. 接入 trading_core 共享交易核心生成真实成本账本与 ValidationResult；
6. **整套产物原子提交**：先写临时目录、逐文件 fsync，再原子改名就位 ⇒ 不存在半套 Run；
7. **指纹**：代码 / 配置 / 输入内容 / 冻结规则 四类 sha256 写入 inputs.json 与 manifest.json；
8. **全链路逐笔证据**：orders / fills / positions / trades / ledger / equity_curve 各自独立落盘。
"""
from __future__ import annotations

import copy
import glob
import hashlib
import json
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from .config import EventTrendConfig
from .contracts import CandidateSnapshot, EventDossier, EventState, StateTransition, ValidationResult
from .engine_adapter import run_event_trend_backtest
from .features import compute_wilder_atr_series
from .instrument_spec import resolve_instrument_spec
from .state_machine import EventStateMachine

# 冻结规则文件（其 SHA256 必须与冻结版本一致）
FROZEN_RULES_REL = "evidence/WX_EVENT_TREND_V0_1_策略与回测冻结规则.md"

# 参与本策略行为的代码文件（代码指纹覆盖面）
CODE_FILES = [
    "策略/WyckoffMaster/event_trend_v0_1/__init__.py",
    "策略/WyckoffMaster/event_trend_v0_1/config.py",
    "策略/WyckoffMaster/event_trend_v0_1/contracts.py",
    "策略/WyckoffMaster/event_trend_v0_1/features.py",
    "策略/WyckoffMaster/event_trend_v0_1/state_machine.py",
    "策略/WyckoffMaster/event_trend_v0_1/pit_bridge.py",
    "策略/WyckoffMaster/event_trend_v0_1/engine_adapter.py",
    "策略/WyckoffMaster/event_trend_v0_1/runner.py",
    "策略/WyckoffMaster/trading_core/contracts.py",
    "策略/WyckoffMaster/trading_core/signal.py",
    "策略/WyckoffMaster/trading_core/order.py",
    "策略/WyckoffMaster/trading_core/risk.py",
    "策略/WyckoffMaster/trading_core/policy.py",
    "策略/WyckoffMaster/trading_core/fill.py",
    "策略/WyckoffMaster/trading_core/position.py",
    "策略/WyckoffMaster/trading_core/funding.py",
    "策略/WyckoffMaster/trading_core/pit.py",
    "策略/WyckoffMaster/trading_core/pit_guard.py",
    "策略/WyckoffMaster/trading_core/ledger.py",
    "策略/WyckoffMaster/trading_core/engine.py",
]


def _sha256_file(path: str) -> Optional[str]:
    if not os.path.exists(path):
        return None
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _frame_fingerprint(df: pd.DataFrame, cols: Optional[List[str]] = None) -> dict:
    """DataFrame 内容指纹（确定性；与行序相关，行序本身是输入的一部分）。"""
    use = [c for c in (cols or list(df.columns)) if c in df.columns]
    sub = df[use]
    h = hashlib.sha256()
    h.update(("|".join(str(c) for c in use)).encode("utf-8"))
    h.update(pd.util.hash_pandas_object(sub, index=False)
             .to_numpy(np.uint64).tobytes())
    return {"rows": int(len(sub)), "columns": [str(c) for c in use],
            "sha256": h.hexdigest()}


def _array_fingerprint(arr: np.ndarray, name: str) -> dict:
    a = np.asarray(arr)
    h = hashlib.sha256()
    if np.issubdtype(a.dtype, np.integer):
        h.update(a.astype(np.int64).tobytes())
    else:
        h.update(np.asarray(a, dtype=np.float64).tobytes())
    return {"name": name, "len": int(a.size), "sha256": h.hexdigest()}


def _jsonl_text(rows: List[dict]) -> str:
    return "".join(json.dumps(r, ensure_ascii=False, sort_keys=True, default=str) + "\n"
                   for r in rows)


def _default_base_dir() -> str:
    """工程根目录解析（BACKTEST-REPAIR-01 · 任务 A）。

    优先级：`WX_PROJECT_ROOT` → `DATA_HUB_PROJECT` → 由本文件位置上溯推导。
    全程不读当前工作目录、不含本机绝对路径 ⇒ 换机器 / 换检出目录 / 在隔离
    工作树里跑，解析结果都跟着代码走，不跟着 shell 的 `pwd` 走。
    """
    env = os.environ.get("WX_PROJECT_ROOT") or os.environ.get("DATA_HUB_PROJECT")
    if env:
        return str(Path(env).expanduser().resolve())
    # runner.py → event_trend_v0_1 → WyckoffMaster → 策略 → 工程根
    return str(Path(__file__).resolve().parents[3])


def _default_parquet_base(base_dir: str) -> str:
    """历史行情根目录：`MARKET_ARCHIVE` 优先，否则落在工程根下。"""
    env = os.environ.get("MARKET_ARCHIVE")
    if env:
        return str(Path(env).expanduser().resolve())
    return os.path.join(base_dir, "数据", "市场数据", "parquet")


def _default_runs_base(base_dir: str) -> str:
    """回测产物根目录：`WX_RUNS_BASE` 优先（隔离工作树可指向树外）。"""
    env = os.environ.get("WX_RUNS_BASE")
    if env:
        return str(Path(env).expanduser().resolve())
    return os.path.join(base_dir, "回测日志", "event_trend_v0_1", "runs")


class EventTrendRunner:
    """真实多标的事件趋势回测与对账执行器。"""

    def __init__(self, base_dir: Optional[str] = None,
                 pit_mode: str = "declared_assumption",
                 oi_declared_lag_ms: Optional[int] = None):
        self.base_dir = str(base_dir) if base_dir is not None else _default_base_dir()
        self.parquet_base = _default_parquet_base(self.base_dir)
        self.runs_base = _default_runs_base(self.base_dir)
        os.makedirs(self.runs_base, exist_ok=True)
        # ── PIT 口径（FRAME-PIT-01）──
        # strict：OI 无发布时点证据 ⇒ 拒用（OI_UNAVAILABLE，不出候选）
        # declared_assumption：照用「桶结束」当可得时刻，但整次运行判不合格
        self.pit_mode = pit_mode
        self.oi_declared_lag_ms = oi_declared_lag_ms

    # ══════════════════════ 指纹 ══════════════════════

    def data_paths_report(self) -> dict:
        """三条历史数据路径的**显式**诊断（BACKTEST-REPAIR-01 · 任务 A）。

        动机：原来三条路径各自静默兜底 —— OI 热库不存在时每标的抛一次异常被吞、
        合约快照目录不存在时直接返回空规格簿、parquet 不存在时 glob 返回空列表。
        三种情况下回测都能「跑完」，产物看起来只是「没有候选」，无法区分
        「策略没信号」和「数据没接上」。这里把路径来源与存在性一次性摊开。

        返回 `{base_dir, parquet, market_db, contracts, ok, problems}`。
        `problems` 为空才代表三条路径都接上了。
        """
        problems: List[str] = []
        parquet = {"path": self.parquet_base, "exists": os.path.isdir(self.parquet_base),
                   "source": "env:MARKET_ARCHIVE" if os.environ.get("MARKET_ARCHIVE")
                             else "project_default"}
        if not parquet["exists"]:
            problems.append(f"parquet_archive_missing:{self.parquet_base}")
        else:
            sym_root = os.path.join(self.parquet_base, "candles", "binance", "um")
            parquet["symbol_dirs"] = (len(os.listdir(sym_root))
                                      if os.path.isdir(sym_root) else 0)
            oi_root = os.path.join(self.parquet_base, "oi", "binance", "um")
            parquet["oi_symbol_dirs"] = (len(os.listdir(oi_root))
                                         if os.path.isdir(oi_root) else 0)

        market_db = {"path": None, "exists": False, "source": None, "error": None}
        try:
            # 只在缺的时候插一次，别把 sys.path 撑成重复项
            _be = os.path.join(self.base_dir, "框架", "backend")
            if _be not in sys.path:
                sys.path.insert(0, _be)
            import oi_feed as _oi_feed
            market_db = _oi_feed.probe_hot_db()
            if not market_db.get("exists"):
                problems.append(f"hot_db_missing:{market_db.get('path')}")
            elif market_db.get("error"):
                problems.append(f"hot_db_unreadable:{market_db.get('error')}")
        except Exception as exc:                              # noqa: BLE001
            market_db["error"] = f"{type(exc).__name__}: {exc}"
            problems.append(f"hot_db_probe_failed:{market_db['error']}")

        contracts = {"path": None, "exists": False, "source": None,
                     "missing_reason": None, "snapshots": None}
        try:
            from .instrument_spec import DEFAULT_CONTRACTS_DIR, SpecBook
            cdir = Path(os.environ.get("CONTRACTS_DIR") or DEFAULT_CONTRACTS_DIR)
            contracts["path"] = str(cdir)
            contracts["source"] = ("env:CONTRACTS_DIR" if os.environ.get("CONTRACTS_DIR")
                                   else "project_default")
            contracts["exists"] = cdir.exists()
            if contracts["exists"]:
                book = SpecBook(cdir, strict=False)
                contracts["snapshots"] = len(book._snapshots)
            else:
                contracts["missing_reason"] = f"contracts_dir_missing:{cdir}"
                problems.append(contracts["missing_reason"])
        except Exception as exc:                              # noqa: BLE001
            contracts["missing_reason"] = f"{type(exc).__name__}: {exc}"
            problems.append(f"contracts_probe_failed:{contracts['missing_reason']}")

        return {"base_dir": self.base_dir, "parquet": parquet, "market_db": market_db,
                "contracts": contracts, "ok": not problems, "problems": problems}

    def code_fingerprint(self) -> dict:
        files: Dict[str, Optional[str]] = {}
        for rel in CODE_FILES:
            files[rel] = _sha256_file(os.path.join(self.base_dir, rel))
        combined = _sha256_text(json.dumps(files, sort_keys=True))
        return {"files": files, "combined_sha256": combined,
                "missing": [k for k, v in files.items() if v is None]}

    def rules_fingerprint(self) -> dict:
        p = os.path.join(self.base_dir, FROZEN_RULES_REL)
        return {"path": FROZEN_RULES_REL, "sha256": _sha256_file(p)}

    def config_fingerprint(self, cfg: EventTrendConfig) -> dict:
        payload = json.dumps(cfg.to_dict(), sort_keys=True, default=str)
        return {"sha256": _sha256_text(payload), "params": cfg.to_dict()}

    def input_fingerprint(self, df_4h, df_1h, df_15m, oi_ts, oi_vals,
                          funding_events: List[dict]) -> dict:
        return {
            "bar_4h": _frame_fingerprint(df_4h, ["open_time", "close_time", "open",
                                                 "high", "low", "close", "volume"]),
            "bar_1h": _frame_fingerprint(df_1h, ["timestamp", "open", "high",
                                                 "low", "close", "volume", "quote_volume"]),
            "bar_15m": _frame_fingerprint(df_15m, ["timestamp", "open", "high",
                                                   "low", "close", "volume", "quote_volume"]),
            "oi_ts": _array_fingerprint(oi_ts, "oi_ts"),
            "oi_values": _array_fingerprint(oi_vals, "oi_values"),
            "funding_events": {
                "count": len(funding_events),
                "sha256": _sha256_text(json.dumps(funding_events, sort_keys=True,
                                                  default=str)),
            },
        }

    # ══════════════════════ 原子提交 ══════════════════════

    def _commit_run_dir(self, run_dir: str, files: Dict[str, str]) -> dict:
        """整套产物原子提交：写临时目录 → 逐文件 fsync → 原子改名就位。

        返回 manifest（含每个文件的 sha256 与字节数），一并写入 manifest.json。
        """
        if os.path.exists(run_dir):
            raise FileExistsError(f"run_dir_already_exists:{run_dir}")
        partial = f"{run_dir}.partial-{os.getpid()}"
        if os.path.exists(partial):
            shutil.rmtree(partial)
        os.makedirs(partial, exist_ok=False)

        manifest_files: Dict[str, dict] = {}
        for name, text in files.items():
            fp = os.path.join(partial, name)
            with open(fp, "w", encoding="utf-8") as f:
                f.write(text)
                f.flush()
                os.fsync(f.fileno())
            manifest_files[name] = {"bytes": len(text.encode("utf-8")),
                                    "sha256": _sha256_text(text)}

        manifest = {
            "run_dir": os.path.basename(run_dir),
            "commit_mode": "atomic_directory_rename",
            "files": manifest_files,
            "files_count": len(manifest_files),
        }
        mp = os.path.join(partial, "manifest.json")
        with open(mp, "w", encoding="utf-8") as f:
            f.write(json.dumps(manifest, indent=2, ensure_ascii=False, sort_keys=True))
            f.flush()
            os.fsync(f.fileno())

        # 目录级原子就位
        os.replace(partial, run_dir)
        return manifest

    def load_symbol_data(
        self,
        symbol: str,
        start_date: Optional[str] = "2024-01-01",
        end_date: Optional[str] = "2024-06-30",
        allow_auto_window: bool = False
    ) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, np.ndarray, np.ndarray, List[dict]]:
        """加载指定标的在此区间内的 4h(含预热), 1h, 15m, OI 5m 与 Funding 数据。

        allow_auto_window=False 时**禁止**「窗口内数据不足就改用该币上线后 180 天」的自适应，
        严格按传入区间跑。批量回测务必传 False：否则短窗口会被静默替换成完全不同的时间段，
        跑出来的交易日期跟标称窗口对不上。
        """
        # 1. 4h K 线
        f_4h = sorted(glob.glob(f"{self.parquet_base}/candles/binance/um/{symbol}/4h/*.parquet"))
        if not f_4h:
            raise FileNotFoundError(f"未找到 {symbol} 4h 数据")
        df_4h_raw = pd.concat([pd.read_parquet(f) for f in f_4h], ignore_index=True)
        df_4h_raw = df_4h_raw.sort_values("open_time").drop_duplicates("open_time").reset_index(drop=True)
        for c in ("open", "high", "low", "close", "volume", "quote_volume"):
            if c in df_4h_raw.columns:
                df_4h_raw[c] = pd.to_numeric(df_4h_raw[c], errors="coerce")

        # 计算全历史 Wilder ATR(14)
        df_4h_raw["atr"] = compute_wilder_atr_series(
            df_4h_raw["high"].to_numpy(np.float64),
            df_4h_raw["low"].to_numpy(np.float64),
            df_4h_raw["close"].to_numpy(np.float64),
            period=14
        )

        # 2. 1h K 线
        f_1h = sorted(glob.glob(f"{self.parquet_base}/candles/binance/um/{symbol}/1h/*.parquet"))
        df_1h_raw = pd.concat([pd.read_parquet(f) for f in f_1h], ignore_index=True)
        df_1h_raw = df_1h_raw.sort_values("open_time").drop_duplicates("open_time").reset_index(drop=True)

        # 3. 15m K 线
        f_15m = sorted(glob.glob(f"{self.parquet_base}/candles/binance/um/{symbol}/15m/*.parquet"))
        df_15m_raw = pd.concat([pd.read_parquet(f) for f in f_15m], ignore_index=True)
        df_15m_raw = df_15m_raw.sort_values("open_time").drop_duplicates("open_time").reset_index(drop=True)

        # 数值列统一为 float（冷层以字符串存储 OHLCV），保证指纹与撮合口径一致
        for dfx in (df_1h_raw, df_15m_raw):
            for c in ("open", "high", "low", "close", "volume", "quote_volume"):
                if c in dfx.columns:
                    dfx[c] = pd.to_numeric(dfx[c], errors="coerce")

        # 4. OI 5m 真实序列
        #    C02-R1：连 `fetched_at` / `source` 一起读。原来只读 `ts`/`open_interest` 两列 ⇒
        #    逐行可得时刻在「加载」这一步就丢了，后面无论怎么传都补不回来（也不允许补造）。
        #    缺列或缺失一律留 `None`，由下游按 NaN 处理（fail-closed），不推导、不回退桶结束。
        f_oi = sorted(glob.glob(f"{self.parquet_base}/oi/binance/um/{symbol}/*.parquet"))

        def _read_oi_frame(_f: str) -> pd.DataFrame:
            try:
                return pd.read_parquet(
                    _f, columns=["ts", "open_interest", "fetched_at", "source"])
            except Exception:                                   # 旧分片可能没有这两列
                _d = pd.read_parquet(_f, columns=["ts", "open_interest"])
                _d["fetched_at"] = None
                _d["source"] = None
                return _d

        df_oi_raw = (pd.concat([_read_oi_frame(f) for f in f_oi], ignore_index=True) if f_oi else pd.DataFrame(columns=["ts", "open_interest", "fetched_at", "source"]))
        df_oi_raw = df_oi_raw.sort_values("ts").drop_duplicates("ts").reset_index(drop=True)
        oi_ts = df_oi_raw["ts"].to_numpy(np.int64)
        oi_vals = df_oi_raw["open_interest"].to_numpy(np.float64)
        # 逐行原始信息（ts / fetched_at / source）原样保留，供 FT-LOW4H 分支交给状态机
        self.oi_rows = [
            {"ts": int(_r.ts),
             "oi": (None if pd.isna(_r.open_interest) else float(_r.open_interest)),
             "fetched_at": (None if pd.isna(_r.fetched_at) else int(_r.fetched_at)),
             "source": (None if pd.isna(_r.source) else str(_r.source))}
            for _r in df_oi_raw.itertuples(index=False)
        ]

        # 5. Funding 真实结算事件
        f_fund = f"{self.parquet_base}/funding/binance/um/{symbol}.parquet"
        funding_events: List[dict] = []
        if os.path.exists(f_fund):
            df_fund = pd.read_parquet(f_fund)
            time_col = "funding_time" if "funding_time" in df_fund.columns else "time_ms"
            for _, row in df_fund.iterrows():
                funding_events.append({
                    "time_ms": int(row[time_col]),
                    "rate": float(row["funding_rate"]),
                    "mark_price": float(row.get("mark_price", 0.0)),
                    "rate_type": "settled"
                })

        # 裁剪到目标区间
        start_ms = int(pd.to_datetime(start_date).timestamp() * 1000)
        end_ms = int(pd.to_datetime(end_date).timestamp() * 1000)

        # 1h 与 15m 截取目标区间
        df_1h = df_1h_raw[(df_1h_raw["open_time"] >= start_ms) & (df_1h_raw["close_time"] <= end_ms)].copy()
        df_15m = df_15m_raw[(df_15m_raw["open_time"] >= start_ms) & (df_15m_raw["close_time"] <= end_ms)].copy()

        # 自适应对齐：若该标的在固定区间未上线或数据不足，自适应采用其真实可得的最长 180 天窗口
        if allow_auto_window and (len(df_15m) < 2000 or len(df_1h) < 500) and len(df_15m_raw) >= 2000:
            earliest_15m = int(df_15m_raw["open_time"].iloc[0])
            latest_15m = int(df_15m_raw["close_time"].iloc[-1])
            # 留出 4h 预热所需的 50 根 4h (约 8 天)
            auto_start = earliest_15m + 50 * 4 * 3600 * 1000
            auto_end = min(latest_15m, auto_start + 180 * 24 * 3600 * 1000)
            if auto_end - auto_start >= 30 * 24 * 3600 * 1000:
                start_ms, end_ms = auto_start, auto_end
                print("  [自适应窗口] %s 区间内数据不足，改用 %s ~ %s（该币上线后 180 天）"
                      % (symbol,
                         pd.to_datetime(auto_start, unit="ms").strftime("%Y-%m-%d"),
                         pd.to_datetime(auto_end, unit="ms").strftime("%Y-%m-%d")),
                      file=sys.stderr, flush=True)
                df_1h = df_1h_raw[(df_1h_raw["open_time"] >= start_ms) & (df_1h_raw["close_time"] <= end_ms)].copy()
                df_15m = df_15m_raw[(df_15m_raw["open_time"] >= start_ms) & (df_15m_raw["close_time"] <= end_ms)].copy()

        df_1h["timestamp"] = df_1h["open_time"]
        df_15m["timestamp"] = df_15m["open_time"]

        # 4h 包含目标区间之前至少 42 根预热 (取前 220 根以支持 ATR 预热)
        idx_start_4h = np.searchsorted(df_4h_raw["close_time"].to_numpy(np.int64), start_ms)
        warmup_start_idx = max(0, idx_start_4h - 220)
        df_4h = df_4h_raw.iloc[warmup_start_idx:].copy()
        df_4h = df_4h[df_4h["close_time"] <= end_ms].reset_index(drop=True)

        return df_4h, df_1h, df_15m, oi_ts, oi_vals, funding_events

    def run_state_machine_simulation(
        self,
        symbol: str,
        df_4h: pd.DataFrame,
        df_1h: pd.DataFrame,
        oi_ts: np.ndarray,
        oi_vals: np.ndarray,
        enable_oi_filter: bool = True,
        chunk_size: Optional[int] = None,
        inject_duplicates: bool = False,
        pit_mode: Optional[str] = None,
        cfg: Optional[EventTrendConfig] = None
    ) -> EventStateMachine:
        """运行确定性事件状态机。支持流式逐条、分块批量或注入重复。

        PIT 受控读取（FRAME-PIT-01）：
        · 每根 1h bar 用于决策前，校验 `close_time + 1ms <= decision_time`（未收盘即抛错）；
        · OI 只取「决策时刻已可得」的观测，可得时刻未证时按 `pit_mode` 拒用或判不合格。

        `cfg`（BACKTEST-REPAIR-01 新增，默认 None = 旧行为）：
        显式传入调用方的 `EventTrendConfig`，避免「回测按 A 配置跑执行、却按默认配置
        跑判定」这种两个事实源。默认值仍是 `EventTrendConfig()`，既有调用方零影响。
        """
        sm = EventStateMachine(
            config=(cfg or EventTrendConfig()), enable_oi_filter=enable_oi_filter,
            pit_mode=(pit_mode or self.pit_mode),
            oi_declared_lag_ms=self.oi_declared_lag_ms,
        )
        sm.symbol = symbol
        sm.pit.register_oi(oi_ts, oi_vals)

        records = df_1h.to_dict("records")
        if inject_duplicates and len(records) > 50:
            dups = [copy.deepcopy(records[50])] * 99
            records = records[:51] + dups + records[51:]

        if chunk_size and chunk_size > 0:
            chunks = [records[i:i + chunk_size] for i in range(0, len(records), chunk_size)]
        else:
            chunks = [records]

        for chunk in chunks:
            for r in chunk:
                t_open = int(r["timestamp"])
                t_close = t_open + 3600 * 1000

                # 硬闸：这根 1h K 线必须已收盘才能进决策
                sm.pit.assert_candles_closed(int(r["close_time"]), t_close, label="1h")

                if t_close % (4 * 3600 * 1000) == 0:
                    sub_4h = df_4h[df_4h["close_time"] <= t_close]
                    if len(sub_4h) >= 42:
                        last_42 = sub_4h.tail(42)
                        atr_val = float(sub_4h["atr"].iloc[-1])
                        # 4h 箱体同样要求已收盘
                        sm.pit.assert_candles_closed(
                            int(sub_4h["close_time"].iloc[-1]), t_close, label="4h")
                        sm.check_and_form_box(
                            bar_4h_close_ms=t_close,
                            history_42_highs=last_42["high"].to_numpy(np.float64),
                            history_42_lows=last_42["low"].to_numpy(np.float64),
                            history_42_closes=last_42["close"].to_numpy(np.float64),
                            current_atr_a=atr_val
                        )

                sm.process_1h_bar(
                    open_time_ms=t_open,
                    open_p=float(r["open"]),
                    high_p=float(r["high"]),
                    low_p=float(r["low"]),
                    close_p=float(r["close"]),
                    volume_v=float(r["volume"]),
                    oi_timestamps=oi_ts,
                    oi_values=oi_vals
                )

        return sm

    def execute_reconciliation_suite(
        self,
        symbol: str,
        df_4h: pd.DataFrame,
        df_1h: pd.DataFrame,
        oi_ts: np.ndarray,
        oi_vals: np.ndarray
    ) -> Dict[str, Any]:
        """执行完整四大确定性与抗扰逐字段对账（比对全部导出内部状态字典）。"""
        sm_a = self.run_state_machine_simulation(symbol, df_4h, df_1h, oi_ts, oi_vals, enable_oi_filter=True)
        st_a = sm_a.export_state()

        sm_a_rep = self.run_state_machine_simulation(symbol, df_4h, df_1h, oi_ts, oi_vals, enable_oi_filter=True)
        st_a_rep = sm_a_rep.export_state()
        diff_repeat = (json.dumps(st_a, sort_keys=True) != json.dumps(st_a_rep, sort_keys=True))

        sm_chunked = self.run_state_machine_simulation(symbol, df_4h, df_1h, oi_ts, oi_vals, enable_oi_filter=True, chunk_size=50)
        st_chunked = sm_chunked.export_state()
        diff_chunked = (json.dumps(st_a, sort_keys=True) != json.dumps(st_chunked, sort_keys=True))

        sm_dup = self.run_state_machine_simulation(symbol, df_4h, df_1h, oi_ts, oi_vals, enable_oi_filter=True, inject_duplicates=True)
        st_dup = sm_dup.export_state()
        diff_dup = (json.dumps(st_a, sort_keys=True) != json.dumps(st_dup, sort_keys=True))

        records = df_1h.to_dict("records")
        split_point = len(records) // 2
        sm_part1 = EventStateMachine(config=EventTrendConfig(), enable_oi_filter=True)
        sm_part1.symbol = symbol
        for r in records[:split_point]:
            t_open = int(r["timestamp"])
            t_close = t_open + 3600 * 1000
            if t_close % (4 * 3600 * 1000) == 0:
                sub_4h = df_4h[df_4h["close_time"] <= t_close]
                if len(sub_4h) >= 42:
                    last_42 = sub_4h.tail(42)
                    atr_val = float(sub_4h["atr"].iloc[-1])
                    sm_part1.check_and_form_box(
                        bar_4h_close_ms=t_close,
                        history_42_highs=last_42["high"].to_numpy(np.float64),
                        history_42_lows=last_42["low"].to_numpy(np.float64),
                        history_42_closes=last_42["close"].to_numpy(np.float64),
                        current_atr_a=atr_val
                    )
            sm_part1.process_1h_bar(
                open_time_ms=t_open, open_p=float(r["open"]), high_p=float(r["high"]),
                low_p=float(r["low"]), close_p=float(r["close"]), volume_v=float(r["volume"]),
                oi_timestamps=oi_ts, oi_values=oi_vals
            )
        snapshot = sm_part1.export_state()

        sm_rec = EventStateMachine.restore_state(snapshot)
        for r in records[split_point:]:
            t_open = int(r["timestamp"])
            t_close = t_open + 3600 * 1000
            if t_close % (4 * 3600 * 1000) == 0:
                sub_4h = df_4h[df_4h["close_time"] <= t_close]
                if len(sub_4h) >= 42:
                    last_42 = sub_4h.tail(42)
                    atr_val = float(sub_4h["atr"].iloc[-1])
                    sm_rec.check_and_form_box(
                        bar_4h_close_ms=t_close,
                        history_42_highs=last_42["high"].to_numpy(np.float64),
                        history_42_lows=last_42["low"].to_numpy(np.float64),
                        history_42_closes=last_42["close"].to_numpy(np.float64),
                        current_atr_a=atr_val
                    )
            sm_rec.process_1h_bar(
                open_time_ms=t_open, open_p=float(r["open"]), high_p=float(r["high"]),
                low_p=float(r["low"]), close_p=float(r["close"]), volume_v=float(r["volume"]),
                oi_timestamps=oi_ts, oi_values=oi_vals
            )
        st_rec = sm_rec.export_state()
        diff_recovery = (json.dumps(st_a, sort_keys=True) != json.dumps(st_rec, sort_keys=True))

        return {
            "symbol": symbol,
            "repeatability_pass": not diff_repeat,
            "chunked_batch_pass": not diff_chunked,
            "duplicate_injection_pass": not diff_dup,
            "crash_recovery_pass": not diff_recovery,
            "state_a": sm_a
        }

    # ══════════════════════ 合约规格 ══════════════════════

    def _resolve_spec(self, symbol: str, df_15m: pd.DataFrame,
                      cfg: EventTrendConfig):
        """解析该标的在**本次窗口**的合约规格。

        · 价格样本只用于**证伪**（观察价是否落在 tick 网格上），不用于推断 tick；
        · 规格缺失 / 无法证明历史适用 ⇒ `formal_pass_allowed=False`，
          该 run 只能作为**机制测试**，不得声明真实历史盈亏已修正。
        """
        ts = pd.to_numeric(df_15m["timestamp"], errors="coerce").dropna()
        ws = int(ts.min()) if len(ts) else None
        we = int(ts.max()) + 900_000 if len(ts) else None
        # 均匀抽样 2000 个真实收盘价做证伪检验（够用且不拖慢）
        closes = pd.to_numeric(df_15m["close"], errors="coerce").dropna()
        if len(closes) > 2000:
            step = max(1, len(closes) // 2000)
            closes = closes.iloc[::step]
        observed = [float(x) for x in closes.tolist() if float(x) > 0]
        return resolve_instrument_spec(
            symbol, window_start_ms=ws, window_end_ms=we,
            venue=getattr(cfg, "venue", "binance"),
            market=getattr(cfg, "market", "um"),
            order_type="MARKET", observed_prices=observed,
        )

    # ══════════════════════ 产物装配 ══════════════════════

    def _build_run_files(
        self, *, run_id: str, symbol: str, variant: str, sm_inst: EventStateMachine,
        res_inst: Dict[str, Any], funnel: Dict[str, Any], run_timestamp: int,
        data_summary: Dict[str, Any], fingerprints: Dict[str, Any],
        pit_audit: Optional[Dict[str, Any]] = None
    ) -> Dict[str, str]:
        """装配一个 Run 的**全部**产物文件（内存中），交给原子提交写盘。"""
        engine_result = res_inst["engine_result"]
        orders = res_inst.get("orders", [])
        ledger_events = res_inst.get("ledger_events", [])
        trades = engine_result.get("trades", [])
        equity_curve = engine_result.get("equity_curve", [])

        fills = [e for e in ledger_events if e.get("event_type") == "ORDER_FILLED"]
        opened = [e for e in ledger_events if e.get("event_type") == "POSITION_OPENED"]
        closed = [e for e in ledger_events if e.get("event_type") == "POSITION_CLOSED"]
        rejects = [e for e in ledger_events if e.get("event_type") == "SIGNAL_REJECTED"]
        funding_evs = [e for e in ledger_events if e.get("event_type") == "FUNDING_FEE"]

        # ── PIT 资格（FRAME-PIT-01）──
        # 正式资格 = 规格可证 AND 可得时点可证。两项必须同时成立，缺一即 False。
        if pit_audit is None:
            if sm_inst is not None and getattr(sm_inst, "pit", None) is not None:
                pit_audit = sm_inst.pit.audit()
                pit_audit["audit_sha256"] = sm_inst.pit.audit_sha256()
            else:
                # 无状态机可查（纯产物装配场景）⇒ 不得默认「合格」
                pit_audit = {"qualified": False,
                             "qualification_failures": ["pit_audit_unavailable:无状态机可查"],
                             "pit_guard_version": None}
        pit_qualified = bool(pit_audit.get("qualified", False))
        spec_allowed = bool((res_inst.get("summary") or {}).get("formal_pass_allowed", False))

        inputs_meta = {
            "run_id": run_id,
            "symbol": symbol,
            "strategy_version": "v0.1.0",
            "variant": variant,
            "timestamp_created": run_timestamp,
            "data_summary": data_summary,
            "fingerprints": fingerprints,
            # 成交精度：规格来源/适用性（缺失即 formal_pass_allowed=False）
            "instrument_spec": res_inst.get("instrument_spec"),
            "spec_status": res_inst.get("spec_status"),
            # PIT：数据可得时点审计 + 决策时钟口径
            "pit_audit": pit_audit,
            "pit_guard_version": pit_audit.get("pit_guard_version"),
        }
        summary_meta = {
            "run_id": run_id, "symbol": symbol, "variant": variant,
            "funnel": funnel,
            "trading_summary": res_inst["summary"],
            "identity": res_inst.get("identity", {}),
            "reconcile": engine_result.get("reconcile", {}),
            "precision": res_inst.get("precision", {}),
            "instrument_spec": res_inst.get("instrument_spec"),
            "spec_status": res_inst.get("spec_status"),
            # 正式资格：规格 + PIT 双闸
            "formal_pass_allowed": bool(spec_allowed and pit_qualified),
            "formal_pass_allowed_components": {
                "instrument_spec": spec_allowed,
                "pit": pit_qualified,
            },
            "formal_pass_block_reasons": (
                ([] if spec_allowed else ["instrument_spec_unproven"])
                + list(pit_audit.get("qualification_failures", []))),
            "pit_audit_sha256": pit_audit.get("audit_sha256"),
            "pit_qualified": pit_qualified,
        }
        ev_list = [ev.to_dict() for ev in getattr(sm_inst, "completed_events", [])]
        act_ev = getattr(sm_inst, "active_event", None)
        if act_ev is not None:
            act_dict = act_ev.to_dict()
            if not act_dict.get("termination_reason"):
                act_dict["termination_reason"] = "active_truncated_at_backtest_end"
            ev_list.append(act_dict)

        files: Dict[str, str] = {
            "inputs.json": json.dumps(inputs_meta, indent=2, ensure_ascii=False, sort_keys=True),
            "summary.json": json.dumps(summary_meta, indent=2, ensure_ascii=False, sort_keys=True),
            "events.jsonl": _jsonl_text(ev_list),
            "candidates.jsonl": _jsonl_text([c.to_dict() for c in
                                             getattr(sm_inst, "candidates", [])]),
            "transitions.jsonl": _jsonl_text([t.to_dict() for t in
                                              getattr(sm_inst, "transitions", [])]),
            "validation_results.jsonl": _jsonl_text([v.to_dict() for v in res_inst["validation_results"]]),
            # ── 全链路逐笔证据 ──
            "orders.jsonl": _jsonl_text(orders),
            "fills.jsonl": _jsonl_text(fills),
            "positions.jsonl": _jsonl_text(opened + closed),
            "trades.jsonl": _jsonl_text(trades),
            "rejects.jsonl": _jsonl_text(rejects),
            "funding.jsonl": _jsonl_text(funding_evs),
            "ledger.jsonl": _jsonl_text(ledger_events),
            "equity_curve.jsonl": _jsonl_text(equity_curve),
            "decisions.jsonl": _jsonl_text(engine_result.get("decisions", [])),
            # ── 成交精度证据（规格来源 / 适用性 / 拒绝明细）──
            "instrument_spec.json": json.dumps(
                {"instrument_spec": res_inst.get("instrument_spec"),
                 "spec_status": res_inst.get("spec_status"),
                 "precision": res_inst.get("precision", {})},
                indent=2, ensure_ascii=False, sort_keys=True),
        }
        return files

    def run_variant_abc(
        self,
        symbol: str,
        df_4h: pd.DataFrame,
        df_1h: pd.DataFrame,
        df_15m: pd.DataFrame,
        oi_ts: np.ndarray,
        oi_vals: np.ndarray,
        funding_events: List[dict],
        cfg: Optional[EventTrendConfig] = None,
        initial_equity: Optional[float] = None,
        create_variant_runs: bool = False
    ) -> Dict[str, Any]:
        """运行 A/B/C 三版本并**原子提交**完整产物与指纹。

        `initial_equity=None`（默认）⇒ 采用 `EventTrendConfig.initial_equity`。
        本函数**不设**自己的本金默认值，避免同一参数出现两个事实源。
        """
        cfg = cfg or EventTrendConfig()
        # ── 合约规格（tick / step / 最小量 / 最小名义额）──
        # 只认交易所 exchangeInfo 原始证据；窗口内的真实价格仅用于**证伪**。
        # 规格缺失/无法证明适用于该窗口 ⇒ formal_pass_allowed=False（不得当正式回测通过）。
        spec = self._resolve_spec(symbol, df_15m, cfg)
        if cfg.rule == "FT-LOW4H":
            # ══════════════════════════════════════════════════════════════════
            # BACKTEST-REPAIR-01（任务 B）· 接线修复
            # ──────────────────────────────────────────────────────────────────
            # 原实现：走 `unified_runtime.RealSignalEngine` + `_MemWatcher`，
            #         OI 由引擎自己从**实时热库**取（`OF.load_oi_rows`），
            #         而传入本函数的 parquet OI（`oi_ts` / `oi_vals`）只进了
            #         `input_fingerprint`，**从未喂给状态机**。
            #
            # 为什么那条路在历史回测里必然出 0 候选（不是「策略没信号」）：
            #   `RealSignalEngine._load_oi_window` 把每行的可得时刻取成 `fetched_at`，
            #   无 `fetched_at` ⇒ `NaN` ⇒ `_oi_slice_as_of` 全部排除，
            #   且该函数**没有** `declared_assumption` 分支（只有 `PitBridge` 有）。
            #   历史 parquet OI 的 `fetched_at` 全为空 ⇒ 每个决策点都「当时读不到 OI」
            #   ⇒ 状态机走 `OI_UNAVAILABLE` ⇒ 突破永不成立 ⇒ 候选恒 0。
            #   实测：`rows_loaded=0 / decisions_with_oi=0 / decisions_without_oi=2904`。
            #
            # 现在：改用回测自有驱动 `run_state_machine_simulation` + **parquet OI**。
            #   · 判定规则不变：同一个 `EventStateMachine`、同一份 `cfg`（逐字段相同）；
            #   · OI 可得性走 `PitBridge` 的 `declared_assumption`：不伪造发布时刻，
            #     只把「桶结束」显式登记为假设，并让整次运行判不合格（`qualified=False`）；
            #   · 每根 bar 过 `pit.assert_candles_closed` 硬闸，4h 箱体只用已收盘 bar；
            #   · A/B/C 三个变体恢复各自语义（原来 `sm_a = sm_b` 且三次都用 variant="B"）。
            # ══════════════════════════════════════════════════════════════════
            # A: 完整版（OI 门槛开）
            sm_a = self.run_state_machine_simulation(
                symbol, df_4h, df_1h, oi_ts, oi_vals,
                enable_oi_filter=bool(cfg.enable_oi_filter), cfg=cfg)
            # B: 关 OI 门槛（消融）
            sm_b = self.run_state_machine_simulation(
                symbol, df_4h, df_1h, oi_ts, oi_vals,
                enable_oi_filter=False, cfg=cfg)

            res_a = run_event_trend_backtest(
                symbol=symbol, df_1h=df_1h, df_15m=df_15m,
                candidates=sm_a.candidates, funding_events=funding_events,
                df_4h=df_4h, variant="A", initial_equity=initial_equity,
                config=cfg, instrument_spec=spec)
            res_b = run_event_trend_backtest(
                symbol=symbol, df_1h=df_1h, df_15m=df_15m,
                candidates=sm_b.candidates, funding_events=funding_events,
                df_4h=df_4h, variant="B", initial_equity=initial_equity,
                config=cfg, instrument_spec=spec)
            # C: 成本压力（同一批候选，费率/滑点加压）
            res_c = run_event_trend_backtest(
                symbol=symbol, df_1h=df_1h, df_15m=df_15m,
                candidates=sm_a.candidates, funding_events=funding_events,
                df_4h=df_4h, variant="C", initial_equity=initial_equity,
                config=cfg, instrument_spec=spec)
        else:
            # A: 完整版 (开 OI)
            sm_a = self.run_state_machine_simulation(symbol, df_4h, df_1h, oi_ts, oi_vals, enable_oi_filter=True)
            # B: 关 OI 门槛
            sm_b = self.run_state_machine_simulation(symbol, df_4h, df_1h, oi_ts, oi_vals, enable_oi_filter=False)

            res_a = run_event_trend_backtest(
                symbol=symbol, df_1h=df_1h, df_15m=df_15m,
                candidates=sm_a.candidates, funding_events=funding_events,
                df_4h=df_4h, variant="A", initial_equity=initial_equity, config=cfg,
                instrument_spec=spec
            )
            res_b = run_event_trend_backtest(
                symbol=symbol, df_1h=df_1h, df_15m=df_15m,
                candidates=sm_b.candidates, funding_events=funding_events,
                df_4h=df_4h, variant="B", initial_equity=initial_equity, config=cfg,
                instrument_spec=spec
            )
            res_c = run_event_trend_backtest(
                symbol=symbol, df_1h=df_1h, df_15m=df_15m,
                candidates=sm_a.candidates, funding_events=funding_events,
                df_4h=df_4h, variant="C", initial_equity=initial_equity, config=cfg,
                instrument_spec=spec
            )

        funnel_a = {
            "boxes": len(sm_a.completed_events) + (1 if sm_a.active_event else 0),
            "sweeps": sum(1 for e in sm_a.completed_events if e.sweep_time_ms is not None),
            "retests": sum(1 for e in sm_a.completed_events if e.retest_time_ms is not None),
            "breakouts": sum(1 for e in sm_a.completed_events if e.breakout_time_ms is not None),
            "volume_rejected": sum(1 for e in sm_a.completed_events if e.current_state == EventState.VOLUME_REJECTED),
            "oi_rejected": sum(1 for e in sm_a.completed_events if e.current_state == EventState.OI_REJECTED),
            "oi_unavailable": sum(1 for e in sm_a.completed_events if e.current_state == EventState.OI_UNAVAILABLE),
            "expired": sum(1 for e in sm_a.completed_events if e.current_state == EventState.EXPIRED),
            "invalidated": sum(1 for e in sm_a.completed_events if e.current_state == EventState.INVALIDATED),
            "candidates": len(sm_a.candidates),
            "executed_trades": res_a["summary"]["trade_count"]
        }

        funnel_b = {
            "boxes": len(sm_b.completed_events) + (1 if sm_b.active_event else 0),
            "candidates": len(sm_b.candidates),
            "executed_trades": res_b["summary"]["trade_count"]
        }

        data_summary = {
            "bar_4h_count": len(df_4h),
            "bar_1h_count": len(df_1h),
            "bar_15m_count": len(df_15m),
            "oi_records_count": len(oi_ts),
            "funding_records_count": len(funding_events),
            "time_range_1h": {
                "start_ms": int(df_1h["timestamp"].iloc[0]),
                "end_ms": int(df_1h["timestamp"].iloc[-1]) + 3600 * 1000,
                "start_utc": str(pd.to_datetime(int(df_1h["timestamp"].iloc[0]), unit="ms")),
                "end_utc": str(pd.to_datetime(int(df_1h["timestamp"].iloc[-1]) + 3600 * 1000, unit="ms"))
            }
        }

        fingerprints = {
            "rules": self.rules_fingerprint(),
            "code": self.code_fingerprint(),
            "config": self.config_fingerprint(cfg),
            "inputs": self.input_fingerprint(df_4h, df_1h, df_15m, oi_ts, oi_vals, funding_events),
        }

        run_ids: Dict[str, str] = {}
        manifests: Dict[str, dict] = {}
        run_timestamp = int(time.time())
        # Run ID 唯一性：同一秒内重复调用不得覆盖已有 Run（原子提交要求目标不存在）
        while any(os.path.exists(os.path.join(
                self.runs_base, f"run_WX_EVENT_TREND_V0_1_{symbol}_{suf}_{run_timestamp}"))
                for suf in ("A", "B", "C", "")):
            run_timestamp += 1

        plan = [
            ("A", sm_a, res_a, funnel_a),
            ("B", sm_b, res_b, funnel_b),
            ("C", sm_a, res_c, funnel_a),
        ]
        if create_variant_runs:
            for v_name, sm_inst, res_inst, fun_inst in plan:
                run_id = f"run_WX_EVENT_TREND_V0_1_{symbol}_{v_name}_{run_timestamp}"
                run_dir = os.path.join(self.runs_base, run_id)
                files = self._build_run_files(
                    run_id=run_id, symbol=symbol, variant=v_name, sm_inst=sm_inst,
                    res_inst=res_inst, funnel=fun_inst, run_timestamp=run_timestamp,
                    data_summary=data_summary, fingerprints=fingerprints)
                manifests[v_name] = self._commit_run_dir(run_dir, files)
                run_ids[v_name] = run_id

        # 主运行目录：以 Variant A 真实回放为基准，附 A/B/C 三版本汇总
        main_run_id = f"run_WX_EVENT_TREND_V0_1_{symbol}_{run_timestamp}"
        main_run_dir = os.path.join(self.runs_base, main_run_id)
        main_files = self._build_run_files(
            run_id=main_run_id, symbol=symbol, variant=("B" if cfg.rule == "FT-LOW4H" else "A"), sm_inst=sm_a,
            res_inst=res_a, funnel=funnel_a, run_timestamp=run_timestamp,
            data_summary=data_summary, fingerprints=fingerprints)
        main_summary = json.loads(main_files["summary.json"])
        main_summary["execution_rule"] = cfg.rule
        if cfg.rule == "FT-LOW4H":
            main_summary["oi_filter_enabled"] = bool(cfg.enable_oi_filter)
            main_summary["ablation_run"] = False
        else:
            main_summary["summary_variant_a"] = res_a["summary"]
            main_summary["summary_variant_b"] = res_b["summary"]
            main_summary["summary_variant_c"] = res_c["summary"]
            main_summary["funnel_variant_b"] = funnel_b
        main_files["summary.json"] = json.dumps(main_summary, indent=2,
                                                ensure_ascii=False, sort_keys=True)
        main_manifest = self._commit_run_dir(main_run_dir, main_files)

        return {
            "main_run_id": main_run_id,
            "run_ids": run_ids,
            "symbol": symbol,
            "funnel_a": funnel_a,
            "funnel_b": funnel_b,
            "fingerprints": fingerprints,
            "manifests": {"main": main_manifest, **manifests},
            # PIT 资格（FRAME-PIT-01）：本次运行的可得时点审计
            "pit_qualified": bool(sm_a.pit.qualified),
            "pit_failures": sm_a.pit.qualification_failures(),
            "pit_audit_sha256": sm_a.pit.audit_sha256(),
            "pit_mode": sm_a.pit.mode.value,
            "res_a": res_a,
            "res_b": res_b,
            "res_c": res_c
        }
