# -*- coding: utf-8 -*-
"""ACCUM-EARLY-06-W6-01 | 02 两套经济口径与51候选区间核对

核对重点：
1. 逐事件盈亏与组合账户表现分开；并列两套经济口径（生效视图 5bps vs 策略包 3bps），严禁合并相加；
2. 核对原 51 候选所处区间：全部 51 条候选均位于「区间二：2026-07-01 ~ 2026-10-12」（已泄露验证集），在 04-A 阶段就已被查看并用于研究，绝不能视作独立样本外；
3. 核对组合账户表现的缺失前提：持仓时间重叠（峰值 9 笔并发），伪回撤随顺序变动（极差 478.38 USDT），组合回撤定性为“未验证”。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path("/Users/boos/Desktop/威科夫因子共振趋势")
OUT_DIR = ROOT / "evidence" / "ACCUM-EARLY-06-W6" / "01" / "out"
OUT_DIR.mkdir(parents=True, exist_ok=True)

PATH_05C_BASELINES = ROOT / "evidence" / "ACCUM-EARLY-05-C" / "out" / "02_逐事件基线.json"
PATH_05C_SEPARATION = ROOT / "evidence" / "ACCUM-EARLY-05-C" / "out" / "03_逐事件与组合口径分离.json"


def main():
    print("[02_两套经济口径与51候选区间核对] 开始运行...")

    # 1. 读取 05-C 基线数据
    with open(PATH_05C_BASELINES, "r", encoding="utf-8") as f:
        b_data = json.load(f)

    # 2. 读取 05-C 组合口径分离数据
    with open(PATH_05C_SEPARATION, "r", encoding="utf-8") as f:
        s_data = json.load(f)

    calibers = b_data.get("calibers", {})
    c_5bps = calibers.get("生效视图_5bps", {})
    c_3bps = calibers.get("策略包_3bps", {})

    # 核对 51 候选所处区间
    interval_def = b_data.get("interval_definition", [])
    per_event_5bps = c_5bps.get("per_event", [])
    assert len(per_event_5bps) == 51, f"预期 51 笔候选，实际 {len(per_event_5bps)}"

    # 逐笔提取 51 候选时间与区间
    candidates_detail = []
    earliest_entry = None
    latest_entry = None
    for t in per_event_5bps:
        e_utc = t.get("entry_utc")
        if earliest_entry is None or e_utc < earliest_entry:
            earliest_entry = e_utc
        if latest_entry is None or e_utc > latest_entry:
            latest_entry = e_utc
        candidates_detail.append({
            "candidate_id": t.get("candidate_id"),
            "symbol": t.get("symbol"),
            "entry_utc": e_utc,
            "exit_utc": t.get("exit_utc"),
            "interval_assigned": t.get("interval"),
            "net_5bps": t.get("realized_net"),
        })

    # 确认全量 51 候选均在区间二
    cands_in_int2 = [c for c in candidates_detail if c["interval_assigned"] == "区间二_已泄露验证集"]
    assert len(cands_in_int2) == 51, f"预期 51 笔候选全在区间二，实际 {len(cands_in_int2)}"

    # 提取两套口径的独立统计
    counts_5bps = c_5bps.get("counts", {})
    counts_3bps = c_3bps.get("counts", {})

    dist_5bps = s_data.get("calibers", {}).get("生效视图_5bps", {}).get("per_event_distribution", {})
    dist_3bps = s_data.get("calibers", {}).get("策略包_3bps", {}).get("per_event_distribution", {})

    # 提取伪回撤顺序敏感性与重叠并发
    sep_5bps = s_data.get("calibers", {}).get("生效视图_5bps", {})
    order_sens = sep_5bps.get("order_sensitivity", {})
    concurrency = sep_5bps.get("concurrency", {})

    # 组装审核产物
    res = {
        "task": "ACCUM-EARLY-06-W6-01",
        "step": "02_两套经济口径与51候选区间核对",
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "two_economic_calibers": {
            "生效视图_5bps": {
                "initial_equity": 5000.0,
                "risk_per_trade_pct": 0.50,
                "slippage_bps": 5,
                "fee_bps": 5,
                "volume_participation_limit_pct": 1.0,
                "source": "10_研究参数生效视图_v2_2_user5000_shared.json",
                "candidates_count": counts_5bps.get("candidates"),
                "executed_count": counts_5bps.get("executed"),
                "rejected_count": counts_5bps.get("rejected"),
                "winning_trades": counts_5bps.get("winners"),
                "losing_trades": counts_5bps.get("losers"),
                "win_rate_pct": counts_5bps.get("win_rate_of_closed_pct"),
                "total_realized_net_usdt": dist_5bps.get("sum"),
                "mean_realized_net_usdt": dist_5bps.get("mean"),
                "median_realized_net_usdt": dist_5bps.get("median"),
                "min_realized_net_usdt": dist_5bps.get("min"),
                "max_realized_net_usdt": dist_5bps.get("max"),
            },
            "策略包_3bps": {
                "initial_equity": 2000.0,
                "risk_per_trade_pct": 0.50,
                "slippage_bps": 3,
                "fee_bps": 5,
                "volume_participation_limit_pct": 0.1,
                "source": "config.py · make_ft_low4h_config()",
                "candidates_count": counts_3bps.get("candidates"),
                "executed_count": counts_3bps.get("executed"),
                "rejected_count": counts_3bps.get("rejected"),
                "rejected_symbol": "CARVUSDT",
                "rejection_reason": "参与率 0.1% 强约束将名义额压缩至 4.4436 USDT，低于币安 5.0 USDT 最小门槛拒单",
                "winning_trades": counts_3bps.get("winners"),
                "losing_trades": counts_3bps.get("losers"),
                "win_rate_pct": counts_3bps.get("win_rate_of_closed_pct"),
                "total_realized_net_usdt": dist_3bps.get("sum"),
                "mean_realized_net_usdt": dist_3bps.get("mean"),
                "median_realized_net_usdt": dist_3bps.get("median"),
                "min_realized_net_usdt": dist_3bps.get("min"),
                "max_realized_net_usdt": dist_3bps.get("max"),
            },
            "caliber_isolation_rule": "两套口径并列报告，严禁跨口径合并相加（求和 -314.3706 USDT 仅为底层对账校验码，不具经济学意义）。未批准第三套混搭口径前，不得擅自合成参数。"
        },
        "candidate_interval_audit": {
            "definition": interval_def,
            "distribution_summary": {
                "区间一_研究集 (2025-01-01 ~ 2026-07-01)": 0,
                "区间二_已泄露验证集 (2026-07-01 ~ 2026-10-12)": 51,
                "区间三_真样本外 (2026-10-12 之后)": 0
            },
            "date_range": {
                "earliest_entry_utc": earliest_entry,
                "latest_entry_utc": latest_entry
            },
            "leakage_verdict": "全部 51 条历史候选 100% 落在「区间二：2026-07-01 ~ 2026-10-12」。该区间已在 04-A 阶段被完整计算、公开并用于调优，属于已查看/已泄露数据，严禁视作样本外验证。"
        },
        "event_level_vs_portfolio_level": {
            "max_concurrent_positions": concurrency.get("同时在仓峰值"),
            "max_concurrent_time_utc": concurrency.get("峰值出现时刻_utc"),
            "overlapping_pairs_count": concurrency.get("存在重叠的笔对"),
            "total_possible_pairs": concurrency.get("可能笔对总数"),
            "overlapping_pairs_pct": concurrency.get("重叠笔对占比_pct"),
            "total_overlap_hours": concurrency.get("重叠总时长_小时"),
            "pseudo_drawdown_sensitivity": {
                "entry_order_drawdown_usdt": order_sens.get("同一批数字、不同排列", {}).get("按入场时间升序（= 上游候选顺序）", [0])[0],
                "exit_order_drawdown_usdt": order_sens.get("同一批数字、不同排列", {}).get("按退出时间升序", [0])[0],
                "pnl_descending_drawdown_usdt": order_sens.get("同一批数字、不同排列", {}).get("按单笔净额从大到小", [0])[0],
                "random_shuffle_p50_drawdown_usdt": order_sens.get("随机置换", {}).get("amount_p50"),
                "order_range_usdt": order_sens.get("极差"),
            },
            "portfolio_verdict": "单事件独立回放缺少并发与资金调度约束；伪回撤随排列极差高达 478.38 USDT。组合账户回撤与收益率均定性为【未验证】。"
        }
    }

    out_file = OUT_DIR / "02_两套经济口径与51候选区间核对.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=2)

    print(f"[02_两套经济口径与51候选区间核对] 完成！产物保存至: {out_file}")


if __name__ == "__main__":
    main()
