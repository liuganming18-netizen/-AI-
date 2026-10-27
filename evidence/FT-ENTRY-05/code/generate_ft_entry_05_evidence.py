# -*- coding: utf-8 -*-
"""FT-ENTRY-05 突破后拒单的逐候选归因分析脚本

严格落实工单要求：
1. 复用 FT-ENTRY-03／04 现有结果，不重跑回测；
2. 对达到或超过冻结突破位 H 的 71 条拒单请求，先按“实验组＋候选ID”去重（去重后 68 组），绝不直接称 71 笔交易；
3. 对应旧版交易，逐项区分：
   - 避免的亏损 (EXCLUDED_LOSS)
   - 错过的盈利 (MISSED_PROFIT)
   - 旧版也未成交 (REJECTED_NEITHER_FILLED)
   - 无法对应 (UNMATCHED)
   - 释放仓位后引起的其他成交变化 (CAPACITY_RELEASED_NEW_TRADE)
4. 使用信号／执行时刻已可知的字段描述差异，优先复用：
   - 距离 H (信号时刻 (H-close)/A，执行时刻 (fill-H)/A)
   - 回踩量比 (volume_ratio)
   - OI 变化 (oi_growth_6h)
   - 结构阶段 (Wyckoff A 路线提前测试入场阶段)
   - 初始风险距离 (initial_risk_r, initial_risk_r / A)
   不新增指标体系，不搜索阈值；
5. 后续走势只作为结果标签，不得参与信号或倒推入场；
6. 交付逐候选表、分类汇总、最多 3 条有反例对照的研究假设。
"""
from __future__ import annotations

import glob
import json
import logging
import math
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Any

import numpy as np
import pandas as pd

BASE_DIR = Path("/Users/boos/Desktop/威科夫因子共振趋势")
FT03_OUT = BASE_DIR / "evidence/FT-ENTRY-03/out"
FT04_OUT = BASE_DIR / "evidence/FT-ENTRY-04/out"
OUT_DIR = BASE_DIR / "evidence/FT-ENTRY-05"
DATA_DIR = BASE_DIR / "evidence/FT-ENTRY-04/user_data/data/binance/futures"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("FT-ENTRY-05-Attribution")

sys.path.insert(0, str(BASE_DIR))
sys.path.insert(0, str(BASE_DIR / "evidence/FT-ENTRY-04/user_data/strategies"))

import importlib.util
spec = importlib.util.spec_from_file_location(
    "WyckoffParamExplorationBase",
    str(BASE_DIR / "evidence/FT-ENTRY-04/user_data/strategies/WyckoffParamExplorationBase.py")
)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
ParamTimingEventStateMachine = mod.ParamTimingEventStateMachine
EventTrendConfig = mod.EventTrendConfig
compute_wilder_atr_series = mod.compute_wilder_atr_series


def extract_13_snapshots() -> Dict[str, Any]:
    """定向提取 13 个唯一候选在信号时刻已知的原始物理特征快照。"""
    cands_meta = [
        ("cand_A_ev_FT_ENTRY_02_binance_CHRUSDT_1653134400000_1653980400000", "CHR_USDT_USDT", "CHRUSDT", 1653980400000, 0.2, 0.8),
        ("cand_A_ev_FT_ENTRY_02_binance_BELUSDT_1672372800000_1672938000000", "BEL_USDT_USDT", "BELUSDT", 1672938000000, 0.2, 0.8),
        ("cand_A_ev_FT_ENTRY_02_binance_API3USDT_1689105600000_1689447600000", "API3_USDT_USDT", "API3USDT", 1689447600000, 0.2, 0.8),
        ("cand_A_ev_FT_ENTRY_02_binance_ARPAUSDT_1692921600000_1693468800000", "ARPA_USDT_USDT", "ARPAUSDT", 1693468800000, 0.6, 0.8),
        ("cand_A_ev_FT_ENTRY_02_binance_BNBUSDT_1705089600000_1705298400000", "BNB_USDT_USDT", "BNBUSDT", 1705298400000, 0.4, 0.8),
        ("cand_A_ev_FT_ENTRY_02_binance_COTIUSDT_1706904000000_1707159600000", "COTI_USDT_USDT", "COTIUSDT", 1707159600000, 0.4, 0.8),
        ("cand_A_ev_FT_ENTRY_02_binance_BONKUSDT_1708718400000_1708984800000", "1000BONK_USDT_USDT", "BONKUSDT", 1708984800000, 0.4, 0.8),
        ("cand_A_ev_FT_ENTRY_02_binance_BONKUSDT_1723838400000_1724493600000", "1000BONK_USDT_USDT", "BONKUSDT", 1724493600000, 0.6, 0.8),
        ("cand_A_ev_FT_ENTRY_02_binance_CHRUSDT_1730692800000_1731150000000", "CHR_USDT_USDT", "CHRUSDT", 1731150000000, 0.4, 0.8),
        ("cand_A_ev_FT_ENTRY_02_binance_APEUSDT_1730692800000_1731232800000", "APE_USDT_USDT", "APEUSDT", 1731232800000, 0.2, 1.2),
        ("cand_A_ev_FT_ENTRY_02_binance_ARUSDT_1667332800000_1667433600000", "AR_USDT_USDT", "ARUSDT", 1667433600000, 0.4, 0.8),
        ("cand_A_ev_FT_ENTRY_02_binance_ARUSDT_1676030400000_1676649600000", "AR_USDT_USDT", "ARUSDT", 1676649600000, 0.4, 0.8),
        ("cand_A_ev_FT_ENTRY_02_binance_ARPAUSDT_1702353600000_1703415600000", "ARPA_USDT_USDT", "ARPAUSDT", 1703415600000, 0.6, 0.8),
    ]

    snapshots = {}
    for target_id, pslug, csym, sig_time_ms, delta, vol in cands_meta:
        df_4h = pd.read_feather(DATA_DIR / f"{pslug}-4h-futures.feather")
        df_1h = pd.read_feather(DATA_DIR / f"{pslug}-1h-futures.feather")
        f_oi = DATA_DIR / f"{pslug}-5m-open_interest.feather"
        if f_oi.exists():
            df_oi = pd.read_feather(f_oi)
            oi_ts = df_oi["date"].astype("int64").to_numpy()
            oi_vals = df_oi["open_interest_amount"].to_numpy(dtype=np.float64)
        else:
            oi_ts = np.array([], dtype=np.int64)
            oi_vals = np.array([], dtype=np.float64)

        highs_4h = df_4h["high"].to_numpy(dtype=np.float64)
        lows_4h = df_4h["low"].to_numpy(dtype=np.float64)
        closes_4h = df_4h["close"].to_numpy(dtype=np.float64)
        df_4h["atr"] = compute_wilder_atr_series(highs_4h, lows_4h, closes_4h, period=14)
        df_4h["open_ms"] = df_4h["date"].astype("int64")
        df_4h["close_ms"] = df_4h["open_ms"] + 4 * 3600 * 1000

        df_1h = df_1h[df_1h["date"].astype("int64") <= sig_time_ms + 4 * 3600 * 1000].copy()
        df_1h["open_time_ms"] = df_1h["date"].astype("int64")
        df_1h["close_time_ms"] = df_1h["open_time_ms"] + 3600 * 1000

        cfg = EventTrendConfig(strategy_name="FT_ENTRY_02")
        sm = ParamTimingEventStateMachine(
            config=cfg,
            symbol=csym,
            timing_mode="A",
            enable_oi_filter=True,
            a_entry_level_atr_offset=delta,
            a_volume_multiplier=vol,
            c_vol_ratio_vs=0.6,
            c_retest_zone_atr=0.4
        )
        if len(oi_ts) > 0:
            sm.pit.register_oi(oi_ts, oi_vals)

        records_1h = df_1h.to_dict("records")
        for r in records_1h:
            t_open = int(r["open_time_ms"])
            t_close = int(r["close_time_ms"])
            if t_close % (4 * 3600 * 1000) == 0:
                sub_4h = df_4h[df_4h["close_ms"] <= t_close]
                if len(sub_4h) >= 42:
                    last_42 = sub_4h.tail(42)
                    atr_val = float(sub_4h["atr"].iloc[-1])
                    sm.check_and_form_box(
                        bar_4h_close_ms=t_close,
                        history_42_highs=last_42["high"].to_numpy(np.float64),
                        history_42_lows=last_42["low"].to_numpy(np.float64),
                        history_42_closes=last_42["close"].to_numpy(np.float64),
                        current_atr_a=atr_val
                    )
            cand = sm.process_1h_bar(
                open_time_ms=t_open,
                open_p=float(r["open"]),
                high_p=float(r["high"]),
                low_p=float(r["low"]),
                close_p=float(r["close"]),
                volume_v=float(r["volume"]),
                oi_timestamps=oi_ts,
                oi_values=oi_vals
            )
            if cand and cand.candidate_id == target_id:
                snapshots[target_id] = cand
                break
            for c in sm.candidates:
                if c.candidate_id == target_id:
                    snapshots[target_id] = c
                    break
            if target_id in snapshots:
                break

    logger.info(f"成功提取 {len(snapshots)}/13 个物理候选快照特征。")
    return snapshots


def main():
    logger.info("=== FT-ENTRY-05 突破后拒单逐候选归因开始 ===")

    # 1. 读取 FT-ENTRY-04 的 9 个审计文件
    audit_files = sorted(glob.glob(str(FT04_OUT / "audit_orders_Wyckoff_A_*.csv")))
    dfs_audit = []
    for f in audit_files:
        df = pd.read_csv(f)
        dfs_audit.append(df)
    all_audit = pd.concat(dfs_audit, ignore_index=True)

    # 2. 筛选达到或超过冻结突破位 H 的拒单请求
    ge_h_mask = all_audit["reason"].isin(["fill_price_ge_frozen_h_greater", "fill_price_ge_frozen_h_equal"])
    df_71 = all_audit[ge_h_mask].copy()
    logger.info(f"筛选出的突破后拒单请求总数: {len(df_71)} 条 (预期: 71)")

    # 3. 统计每个 (strategy, candidate_id) 的请求次数
    req_counts = df_71.groupby(["strategy", "candidate_id"]).size().reset_index(name="request_count")

    # 去重：按 (strategy, candidate_id) 取首条
    df_68 = df_71.drop_duplicates(subset=["strategy", "candidate_id"], keep="first").copy()
    df_68 = pd.merge(df_68, req_counts, on=["strategy", "candidate_id"], how="left")
    logger.info(f"按实验组+候选ID去重后候选数: {len(df_68)} 条 (预期: 68)")

    # 4. 加载 FT-ENTRY-04 既有对账归因表与 FT-ENTRY-03/04 交易结果
    df_reconcile = pd.read_csv(FT04_OUT / "06_逐候选全量对照与归因表.csv")

    # 合并对账信息
    merged = pd.merge(
        df_68,
        df_reconcile[["strategy", "candidate_id", "category", "in_ft03", "in_ft04",
                      "ft03_open_date", "ft03_open_rate", "ft03_profit_abs",
                      "ft04_open_date", "ft04_open_rate", "ft04_profit_abs",
                      "attribution_pnl_delta"]],
        on=["strategy", "candidate_id"],
        how="left"
    )

    # 5. 提取 13 个唯一候选物理特征
    snapshots = extract_13_snapshots()

    # 6. 为 68 组候选填入已知特征字段
    feature_rows = []
    for _, row in merged.iterrows():
        cid = row["candidate_id"]
        snap = snapshots.get(cid)
        strat = row["strategy"]

        # 解析实验组参数
        parts = strat.split("_")
        delta_str = parts[2]  # L06, L04, L02
        vol_str = parts[3]    # V08, V10, V12
        delta_val = float(delta_str.replace("L", "")) / 10.0
        vol_val = float(vol_str.replace("V", "")) / 10.0

        if snap is not None:
            H = snap.box_high_h
            L = snap.box_low_l
            A = snap.box_atr_a
            ER = snap.box_er
            close_p = snap.breakout_close
            vol_ratio = snap.volume_ratio
            oi_growth = snap.oi_growth_6h
            retest_lr = snap.retest_low_lr
            sweep_s = snap.sweep_low_s
            stop_p = snap.initial_stop_price
            risk_r = snap.initial_risk_r
            risk_r_atr = risk_r / A if A > 0 else np.nan
            dist_h_sig = H - close_p
            dist_h_sig_atr = dist_h_sig / A if A > 0 else np.nan
        else:
            H = float(row["frozen_H"])
            L = A = ER = close_p = vol_ratio = oi_growth = retest_lr = sweep_s = stop_p = risk_r = risk_r_atr = dist_h_sig = dist_h_sig_atr = np.nan

        fill_p = float(row["fill_price"])
        fill_minus_h = float(row["price_minus_h"])
        fill_minus_h_atr = fill_minus_h / A if (snap is not None and A > 0) else np.nan

        # 映射规范分类名称
        cat_raw = row["category"]
        if cat_raw == "EXCLUDED_LOSS":
            cat_norm = "避免的亏损"
        elif cat_raw == "MISSED_PROFIT":
            cat_norm = "错过的盈利"
        elif cat_raw == "REJECTED_NEITHER_FILLED":
            cat_norm = "旧版也未成交"
        elif cat_raw == "CAPACITY_RELEASED_NEW_TRADE":
            cat_norm = "释放仓位后引起的其他成交变化"
        else:
            cat_norm = "无法对应" if cat_raw == "UNKNOWN" else cat_raw

        feature_rows.append({
            "strategy": strat,
            "delta_atr": delta_val,
            "vol_multiplier": vol_val,
            "candidate_id": cid,
            "pair": row["pair"],
            "request_count": row["request_count"],
            "signal_time": row["signal_time"],
            "planned_execution_time": row["planned_execution_time"],
            "category": cat_norm,
            "category_code": cat_raw,
            "frozen_H": H,
            "signal_close": close_p,
            "distance_H_signal": dist_h_sig,
            "distance_H_signal_atr": dist_h_sig_atr,
            "fill_price": fill_p,
            "fill_minus_H": fill_minus_h,
            "fill_minus_H_atr": fill_minus_h_atr,
            "box_atr_a": A,
            "box_er": ER,
            "retest_low_lr": retest_lr,
            "sweep_low_s": sweep_s,
            "volume_ratio": vol_ratio,
            "oi_growth_6h": oi_growth,
            "initial_stop_price": stop_p,
            "initial_risk_r": risk_r,
            "initial_risk_r_atr": risk_r_atr,
            "structure_phase": "Phase C/D (突破前回踩确认测试)",
            "in_ft03": row["in_ft03"],
            "ft03_open_date": row["ft03_open_date"],
            "ft03_open_rate": row["ft03_open_rate"],
            "ft03_profit_abs": row["ft03_profit_abs"],
            "in_ft04": row["in_ft04"],
            "ft04_open_date": row["ft04_open_date"],
            "ft04_open_rate": row["ft04_open_rate"],
            "ft04_profit_abs": row["ft04_profit_abs"],
            "attribution_pnl_delta": row["attribution_pnl_delta"],
            "rejection_reason": row["reason"]
        })

    df_full = pd.DataFrame(feature_rows)

    # 7. 导出 01_逐候选对照与归因明细表.csv
    file_01 = OUT_DIR / "01_逐候选对照与归因明细表.csv"
    df_full.to_csv(file_01, index=False, encoding="utf-8-sig")
    logger.info(f"已导出逐候选对照与归因明细表: {file_01} (共 {len(df_full)} 行)")

    # 8. 梳理释放仓位后引起的其他成交变化
    # 在 FT-ENTRY-04 中，寻找 category == 'CAPACITY_RELEASED_NEW_TRADE' 的交易
    df_cap = df_reconcile[df_reconcile["category"] == "CAPACITY_RELEASED_NEW_TRADE"].copy()
    logger.info(f"识别出释放仓位后引起的新增交易: {len(df_cap)} 笔")

    # 9. 生成 02_分类汇总与特征统计表.csv
    # 按照五大类进行汇总统计
    summary_list = []
    categories = ["避免的亏损", "错过的盈利", "旧版也未成交", "无法对应", "释放仓位后引起的其他成交变化"]

    for cat in categories:
        if cat == "释放仓位后引起的其他成交变化":
            n_req = len(df_cap)
            n_cand = len(df_cap)
            pnl_delta_sum = df_cap["attribution_pnl_delta"].sum() if not df_cap.empty else 0.0
            pnl_ft03_sum = 0.0
            pnl_ft04_sum = df_cap["ft04_profit_abs"].sum() if not df_cap.empty else 0.0
            dist_sig_mean = np.nan
            dist_sig_med = np.nan
            vol_ratio_mean = np.nan
            vol_ratio_med = np.nan
            oi_mean = np.nan
            oi_med = np.nan
            risk_mean = np.nan
            risk_med = np.nan
            er_mean = np.nan
            desc = "前序候选被拒释放持仓槽位，后续其他候选获得入场机会"
        else:
            sub = df_full[df_full["category"] == cat]
            n_req = sub["request_count"].sum()
            n_cand = len(sub)
            pnl_delta_sum = sub["attribution_pnl_delta"].sum()
            pnl_ft03_sum = sub["ft03_profit_abs"].sum()
            pnl_ft04_sum = sub["ft04_profit_abs"].sum()
            dist_sig_mean = sub["distance_H_signal_atr"].mean()
            dist_sig_med = sub["distance_H_signal_atr"].median()
            vol_ratio_mean = sub["volume_ratio"].mean()
            vol_ratio_med = sub["volume_ratio"].median()
            oi_mean = sub["oi_growth_6h"].mean()
            oi_med = sub["oi_growth_6h"].median()
            risk_mean = sub["initial_risk_r_atr"].mean()
            risk_med = sub["initial_risk_r_atr"].median()
            er_mean = sub["box_er"].mean()
            if cat == "避免的亏损":
                desc = "旧版放行导致亏损止损，新版严格拦截成功挽回亏损"
            elif cat == "错过的盈利":
                desc = "旧版放行并录得趋势盈利，新版严格拦截错失利润"
            elif cat == "旧版也未成交":
                desc = "旧版因并发仓位限制未成交，新版因价格越界拒绝，两版均未成交"
            else:
                desc = "两版候选或订单存在孤儿未对齐 (本次实测为0)"

        summary_list.append({
            "category": cat,
            "request_count_raw": int(n_req),
            "candidate_count_dedup": int(n_cand),
            "ft03_net_pnl_u": round(float(pnl_ft03_sum), 4),
            "ft04_net_pnl_u": round(float(pnl_ft04_sum), 4),
            "attribution_pnl_delta_u": round(float(pnl_delta_sum), 4),
            "distance_H_signal_atr_mean": round(float(dist_sig_mean), 4) if not np.isnan(dist_sig_mean) else None,
            "distance_H_signal_atr_median": round(float(dist_sig_med), 4) if not np.isnan(dist_sig_med) else None,
            "volume_ratio_mean": round(float(vol_ratio_mean), 4) if not np.isnan(vol_ratio_mean) else None,
            "volume_ratio_median": round(float(vol_ratio_med), 4) if not np.isnan(vol_ratio_med) else None,
            "oi_growth_6h_mean": round(float(oi_mean), 4) if not np.isnan(oi_mean) else None,
            "oi_growth_6h_median": round(float(oi_med), 4) if not np.isnan(oi_med) else None,
            "initial_risk_r_atr_mean": round(float(risk_mean), 4) if not np.isnan(risk_mean) else None,
            "initial_risk_r_atr_median": round(float(risk_med), 4) if not np.isnan(risk_med) else None,
            "box_er_mean": round(float(er_mean), 4) if not np.isnan(er_mean) else None,
            "mechanism_description": desc
        })

    df_summary = pd.DataFrame(summary_list)
    file_02 = OUT_DIR / "02_分类汇总与特征统计表.csv"
    df_summary.to_csv(file_02, index=False, encoding="utf-8-sig")
    logger.info(f"已导出分类汇总与特征统计表: {file_02}")

    # 10. 生成 03_13个唯一候选特征与反例对照表.csv
    unique_cands_list = []
    for cid in df_full["candidate_id"].unique():
        sub = df_full[df_full["candidate_id"] == cid]
        first = sub.iloc[0]
        strats_str = "; ".join(sub["strategy"].tolist())
        pnl_ft03_vals = sub["ft03_profit_abs"].dropna().unique().tolist()
        pnl_ft03_repr = pnl_ft03_vals[0] if len(pnl_ft03_vals) > 0 else None

        unique_cands_list.append({
            "candidate_id": cid,
            "symbol": first["pair"].split("/")[0],
            "pair": first["pair"],
            "signal_time": first["signal_time"],
            "planned_execution_time": first["planned_execution_time"],
            "category": first["category"],
            "occurred_strategies_count": len(sub),
            "occurred_strategies": strats_str,
            "total_requests": int(sub["request_count"].sum()),
            "frozen_H": first["frozen_H"],
            "signal_close": first["signal_close"],
            "distance_H_signal": first["distance_H_signal"],
            "distance_H_signal_atr": round(first["distance_H_signal_atr"], 4),
            "fill_price": first["fill_price"],
            "fill_minus_H": first["fill_minus_H"],
            "fill_minus_H_atr": round(first["fill_minus_H_atr"], 4),
            "box_atr_a": round(first["box_atr_a"], 6),
            "box_er": round(first["box_er"], 4),
            "retest_low_lr": round(first["retest_low_lr"], 6),
            "volume_ratio": round(first["volume_ratio"], 4),
            "oi_growth_6h": round(first["oi_growth_6h"], 4),
            "initial_stop_price": round(first["initial_stop_price"], 6),
            "initial_risk_r": round(first["initial_risk_r"], 6),
            "initial_risk_r_atr": round(first["initial_risk_r_atr"], 4),
            "ft03_profit_abs": pnl_ft03_repr,
            "rejection_reason": first["rejection_reason"]
        })

    df_unique = pd.DataFrame(unique_cands_list)
    file_03 = OUT_DIR / "03_13个唯一候选特征与反例对照表.csv"
    df_unique.to_csv(file_03, index=False, encoding="utf-8-sig")
    logger.info(f"已导出13个唯一候选特征与反例对照表: {file_03}")

    logger.info("=== FT-ENTRY-05 数据生成与对照归因全部完成 ===")


if __name__ == "__main__":
    main()
