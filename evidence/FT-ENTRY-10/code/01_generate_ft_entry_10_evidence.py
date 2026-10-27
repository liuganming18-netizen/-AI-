# -*- coding: utf-8 -*-
"""FT-ENTRY-10: C1 改善来源拆解与 C2 表现归因分析脚本

严格落实工单要求：
1. 以“同一参数组＋原始候选身份”匹配 C0、C1、C2 三种模式；
2. 拆解 C1 过滤掉的 C0 亏损与盈利机会（避免的亏损 vs 错过的盈利）；
3. 拆解共同成交候选因延迟入场产生的价格、初始风险 R 及结果变化；
4. 拆解槽位与资金占用变化带来的新增／消失交易；
5. 拆解 C2 相对 C1 进一步过滤或改变的交易；
6. 逐组收益差异分解与严格对账（列出确定值与残差）；
7. 输出全量 CSV 证据表与深度报告。
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
from pathlib import Path
from typing import Dict, List, Any, Tuple

import numpy as np
import pandas as pd

BASE_DIR = Path("/Users/boos/Desktop/威科夫因子共振趋势")
FT09_DIR = BASE_DIR / "evidence/FT-ENTRY-09"
FT10_DIR = BASE_DIR / "evidence/FT-ENTRY-10"
OUT_DIR = FT10_DIR / "out"
OUT_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("FT-ENTRY-10")

MATRIX_SUFFIXES = [
    "Vs04_Z02", "Vs04_Z04", "Vs04_Z06",
    "Vs06_Z02", "Vs06_Z04", "Vs06_Z06",
    "Vs08_Z02", "Vs08_Z04", "Vs08_Z06",
]


def parse_tag(tag: str) -> Tuple[str, str, int]:
    """解析 enter_tag -> (timing_mode, event_id, timestamp_ms)"""
    m = re.match(r"cand_(C[0-9]?)_(ev_.+)_(\d+)", str(tag))
    if m:
        return m.group(1), m.group(2), int(m.group(3))
    return "", "", 0


def main():
    logger.info("=== FT-ENTRY-10 归因分析启动 ===")

    all_candidate_rows = []
    group_decomp_rows = []
    c1_c2_decomp_rows = []
    residual_rows = []

    for suffix in MATRIX_SUFFIXES:
        parts = suffix.split("_")
        vs = float(parts[0].replace("Vs", "")) / 10.0
        z = float(parts[1].replace("Z", "")) / 10.0

        # 读取 3 个模式的 trades 表
        df0 = pd.read_csv(FT09_DIR / f"out/trades_Wyckoff_C_{suffix}.csv")
        df1 = pd.read_csv(FT09_DIR / f"out/trades_Wyckoff_C1_{suffix}.csv")
        df2 = pd.read_csv(FT09_DIR / f"out/trades_Wyckoff_C2_{suffix}.csv")

        # 读取确认审计表
        conf1_file = FT09_DIR / f"out/audit_confirmation_Wyckoff_C1_{suffix}.csv"
        conf2_file = FT09_DIR / f"out/audit_confirmation_Wyckoff_C2_{suffix}.csv"
        df_conf1 = pd.read_csv(conf1_file) if conf1_file.exists() else pd.DataFrame()
        df_conf2 = pd.read_csv(conf2_file) if conf2_file.exists() else pd.DataFrame()

        # 构建确认审计映射: (pair, event_id, orig_signal_time_ms) -> row
        conf1_map = {}
        for _, r in df_conf1.iterrows():
            k = (r["symbol"], r["event_id"], int(r["orig_signal_time_ms"]))
            conf1_map[k] = r

        conf2_map = {}
        for _, r in df_conf2.iterrows():
            k = (r["symbol"], r["event_id"], int(r["orig_signal_time_ms"]))
            conf2_map[k] = r

        # 按原始候选分组
        # C0 的原始候选时间戳就是 tag 末尾的时间戳
        c0_groups = {}
        for _, r in df0.iterrows():
            m, ev, t = parse_tag(r["enter_tag"])
            k = (r["pair"], ev, t)
            if k not in c0_groups:
                c0_groups[k] = []
            c0_groups[k].append(r)

        # C1 的原始候选时间戳 = tag 末尾时间戳 - 3600000 ms (1h)
        c1_groups = {}
        for _, r in df1.iterrows():
            m, ev, t = parse_tag(r["enter_tag"])
            orig_t = t - 3600000
            k = (r["pair"], ev, orig_t)
            if k not in c1_groups:
                c1_groups[k] = []
            c1_groups[k].append(r)

        # C2 的原始候选时间戳 = tag 末尾时间戳 - 3600000 ms
        c2_groups = {}
        for _, r in df2.iterrows():
            m, ev, t = parse_tag(r["enter_tag"])
            orig_t = t - 3600000
            k = (r["pair"], ev, orig_t)
            if k not in c2_groups:
                c2_groups[k] = []
            c2_groups[k].append(r)

        all_keys = set(c0_groups.keys()) | set(c1_groups.keys()) | set(c2_groups.keys())
        keys_c0 = set(c0_groups.keys())
        keys_c1 = set(c1_groups.keys())
        keys_c2 = set(c2_groups.keys())

        # 逐候选对比
        for k in sorted(all_keys):
            pair, ev, orig_t = k
            clean_sym = pair.split("/")[0].replace("1000", "") + "USDT"

            in_c0 = k in keys_c0
            in_c1 = k in keys_c1
            in_c2 = k in keys_c2

            trades0 = c0_groups.get(k, [])
            trades1 = c1_groups.get(k, [])
            trades2 = c2_groups.get(k, [])

            p0_sum = sum(t["profit_abs"] for t in trades0) if in_c0 else 0.0
            p1_sum = sum(t["profit_abs"] for t in trades1) if in_c1 else 0.0
            p2_sum = sum(t["profit_abs"] for t in trades2) if in_c2 else 0.0

            # 首次开仓信息
            t0_first = trades0[0] if in_c0 else None
            t1_first = trades1[0] if in_c1 else None
            t2_first = trades2[0] if in_c2 else None

            price_c0 = t0_first["open_rate"] if t0_first is not None else np.nan
            price_c1 = t1_first["open_rate"] if t1_first is not None else np.nan
            price_c2 = t2_first["open_rate"] if t2_first is not None else np.nan

            p0_stop = t0_first["initial_stop_loss_abs"] if t0_first is not None else (
                t1_first["initial_stop_loss_abs"] if t1_first is not None else np.nan
            )

            r_c0 = (price_c0 - p0_stop) if not np.isnan(price_c0) and not np.isnan(p0_stop) else np.nan
            r_c1 = (price_c1 - p0_stop) if not np.isnan(price_c1) and not np.isnan(p0_stop) else np.nan
            r_c2 = (price_c2 - p0_stop) if not np.isnan(price_c2) and not np.isnan(p0_stop) else np.nan

            # 确认审计查询
            conf_k = (clean_sym, ev, orig_t)
            rec_conf1 = conf1_map.get(conf_k)
            rec_conf2 = conf2_map.get(conf_k)

            c1_decision = rec_conf1["decision"] if rec_conf1 is not None else ("EXECUTED" if in_c1 else "UNKNOWN")
            c1_reason = rec_conf1["reason"] if rec_conf1 is not None else ("executed_entry" if in_c1 else "unrecorded")

            c2_decision = rec_conf2["decision"] if rec_conf2 is not None else ("EXECUTED" if in_c2 else "UNKNOWN")
            c2_reason = rec_conf2["reason"] if rec_conf2 is not None else ("executed_entry" if in_c2 else "unrecorded")

            # 归因分类标签
            if in_c0 and not in_c1:
                category = "避免的亏损" if p0_sum < 0 else "错过的盈利"
            elif in_c0 and in_c1:
                if p1_sum > p0_sum:
                    category = "共同成交改善"
                elif p1_sum < p0_sum:
                    category = "共同成交变差"
                else:
                    category = "共同成交持平"
            elif not in_c0 and in_c1:
                category = "槽位释放新增成交"
            else:
                category = "其他模式独立成交"

            # C2 相对 C1 比较
            if in_c1 and not in_c2:
                c2_vs_c1_label = "C2避免的C1亏损" if p1_sum < 0 else "C2错过的C1盈利"
            elif in_c1 and in_c2:
                c2_vs_c1_label = "C2共同成交改善" if p2_sum > p1_sum else ("C2共同成交变差" if p2_sum < p1_sum else "C2共同成交持平")
            elif not in_c1 and in_c2:
                c2_vs_c1_label = "C2槽位释放新增成交"
            else:
                c2_vs_c1_label = "非C1成交候选"

            all_candidate_rows.append({
                "param_suffix": suffix,
                "vs": vs,
                "z": z,
                "pair": pair,
                "event_id": ev,
                "orig_signal_time_ms": orig_t,
                "orig_signal_time": t0_first["open_date"] if t0_first is not None else (t1_first["open_date"] if t1_first is not None else ""),
                "initial_stop_P0": p0_stop,
                "in_c0": in_c0,
                "c0_trades_count": len(trades0),
                "c0_profit_usdt": round(p0_sum, 2) if in_c0 else 0.0,
                "c0_open_rate": price_c0,
                "c0_initial_risk_R": round(r_c0, 6) if not np.isnan(r_c0) else np.nan,
                "c0_exit_reason": t0_first["exit_reason"] if t0_first is not None else "",
                "in_c1": in_c1,
                "c1_decision": c1_decision,
                "c1_filter_reason": c1_reason,
                "c1_profit_usdt": round(p1_sum, 2) if in_c1 else 0.0,
                "c1_open_rate": price_c1,
                "c1_initial_risk_R": round(r_c1, 6) if not np.isnan(r_c1) else np.nan,
                "c1_exit_reason": t1_first["exit_reason"] if t1_first is not None else "",
                "in_c2": in_c2,
                "c2_decision": c2_decision,
                "c2_filter_reason": c2_reason,
                "c2_profit_usdt": round(p2_sum, 2) if in_c2 else 0.0,
                "c2_open_rate": price_c2,
                "c2_initial_risk_R": round(r_c2, 6) if not np.isnan(r_c2) else np.nan,
                "c2_exit_reason": t2_first["exit_reason"] if t2_first is not None else "",
                "delay_entry_price_diff_pct": round((price_c1 - price_c0) / price_c0 * 100.0, 3) if not np.isnan(price_c0) and not np.isnan(price_c1) else np.nan,
                "delay_entry_R_diff_pct": round((r_c1 - r_c0) / r_c0 * 100.0, 3) if not np.isnan(r_c0) and not np.isnan(r_c1) and r_c0 > 0 else np.nan,
                "c1_attribution_category": category,
                "c2_vs_c1_attribution": c2_vs_c1_label
            })

        # ── 组级别差异分解 ──
        # 1. C1 vs C0 分解
        common_01 = keys_c0 & keys_c1
        filtered_0 = keys_c0 - keys_c1
        new_in_1 = keys_c1 - keys_c0

        avoided_loss_val = 0.0
        avoided_loss_cnt = 0
        missed_win_val = 0.0
        missed_win_cnt = 0

        for k in filtered_0:
            trades = c0_groups[k]
            p = sum(t["profit_abs"] for t in trades)
            if p < 0:
                avoided_loss_val += abs(p)
                avoided_loss_cnt += len(trades)
            else:
                missed_win_val += p
                missed_win_cnt += len(trades)

        net_filter_contrib = avoided_loss_val - missed_win_val

        common_p0 = 0.0
        common_p1 = 0.0
        price_diffs_common = []
        r_diffs_common = []

        for k in common_01:
            p0_k = sum(t["profit_abs"] for t in c0_groups[k])
            p1_k = sum(t["profit_abs"] for t in c1_groups[k])
            common_p0 += p0_k
            common_p1 += p1_k

            t0 = c0_groups[k][0]
            t1 = c1_groups[k][0]
            price_diffs_common.append((t1["open_rate"] - t0["open_rate"]) / t0["open_rate"] * 100.0)
            r0 = t0["open_rate"] - t0["initial_stop_loss_abs"]
            r1 = t1["open_rate"] - t1["initial_stop_loss_abs"]
            if r0 > 0:
                r_diffs_common.append((r1 - r0) / r0 * 100.0)

        common_delta = common_p1 - common_p0

        new_c1_val = sum(sum(t["profit_abs"] for t in c1_groups[k]) for k in new_in_1)
        new_c1_cnt = sum(len(c1_groups[k]) for k in new_in_1)

        actual_c0_profit = df0["profit_abs"].sum()
        actual_c1_profit = df1["profit_abs"].sum()
        actual_delta_c1_c0 = actual_c1_profit - actual_c0_profit

        explained_delta = net_filter_contrib + common_delta + new_c1_val
        residual = actual_delta_c1_c0 - explained_delta

        group_decomp_rows.append({
            "param_suffix": suffix,
            "vs": vs,
            "z": z,
            "actual_c0_profit_usdt": round(actual_c0_profit, 2),
            "actual_c1_profit_usdt": round(actual_c1_profit, 2),
            "actual_delta_usdt": round(actual_delta_c1_c0, 2),
            "avoided_losses_usdt": round(avoided_loss_val, 2),
            "avoided_losses_count": avoided_loss_cnt,
            "missed_wins_usdt": round(missed_win_val, 2),
            "missed_wins_count": missed_win_cnt,
            "net_filter_contribution_usdt": round(net_filter_contrib, 2),
            "common_trades_count": len(common_01),
            "common_c0_profit_usdt": round(common_p0, 2),
            "common_c1_profit_usdt": round(common_p1, 2),
            "common_delta_usdt": round(common_delta, 2),
            "avg_delay_price_diff_pct": round(np.mean(price_diffs_common), 2) if price_diffs_common else 0.0,
            "avg_delay_R_diff_pct": round(np.mean(r_diffs_common), 2) if r_diffs_common else 0.0,
            "new_c1_trades_count": new_c1_cnt,
            "new_c1_profit_usdt": round(new_c1_val, 2),
            "unexplained_residual_usdt": round(residual, 2)
        })

        # 2. C2 vs C1 分解
        common_12 = keys_c1 & keys_c2
        filtered_1 = keys_c1 - keys_c2
        new_in_2 = keys_c2 - keys_c1

        avoided_loss_c2 = 0.0
        avoided_cnt_c2 = 0
        missed_win_c2 = 0.0
        missed_cnt_c2 = 0

        for k in filtered_1:
            p = sum(t["profit_abs"] for t in c1_groups[k])
            if p < 0:
                avoided_loss_c2 += abs(p)
                avoided_cnt_c2 += len(c1_groups[k])
            else:
                missed_win_c2 += p
                missed_cnt_c2 += len(c1_groups[k])

        net_filter_c2 = avoided_loss_c2 - missed_win_c2

        common_p1_for_2 = 0.0
        common_p2 = 0.0
        price_diffs_c2 = []
        r_diffs_c2 = []

        for k in common_12:
            p1_k = sum(t["profit_abs"] for t in c1_groups[k])
            p2_k = sum(t["profit_abs"] for t in c2_groups[k])
            common_p1_for_2 += p1_k
            common_p2 += p2_k

            t1 = c1_groups[k][0]
            t2 = c2_groups[k][0]
            price_diffs_c2.append((t2["open_rate"] - t1["open_rate"]) / t1["open_rate"] * 100.0)
            r1 = t1["open_rate"] - t1["initial_stop_loss_abs"]
            r2 = t2["open_rate"] - t2["initial_stop_loss_abs"]
            if r1 > 0:
                r_diffs_c2.append((r2 - r1) / r1 * 100.0)

        common_delta_c2 = common_p2 - common_p1_for_2
        new_c2_val = sum(sum(t["profit_abs"] for t in c2_groups[k]) for k in new_in_2)
        new_c2_cnt = sum(len(c2_groups[k]) for k in new_in_2)

        actual_c2_profit = df2["profit_abs"].sum()
        actual_delta_c2_c1 = actual_c2_profit - actual_c1_profit
        residual_c2 = actual_delta_c2_c1 - (net_filter_c2 + common_delta_c2 + new_c2_val)

        c1_c2_decomp_rows.append({
            "param_suffix": suffix,
            "vs": vs,
            "z": z,
            "actual_c1_profit_usdt": round(actual_c1_profit, 2),
            "actual_c2_profit_usdt": round(actual_c2_profit, 2),
            "actual_delta_c2_c1_usdt": round(actual_delta_c2_c1, 2),
            "c2_avoided_c1_loss_usdt": round(avoided_loss_c2, 2),
            "c2_avoided_c1_loss_cnt": avoided_cnt_c2,
            "c2_missed_c1_win_usdt": round(missed_win_c2, 2),
            "c2_missed_c1_win_cnt": missed_cnt_c2,
            "c2_net_filter_contrib_usdt": round(net_filter_c2, 2),
            "c2_common_trades_cnt": len(common_12),
            "c2_common_delta_usdt": round(common_delta_c2, 2),
            "avg_c2_delay_price_diff_pct": round(np.mean(price_diffs_c2), 2) if price_diffs_c2 else 0.0,
            "avg_c2_delay_R_diff_pct": round(np.mean(r_diffs_c2), 2) if r_diffs_c2 else 0.0,
            "c2_new_trades_cnt": new_c2_cnt,
            "c2_new_trades_profit_usdt": round(new_c2_val, 2),
            "residual_c2_usdt": round(residual_c2, 2)
        })

        # 残差审计项
        residual_rows.append({
            "param_suffix": suffix,
            "comparison": "C1 vs C0",
            "actual_delta": round(actual_delta_c1_c0, 2),
            "explained_sum": round(explained_delta, 2),
            "residual": round(residual, 2),
            "status": "EXACT_MATCH" if abs(residual) < 0.01 else "RESIDUAL_PRESENT"
        })
        residual_rows.append({
            "param_suffix": suffix,
            "comparison": "C2 vs C1",
            "actual_delta": round(actual_delta_c2_c1, 2),
            "explained_sum": round(net_filter_c2 + common_delta_c2 + new_c2_val, 2),
            "residual": round(residual_c2, 2),
            "status": "EXACT_MATCH" if abs(residual_c2) < 0.01 else "RESIDUAL_PRESENT"
        })

    # 保存三大 CSV
    df_cand = pd.DataFrame(all_candidate_rows)
    df_cand.to_csv(FT10_DIR / "01_逐候选跨模式对照表.csv", index=False, encoding="utf-8-sig")
    logger.info(f"保存 01_逐候选跨模式对照表.csv: {len(df_cand)} 行")

    df_group = pd.DataFrame(group_decomp_rows)
    df_group.to_csv(FT10_DIR / "02_逐组收益差异分解表_C1_vs_C0.csv", index=False, encoding="utf-8-sig")
    logger.info(f"保存 02_逐组收益差异分解表_C1_vs_C0.csv: {len(df_group)} 行")

    df_c12 = pd.DataFrame(c1_c2_decomp_rows)
    df_c12.to_csv(FT10_DIR / "02_逐组收益差异分解表_C2_vs_C1.csv", index=False, encoding="utf-8-sig")
    logger.info(f"保存 02_逐组收益差异分解表_C2_vs_C1.csv: {len(df_c12)} 行")

    df_res = pd.DataFrame(residual_rows)
    df_res.to_csv(FT10_DIR / "03_残差审计与对账表.csv", index=False, encoding="utf-8-sig")
    logger.info(f"保存 03_残差审计与对账表.csv: {len(df_res)} 行")

    logger.info("=== 数据表生成完毕 ===")


if __name__ == "__main__":
    main()
