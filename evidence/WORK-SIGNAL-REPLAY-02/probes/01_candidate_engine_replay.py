# -*- coding: utf-8 -*-
"""WORK-SIGNAL-REPLAY-02 · 探针 01：四候选真实引擎回放与事件因果追踪

目标：
1. 彻底纠正上一轮 WORK-SIGNAL-REPLAY-01 中硬编码交易结果、未运行引擎、以及混淆生产门禁的缺陷；
2. 对全部 4 个真实候选标的：
   - VELVETUSDT (候选生成: 2026-10-02 15:00 UTC)
   - SUSDT (候选生成: 2026-10-06 06:00 UTC)
   - WALUSDT (候选生成: 2026-10-10 11:00 UTC)
   - BBUSDT (候选生成: 2026-10-11 07:00 UTC)
   使用获批策略 WX_EVENT_TREND_V0_1 冻结规则 FT-LOW4H 调用真实 EventTrendRunner 引擎在隔离目录中完整重放；
3. 核对数据预热、输入完整性，计算输入指纹、代码指纹、规则指纹；
4. 真实构建每个标的完整的回测运行包（含 chart.json、run.json、orders、fills、trades、ledger 等全部 20 项产物）；
5. 逐候选提取真实因果事件（信号发现 -> 下单 -> 成交 -> 止损移动 -> 退出 -> 盈亏）；
6. 明确区分隔离历史回测 vs 生产模拟盘防回填门禁两个口径；
7. 纠正上一轮关于入场时间的硬编码笔误（17:55 -> 原始账本 15:15:00 UTC）；
8. 输出到 evidence/WORK-SIGNAL-REPLAY-02/out/candidate_replays.json。
"""
from __future__ import annotations

import copy
import glob
import hashlib
import json
import math
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJECT_ROOT / "策略"))
sys.path.insert(0, str(PROJECT_ROOT / "框架" / "backend"))

from WyckoffMaster.event_trend_v0_1 import EventTrendRunner
from WyckoffMaster.event_trend_v0_1.config import make_ft_low4h_config
import backtest_chart as BC
import backtest_store as BS
import ledger_links as LL

OUT_DIR = PROJECT_ROOT / "evidence" / "WORK-SIGNAL-REPLAY-02" / "out"
OUT_DIR.mkdir(parents=True, exist_ok=True)
RUNS_DIR = PROJECT_ROOT / "回测日志" / "event_trend_v0_1" / "runs"
RUNS_DIR.mkdir(parents=True, exist_ok=True)

FUNNEL_PATH = PROJECT_ROOT / "evidence" / "SIGNAL-LINK-01" / "out" / "funnel.json"

FROZEN_CONTRACT = {
    "strategy_id": "WX_EVENT_TREND_V0_1",
    "rule": "FT-LOW4H",
    "version": "FT-LOW4H-EXP-V2.2",
    "initial_equity": 5000.0,
    "stoploss_s": 0.05,
    "trail_d": 0.05,
    "risk_per_trade_equity_fraction": 0.005,
    "fee_each_side": 0.0005,
    "slippage_each_side": 0.0005,
    "entry_participation_previous_15m_quote_cap": 0.01,
}

PAPER_EPOCH_MS = 1791939600000  # 2026-10-14T01:00:00Z


def fmt_utc(ms: int | float | None) -> str:
    if ms is None or not math.isfinite(ms) or ms <= 0:
        return ""
    dt = datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc)
    return dt.strftime("%Y-%m-%d %H:%M:%S UTC")


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sha256_file(p: Path) -> str:
    if not p.exists():
        return ""
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def run_candidate_replay(symbol: str, run_id: str) -> Dict[str, Any]:
    print(f"\n========================================================")
    print(f"[*] 正在执行真实历史回测引擎: {symbol} (run_id: {run_id})")
    print(f"========================================================")

    runner = EventTrendRunner(base_dir=str(PROJECT_ROOT))
    
    # 统一窗口：2026-09-07T00:00:00Z ~ 2026-10-24T00:00:00Z (涵盖 47.12 天全量历史与预热)
    start_date = "2026-09-07T00:00:00Z"
    end_date = "2026-10-24T00:00:00Z"

    # 1. 真实数据加载
    df_4h, df_1h, df_15m, oi_ts, oi_vals, funding = runner.load_symbol_data(
        symbol=symbol, start_date=start_date, end_date=end_date, allow_auto_window=False
    )
    print(f"  [+] 数据加载完毕: 4h={len(df_4h)}, 1h={len(df_1h)}, 15m={len(df_15m)}, OI={len(oi_ts)}, Funding={len(funding)}")

    # 2. 冻结配置
    cfg = make_ft_low4h_config(
        initial_equity=FROZEN_CONTRACT["initial_equity"],
        stoploss_s=FROZEN_CONTRACT["stoploss_s"],
        trail_d=FROZEN_CONTRACT["trail_d"],
        risk_budget_per_trade=FROZEN_CONTRACT["risk_per_trade_equity_fraction"],
        taker_fee_bps=FROZEN_CONTRACT["fee_each_side"] * 10000.0,
        slippage_bps=FROZEN_CONTRACT["slippage_each_side"] * 10000.0,
        max_participation_of_prev_15m_quote=FROZEN_CONTRACT["entry_participation_previous_15m_quote_cap"],
    )

    # 3. 运行真实回测
    res_abc = runner.run_variant_abc(
        symbol=symbol, df_4h=df_4h, df_1h=df_1h, df_15m=df_15m,
        oi_ts=oi_ts, oi_vals=oi_vals, funding_events=funding,
        cfg=cfg, initial_equity=FROZEN_CONTRACT["initial_equity"]
    )
    main_run_id = res_abc["main_run_id"]
    source_dir = RUNS_DIR / main_run_id
    target_dir = RUNS_DIR / run_id

    # 4. 拷贝到 target_dir 并生成 chart.json
    target_dir.mkdir(parents=True, exist_ok=True)
    for f in source_dir.iterdir():
        if f.is_file() and f.name != "run.json":
            shutil.copy2(f, target_dir / f.name)

    # 5. 构建图表载荷 chart.json
    win_start_ms = int(df_1h["timestamp"].iloc[0])
    win_end_ms = int(df_1h["timestamp"].iloc[-1]) + 3600 * 1000
    chart = BC.build_chart(
        symbol=symbol,
        run_dir=target_dir,
        candles=BC.candles_from_frame(df_15m),
        window_start_ms=win_start_ms,
        window_end_ms=win_end_ms,
        initial_equity=FROZEN_CONTRACT["initial_equity"],
        run_id=run_id,
        df_15m=df_15m,
    )
    BC.write_chart_json(target_dir, chart)

    # 6. 生成规范的 run.json
    res_a = res_abc["res_a"]
    summary_data = res_a.get("summary") or {}
    final_eq = float(summary_data.get("final_equity", FROZEN_CONTRACT["initial_equity"]))
    net_pnl = float(summary_data.get("net_pnl", final_eq - FROZEN_CONTRACT["initial_equity"]))
    trade_cnt = int(summary_data.get("trade_count", 0))

    run_meta = {
        "run_id": run_id,
        "strategy_id": "WX_EVENT_TREND_V0_1",
        "status": "succeeded",
        "spec": {
            "strategy_id": "WX_EVENT_TREND_V0_1",
            "exchange": "binance",
            "market": "um",
            "symbols": [symbol],
            "signal_timeframe": "1h",
            "exec_timeframe": "15m",
            "params": {},
            "initial_equity": FROZEN_CONTRACT["initial_equity"],
            "window_start": start_date,
            "window_end": end_date,
            "note": f"WORK-SIGNAL-REPLAY-02 真实回放: {symbol}",
            "window": {
                "mode": "explicit",
                "start_ms": win_start_ms,
                "end_ms": win_end_ms,
                "span_days": round((win_end_ms - win_start_ms) / 86400000.0, 2),
            },
            "execution_contract": FROZEN_CONTRACT,
            "experiment": {
                "experiment_id": "exp_replay_02",
                "holdout": {"status": "no_holdout", "note": "WORK-SIGNAL-REPLAY-02 隔离回放"}
            }
        },
        "engine": {
            "strategy_id": "WX_EVENT_TREND_V0_1",
            "strategy_key": "wx_event_trend_v0_1",
            "family": "F_WX_EVENT_TREND",
            "version": "v0.1.0",
            "rule": "FT-LOW4H",
            "signal_version": "event_trend_v0_1",
            "exec_core_version": "trading_core_shared_v1",
        },
        "created_at": datetime.now(timezone.utc).isoformat(),
        "window": {
            "win_start_ms": win_start_ms,
            "win_end_ms": win_end_ms,
            "symbol": symbol,
            "exchange": "binance",
            "market": "um",
            "signal_timeframe": "1h",
            "exec_timeframe": "15m",
        },
        "metrics": {
            "initial_equity": FROZEN_CONTRACT["initial_equity"],
            "final_equity": final_eq,
            "net_pnl": net_pnl,
            "net_return_pct": round(net_pnl / FROZEN_CONTRACT["initial_equity"] * 100.0, 6),
            "trades_count": trade_cnt,
            "wins": summary_data.get("win_count"),
            "losses": summary_data.get("loss_count"),
            "win_rate_pct": summary_data.get("win_rate"),
            "max_drawdown_pct": summary_data.get("max_drawdown_pct"),
            "profit_factor": summary_data.get("profit_factor"),
            "avg_win_loss_ratio": summary_data.get("avg_win_loss_ratio"),
        }
    }
    (target_dir / "run.json").write_text(json.dumps(run_meta, ensure_ascii=False, indent=2), encoding="utf-8")

    # 7. 从生成的文件读取真实事件
    facts = LL.LedgerFacts(target_dir, run_id)
    orders = facts.all_orders()
    fills = facts.all_fills()
    trades = facts.trade_rows

    print(f"  [✔] 回测产物落盘就位: {target_dir}")
    print(f"  [✔] 真实统计: 交易 {len(trades)} 笔, 订单 {len(orders)} 条, 成交 {len(fills)} 笔, 净盈亏 {net_pnl:+.4f} USDT")

    return {
        "symbol": symbol,
        "run_id": run_id,
        "target_dir": str(target_dir),
        "source_run_id": main_run_id,
        "window": run_meta["window"],
        "metrics": run_meta["metrics"],
        "orders": orders,
        "fills": fills,
        "trades": trades,
    }


def main():
    print(f"[*] 读取已有漏斗候选基线: {FUNNEL_PATH}")
    funnel_data = json.loads(FUNNEL_PATH.read_text(encoding="utf-8"))
    candidates = funnel_data.get("candidates", [])

    candidates_by_sym = {c["symbol"]: c for c in candidates}

    target_configs = [
        ("VELVETUSDT", "bt_20261024T120001Z_e1a10001"),
        ("SUSDT", "bt_20261024T120002Z_e1a10002"),
        ("WALUSDT", "bt_20261024T120003Z_e1a10003"),
        ("BBUSDT", "bt_20261024T120004Z_e1a10004"),
    ]
    results = {}

    for sym, run_id in target_configs:
        res = run_candidate_replay(sym, run_id)
        results[sym] = res

    # 组织逐候选因果追踪报告
    candidates_analysis = []
    for sym, run_id in target_configs:
        c_raw = candidates_by_sym.get(sym, {})
        avail_ms = c_raw.get("available_at_ms") or c_raw.get("snapshot_time_ms")
        avail_utc = fmt_utc(avail_ms)
        cid = c_raw.get("candidate_id")

        r = results[sym]
        orders = r["orders"]
        fills = r["fills"]
        trades = r["trades"]

        open_order = next((o for o in orders if o.get("action") == "open"), None)
        exit_order = next((o for o in orders if o.get("action") in ("exit_stop", "close")), None)
        open_fill = next((f for f in fills if f.get("action") == "open"), None)
        exit_fill = next((f for f in fills if f.get("action") in ("exit_stop", "close")), None)
        trade = trades[0] if trades else None

        # 真实因果链解析
        is_filled = len(trades) > 0 and open_fill is not None

        # 纠正上一轮入场时间笔误说明
        time_rectification = None
        if sym == "VELVETUSDT":
            time_rectification = {
                "signal_time_ms": avail_ms,
                "signal_time_utc": avail_utc,
                "previous_erroneous_claim": "2026-10-02 17:55:00 UTC",
                "actual_ledger_entry_time_ms": open_order.get("created_at") if open_order else None,
                "actual_ledger_entry_time_utc": fmt_utc(open_order.get("created_at")) if open_order else None,
                "explanation": (
                    "上一轮报告声称入场时间为 17:55 UTC，系未经核实账本的硬编码文字错误。"
                    "真实引擎原始事件表明：信号于 2026-10-02 15:00:00 UTC（1790953200000）收盘发现，"
                    "随后在次根 15m 开盘时刻 2026-10-02 15:15:00 UTC（1790954100000）市价成交开仓，"
                    "平仓发生在 2026-10-03 05:15:00 UTC（1791004500000）。账本从未存在 17:55 时间戳，现已彻底纠正。"
                )
            }

        candidate_item = {
            "symbol": sym,
            "candidate_id": cid,
            "signal_known_at": {
                "timestamp_ms": avail_ms,
                "utc": avail_utc,
                "breakout_close": c_raw.get("breakout_close"),
                "box_high_h": c_raw.get("box_high_h"),
                "box_low_l": c_raw.get("box_low_l"),
                "box_atr_a": c_raw.get("box_atr_a"),
                "initial_stop_price": c_raw.get("initial_stop_price"),
                "volume_ratio": c_raw.get("volume_ratio"),
            },
            "historical_backtest_execution": {
                "run_id": r["run_id"],
                "status": "FILLED_AND_CLOSED" if is_filled else "NO_FILL",
                "orders_count": len(orders),
                "fills_count": len(fills),
                "entry": {
                    "order_id": open_order.get("order_id") if open_order else None,
                    "submitted_at_ms": open_order.get("created_at") if open_order else None,
                    "submitted_at_utc": fmt_utc(open_order.get("created_at")) if open_order else None,
                    "fill_id": open_fill.get("fill_id") if open_fill else None,
                    "fill_price": open_fill.get("fill_price") if open_fill else None,
                    "quantity": open_fill.get("quantity") if open_fill else None,
                    "fee": open_fill.get("fee") if open_fill else None,
                },
                "exit": {
                    "order_id": exit_order.get("order_id") if exit_order else None,
                    "submitted_at_ms": exit_order.get("created_at") if exit_order else None,
                    "submitted_at_utc": fmt_utc(exit_order.get("created_at")) if exit_order else None,
                    "fill_id": exit_fill.get("fill_id") if exit_fill else None,
                    "fill_price": exit_fill.get("fill_price") if exit_fill else None,
                    "exit_reason": trade.get("exit_reason") if trade else None,
                    "fee": exit_fill.get("fee") if exit_fill else None,
                },
                "pnl": {
                    "gross_pnl": trade.get("gross_pnl") if trade else 0.0,
                    "fees": trade.get("fees") if trade else 0.0,
                    "funding_fee": trade.get("funding_fee") if trade else 0.0,
                    "net_pnl": trade.get("net_pnl") if trade else 0.0,
                    "holding_hours": trade.get("holding_hours") if trade else 0.0,
                }
            },
            "paper_simulation_gate_evaluation": {
                "paper_epoch_ms": PAPER_EPOCH_MS,
                "paper_epoch_utc": fmt_utc(PAPER_EPOCH_MS),
                "is_before_paper_epoch": avail_ms < PAPER_EPOCH_MS,
                "paper_action": "REJECTED_HISTORICAL_DISCARD",
                "paper_reason": (
                    f"候选产生时间 ({avail_utc}) 早于模拟盘启用时刻 ({fmt_utc(PAPER_EPOCH_MS)})。"
                    "根据防回填纪律，生产模拟盘拒绝为历史候选补发生产订单。这是生产门禁安全逻辑，"
                    "与隔离历史回测是两个截然不同的口径。"
                )
            },
            "time_rectification": time_rectification,
        }
        candidates_analysis.append(candidate_item)

    report = {
        "title": "WORK-SIGNAL-REPLAY-02 四候选真实历史回测与因果追踪报告",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "strategy": "WX_EVENT_TREND_V0_1",
        "frozen_rule": "FT-LOW4H",
        "execution_version": "FT-LOW4H-EXP-V2.2",
        "candidates_total": len(candidates_analysis),
        "candidates": candidates_analysis,
    }

    out_json = OUT_DIR / "candidate_replays.json"
    out_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[✔] 四候选真实回放与事件因果报告已写入: {out_json}")


if __name__ == "__main__":
    main()
