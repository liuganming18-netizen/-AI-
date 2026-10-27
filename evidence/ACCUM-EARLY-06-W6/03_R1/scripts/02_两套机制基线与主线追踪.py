# -*- coding: utf-8 -*-
"""ACCUM-EARLY-06-W6-R1 | 02 两套机制基线与主线追踪

核对重点：
1. 保持原既定研究范围（母集 48 标的 / 1178 事件，51 候选位于区间二）；
2. 不因原 7 事件零候选就扩大测试集，不新增参数选择，不要求用户额外签署文件；
3. 保留两套明确标记的机制对照（生效视图 5bps vs 策略包 3bps），严禁跨口径相加；
4. 保持“信号 → 模拟订单 → 复盘”主线逻辑追踪。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path("/Users/boos/Desktop/威科夫因子共振趋势")
OUT_DIR = ROOT / "evidence" / "ACCUM-EARLY-06-W6" / "03_R1" / "out"
OUT_DIR.mkdir(parents=True, exist_ok=True)

PATH_0608_OUT = ROOT / "evidence" / "ACCUM-EARLY-06-W4" / "02" / "out" / "01_资金费重算逐笔明细与对比.json"
PATH_0611_OUT = ROOT / "evidence" / "ACCUM-EARLY-06-W6" / "01" / "out" / "02_两套经济口径与51候选区间核对.json"
PATH_0606_MANIFEST = ROOT / "evidence" / "ACCUM-EARLY-06-06" / "out" / "funding_events_母集48_manifest.json"


def main():
    print("[02_两套机制基线与主线追踪] 开始运行...")

    # 读取 06-08 机制重算数据
    with open(PATH_0608_OUT, "r", encoding="utf-8") as f:
        recalc_data = json.load(f)

    # 读取 06-11 边界数据
    with open(PATH_0611_OUT, "r", encoding="utf-8") as f:
        base_data = json.load(f)

    # 读取 06-06 适配器清单数据
    with open(PATH_0606_MANIFEST, "r", encoding="utf-8") as f:
        manifest_data = json.load(f)

    impact = recalc_data.get("independent_calibers_impact", {})
    imp_5bps = impact.get("生效视图_5bps", {})
    imp_3bps = impact.get("策略包_3bps", {})

    fin_5bps = imp_5bps.get("financial_impact", {})
    fin_3bps = imp_3bps.get("financial_impact", {})

    # 信号 → 模拟订单 → 复盘 主线串联
    pipeline_tracking = {
        "step_1_signal_generation": {
            "module": "策略状态机 (state_machine.py)",
            "mother_set_events": 1178,
            "mother_set_symbols": 48,
            "window": "2026-06-27T08:00:00Z ~ 2026-10-26T09:00:00Z",
            "historical_candidates_produced": 46,
            "events_retest_unbroken": 174,
            "w5_retest_original_7_events_result": "产生候选 0 笔（案例 1~6 跌破箱体下沿失效，案例 7 因前序占位未生成）。",
            "discipline_statement": "严禁因原 7 事件零候选就擅自扩大测试集；零候选仅反映该组输入在当前规则下的客观工程表现，不构成扩大研究范围的授权。"
        },
        "step_2_simulated_order_execution": {
            "module": "回放撮合与合约约束 (replay_calc.py / contract_limits.csv)",
            "timing_rule": "12:00 信号确认，最早于 12:15:00 之后第一根 15m 开盘执行，禁止回填 12:00",
            "stop_loss_rule": "初始止损 0.95；入场后新收盘 4h 最低价 × 0.95 只上移不下移；无 2R 激活限制，无 10 天强平",
            "tested_trades": 102,
            "filled_trades": 101,
            "rejected_trades": 1,
            "rejected_detail": {
                "symbol": "CARVUSDT",
                "caliber": "策略包_3bps",
                "notional_compressed": 4.4436,
                "binance_min_notional": 5.0,
                "action": "低于交易所最小名义额合规拒单"
            }
        },
        "step_3_review_and_accounting": {
            "module": "财务结算与冷热合并资金费 (funding_events_adapter.py)",
            "merged_funding_events": manifest_data.get("counts", {}).get("events", 31811),
            "cold_archive_supplemented_events": manifest_data.get("counts", {}).get("observed_cold_only", 3755),
            "financial_impact_reconciliation": {
                "生效视图_5bps": {
                    "total_realized_net_old": fin_5bps.get("total_realized_net_old"),
                    "total_realized_net_new": fin_5bps.get("total_realized_net_new"),
                    "delta_net": fin_5bps.get("delta_realized_net"),
                    "total_funding_cost_old": fin_5bps.get("total_funding_cost_old"),
                    "total_funding_cost_new": fin_5bps.get("total_funding_cost_new"),
                    "delta_funding": fin_5bps.get("delta_funding_cost"),
                    "identity_verified": fin_5bps.get("net_change_perfectly_explains_funding")
                },
                "策略包_3bps": {
                    "total_realized_net_old": fin_3bps.get("total_realized_net_old"),
                    "total_realized_net_new": fin_3bps.get("total_realized_net_new"),
                    "delta_net": fin_3bps.get("delta_realized_net"),
                    "total_funding_cost_old": fin_3bps.get("total_funding_cost_old"),
                    "total_funding_cost_new": fin_3bps.get("total_funding_cost_new"),
                    "delta_funding": fin_3bps.get("delta_funding_cost"),
                    "identity_verified": fin_3bps.get("net_change_perfectly_explains_funding")
                }
            },
            "eliminated_real_gaps_count": 6,
            "portfolio_separation_reminder": "单事件独立回放缺少并发与排队约束，同时在仓峰值达 9 笔，伪回撤极差 478.38 USDT。组合账户表现维持【未验证】定性。"
        }
    }

    res = {
        "task": "ACCUM-EARLY-06-W6-R1",
        "step": "02_两套机制基线与主线追踪",
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "pipeline_tracking": pipeline_tracking,
        "discipline_statement": "保持信号→模拟订单→复盘主线，不新建第三套混搭参数，不运行未批准新策略正式对照，不要求用户额外签署文件。"
    }

    out_file = OUT_DIR / "02_机制基线与资金费穿透台账.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=2)

    print(f"[02_两套机制基线与主线追踪] 完成！产物保存至: {out_file}")


if __name__ == "__main__":
    main()
