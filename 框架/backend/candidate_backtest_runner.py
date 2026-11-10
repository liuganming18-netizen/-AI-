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
import experiment_registry as ER
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


def normalize_spec(spec: Dict[str, Any], candidate: Any = None,
                   pit_mode: str = "approximate") -> Dict[str, Any]:
    """规范化回测配置，补齐缺省值、新增字段、exchange/market等，用于严格比对防配置漂移。"""
    s = copy.deepcopy(spec)
    syms = list(s.get("symbols") or [])
    s["symbols"] = syms
    s["exchange"] = str(s.get("exchange") or "binance").lower()
    s["market"] = str(s.get("market") or "usdt_futures").lower()
    s["signal_timeframe"] = str(s.get("signal_timeframe") or "1h").lower()
    s["exec_timeframe"] = str(s.get("exec_timeframe") or "15m").lower()
    s["warmup_bars"] = int(s.get("warmup_bars") or 240)
    s["pit_mode"] = str(s.get("pit_mode") or pit_mode or "approximate").lower()

    win = s.get("window") or {}
    s["window"] = {
        "mode": str(win.get("mode") or "explicit"),
        "start_ms": int(win["start_ms"]) if "start_ms" in win else None,
        "end_ms": int(win["end_ms"]) if "end_ms" in win else None,
    }

    s["signal_overrides"] = dict(s.get("signal_overrides") or {})
    s["exec_overrides"] = dict(s.get("exec_overrides") or {})

    if candidate is not None:
        strat = getattr(candidate, "STRATEGY_ID", getattr(candidate, "strategy_id", str(candidate)))
        s["strategy_id"] = str(strat)
    elif "strategy_id" in s:
        s["strategy_id"] = str(s["strategy_id"])
    else:
        s["strategy_id"] = "CANDIDATE_STRATEGY"

    return s


def inspect_and_hash_inputs(
    sym: str,
    *,
    data_snapshot: Optional[Dict[str, Any]] = None,
    snapshot_path: Optional[Union[str, Path]] = None,
    repo: Optional[MarketDataRepository] = None,
    spec: Optional[Dict[str, Any]] = None,
    win: Optional[Dict[str, Any]] = None,
) -> Tuple[Dict[str, Any], Dict[str, str]]:
    """在写任何状态前，统一校验并计算完整输入身份（涵盖 signal, exec, funding_events 三要素）。

    返回: (raw_data_dict, hashes_dict)
    hashes_dict 包含 signal_bars_sha256, exec_bars_sha256, funding_events_sha256
    """
    # 1. 内存快照
    if data_snapshot is not None:
        if "sig_df" not in data_snapshot or "exec_df" not in data_snapshot:
            raise CandidateExecutionError("内存快照缺失 sig_df 或 exec_df，输入不完整")
        if "funding_events" not in data_snapshot:
            raise CandidateExecutionError("内存快照缺失 funding_events 字段，输入身份不完整（必须覆盖资金费）")
        events = data_snapshot.get("funding_events")
        if not isinstance(events, (list, tuple)):
            raise CandidateExecutionError("funding_events 必须为列表格式")
        sig_bytes = _df_to_csv_bytes(data_snapshot["sig_df"])
        exec_bytes = _df_to_csv_bytes(data_snapshot["exec_df"])
        ev_bytes = json.dumps(list(events), ensure_ascii=False, indent=2).encode("utf-8")
        raw_data = {
            "sig_df": data_snapshot["sig_df"].copy(),
            "exec_df": data_snapshot["exec_df"].copy(),
            "funding_events": list(events),
            "sig_bytes": sig_bytes,
            "exec_bytes": exec_bytes,
            "ev_bytes": ev_bytes,
            "source": "memory_snapshot",
        }
        hashes = {
            "signal_bars_sha256": _sha_bytes(sig_bytes),
            "exec_bars_sha256": _sha_bytes(exec_bytes),
            "funding_events_sha256": _sha_bytes(ev_bytes),
        }
        return raw_data, hashes

    # 2. 外部快照路径
    if snapshot_path is not None:
        p_snap = Path(snapshot_path)
        if not p_snap.exists():
            raise FileNotFoundError(f"指定的固定快照路径不存在：{snapshot_path}")
        sig_file = p_snap / f"{sym}_signal_bars.csv"
        exec_file = p_snap / f"{sym}_exec_bars.csv"
        ev_file = p_snap / f"{sym}_funding_events.json"
        if not (sig_file.exists() and exec_file.exists()):
            raise CandidateExecutionError(f"外部快照文件不完整，缺失 signal 或 exec K线：{snapshot_path}")
        if not ev_file.exists():
            raise CandidateExecutionError(f"外部快照文件不完整，缺失资金费事件文件：{ev_file}（必须覆盖资金费）")

        sig_bytes = sig_file.read_bytes()
        exec_bytes = exec_file.read_bytes()
        ev_bytes = ev_file.read_bytes()

        try:
            events = json.loads(ev_bytes.decode("utf-8"))
            if not isinstance(events, list):
                raise ValueError("funding_events 不是列表")
        except Exception as e:
            raise CandidateExecutionError(f"外部快照资金费事件文件损坏：{e}") from e

        import io
        sig_df = pd.read_csv(io.BytesIO(sig_bytes))
        exec_df = pd.read_csv(io.BytesIO(exec_bytes))

        # 校验外部 manifest 自洽性
        man_file = p_snap / "inputs_manifest.json"
        if man_file.exists():
            try:
                ext_man = json.loads(man_file.read_text(encoding="utf-8"))
            except Exception as e:
                raise CandidateExecutionError(f"外部快照 manifest 解析失败，凭据损坏：{e}") from e
            if ext_man.get("symbol") and ext_man["symbol"] != sym:
                raise CandidateExecutionError(f"外部快照标的不匹配：期望 {sym}，快照为 {ext_man['symbol']}")
            if "signal_bars_sha256" in ext_man and ext_man["signal_bars_sha256"] != _sha_bytes(sig_bytes):
                raise CandidateExecutionError("外部快照信号K线与自身manifest不一致（凭据损坏）")
            if "exec_bars_sha256" in ext_man and ext_man["exec_bars_sha256"] != _sha_bytes(exec_bytes):
                raise CandidateExecutionError("外部快照撮合K线与自身manifest不一致（凭据损坏）")
            if "funding_events_sha256" in ext_man and ext_man["funding_events_sha256"] != _sha_bytes(ev_bytes):
                raise CandidateExecutionError("外部快照资金费事件与自身manifest不一致（凭据损坏）")

        raw_data = {
            "sig_df": sig_df, "exec_df": exec_df, "funding_events": events,
            "sig_bytes": sig_bytes, "exec_bytes": exec_bytes, "ev_bytes": ev_bytes,
            "source": f"file_snapshot:{p_snap}",
        }
        hashes = {
            "signal_bars_sha256": _sha_bytes(sig_bytes),
            "exec_bars_sha256": _sha_bytes(exec_bytes),
            "funding_events_sha256": _sha_bytes(ev_bytes),
        }
        return raw_data, hashes

    # 3. 从只读 Repo 提取
    import backtest_runner as BR_RUN
    active_repo = repo or BR_RUN._repo()
    raw_inputs = BR_RUN.load_inputs(spec, win, active_repo)
    sig_df = raw_inputs["sig_df"]
    exec_df = raw_inputs["exec_df"]
    events = list(raw_inputs.get("funding_events") or [])
    sig_bytes = _df_to_csv_bytes(sig_df)
    exec_bytes = _df_to_csv_bytes(exec_df)
    ev_bytes = json.dumps(events, ensure_ascii=False, indent=2).encode("utf-8")
    raw_data = {
        "sig_df": sig_df, "exec_df": exec_df, "funding_events": events,
        "sig_bytes": sig_bytes, "exec_bytes": exec_bytes, "ev_bytes": ev_bytes,
        "source": "immutable_pinned_from_readonly_repo",
    }
    hashes = {
        "signal_bars_sha256": _sha_bytes(sig_bytes),
        "exec_bars_sha256": _sha_bytes(exec_bytes),
        "funding_events_sha256": _sha_bytes(ev_bytes),
    }
    return raw_data, hashes


def create_or_bind_snapshot(
    run_id: str,
    spec: Dict[str, Any],
    win: Dict[str, Any],
    *,
    data_snapshot: Optional[Dict[str, Any]] = None,
    snapshot_path: Optional[Union[str, Path]] = None,
    repo: Optional[MarketDataRepository] = None,
) -> Dict[str, Any]:
    """门禁 1：输入绑定固定开发快照；原始数据保持只读。"""
    sym = win["symbol"]
    d_snap = S.snapshot_dir(run_id)
    d_snap.mkdir(parents=True, exist_ok=True)

    raw_data, hashes = inspect_and_hash_inputs(
        sym, data_snapshot=data_snapshot, snapshot_path=snapshot_path,
        repo=repo, spec=spec, win=win
    )

    sig_file = d_snap / f"{sym}_signal_bars.csv"
    exec_file = d_snap / f"{sym}_exec_bars.csv"
    ev_file = d_snap / f"{sym}_funding_events.json"

    # 首次登记或已确认一致写入
    S.write_snapshot(run_id, f"{sym}_signal_bars.csv", raw_data["sig_bytes"])
    S.write_snapshot(run_id, f"{sym}_exec_bars.csv", raw_data["exec_bytes"])
    S.write_snapshot(run_id, f"{sym}_funding_events.json", raw_data["ev_bytes"])

    manifest = {
        "run_id": run_id,
        "source": raw_data["source"],
        "symbol": sym,
        "signal_bars_sha256": hashes["signal_bars_sha256"],
        "exec_bars_sha256": hashes["exec_bars_sha256"],
        "funding_events_sha256": hashes["funding_events_sha256"],
        "funding_events_count": len(raw_data["funding_events"]),
        "created_at_utc": _iso(int(time.time() * 1000)),
    }
    S.write_inputs_manifest(run_id, manifest)

    return {
        "sig_df": raw_data["sig_df"],
        "exec_df": raw_data["exec_df"],
        "funding_events": raw_data["funding_events"],
        "manifest": manifest,
        "snapshot_source": raw_data["source"],
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
    execution_interceptor: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> Dict[str, Any]:
    """可由冻结候选调用的标准化回测执行入口。"""
    t0 = time.time()
    syms = spec.get("symbols") or []
    if len(syms) != 1:
        raise ValueError(f"回测规格 symbols 必须为长度 1，当前为 {syms}")
    sym = syms[0]

    # 1. 规范化配置
    import backtest_runner as BR_RUN
    curr_norm_spec = normalize_spec(spec, candidate, pit_mode=pit_mode)
    win_spec = curr_norm_spec["window"]
    if win_spec.get("mode") != "explicit":
        raise ValueError("候选回测入口必须传入显式 UTC 时间窗口 window: {mode: 'explicit', start_ms, end_ms}")
    win_start_ms = win_spec["start_ms"]
    win_end_ms = win_spec["end_ms"]
    if win_start_ms is None or win_end_ms is None or win_start_ms >= win_end_ms:
        raise ValueError(f"非法时间窗口：start_ms ({win_start_ms}) >= end_ms ({win_end_ms})")

    # 2. 真实实验注册门禁校验（在写运行状态前）
    exp_spec = spec.get("experiment") or {}
    eid = exp_spec.get("experiment_id") or spec.get("experiment_id")
    is_mechanism_test = bool(spec.get("is_mechanism_test") or spec.get("test_isolated"))

    # 正式候选判定：显式 is_formal/mode=formal，或正式策略 candidate (WX_*) 且非机制测试
    is_formal_candidate = (
        bool(spec.get("is_formal", False)) or
        str(spec.get("mode", "")).lower() == "formal" or
        (isinstance(candidate, str) and candidate.startswith("WX_") and not is_mechanism_test)
    )

    if is_formal_candidate:
        effective_holdout_sealed = True  # 正式候选强制保持盲测封闭，严禁绕过
        if not eid:
            raise CandidateExecutionError(
                "实验门禁阻断：正式候选必须显式绑定已批准并已冻结的 experiment_id（不得临时起意或省略 is_formal 绕过）！"
            )
    else:
        effective_holdout_sealed = holdout_sealed

    # 检查实验注册表
    if eid:
        reg = ER.get_registry()
        st_exps = reg.state().get("experiments") or {}
        exp = st_exps.get(eid)
        if not exp:
            raise CandidateExecutionError(f"实验门禁阻断：指定的实验 ID 不存在（未登记）：{eid}")
        if exp.get("abandoned"):
            raise CandidateExecutionError(f"实验门禁阻断：实验已明确放弃：{eid}（{exp['abandoned'].get('reason')}）")
        if not exp.get("frozen_at"):
            raise CandidateExecutionError(f"实验门禁阻断：实验尚未冻结：{eid} ⇒ 冻结前不得产出正式回测结果！")

        scope = exp.get("sample_scope") or {}
        scope_syms = scope.get("symbols") or []
        if scope_syms and sym not in scope_syms:
            raise CandidateExecutionError(f"实验门禁阻断：标的 {sym} 不在实验 {eid} 批准的标的范围 {scope_syms} 内！")

        scope_start = scope.get("window_start") or scope.get("start_ms")
        scope_end = scope.get("window_end") or scope.get("end_ms")
        if scope_start and win_start_ms < int(scope_start):
            raise CandidateExecutionError(f"实验门禁阻断：窗口起点 {win_start_ms} 超出实验批准范围（最早 {scope_start}）")
        if scope_end and win_end_ms > int(scope_end):
            raise CandidateExecutionError(f"实验门禁阻断：窗口终点 {win_end_ms} 超出实验批准范围（最晚 {scope_end}）")

    # 3. 盲测保护门禁（门禁 6）
    _verify_holdout_boundary(win_start_ms, win_end_ms, holdout_range=holdout_range,
                             holdout_sealed=effective_holdout_sealed)

    # 4. 计算运行 ID 与预备窗口上下文
    iv_sig = BR_RUN.INTERVAL_MS.get(curr_norm_spec.get("signal_timeframe", "1h"), BR_RUN.HOUR_MS)
    warmup_bars = curr_norm_spec.get("warmup_bars", 240)
    sig_start_req = win_start_ms - warmup_bars * iv_sig

    win = {
        "symbol": sym,
        "exchange": curr_norm_spec["exchange"],
        "market": curr_norm_spec["market"],
        "signal_timeframe": curr_norm_spec["signal_timeframe"],
        "exec_timeframe": curr_norm_spec["exec_timeframe"],
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

    # 5. 在写任何运行状态、快照及账本前，统一预校验输入身份（门禁 1）
    raw_data, input_hashes = inspect_and_hash_inputs(
        sym, data_snapshot=data_snapshot, snapshot_path=snapshot_path,
        spec=curr_norm_spec, win=win
    )

    spec_hash = _sha_bytes(json.dumps(curr_norm_spec, sort_keys=True).encode("utf-8"))[:8]
    effective_run_id = run_id or S.new_run_id(spec_hash)

    # 设置独立运行输出根目录
    if output_dir is not None:
        p_out = str(Path(output_dir).resolve())
        os.environ["EVENT_TREND_RUNS_DIR"] = p_out
        os.environ["OI_RESONANCE_RUNS_DIR"] = p_out

    d_run = S.run_dir(effective_run_id)

    # 6. 同 run_id 规范化比对与快照身份防覆盖检查（必须在 set_status(RUNNING) 之前拒绝）
    if d_run.exists():
        existing_doc = S.read_run(effective_run_id) or {}
        if existing_doc:
            old_spec_raw = existing_doc.get("spec") or {}
            old_norm = normalize_spec(old_spec_raw, existing_doc.get("strategy_id"))
            compare_keys = [
                "symbols", "exchange", "market", "signal_timeframe", "exec_timeframe",
                "warmup_bars", "pit_mode", "window", "strategy_id",
                "signal_overrides", "exec_overrides"
            ]
            for ck in compare_keys:
                if old_norm.get(ck) != curr_norm_spec.get(ck):
                    raise CandidateExecutionError(
                        f"同运行编号拒绝更换输入、规则或配置（run_id={effective_run_id}）："
                        f"配置项 {ck} 发生漂移（已有: {old_norm.get(ck)}，当前: {curr_norm_spec.get(ck)}）"
                    )

            # 比对已有快照身份（inputs_manifest.json）
            old_manifest_p = d_run / "inputs_manifest.json"
            if old_manifest_p.exists():
                try:
                    old_man = json.loads(old_manifest_p.read_text(encoding="utf-8"))
                except Exception as ex:
                    raise CandidateExecutionError(
                        f"同运行编号复用凭据 inputs_manifest.json 损坏，拒绝继续：{ex}"
                    ) from ex

                for hkey in ("signal_bars_sha256", "exec_bars_sha256", "funding_events_sha256"):
                    if hkey in old_man and old_man[hkey] != input_hashes[hkey]:
                        raise CandidateExecutionError(
                            f"同运行编号拒绝更换输入快照（run_id={effective_run_id}）："
                            f"指纹 {hkey} 不一致（旧绑定: {old_man[hkey]}，新输入: {input_hashes[hkey]}）。"
                            f"不同内容的外部快照即使带有自己的有效manifest，也不能覆盖旧run的绑定身份！"
                        )
            elif existing_doc.get("status") == S.STATUS_SUCCEEDED:
                raise CandidateExecutionError(
                    f"同运行编号复用凭据缺失（run_id={effective_run_id}）："
                    f"旧运行已成功但缺失 inputs_manifest.json，严禁无凭据重跑或覆盖！"
                )

    # ──────────────────────────────────────────────────────────────────────────
    # 所有前置校验通过，方可初始化或更新运行状态为 RUNNING
    # ──────────────────────────────────────────────────────────────────────────
    initial_doc = {
        "run_id": effective_run_id,
        "spec": curr_norm_spec,
        "strategy_id": curr_norm_spec["strategy_id"],
        "created_at": S.utc_now_iso(),
        "status": S.STATUS_RUNNING,
        "pit_mode": pit_mode,
        "holdout_sealed": effective_holdout_sealed,
    }
    if not d_run.exists():
        S.create_run(effective_run_id, initial_doc)
    else:
        S.set_status(effective_run_id, S.STATUS_RUNNING)

    _LOG.run_log("candidate_run_started", run_id=effective_run_id, symbol=sym,
                 win_start_ms=win_start_ms, win_end_ms=win_end_ms, pit_mode=pit_mode)

    # 7. 写入快照并生成 manifest（若尚未写入）
    d_snap = S.snapshot_dir(effective_run_id)
    d_snap.mkdir(parents=True, exist_ok=True)
    man_p = d_run / "inputs_manifest.json"
    if not man_p.exists():
        S.write_snapshot(effective_run_id, f"{sym}_signal_bars.csv", raw_data["sig_bytes"])
        S.write_snapshot(effective_run_id, f"{sym}_exec_bars.csv", raw_data["exec_bytes"])
        S.write_snapshot(effective_run_id, f"{sym}_funding_events.json", raw_data["ev_bytes"])
        manifest = {
            "run_id": effective_run_id,
            "source": raw_data["source"],
            "symbol": sym,
            "signal_bars_sha256": input_hashes["signal_bars_sha256"],
            "exec_bars_sha256": input_hashes["exec_bars_sha256"],
            "funding_events_sha256": input_hashes["funding_events_sha256"],
            "funding_events_count": len(raw_data["funding_events"]),
            "created_at_utc": _iso(int(time.time() * 1000)),
        }
        S.write_inputs_manifest(effective_run_id, manifest)
    else:
        manifest = json.loads(man_p.read_text(encoding="utf-8"))

    sig_df = raw_data["sig_df"]
    exec_df = raw_data["exec_df"]
    events = raw_data["funding_events"]

    for df in (sig_df, exec_df):
        if "open_time" in df.columns and "timestamp" not in df.columns:
            df["timestamp"] = df["open_time"]
        elif "timestamp" in df.columns and "open_time" not in df.columns:
            df["open_time"] = df["timestamp"]

    # 8. 历史时钟过滤（门禁 2）
    iv_exec = BR_RUN.INTERVAL_MS.get(curr_norm_spec.get("exec_timeframe", "15m"), BR_RUN.Q_MS)
    sig_closed_unfuture = sig_df[(sig_df["is_closed"] == 1) & (sig_df["open_time"] + iv_sig <= win_end_ms)]
    exec_closed_unfuture = exec_df[(exec_df["is_closed"] == 1) & (exec_df["open_time"] + iv_exec <= win_end_ms)]

    if len(sig_closed_unfuture) < len(sig_df) or len(exec_closed_unfuture) < len(exec_df):
        sig_df = sig_closed_unfuture.reset_index(drop=True)
        exec_df = exec_closed_unfuture.reset_index(drop=True)

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
        S.set_status(effective_run_id, S.STATUS_FAILED, failure_reason=err_msg, pit_status="PIT_UNPROVEN")
        _LOG.run_log("candidate_run_failed", run_id=effective_run_id, error=err_msg)
        return S.read_run(effective_run_id)

    pit_status = "PIT_PROVEN" if pit_proven else "PIT_APPROXIMATE"

    # 9. 执行撮合与策略（门禁 3）
    exec_fn = getattr(candidate, "run_portfolio_execution", None)
    if exec_fn is None:
        exec_fn = BR.EXEC.run_portfolio_execution

    sp = BR.signal_params(**(curr_norm_spec.get("signal_overrides") or {}))
    ep = BR.exec_params(**(curr_norm_spec.get("exec_overrides") or {}))

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
        if execution_interceptor is not None:
            execution_interceptor({"run_id": effective_run_id, "stage": "before_execution"})

        with _RUN_LOCK:
            res = exec_fn(
                md, strategy_params=sp, exec_cfg=ep, time_unit="ms",
                trade_start_sec=win["trade_start_sec"]
            )

        if execution_interceptor is not None:
            execution_interceptor({"run_id": effective_run_id, "stage": "after_execution", "res": res})
    except Exception as exc:
        tb = traceback.format_exc()
        err_msg = f"候选执行异常（门禁 3）：{type(exc).__name__}: {exc}"
        S.set_status(effective_run_id, S.STATUS_FAILED, failure_reason=err_msg, traceback=tb[-4000:])
        _LOG.run_log("candidate_run_failed", run_id=effective_run_id, error=err_msg)
        return S.read_run(effective_run_id)

    # 10. 账本落盘与原子清理残留行（门禁 4、门禁 5）
    trades = res.get("trades") or []
    orders = res.get("orders") or []
    positions = res.get("positions") or []
    funding_records = res.get("funding_records") or []
    equity_curve = res.get("equity_curve") or []

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

    ledger_db = S.ledger_path(effective_run_id)
    with sqlite3.connect(str(ledger_db)) as con:
        con.execute("PRAGMA journal_mode=WAL;")
        con.executescript(S.LEDGER_SCHEMA)
        # 恢复或重新执行时清空可能存在的中断残留行
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

    # 11. 指标计算与账本对账
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
        "snapshot_manifest": manifest,
        "duration_seconds": round(time.time() - t0, 3),
        "holdout_sealed": effective_holdout_sealed,
    }
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
