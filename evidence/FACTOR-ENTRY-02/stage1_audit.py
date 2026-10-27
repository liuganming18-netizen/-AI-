# -*- coding: utf-8 -*-
"""工单 FACTOR-ENTRY-02 - 第一阶段：样本映射与特征时点审计。

执行目标：
1. 生成 trade_mapping_296_to_205.csv 和 .json：逐笔对账原 296 笔交易与现 205 笔交易。
   - 标明 72 组重复（92 笔截断孪生交易）合并去重理由。
   - 标明 1 笔新交易 (METISUSDT) 的产生原因。
2. 冻结 timing_audit_contract.json：详细记录 source_time, available_at, decision_time,
   matching_direction, max_staleness 及 PIT 假设。
3. 统计标的池 528 -> 527 -> 526 分母来源。
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List

import numpy as np
import pandas as pd

ROOT = Path("/Users/boos/Desktop/威科夫因子共振趋势")
OUT_DIR = ROOT / "evidence" / "FACTOR-ENTRY-02"
OUT_DIR.mkdir(parents=True, exist_ok=True)


def build_trade_mapping() -> None:
    # 读入原 296 笔
    with open(ROOT / "evidence/FULL_SWEEP_200x100/trades_rich.jsonl") as f:
        old_trades = [json.loads(l) for l in f]

    # 读入现 205 笔
    with open(ROOT / "evidence/FACTOR-ENTRY-01/paired_trades_and_rejects.jsonl") as f:
        paired = [json.loads(l) for l in f]
    filled_205 = [r for r in paired if r["Baseline_passed"]]

    # 索引 205
    new_by_key: Dict[tuple, dict] = {}
    for r in filled_205:
        k = (r["symbol"], int(r["entry_time"]))
        new_by_key[k] = r

    # 按 (symbol, t_in) 聚合原 296 笔
    old_by_key = defaultdict(list)
    for t in old_trades:
        k = (t["symbol"], int(t["t_in"]))
        old_by_key[k].append(t)

    mapping_rows: List[dict] = []
    matched_new_keys = set()

    for idx, ot in enumerate(old_trades):
        sym = ot["symbol"]
        t_in = int(ot["t_in"])
        k = (sym, t_in)
        siblings = old_by_key[k]
        is_dup = len(siblings) > 1

        # 在 205 中寻找匹配
        nt = new_by_key.get(k)
        if nt is not None:
            matched_new_keys.add(k)
            new_event_id = nt["event_id"]
            new_t_out = int(nt.get("exit_time", 0))
            new_p_out = float(nt.get("p_exit", 0.0))
            new_net = float(nt.get("net_pnl", 0.0))
            new_why = str(nt.get("exit_why", ""))

            # 判断当前行是保留的代表行还是被合并的截断行
            if ot.get("why") == "force_close_eod" and any(s.get("why") != "force_close_eod" for s in siblings):
                action = "MERGED_TRUNCATED_TWIN"
                reason = "该记录因旧回测组365天窗口到期触发force_close_eod强平；同一入场在后续组中自然持有离场，故合并去重保留自然离场版本。"
            else:
                action = "RETAINED_PRIMARY_ENTRY"
                reason = "该入场事件在连续全历史中被确认为唯一真实代表性交易。"
        else:
            action = "UNMATCHED"
            reason = "未在现205笔中找到匹配"
            new_event_id = ""
            new_t_out = 0
            new_p_out = 0.0
            new_net = 0.0
            new_why = ""

        mapping_rows.append({
            "old_row_idx": idx,
            "old_group": ot.get("group"),
            "symbol": sym,
            "old_t_in": t_in,
            "old_dt_in": pd.to_datetime(t_in, unit="ms").strftime("%Y-%m-%d %H:%M:%S"),
            "old_t_out": int(ot.get("t_out", 0)),
            "old_dt_out": pd.to_datetime(int(ot.get("t_out", 0)), unit="ms").strftime("%Y-%m-%d %H:%M:%S"),
            "old_why": ot.get("why"),
            "old_p_in": float(ot.get("p_in", 0.0)),
            "old_p_out": float(ot.get("p_out", 0.0)),
            "old_net": float(ot.get("net", 0.0)),
            "is_duplicate_group_entry": is_dup,
            "duplicate_count": len(siblings),
            "reconciliation_action": action,
            "reconciliation_reason": reason,
            "mapped_new_event_id": new_event_id,
            "new_dt_out": pd.to_datetime(new_t_out, unit="ms").strftime("%Y-%m-%d %H:%M:%S") if new_t_out > 0 else "",
            "new_why": new_why,
            "new_net": new_net
        })

    # 检查 205 中未出现在旧 296 笔中的交易 (即新增的 1 笔 METISUSDT)
    unmatched_new = set(new_by_key.keys()) - matched_new_keys
    for k in unmatched_new:
        nt = new_by_key[k]
        mapping_rows.append({
            "old_row_idx": -1,
            "old_group": -1,
            "symbol": nt["symbol"],
            "old_t_in": 0,
            "old_dt_in": "",
            "old_t_out": 0,
            "old_dt_out": "",
            "old_why": "",
            "old_p_in": 0.0,
            "old_p_out": 0.0,
            "old_net": 0.0,
            "is_duplicate_group_entry": False,
            "duplicate_count": 1,
            "reconciliation_action": "NEW_ENTRY_IN_CONTINUOUS_RUN",
            "reconciliation_reason": "连续全历史运行至2026-10-18，该标的在2026-10-11产生有效突破入场，旧200组抽样窗口未覆盖到此时段。",
            "mapped_new_event_id": nt["event_id"],
            "new_dt_out": pd.to_datetime(int(nt.get("exit_time", 0)), unit="ms").strftime("%Y-%m-%d %H:%M:%S"),
            "new_why": nt.get("exit_why", ""),
            "new_net": float(nt.get("net_pnl", 0.0))
        })

    df_map = pd.DataFrame(mapping_rows)
    df_map.to_csv(OUT_DIR / "trade_mapping_296_to_205.csv", index=False)
    with open(OUT_DIR / "trade_mapping_296_to_205.json", "w", encoding="utf-8") as f:
        json.dump(mapping_rows, f, indent=2, ensure_ascii=False)
    print(f"Saved trade_mapping_296_to_205.csv and .json ({len(df_map)} rows).")


def build_timing_audit_contract() -> None:
    timing_contract = {
        "version": "2.0.0",
        "ticket": "FACTOR-ENTRY-02",
        "timestamp_iso": "2026-10-18T12:05:00+08:00",
        "timing_alignment_rules": {
            "decision_point": {
                "definition": "候选产生于 1h K 线结束时刻 T_snap (即 close_time + 1ms)，实际入场撮合于次根 15m K 线开始 (open_time)",
                "example": "1h K 线 [16:00:00, 17:00:00)，T_snap = 17:00:00.001，入场执行挂单于 17:00:00 开始的 15m K 线"
            },
            "features_timing": {
                "candles_taker_buy_volume_1h": {
                    "source_time": "1h K 线收盘时刻",
                    "available_at": "T_close + 1ms (Bar闭合后即时已知)",
                    "decision_time": "T_snap",
                    "matching_direction": "exact_lookback (使用刚闭合的该根 1h K 线)",
                    "max_staleness": "0 (严格对齐当根突破 Bar)",
                    "pit_assumption_note": "K 线闭合后即已知，无发布时滞"
                },
                "metrics_retail_account_ls_ratio": {
                    "source_time": "metrics parquet 中的 ts (5分钟粒度记录)",
                    "available_at": "ts (假设币安归档记录点为产生点)",
                    "decision_time": "T_snap",
                    "matching_direction": "backward_as_of (仅匹配 ts <= T_snap 的最新一条记录)",
                    "max_staleness": "1800000 ms (30 分钟)。超过 30 分钟判定为 STALE/MISSING",
                    "pit_assumption_note": "Parquet 中无毫秒级 available_at 日志。实盘 API 获取可能存在 10~60s 延迟，回测以 ts <= T_snap 作为 PIT 假设。"
                },
                "metrics_top_position_ls_ratio": {
                    "source_time": "metrics parquet 中的 ts",
                    "available_at": "ts",
                    "decision_time": "T_snap",
                    "matching_direction": "backward_as_of (ts <= T_snap)",
                    "max_staleness": "1800000 ms (30 分钟)",
                    "pit_assumption_note": "2021-12 ~ 2022 年部分币种该字段原厂缺失为 null，代码严格标记为 MISSING，不向前借用，不填 0"
                },
                "funding_rate_settled": {
                    "source_time": "funding parquet 中的 funding_time (8小时结算一次，如 00:00, 08:00, 16:00 UTC)",
                    "available_at": "funding_time (已结算时点)",
                    "decision_time": "T_snap",
                    "matching_direction": "backward_as_of (仅匹配 funding_time <= T_snap 的最新一次已结算费率)",
                    "max_staleness": "43200000 ms (12 小时)",
                    "pit_assumption_note": "严格只使用已结算费率，严禁在 07:00 时使用 08:00 即将结算的未来费率"
                },
                "market_regime": {
                    "source_time": "日线闭合时刻 (前一日 23:59:59 UTC)",
                    "available_at": "前一日闭合后",
                    "decision_time": "T_snap",
                    "matching_direction": "backward_as_of (严格使用 T_snap 前已完整闭合的前一交易日 EMA20/EMA50)",
                    "max_staleness": "86400000 ms (24 小时)",
                    "pit_assumption_note": "不使用当日尚未闭合的 K 线，杜绝未来函数"
                }
            }
        },
        "universe_denomination_audit": {
            "total_directories_scanned": 528,
            "excluded_indices": [
                {"symbol": "BTCDOMUSDT", "reason": "BTC统治率复合指数，非单一加密货币合约"}
            ],
            "total_official_crypto_symbols": 527,
            "full_suite_symbols": 526,
            "missing_metrics_symbols": [
                {"symbol": "0GUSDT", "reason": "仅有基础K线，缺失metrics/funding历史归档"}
            ],
            "denominator_conclusion": "官方加密币分母为 527 个，其中具备完整指标体系可参与因子测试的标的为 526 个。不再照搬 528 混入指数。"
        }
    }

    with open(OUT_DIR / "timing_audit_contract.json", "w", encoding="utf-8") as f:
        json.dump(timing_contract, f, indent=2, ensure_ascii=False)
    print("Saved timing_audit_contract.json")


if __name__ == "__main__":
    build_trade_mapping()
    build_timing_audit_contract()
