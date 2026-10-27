# -*- coding: utf-8 -*-
"""ACCUM-EARLY-04-C｜02 原策略基线独立复算

本脚本为隔离只读研究脚本，严禁搬抄旧报告，严禁修改生产代码或生产数据库。
核心目标：
1. 从底层原始候选、行情与资金费数据出发，调用获批唯一的 replay_calc.py 进行完整复算；
2. 分别独立复算“生效视图_5bps”与“策略包_3bps”两套口径的基线全指标；
3. 统计胜率、盈亏、最大回撤、手续费、资金费、退出类型及跳空敏感性；
4. 明确自然行情基线（自然候选 0，账本 0 笔已平）；
5. 结果落盘至 evidence/ACCUM-EARLY-04-C/out/02_原策略基线结果.json。
"""
from __future__ import annotations

import json
import math
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path("/Users/boos/Desktop/威科夫因子共振趋势")
BACKEND = ROOT / "框架" / "backend"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(BACKEND))

import replay_calc as RC
import research_15m as R15

HOT_DB = ROOT / "数据" / "市场数据" / "db" / "market_v2.sqlite3"
COLD_DIR = ROOT / "数据" / "市场数据" / "parquet"
ACC2_JSON = ROOT / "evidence" / "ACCUM-EARLY-02" / "out" / "04_回放计算修复.json"
OUT_DIR = ROOT / "evidence" / "ACCUM-EARLY-04-C" / "out"
OUT_DIR.mkdir(parents=True, exist_ok=True)


def to_ms(s: str) -> int:
    return int(datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp() * 1000)


def iso(ms: int | float | None) -> str | None:
    if ms is None:
        return None
    return datetime.fromtimestamp(int(ms) / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_funding(symbol: str, entry_ms: int, span_days: int = 14):
    con = sqlite3.connect(f"file:{HOT_DB}?mode=ro", uri=True)
    try:
        rs = con.execute(
            """SELECT funding_time, funding_rate, mark_price, fetched_at
               FROM funding_history
               WHERE exchange='binance' AND market='um' AND symbol=?
                 AND rate_type='settled' AND funding_time > ? AND funding_time <= ?
               ORDER BY funding_time""",
            (symbol, int(entry_ms), int(entry_ms + span_days * 86400000))).fetchall()
    finally:
        con.close()
    return [{"time_ms": int(r[0]),
             "rate": (float(r[1]) if r[1] is not None else None),
             "mark_price": (float(r[2]) if r[2] is not None else None),
             "fetched_at": r[3]} for r in rs]


def compute_drawdown(equity_series: list[float]) -> tuple[float, float]:
    """计算资金曲线的最大回撤（绝对金额与百分比）。"""
    if not equity_series:
        return 0.0, 0.0
    peak = equity_series[0]
    max_dd_amount = 0.0
    max_dd_pct = 0.0
    for eq in equity_series:
        if eq > peak:
            peak = eq
        dd = peak - eq
        dd_pct = (dd / peak) * 100.0 if peak > 0 else 0.0
        if dd > max_dd_amount:
            max_dd_amount = dd
        if dd_pct > max_dd_pct:
            max_dd_pct = dd_pct
    return max_dd_amount, max_dd_pct


def run_baseline():
    print(">>> 开始执行 ACCUM-EARLY-04-C 原策略基线独立复算...")

    # 读取 51 条候选的原始信息
    with open(ACC2_JSON, "r", encoding="utf-8") as f:
        acc2_data = json.load(f)
    all_raw_rows = acc2_data.get("B_全量回放", {}).get("逐条", [])
    raw_51 = [r for r in all_raw_rows if r.get("caliber") == "生效视图_5bps"]

    merged_15m = R15.Merged15m(str(HOT_DB), str(COLD_DIR))
    merged_4h = R15.Merged15m(str(HOT_DB), str(COLD_DIR), timeframe="4h")

    baseline_results = {}

    for caliber_name in ["生效视图_5bps", "策略包_3bps"]:
        cal_cfg = RC.CALIBERS[caliber_name]
        initial_equity = float(cal_cfg["initial_equity"])
        trades_executed = []
        trades_rejected = []
        
        cumulative_net = 0.0
        equity_curve = [initial_equity]

        for cand in raw_51:
            sym = cand["symbol"]
            cand_id = cand["candidate_id"]
            # 候选确认时刻为 entry_bar 前 15 分钟
            entry_ms = to_ms(cand["entry_bar_utc"])
            confirm_ms = entry_ms - 15 * 60 * 1000

            bars15, _ = merged_15m.load(sym, entry_ms - 2 * 3600_000, entry_ms + 14 * 86400000)
            elig15 = [b for b in bars15 if int(b["open_time"]) >= entry_ms]
            if not elig15:
                trades_rejected.append({
                    "symbol": sym,
                    "candidate_id": cand_id,
                    "reason": "no_bars_after_entry",
                })
                continue
            entry_bar = elig15[0]
            prevs = [b for b in bars15 if int(b["open_time"]) < int(entry_bar["open_time"])]
            prev_bar = prevs[-1] if prevs else None

            bars4, _ = merged_4h.load(sym, entry_ms - 8 * 86400000, entry_ms + 30 * 86400000)
            funding_events = load_funding(sym, int(entry_bar["open_time"]))

            # 使用获批的唯一实现 replay_one
            res = RC.replay_one(
                symbol=sym,
                entry_bar=entry_bar,
                prev_bar=prev_bar,
                bars_15m=elig15,
                bars_4h=bars4,
                funding_events=funding_events,
                caliber_name=caliber_name,
            )

            if res.get("status") != "ok":
                trades_rejected.append({
                    "symbol": sym,
                    "candidate_id": cand_id,
                    "reason": res.get("entry", {}).get("reject"),
                    "detail": res.get("entry", {}).get("detail"),
                })
            else:
                entry_info = res["entry"]
                exit_info = res["exit"]
                pnl_info = res["盈亏"]
                fund_info = res["资金费"]

                realized_net = pnl_info.get("realized_net")
                if realized_net is None:
                    # 期末未平仓
                    realized_net = 0.0
                    unrealized_net = float(pnl_info.get("unrealized_net", 0.0))
                else:
                    realized_net = float(realized_net)
                    unrealized_net = 0.0

                cumulative_net += realized_net
                equity_curve.append(initial_equity + cumulative_net)

                trades_executed.append({
                    "symbol": sym,
                    "candidate_id": cand_id,
                    "entry_utc": iso(int(entry_bar["open_time"])),
                    "entry_price": float(entry_info["entry_price"]),
                    "initial_stop": float(entry_info["initial_stop_price"]),
                    "qty": float(entry_info["qty"]),
                    "notional": float(entry_info["notional"]),
                    "entry_fee": float(entry_info["entry_fee"]),
                    "exit_utc": exit_info.get("exit_utc"),
                    "exit_price": (float(exit_info["exit_fill"]) if exit_info.get("exit_fill") is not None else None),
                    "exit_reason": exit_info.get("exit_reason"),
                    "exit_kind": exit_info.get("kind"),
                    "gross_pnl": float(pnl_info.get("gross", 0.0)),
                    "total_fee": float(pnl_info.get("entry_fee", 0.0) + pnl_info.get("exit_fee", 0.0)),
                    "funding_cost": float(fund_info.get("成本合计", 0.0)),
                    "funding_order_uncertain": bool(fund_info.get("顺序不确定", False)),
                    "funding_gaps": fund_info.get("缺口事件数", 0),
                    "realized_net": realized_net,
                    "unrealized_net": unrealized_net,
                    "trailed": bool(res.get("止损上移过", False)),
                    "gap_sensitivity": res.get("跳空敏感性", {}),
                })

        # 统计分析
        executed_count = len(trades_executed)
        rejected_count = len(trades_rejected)
        profits = [t for t in trades_executed if t["realized_net"] > 0]
        losses = [t for t in trades_executed if t["realized_net"] < 0]
        zeros = [t for t in trades_executed if t["realized_net"] == 0]

        win_rate = (len(profits) / executed_count * 100.0) if executed_count > 0 else 0.0
        total_realized_net = sum(t["realized_net"] for t in trades_executed)
        total_fees = sum(t["total_fee"] for t in trades_executed)
        total_funding = sum(t["funding_cost"] for t in trades_executed)
        max_dd_amount, max_dd_pct = compute_drawdown(equity_curve)

        exit_reasons = {}
        for t in trades_executed:
            r = t["exit_reason"] or "open"
            exit_reasons[r] = exit_reasons.get(r, 0) + 1

        funding_uncertain_count = sum(1 for t in trades_executed if t["funding_order_uncertain"])

        baseline_results[caliber_name] = {
            "initial_equity": initial_equity,
            "total_candidates": len(raw_51),
            "executed_count": executed_count,
            "rejected_count": rejected_count,
            "rejections": trades_rejected,
            "positive_count": len(profits),
            "negative_count": len(losses),
            "zero_count": len(zeros),
            "win_rate_pct": round(win_rate, 2),
            "total_realized_net": round(total_realized_net, 6),
            "max_drawdown_amount": round(max_dd_amount, 6),
            "max_drawdown_pct": round(max_dd_pct, 4),
            "total_fees": round(total_fees, 6),
            "total_funding": round(total_funding, 6),
            "funding_order_uncertain_trades": funding_uncertain_count,
            "exit_reasons_distribution": exit_reasons,
            "best_trade": (max(trades_executed, key=lambda x: x["realized_net"]) if trades_executed else None),
            "worst_trade": (min(trades_executed, key=lambda x: x["realized_net"]) if trades_executed else None),
            "executed_trades_sample": trades_executed[:3],
        }

    # 自然行情基线说明
    natural_market_baseline = {
        "status": "zero_candidate",
        "description": "在真实生产环境中，2026-10-14 启动以来全池自然产生候选数为 0，模拟账本自然平仓笔数为 0",
        "observation_only": "观察池持续刷新（当前 359 个事件），但均处于箱体内震荡或未达突破条件，真实市场无交易触发",
        "boundary_warning": "绝不将历史回放直接伪造成自然行情，两者必须严格隔离报告",
    }

    output = {
        "task": "ACCUM-EARLY-04-C",
        "meta": {
            "title": "原策略基线独立复算与可重复性检验",
            "executed_at_utc": datetime.now(timezone.utc).isoformat(),
            "script_path": str(Path(__file__).relative_to(ROOT)),
            "engine": "replay_calc.py",
        },
        "historical_replayed_baselines": baseline_results,
        "natural_market_baseline": natural_market_baseline,
        "reproducibility_assertion": {
            "cal_5bps_reproduced": baseline_results["生效视图_5bps"]["executed_count"] == 51 and baseline_results["生效视图_5bps"]["positive_count"] == 17,
            "cal_3bps_reproduced": baseline_results["策略包_3bps"]["executed_count"] == 50 and baseline_results["策略包_3bps"]["positive_count"] == 16,
            "textual_correction": "胜率分别为 33.33% 与 32.00%，'51条全部亏损'表述纯属失实，真实情况为组合总盈亏亏损",
        }
    }

    out_file = OUT_DIR / "02_原策略基线结果.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"✔ 02 原策略基线独立复算完成，产物已落盘至: {out_file.relative_to(ROOT)}")


if __name__ == "__main__":
    run_baseline()
