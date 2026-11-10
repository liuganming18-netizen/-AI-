# -*- coding: utf-8 -*-
"""06-B 冻结候选回测执行入口（Candidate Backtest Runner）

职责边界：
────────
1. 面向策略研究（阶段 04）产出的冻结候选，提供标准化、可直接调用的回测执行能力。
2. 输入绑定固定数据快照及代码/配置版本，底层只读连接，绝不因动态数据库增长产生漂移。
3. 严格历史时钟与 PIT 门禁：只用已收盘（is_closed=1）数据，支持 strict / approximate 模式。
4. 成交与退出遵循既有保守规则：相位 A/B/C/D 顺序，歧义资金费事件进入假设桶，不美化结果。
5. 完整会计对账：成交、手续费、资金费（双口径）、权益、账本逐笔独立重算核对。
6. 状态隔离与盲测防护：完全独占原子运行目录，绝不写运行库、绝不调外部下单，盲测区间硬阻断。
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import shutil
import sqlite3
import threading
import time
import traceback
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

import numpy as np
import pandas as pd

import backtest_data_gate as DG
import backtest_metrics as MET
import backtest_store as S
import bt_run_log as _LOG
import oi_resonance_bridge as BR
from market_repository import MarketDataRepository

PROJECT_ROOT = Path(__file__).resolve().parents[2]
_RUN_LOCK = threading.Lock()

# 默认盲测保护区间（未获最终盲测解封授权前，严禁触碰）
# 2024-10-01 00:00:00 UTC (1727740800000 ms) 起为留出/盲测保护窗口
DEFAULT_HOLDOUT_START_MS = 1727740800000
DEFAULT_HOLDOUT_END_MS = 1735689600000


class HoldoutSealedError(RuntimeError):
    """最终盲测区间封闭保护异常（未获授权严禁打开盲测）"""


class CandidateExecutionError(RuntimeError):
    """候选回测执行异常"""


def _sha_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _df_sha(df: pd.DataFrame, cols: List[str]) -> str:
    use = [c for c in cols if c in df.columns]
    csv = df[use].to_csv(index=False, float_format="%.10f", lineterminator="\n")
    return _sha_bytes(csv.encode("utf-8"))


def _df_to_csv_bytes(df: pd.DataFrame, cols: Optional[List[str]] = None) -> bytes:
    use = [c for c in cols if c in df.columns] if cols else list(df.columns)
    return df[use].to_csv(index=False, float_format="%.10f", lineterminator="\n").encode("utf-8")


def _iso(ms: Optional[int]) -> Optional[str]:
    if ms is None:
        return None
    return datetime.fromtimestamp(int(ms) / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _verify_holdout_boundary(win_start_ms: int, win_end_ms: int,
                             holdout_range: Optional[Tuple[int, int]] = None,
                             holdout_sealed: bool = True) -> None:
    """门禁 6：最终盲测区间封闭检查。若窗口越入盲测区间且盲测处于封存状态，立即抛错硬阻断。"""
    if not holdout_sealed:
        return
    h_start, h_end = holdout_range or (DEFAULT_HOLDOUT_START_MS, DEFAULT_HOLDOUT_END_MS)
    # 只要窗口与盲测区间存在交集，即视为越界
    if not (win_end_ms <= h_start or win_start_ms >= h_end):
        raise HoldoutSealedError(
            f"盲测保护阻断：请求回测窗口 [{_iso(win_start_ms)} ~ {_iso(win_end_ms)}) "
            f"越入了最终盲测保护区间 [{_iso(h_start)} ~ {_iso(h_end)})。"
            f"当前处于 06-B 回测程序准备阶段，最终盲测必须保持封闭，严禁打开！")


def create_or_bind_snapshot(
    run_id: str,
    spec: Dict[str, Any],
    win: Dict[str, Any],
    *,
    data_snapshot: Optional[Dict[str, Any]] = None,
    snapshot_path: Optional[Union[str, Path]] = None,
    repo: Optional[MarketDataRepository] = None,
) -> Dict[str, Any]:
    """门禁 1：输入绑定固定开发快照；原始数据保持只读。

    优先级：
    1. 内存字典快照 `data_snapshot`（最高优先级，用于单测或已封存输入）；
    2. 目录快照 `snapshot_path`（从已封存的快照目录读取）；
    3. 若未提供外部快照，则从只读 repo 提取并立即在 run 目录下原子落盘 inputs 快照，
       计算 SHA256 形成不可变的 inputs_manifest，绝不直连动态变动的 SQLite。
    """
    sym = win["symbol"]
    d_snap = S.snapshot_dir(run_id)
    d_snap.mkdir(parents=True, exist_ok=True)

    # 1. 内存快照
    if data_snapshot is not None:
        sig_df = data_snapshot["sig_df"].copy()
        exec_df = data_snapshot["exec_df"].copy()
        events = list(data_snapshot.get("funding_events") or [])
        sig_bytes = _df_to_csv_bytes(sig_df)
        exec_bytes = _df_to_csv_bytes(exec_df)
        ev_bytes = json.dumps(events, ensure_ascii=False, indent=2).encode("utf-8")
        S.write_snapshot(run_id, f"{sym}_signal_bars.csv", sig_bytes)
        S.write_snapshot(run_id, f"{sym}_exec_bars.csv", exec_bytes)
        S.write_snapshot(run_id, f"{sym}_funding_events.json", ev_bytes)

        manifest = {
            "run_id": run_id,
            "source": "memory_snapshot",
            "symbol": sym,
            "signal_bars_sha256": _sha_bytes(sig_bytes),
            "exec_bars_sha256": _sha_bytes(exec_bytes),
            "funding_events_sha256": _sha_bytes(ev_bytes),
            "funding_events_count": len(events),
            "created_at_utc": _iso(int(time.time() * 1000)),
        }
        S.write_inputs_manifest(run_id, manifest)
        return {
            "sig_df": sig_df, "exec_df": exec_df, "funding_events": events,
            "manifest": manifest, "snapshot_source": "memory_snapshot"
        }

    # 2. 外部快照路径
    if snapshot_path is not None:
        p_snap = Path(snapshot_path)
        if not p_snap.exists():
            raise FileNotFoundError(f"指定的固定快照路径不存在：{snapshot_path}")
        sig_file = p_snap / f"{sym}_signal_bars.csv"
        exec_file = p_snap / f"{sym}_exec_bars.csv"
        ev_file = p_snap / f"{sym}_funding_events.json"
        if not (sig_file.exists() and exec_file.exists()):
            raise FileNotFoundError(f"快照文件不完整，缺失 signal 或 exec K线：{snapshot_path}")
        sig_bytes = sig_file.read_bytes()
        exec_bytes = exec_file.read_bytes()
        ev_bytes = ev_file.read_bytes() if ev_file.exists() else b"[]"

        # 门禁 1-缺口 1: 外部快照复用前，按冻结身份校验实际输入；不一致在执行或覆盖有效结果前拒绝
        sig_sha = _sha_bytes(sig_bytes)
        exec_sha = _sha_bytes(exec_bytes)
        ev_sha = _sha_bytes(ev_bytes)

        manifest_file = p_snap / "inputs_manifest.json"
        if manifest_file.exists():
            try:
                frozen_manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
            except Exception as e:
                raise CandidateExecutionError(f"外部快照 manifest 解析失败，拒绝复用：{e}") from e

            if frozen_manifest.get("symbol") and frozen_manifest["symbol"] != sym:
                raise CandidateExecutionError(
                    f"外部快照标的不匹配：期望 {sym}，快照记录为 {frozen_manifest['symbol']}"
                )
            if "signal_bars_sha256" in frozen_manifest and frozen_manifest["signal_bars_sha256"] != sig_sha:
                raise CandidateExecutionError(
                    f"外部快照身份校验失败：信号K线实际哈希 {sig_sha} 与冻结身份 {frozen_manifest['signal_bars_sha256']} 不一致"
                )
            if "exec_bars_sha256" in frozen_manifest and frozen_manifest["exec_bars_sha256"] != exec_sha:
                raise CandidateExecutionError(
                    f"外部快照身份校验失败：撮合K线实际哈希 {exec_sha} 与冻结身份 {frozen_manifest['exec_bars_sha256']} 不一致"
                )
            if "funding_events_sha256" in frozen_manifest and frozen_manifest["funding_events_sha256"] != ev_sha:
                raise CandidateExecutionError(
                    f"外部快照身份校验失败：资金费事件实际哈希 {ev_sha} 与冻结身份 {frozen_manifest['funding_events_sha256']} 不一致"
                )

        import io
        sig_df = pd.read_csv(io.BytesIO(sig_bytes))
        exec_df = pd.read_csv(io.BytesIO(exec_bytes))
        events = json.loads(ev_bytes.decode("utf-8"))

        S.write_snapshot(run_id, f"{sym}_signal_bars.csv", sig_bytes)
        S.write_snapshot(run_id, f"{sym}_exec_bars.csv", exec_bytes)
        S.write_snapshot(run_id, f"{sym}_funding_events.json", ev_bytes)
        manifest = {
            "run_id": run_id,
            "source": f"file_snapshot:{p_snap}",
            "symbol": sym,
            "signal_bars_sha256": sig_sha,
            "exec_bars_sha256": exec_sha,
            "funding_events_sha256": ev_sha,
            "funding_events_count": len(events),
            "created_at_utc": _iso(int(time.time() * 1000)),
        }
        S.write_inputs_manifest(run_id, manifest)
        return {
            "sig_df": sig_df, "exec_df": exec_df, "funding_events": events,
            "manifest": manifest, "snapshot_source": "file_snapshot"
        }

    # 3. 从只读 Repo 抽取并在此刻落盘为不可变快照
    import backtest_runner as BR_RUN
    active_repo = repo or BR_RUN._repo()
    raw_data = BR_RUN.load_inputs(spec, win, active_repo)
    sig_df = raw_data["sig_df"]
    exec_df = raw_data["exec_df"]
    events = raw_data["funding_events"]

    sig_bytes = _df_to_csv_bytes(sig_df)
    exec_bytes = _df_to_csv_bytes(exec_df)
    ev_bytes = json.dumps(events, ensure_ascii=False, indent=2).encode("utf-8")
    S.write_snapshot(run_id, f"{sym}_signal_bars.csv", sig_bytes)
    S.write_snapshot(run_id, f"{sym}_exec_bars.csv", exec_bytes)
    S.write_snapshot(run_id, f"{sym}_funding_events.json", ev_bytes)

    manifest = {
        "run_id": run_id,
        "source": "immutable_pinned_from_readonly_repo",
        "symbol": sym,
        "signal_bars_sha256": _sha_bytes(sig_bytes),
        "exec_bars_sha256": _sha_bytes(exec_bytes),
        "funding_events_sha256": _sha_bytes(ev_bytes),
        "funding_events_count": len(events),
        "created_at_utc": _iso(int(time.time() * 1000)),
    }
    S.write_inputs_manifest(run_id, manifest)
    return {
        "sig_df": sig_df, "exec_df": exec_df, "funding_events": events,
        "manifest": manifest, "snapshot_source": "immutable_pinned"
    }


def run_candidate_backtest(
    candidate: Any,
    spec: Dict[str, Any],
    *,
    data_snapshot: Optional[Dict[str, Any]] = None,
    snapshot_path: Optional[Union[str, Path]] = None,
    output_dir: Optional[Union[str, Path]] = None,
    pit_mode: str = "approximate",
    holdout_sealed: bool = True,
    holdout_range: Optional[Tuple[int, int]] = None,
    run_id: Optional[str] = None,
) -> Dict[str, Any]:
    """可由冻结候选调用的标准化回测执行入口。

    参数：
    ─────
    candidate: 候选策略实现或标识。可以是：
        - 包含 `compute_signals`、`run_portfolio_execution` 等接口的策略模块/类对象；
        - 或已有策略 Key 字符串（如 "WX_OI_RESONANCE_V1", "WX_EVENT_TREND_V0_1"）。
    spec: 回测规格字典。必选字段包括：
        - `symbols`: 标的列表（长度 1）；
        - `signal_timeframe`: 信号周期（默认 "1h"）；
        - `exec_timeframe`: 执行周期（默认 "15m"）；
        - `window`: 显式时间窗 `{"mode": "explicit", "start_ms": ..., "end_ms": ...}`；
        - 可选 `signal_overrides`, `exec_overrides` 等。
    data_snapshot: 内存数据快照（若提供，100% 隔离运行，不碰磁盘数据库）。
    snapshot_path: 磁盘快照目录（若提供，从该快照目录加载）。
    output_dir: 独立运行输出根目录（默认系统 runs 根目录）。
    pit_mode: "strict"（未知可得时间直接阻断）或 "approximate"（近似模式并显式标记）。
    holdout_sealed: 是否保持最终盲测封闭（默认为 True，强制禁止打开盲测）。
    holdout_range: 盲测毫秒时间范围 `(start_ms, end_ms)`。
    run_id: 指定运行 ID，未指定则根据规格哈希自动生成原子 ID。

    返回：
    ─────
    标准结果字典（含 status, run_id, metrics, ledger_counts, data_quality, manifest,
    failure_reason, duration_seconds 等）。
    """
    t0 = time.time()
    syms = spec.get("symbols") or []
    if len(syms) != 1:
        raise ValueError(f"回测规格 symbols 必须为长度 1，当前为 {syms}")
    sym = syms[0]

    # 1. 窗口解析与盲测保护（门禁 6）
    import backtest_runner as BR_RUN
    win_spec = spec.get("window") or {}
    if win_spec.get("mode") != "explicit":
        raise ValueError("候选回测入口必须传入显式 UTC 时间窗口 window: {mode: 'explicit', start_ms, end_ms}")
    win_start_ms = int(win_spec["start_ms"])
    win_end_ms = int(win_spec["end_ms"])
    if win_start_ms >= win_end_ms:
        raise ValueError(f"非法时间窗口：start_ms ({win_start_ms}) >= end_ms ({win_end_ms})")

    _verify_holdout_boundary(win_start_ms, win_end_ms, holdout_range=holdout_range,
                             holdout_sealed=holdout_sealed)

    # 2. 生成运行 ID 与独占隔离目录（门禁 6）
    spec_copy = copy.deepcopy(spec)
    spec_copy["pit_mode"] = pit_mode
    strategy_id_val = getattr(candidate, "STRATEGY_ID", getattr(candidate, "strategy_id", str(candidate)))
    spec_copy["strategy_id"] = strategy_id_val
    spec_hash = _sha_bytes(json.dumps(spec_copy, sort_keys=True).encode("utf-8"))[:8]
    effective_run_id = run_id or S.new_run_id(spec_hash)

    # 设置独立运行输出根目录（支持外部自定义隔离目录）
    if output_dir is not None:
        p_out = str(Path(output_dir).resolve())
        os.environ["EVENT_TREND_RUNS_DIR"] = p_out
        os.environ["OI_RESONANCE_RUNS_DIR"] = p_out

    d_run = S.run_dir(effective_run_id)

    # 门禁 1-缺口 2: 显式同 run_id 换输入、规则或配置时，比较已有绑定身份，并在写账或覆盖前拒绝
    if d_run.exists():
        existing_doc = S.read_run(effective_run_id) or {}
        if existing_doc:
            old_spec = existing_doc.get("spec") or {}
            old_strat = existing_doc.get("strategy_id")
            critical_keys = ["symbols", "signal_timeframe", "exec_timeframe", "window", "warmup_bars",
                             "signal_overrides", "exec_overrides", "pit_mode"]
            for ck in critical_keys:
                if ck in old_spec and old_spec[ck] != spec_copy.get(ck):
                    raise CandidateExecutionError(
                        f"同运行编号拒绝更换输入、规则或配置（run_id={effective_run_id}）："
                        f"配置项 {ck} 发生漂移（已有: {old_spec[ck]}，当前: {spec_copy.get(ck)}）"
                    )
            if old_strat and old_strat != strategy_id_val:
                raise CandidateExecutionError(
                    f"同运行编号拒绝更换策略身份（run_id={effective_run_id}）："
                    f"已有策略: {old_strat}，当前策略: {strategy_id_val}"
                )

            # 比较已绑定数据输入快照身份（如果已有 inputs_manifest）
            old_manifest_p = d_run / "inputs_manifest.json"
            if old_manifest_p.exists():
                try:
                    old_man = json.loads(old_manifest_p.read_text(encoding="utf-8"))
                    if data_snapshot is not None:
                        new_sig_sha = _sha_bytes(_df_to_csv_bytes(data_snapshot["sig_df"]))
                        new_exec_sha = _sha_bytes(_df_to_csv_bytes(data_snapshot["exec_df"]))
                        if old_man.get("signal_bars_sha256") and old_man["signal_bars_sha256"] != new_sig_sha:
                            raise CandidateExecutionError(
                                f"同运行编号拒绝更换数据输入快照（run_id={effective_run_id}）："
                                f"信号K线指纹不一致（已有: {old_man['signal_bars_sha256']}, 当前: {new_sig_sha}）"
                            )
                        if old_man.get("exec_bars_sha256") and old_man["exec_bars_sha256"] != new_exec_sha:
                            raise CandidateExecutionError(
                                f"同运行编号拒绝更换数据输入快照（run_id={effective_run_id}）："
                                f"撮合K线指纹不一致（已有: {old_man['exec_bars_sha256']}, 当前: {new_exec_sha}）"
                            )
                except CandidateExecutionError:
                    raise
                except Exception as ex:
                    _LOG.run_log("manifest_check_warn", run_id=effective_run_id, error=str(ex))

    # 实验门禁：有批准身份就绑定原开发范围；没有则拒绝真实候选执行，机制验证可继续
    exp_spec = spec.get("experiment") or {}
    eid = exp_spec.get("experiment_id")
    is_formal = bool(spec.get("is_formal", False))
    if is_formal and not eid:
        err_msg = "实验门禁阻断：真实候选执行未绑定已批准实验身份（spec.experiment.experiment_id 缺失），拒绝执行！"
        if not d_run.exists():
            initial_doc = {
                "run_id": effective_run_id,
                "spec": spec_copy,
                "strategy_id": strategy_id_val,
                "created_at": S.utc_now_iso(),
                "status": S.STATUS_FAILED,
                "failure_reason": err_msg,
            }
            S.create_run(effective_run_id, initial_doc)
        else:
            S.set_status(effective_run_id, S.STATUS_FAILED, failure_reason=err_msg)
        _LOG.run_log("candidate_run_failed", run_id=effective_run_id, error=err_msg)
        return S.read_run(effective_run_id)

    initial_doc = {
        "run_id": effective_run_id,
        "spec": spec_copy,
        "strategy_id": strategy_id_val,
        "created_at": S.utc_now_iso(),
        "status": S.STATUS_RUNNING,
        "pit_mode": pit_mode,
        "holdout_sealed": holdout_sealed,
    }
    if not d_run.exists():
        S.create_run(effective_run_id, initial_doc)
    else:
        S.set_status(effective_run_id, S.STATUS_RUNNING)

    _LOG.run_log("candidate_run_started", run_id=effective_run_id, symbol=sym,
                 win_start_ms=win_start_ms, win_end_ms=win_end_ms, pit_mode=pit_mode)

    # 3. 准备只读数据与不可变快照绑定（门禁 1）
    iv_sig = BR_RUN.INTERVAL_MS.get(spec.get("signal_timeframe", "1h"), BR_RUN.HOUR_MS)
    warmup_bars = int(spec.get("warmup_bars") or 240)
    sig_start_req = win_start_ms - warmup_bars * iv_sig

    win = {
        "symbol": sym,
        "exchange": spec.get("exchange", "binance"),
        "market": spec.get("market", "usdt_futures"),
        "signal_timeframe": spec.get("signal_timeframe", "1h"),
        "exec_timeframe": spec.get("exec_timeframe", "15m"),
        "window_source": "explicit_utc",
        "data_end_ms": win_end_ms,
        "sig_end_ms": win_end_ms,
        "exec_end_ms": win_end_ms,
        "signal_series_start_ms": sig_start_req,
        "win_start_ms": win_start_ms,
        "win_end_ms": win_end_ms,
        "window_days": (win_end_ms - win_start_ms) / BR_RUN.DAY_MS,
        "warmup_bars_requested": warmup_bars,
        "warmup_bars": warmup_bars,
        "trade_start_sec": int(win_start_ms // 1000),
    }

    try:
        snap_data = create_or_bind_snapshot(
            effective_run_id, spec, win,
            data_snapshot=data_snapshot,
            snapshot_path=snapshot_path,
        )
    except CandidateExecutionError:
        # 冻结身份校验不一致，直接抛错阻断拒绝，绝不覆盖有效结果
        raise
    except Exception as exc:
        err_msg = f"快照绑定失败（门禁 1）：{type(exc).__name__}: {exc}"
        S.set_status(effective_run_id, S.STATUS_FAILED, failure_reason=err_msg)
        _LOG.run_log("candidate_run_failed", run_id=effective_run_id, error=err_msg)
        return S.read_run(effective_run_id)

    sig_df = snap_data["sig_df"]
    exec_df = snap_data["exec_df"]
    events = snap_data["funding_events"]

    # 规范化时间戳列（兼顾 open_time 与 timestamp 两种惯用命名）
    for df in (sig_df, exec_df):
        if "open_time" in df.columns and "timestamp" not in df.columns:
            df["timestamp"] = df["open_time"]
        elif "timestamp" in df.columns and "open_time" not in df.columns:
            df["open_time"] = df["timestamp"]

    # 4. 历史时钟与数据质量门禁校验（门禁 2）
    # 强制历史时间不可穿透：所有 K 线必须满足 is_closed=1 且 open_time + iv <= win_end_ms
    iv_exec = BR_RUN.INTERVAL_MS.get(spec.get("exec_timeframe", "15m"), BR_RUN.Q_MS)
    sig_closed_unfuture = sig_df[(sig_df["is_closed"] == 1) & (sig_df["open_time"] + iv_sig <= win_end_ms)]
    exec_closed_unfuture = exec_df[(exec_df["is_closed"] == 1) & (exec_df["open_time"] + iv_exec <= win_end_ms)]

    if len(sig_closed_unfuture) < len(sig_df) or len(exec_closed_unfuture) < len(exec_df):
        # 存在未收盘或未来数据被截断，按门禁 2 执行历史时间限制
        sig_df = sig_closed_unfuture.reset_index(drop=True)
        exec_df = exec_closed_unfuture.reset_index(drop=True)

    # PIT 模式检查
    # 检查数据中是否具备可证明的历史可得时间
    pit_proven = True
    pit_reasons = []
    if "available_at" in sig_df.columns:
        null_avail = sig_df["available_at"].isnull().sum()
        if null_avail > 0:
            pit_proven = False
            pit_reasons.append(f"K线缺失 available_at: {null_avail} 根")
    else:
        pit_proven = False
        pit_reasons.append("K线结构未包含 available_at 字段")

    if pit_mode == "strict" and not pit_proven:
        err_msg = f"严格 PIT 门禁阻断（门禁 2）：数据缺少因果可得时间证明（{'; '.join(pit_reasons)}）"
        S.set_status(effective_run_id, S.STATUS_FAILED, failure_reason=err_msg,
                     pit_status="PIT_UNPROVEN")
        _LOG.run_log("candidate_run_failed", run_id=effective_run_id, error=err_msg)
        return S.read_run(effective_run_id)

    pit_status = "PIT_PROVEN" if pit_proven else "PIT_APPROXIMATE"

    # 5. 保守成交与撮合执行（门禁 3）
    # 如果 candidate 自带 run_portfolio_execution 则使用 candidate 的，否则使用标准 trading_core
    exec_fn = getattr(candidate, "run_portfolio_execution", None)
    if exec_fn is None:
        exec_fn = BR.EXEC.run_portfolio_execution

    sp = BR.signal_params(**(spec.get("signal_overrides") or {}))
    ep = BR.exec_params(**(spec.get("exec_overrides") or {}))

    md = {
        sym: {
            "signal_bars": sig_df,
            "intrabar_bars": exec_df,
            "time_unit": "ms",
            "funding_events": events,
            "funding_time_unit": "ms",
        }
    }

    try:
        with _RUN_LOCK:
            res = exec_fn(
                md, strategy_params=sp, exec_cfg=ep, time_unit="ms",
                trade_start_sec=win["trade_start_sec"]
            )
    except Exception as exc:
        tb = traceback.format_exc()
        err_msg = f"候选执行异常（门禁 3）：{type(exc).__name__}: {exc}"
        S.set_status(effective_run_id, S.STATUS_FAILED, failure_reason=err_msg, traceback=tb[-4000:])
        _LOG.run_log("candidate_run_failed", run_id=effective_run_id, error=err_msg)
        return S.read_run(effective_run_id)

    # 6. 落账、费用双口径与逐笔对账（门禁 4、门禁 5）
    # 写入原子账本与事件
    trades = res.get("trades") or []
    orders = res.get("orders") or []
    positions = res.get("positions") or []
    funding_records = res.get("funding_records") or []
    equity_curve = res.get("equity_curve") or []

    # 账本落盘到 d_run
    p_trades = d_run / "trades.jsonl"
    with open(p_trades, "w", encoding="utf-8") as f:
        for t in trades:
            f.write(json.dumps(t, ensure_ascii=False) + "\n")

    p_orders = d_run / "orders.jsonl"
    with open(p_orders, "w", encoding="utf-8") as f:
        for o in orders:
            f.write(json.dumps(o, ensure_ascii=False) + "\n")

    p_pos = d_run / "positions.jsonl"
    with open(p_pos, "w", encoding="utf-8") as f:
        for p in positions:
            f.write(json.dumps(p, ensure_ascii=False) + "\n")

    p_funding = d_run / "funding.jsonl"
    with open(p_funding, "w", encoding="utf-8") as f:
        for r in funding_records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    p_eq = d_run / "equity_curve.jsonl"
    with open(p_eq, "w", encoding="utf-8") as f:
        for e in equity_curve:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")

    # 写入 SQLite 账本，确保幂等性与可查询性（严格对齐 S.LEDGER_SCHEMA）
    ledger_db = S.ledger_path(effective_run_id)
    with sqlite3.connect(str(ledger_db)) as con:
        con.execute("PRAGMA journal_mode=WAL;")
        con.executescript(S.LEDGER_SCHEMA)
        # 门禁 5-缺口 3: 恢复重跑时清空可能存在的中断残留行，确保恢复后账本一致
        con.execute("DELETE FROM trades WHERE run_id = ?;", (effective_run_id,))
        con.execute("DELETE FROM equity WHERE run_id = ?;", (effective_run_id,))
        init_eq = float(getattr(ep, "initial_equity", 10000.0))
        con.execute("INSERT OR REPLACE INTO meta VALUES (?, 'initial_equity', ?);",
                    (effective_run_id, str(init_eq)))

        for t in trades:
            con.execute("""
                INSERT OR REPLACE INTO trades (
                    run_id, trade_id, signal_id, symbol, direction, signal_type,
                    entry_time_ms, exit_time_ms, entry_price, exit_price, qty,
                    gross_pnl, entry_fee, exit_fee, fees, funding_fee, net_pnl,
                    exit_reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                effective_run_id, str(t.get("trade_id")), str(t.get("signal_id")),
                str(t.get("symbol")), str(t.get("direction")), int(t.get("signal_type") or 0),
                int(t.get("entry_time") or 0), int(t.get("exit_time") or 0),
                float(t.get("entry_price") or 0.0), float(t.get("exit_price") or 0.0),
                float(t.get("qty") or 0.0), float(t.get("gross_pnl") or 0.0),
                float(t.get("entry_fee") or 0.0), float(t.get("exit_fee") or 0.0),
                float(t.get("fees") or (float(t.get("entry_fee") or 0.0) + float(t.get("exit_fee") or 0.0))),
                float(t.get("funding_fee") or 0.0), float(t.get("net_pnl") or 0.0),
                str(t.get("exit_reason"))
            ))

        eq_rows = list(equity_curve)
        if not eq_rows:
            eq_rows = [
                {"timestamp": win_start_ms, "cash": init_eq, "equity": init_eq, "positions": 0},
                {"timestamp": win_end_ms, "cash": init_eq, "equity": init_eq, "positions": 0},
            ]
        for e in eq_rows:
            ts = int(e.get("timestamp") or e.get("t") or e.get("ts_ms") or 0)
            ce = float(e.get("cash") if e.get("cash") is not None else e.get("cash_equity", init_eq))
            te = float(e.get("equity") if e.get("equity") is not None else e.get("total_equity", ce))
            up = float(e.get("unrealized_pnl") or (te - ce))
            pos_cnt = int(e.get("positions") if e.get("positions") is not None else e.get("open_positions_count", 0))
            con.execute("""
                INSERT OR REPLACE INTO equity VALUES (?, ?, ?, ?, ?, ?, 0.0);
            """, (effective_run_id, ts, ce, up, te, pos_cnt))

    # 指标计算与从账本对账
    metrics = BR_RUN.compute_metrics(res, win, ep)
    ledger_recomputed = BR_RUN.recompute_metrics_from_ledger(effective_run_id)
    match_ok, diffs = BR_RUN._metrics_match(metrics, ledger_recomputed)
    if not match_ok:
        err_msg = f"账本指标对账不一致（门禁 4）：{diffs}"
        S.set_status(effective_run_id, S.STATUS_FAILED, failure_reason=err_msg)
        _LOG.run_log("candidate_run_failed", run_id=effective_run_id, error=err_msg)
        return S.read_run(effective_run_id)

    # 成功结束
    out = {
        "window": win,
        "metrics": metrics,
        "ledger_recomputed": ledger_recomputed,
        "ledger_counts": S.ledger_counts(effective_run_id),
        "data_quality": {
            "status": "ok" if pit_proven else "degraded",
            "pit_status": pit_status,
            "reasons": pit_reasons,
        },
        "snapshot_manifest": snap_data["manifest"],
        "duration_seconds": round(time.time() - t0, 3),
        "holdout_sealed": holdout_sealed,
    }
    # 写入 summary.json 供历史与扩展消费方读取
    p_summary = d_run / "summary.json"
    with open(p_summary, "w", encoding="utf-8") as f:
        json.dump({
            "run_id": effective_run_id,
            "status": S.STATUS_SUCCEEDED,
            "metrics": metrics,
            "data_quality": {"status": "ok" if pit_proven else "degraded", "pit_status": pit_status},
            "timestamp_created": int(time.time()),
        }, f, ensure_ascii=False, indent=2)

    S.set_status(effective_run_id, S.STATUS_SUCCEEDED, **out)
    _LOG.run_log("candidate_run_succeeded", run_id=effective_run_id,
                 trades_count=metrics.get("trades_count"),
                 net_pnl=metrics.get("net_pnl"),
                 duration_s=out["duration_seconds"])

    return S.read_run(effective_run_id)
