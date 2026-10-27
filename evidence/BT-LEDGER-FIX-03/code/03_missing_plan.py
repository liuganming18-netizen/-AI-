# -*- coding: utf-8 -*-
"""BT-LEDGER-FIX-03｜四、缺失事件的补证方案（只出方案，不擅自重跑）。

原则
────
1. 不宣称「缺失已补齐」。缺失项保留 `部分完成/阻塞`。
2. 不编造交易所 ID，不把交易汇总拆两行冒充委托/成交。
3. 先给**最小补证**：1 组试点 + 等价性判据；通过后再谈 45 组。
4. 明确指出：补证会不会改变运行身份（strategy_sha256 / config_sha256）。

产出
────
out/08_缺失字段与补证方案.csv   逐字段 × 影响面 × 补证路径 × 是否需重跑
out/09_补证方案.json            方案 A/B/C 对比 + 最小补证步骤 + 成本 + 风险 + 判据
"""
from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
cm = importlib.import_module("00_common")

OUT = cm.OUT

# 字段 × 影响面 × 补证路径
MISSING = [
    # field, affected_runs, affected_rows, layer, reason, path, need_rerun, identity_impact, blocked
    ("order_id / order.id（引擎真订单号）", 45, 6790, "order",
     "Trade.to_json(minified=True) 裁掉；BT_DATA_COLUMNS 白名单二次裁剪",
     "进程内全量导出（方案 B）或策略 order_filled 钩子（方案 A）",
     "是", "A 改策略文件⇒strategy_sha256 变；B 不改策略⇒身份不变", "阻塞（本单不重跑）"),
    ("status", 45, 6790, "order",
     "同上；回测建单置 'open'，close_bt_order 置 'closed'",
     "同上（方案 B 可拿到真实值，本单已给 derived='closed'）",
     "是（原始值）/ 否（derived 已可用）", "同上", "部分完成"),
    ("filled / remaining", 45, 6790, "order",
     "同上；close_bt_order 置 filled=amount, remaining=0",
     "同上（本单已给 derived）", "是（原始值）/ 否（derived 已可用）", "同上", "部分完成"),
    ("order_type", 45, 6790, "order",
     "同上；取 strategy.order_types，本批策略未覆盖⇒默认 limit",
     "同上（本单已给 derived='limit'，依据 interface.py:95-101）", "否（derived 足够）", "同上", "部分完成"),
    ("price / average（委托价）", 45, 6790, "order",
     "同上；回测建单即置 propose_rate，成交后不改",
     "同上（本单已给 derived == safe_price）", "否（derived 足够）", "同上", "部分完成"),
    ("order_date / order_timestamp（委托创建时刻）", 45, 6790, "order",
     "同上；回测建单与成交在同一根 K 线、同一 current_time",
     "同上（本单已给 derived == fill_ts）", "否（derived 足够）", "同上", "部分完成"),
    ("ft_fee_base", 45, 6790, "order", "同上；U 本位合约手续费以计价币计⇒None",
     "同上（本单已给 derived=None）", "否（derived 足够）", "同上", "部分完成"),
    ("funding_fee（逐单归属）", 45, 6790, "order",
     "close_bt_order 把 funding_fee_running 整体结转并清零，导出只剩 trade.funding_fees 总额",
     "只能靠方案 B 抓 Order.funding_fee；总额已有 raw 值", "是", "同上", "阻塞（逐单拆分）"),
    ("trade_id（引擎 trade.id）", 45, 3395, "trade",
     "Trade.to_json 的 trade_id 被 BT_DATA_COLUMNS 裁掉；引擎内部 trade_id_counter 真实存在",
     "方案 B 抓 LocalTrade.id；当前 1..n 为秩派生，同刻并列时含未观测前提",
     "是", "同上", "阻塞（当前为 derived_unverified）"),
    ("amount_requested", 45, 3395, "trade", "导出裁剪；回测中通常 == amount，但无值可证",
     "方案 B", "是", "同上", "阻塞（低价值）"),
    ("exit_order_status", 45, 3395, "trade", "导出裁剪", "方案 B（本单已给 derived='closed'）",
     "否（derived 足够）", "同上", "部分完成"),
    ("fill_id / 交易所订单号 / 部分成交明细", 45, 6790, "order",
     "回测无交易所回报，环境本身不存在该概念",
     "**无路径**。只有 dry-run/实盘才有；不得自造", "永不", "—", "不可补（结构性）"),
]

PLAN = {
    "constraint": {
        "no_rerun_in_this_ticket": True,
        "reason": "工单要求「不重复运行已闭合实验」；本单只出方案与离线验证",
        "identity_rule": "运行身份只认 strategy_sha256 + config_sha256（run.json.hashes 已登记）",
    },
    "options": [
        {
            "id": "A",
            "name": "策略侧 order_filled 钩子落盘",
            "how": "在隔离策略副本里实现 order_filled(pair, trade, order, current_time)，"
                   "把 order.to_json(entry_side, minified=False) 逐条写盘",
            "identity_impact": "**改策略文件 ⇒ strategy_sha256 变化 ⇒ 45 个 run 身份全部改变**",
            "verdict": "不推荐（破坏现有运行身份，与工单第 5 条冲突）",
        },
        {
            "id": "B",
            "name": "进程内全量导出（不改策略、不改引擎包）",
            "how": "在**同一个回测进程**内、`Backtesting.backtest()` 返回后立即遍历 "
                   "`LocalTrade.bt_trades`，对每个 order 调 `to_json(entry_side, minified=False)`，"
                   "另存 `orders_full.jsonl` / `trades_full.jsonl`；再调用原有导出落盘",
            "identity_impact": "策略文件与 config 均不改 ⇒ strategy_sha256 / config_sha256 不变",
            "verdict": "**推荐**（唯一既能拿到真 ID、又不改运行身份的路子）",
            "risk": "① 需自己复现 CLI 的调用顺序（Backtesting.start()），配置漂移会改结果；"
                    "② `LocalTrade.bt_trades` 在进程结束后即销毁，必须同进程内导出；"
                    "③ 仍是「重跑」，必须用等价性判据证明计算未变",
        },
        {
            "id": "C",
            "name": "改 freqtrade 包内 minified 默认值",
            "how": "把 backtesting.py:1810 的 minified 改成 False，或扩 BT_DATA_COLUMNS",
            "identity_impact": "改引擎包 ⇒ 引擎版本语义变化，全部历史结果失去可比性",
            "verdict": "禁止（等于改公共执行引擎）",
        },
    ],
    "minimal_remediation": {
        "step1": {
            "what": "单组试点：只跑 `Wyckoff_C1_S05_D05_Vs04_Z02`（21 笔 / 42 单，耗时 277.3s）",
            "why": "最小可证伪单元：笔数少、含 trailing_stop_loss 与 stop_loss 两种退出",
            "outputs": ["orders_full.jsonl（21 单 × 21 字段）", "trades_full.jsonl（含 trade.id）"],
        },
        "step2_equivalence_gate": {
            "what": "等价性判据（必须全过才允许扩到 45 组）",
            "criteria": [
                "笔数、每笔订单数（2）与旧结果完全一致",
                "7 个原始字段逐条 **bit 级相同**（amount / safe_price / ft_order_side / "
                "order_filled_timestamp / ft_is_entry / ft_order_tag / cost）",
                "逐笔 profit_abs 与旧结果差 ≤ 1e-8（引擎 round 上界）",
                "Σprofit_abs 与旧结果差 ≤ 1e-9（相对）",
                "新拿到的 order_id 是 1..N 的连续整数串（与 order_id_counter 语义一致）",
                "trade.id 为 1..N 且无空洞（若出现空洞 ⇒ 说明存在被丢弃交易，旧派生 trade_id 必须重算）",
            ],
            "fail_action": "任一条不过 ⇒ 停，不改 45 组；把差异写成新的阻塞项",
        },
        "step3": {
            "what": "试点通过后，45 组按同样方式重跑并登记为**新批次**（新 batch_id，"
                    "lineage 指向 runs_work_dynamic_bt_01）",
            "note": "旧批次 45 组证据原样保留，不覆盖；新旧用 strategy_sha256 相同 + 新导出文件名区分",
        },
        "cost": {},
    },
    "still_impossible_after_rerun": [
        "fill_id（回测无交易所回报）",
        "一单多次部分成交的逐次明细（回测 1 单 = 1 次全额成交）",
        "交易所侧真实委托/成交回报时间（回测时间来自 K 线，不是交易所回执）",
    ],
    "honest_status": {
        "order_id": "阻塞（需重跑，本单未做）",
        "status/filled/remaining/order_type/price/order_date": "部分完成（有引擎源码可证的 derived 值，无原始记录）",
        "trade_id": "部分完成（derived_unverified，同刻并列含未观测前提）",
        "funding_fee 逐单归属": "阻塞（总额有，拆分不可证）",
        "fill_id": "不可补（结构性）",
    },
}


def main() -> None:
    idx = cm.run_index()
    df = pd.DataFrame(MISSING, columns=[
        "field", "affected_runs", "affected_rows", "layer", "reason",
        "remediation_path", "need_rerun", "identity_impact", "status"])
    df.to_csv(OUT / "08_缺失字段与补证方案.csv", index=False, encoding="utf-8-sig")

    # 成本：用 run 索引里的实测 elapsed_sec，不估
    total_s = float(idx["elapsed_sec"].sum())
    pilot = idx.loc[idx["strategy"] == "Wyckoff_C1_S05_D05_Vs04_Z02", "elapsed_sec"]
    pilot_s = float(pilot.iloc[0]) if len(pilot) else None
    PLAN["minimal_remediation"]["cost"] = {
        "pilot_seconds": pilot_s,
        "all45_seconds_measured": round(total_s, 1),
        "all45_hours_measured": round(total_s / 3600.0, 2),
        "basis": "BT-01 out/02_运行索引.csv 的 elapsed_sec 实测求和（不是估算）",
        "extra_export_overhead": "预计 < 2%（只在结束时多写一个 jsonl）",
    }
    PLAN["scope"] = {"runs": int(len(idx)),
                     "orders_affected": 6790, "trades_affected": 3395}
    (OUT / "09_补证方案.json").write_text(json.dumps(PLAN, ensure_ascii=False, indent=2),
                                     encoding="utf-8")
    print(df[["field", "need_rerun", "status"]].to_string(index=False))
    print("pilot_s:", pilot_s, "all45_h:", PLAN["minimal_remediation"]["cost"]["all45_hours_measured"])


if __name__ == "__main__":
    main()
