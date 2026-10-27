# -*- coding: utf-8 -*-
"""工单 FACTOR-ENTRY-01 - 统一候选事件池特征增强与微观路径全覆盖。

整合范围：
1. 旧 296 笔成交经窗口截断去重后的 204 笔唯一真实入场交易。
2. 连续历史全时段回测中捕获的全部候选事件（含 22 个引擎拒单候选事件）。
3. 对每个事件执行严格的向后 As-of 特征提取：
   - Candles 1h/15m: taker_buy_ratio, upper_shadow_ratio, bar_return
   - Metrics 5m: retail_account_ls_ratio, top_position_ls_ratio, count_toptrader_ls_ratio, sum_taker_ls_vol_ratio (最大陈旧 30m)
   - Funding: 已结算 funding_rate (最大陈旧 12h)
   - 日线趋势: BULL / BEAR / SIDEWAYS
   - 微观路径: MAE / MFE / EARLY_ADVERSE / MFE_PULLBACK
   - 样本分期: TRAIN (2021-12~2023-12) / VAL (2024) / HOLDOUT (2025~2026)
"""
from __future__ import annotations

import glob
import json
import os
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

ROOT = Path("/Users/boos/Desktop/威科夫因子共振趋势")
OUT_DIR = ROOT / "evidence" / "FACTOR-ENTRY-01"
PARQUET_BASE = ROOT / "数据" / "市场数据" / "parquet"

METRICS_MAX_STALENESS_MS = 1800000  # 30 分钟
FUNDING_MAX_STALENESS_MS = 43200000  # 12 小时


def get_split_phase(t_ms: int) -> str:
    dt = pd.to_datetime(t_ms, unit="ms")
    if dt < pd.Timestamp("2024-01-01"):
        return "TRAIN_IN_SAMPLE"
    elif dt < pd.Timestamp("2025-01-01"):
        return "VALIDATION"
    else:
        return "HOLDOUT_TEST"


def compute_daily_regime_map(symbol: str) -> Tuple[np.ndarray, np.ndarray]:
    """为指定币种计算日线级别 EMA20/EMA50 趋势状态数组。"""
    c_files = sorted(glob.glob(str(PARQUET_BASE / f"candles/binance/um/{symbol}/4h/*.parquet")))
    if not c_files:
        return np.array([], dtype=np.int64), np.array([])
    try:
        df_4h = pd.concat([pd.read_parquet(f, columns=["open_time", "close_time", "open", "high", "low", "close"]) for f in c_files], ignore_index=True)
        df_4h = df_4h.sort_values("open_time").drop_duplicates("open_time").reset_index(drop=True)
        df_4h["date"] = pd.to_datetime(df_4h["open_time"], unit="ms").dt.floor("1D")
        d_agg = df_4h.groupby("date").agg({
            "open": "first", "high": "max", "low": "min", "close": "last", "open_time": "first"
        }).reset_index()
        d_agg["ema20"] = d_agg["close"].astype(float).ewm(span=20, adjust=False).mean()
        d_agg["ema50"] = d_agg["close"].astype(float).ewm(span=50, adjust=False).mean()
        d_agg["regime"] = "SIDEWAYS"
        bull = (d_agg["close"].astype(float) > d_agg["ema20"]) & (d_agg["ema20"] > d_agg["ema50"])
        bear = (d_agg["close"].astype(float) < d_agg["ema20"]) & (d_agg["ema20"] < d_agg["ema50"])
        d_agg.loc[bull, "regime"] = "BULL"
        d_agg.loc[bear, "regime"] = "BEAR"
        d_agg["date_ts"] = d_agg["date"].astype(np.int64) // 10**6
        return d_agg["date_ts"].to_numpy(np.int64), d_agg["regime"].to_numpy()
    except Exception:
        return np.array([], dtype=np.int64), np.array([])


def enrich_all() -> None:
    t0 = time.time()
    # 1. 读入已有的 185 个连续回测候选
    cand_file = OUT_DIR / "candidate_features.parquet"
    existing_cands = pd.read_parquet(cand_file).to_dict("records") if cand_file.exists() else []
    existing_keys = {f"{c['symbol']}_{c['snapshot_time_ms']}" for c in existing_cands}

    # 2. 读入旧 FULL_SWEEP 中的 296 笔成交并去重得到 204 笔
    with open(ROOT / "evidence/FULL_SWEEP_200x100/trades_rich.jsonl") as f:
        old_trades = [json.loads(l) for l in f]

    by_entry = defaultdict(list)
    for t in old_trades:
        by_entry[(t["symbol"], int(t["t_in"]))].append(t)

    resolved_old_trades = []
    for (sym, t_in), rows in by_entry.items():
        natural = [r for r in rows if r.get("why") != "force_close_eod"]
        if natural:
            best_r = natural[0]
            best_r["has_truncated_twin"] = len(rows) > 1
        else:
            best_r = rows[0]
            best_r["has_truncated_twin"] = False
        resolved_old_trades.append(best_r)

    print(f"Loaded existing candidates: {len(existing_cands)}")
    print(f"Loaded unique resolved old trades: {len(resolved_old_trades)}")

    # 3. 整理全部标的集合
    all_syms = sorted(set([c["symbol"] for c in existing_cands] + [t["symbol"] for t in resolved_old_trades]))
    print(f"Total unique symbols across both sets: {len(all_syms)}")

    # 为每个标的缓存 metrics, funding, regime, 1h/15m candles
    data_cache: Dict[str, dict] = {}
    print("Pre-loading data cache for symbols...")
    for sym in all_syms:
        # metrics
        m_files = sorted(glob.glob(str(PARQUET_BASE / f"metrics/binance/um/{sym}/*.parquet")))
        df_m = pd.DataFrame()
        if m_files:
            try:
                df_m = pd.concat([pd.read_parquet(f) for f in m_files], ignore_index=True)
                df_m = df_m.sort_values("ts").drop_duplicates("ts").reset_index(drop=True)
            except Exception:
                pass

        # funding
        f_file = PARQUET_BASE / f"funding/binance/um/{sym}.parquet"
        df_f = pd.DataFrame()
        if f_file.exists():
            try:
                df_f = pd.read_parquet(f_file)
                f_time_col = "funding_time" if "funding_time" in df_f.columns else "time_ms"
                df_f["f_time"] = df_f[f_time_col].astype(np.int64)
                df_f = df_f.sort_values("f_time").drop_duplicates("f_time").reset_index(drop=True)
            except Exception:
                pass

        # 1h candles
        c1h_files = sorted(glob.glob(str(PARQUET_BASE / f"candles/binance/um/{sym}/1h/*.parquet")))
        df_1h = pd.DataFrame()
        if c1h_files:
            try:
                df_1h = pd.concat([pd.read_parquet(f) for f in c1h_files], ignore_index=True)
                df_1h = df_1h.sort_values("open_time").drop_duplicates("open_time").reset_index(drop=True)
                for col in ["open", "high", "low", "close", "volume", "quote_volume", "taker_buy_volume", "taker_buy_quote_volume"]:
                    if col in df_1h.columns:
                        df_1h[col] = pd.to_numeric(df_1h[col], errors="coerce")
            except Exception:
                pass

        # 15m candles
        c15_files = sorted(glob.glob(str(PARQUET_BASE / f"candles/binance/um/{sym}/15m/*.parquet")))
        df_15 = pd.DataFrame()
        if c15_files:
            try:
                df_15 = pd.concat([pd.read_parquet(f, columns=["open_time", "close_time", "open", "high", "low", "close"]) for f in c15_files], ignore_index=True)
                df_15 = df_15.sort_values("open_time").drop_duplicates("open_time").reset_index(drop=True)
                for col in ["open", "high", "low", "close"]:
                    df_15[col] = pd.to_numeric(df_15[col], errors="coerce")
            except Exception:
                pass

        # regime
        reg_ts, reg_vals = compute_daily_regime_map(sym)

        data_cache[sym] = {
            "df_m": df_m,
            "df_f": df_f,
            "df_1h": df_1h,
            "df_15": df_15,
            "reg_ts": reg_ts,
            "reg_vals": reg_vals
        }

    # 4. 构建统一的全部唯一事件列表
    unified_records: List[dict] = []
    # 首先把已有的 candidates 纳入并打上分期标签
    seen_event_keys = set()
    for c in existing_cands:
        k = f"{c['symbol']}_{c['snapshot_time_ms']}"
        seen_event_keys.add(k)
        c["split_phase"] = get_split_phase(c["snapshot_time_ms"])
        c["has_truncated_twin"] = False
        unified_records.append(c)

    # 其次，遍历旧 204 笔唯一真实交易，补充尚未在 existing_cands 中的那些
    added_from_old = 0
    for ot in resolved_old_trades:
        sym = ot["symbol"]
        t_in = int(ot["t_in"])
        # 信号通常在 t_in 之前 15m ~ 1h 产生，我们寻找对应的 1h K 线结束时间
        t_snap = t_in - 900000  # 假设为前一根 15m 收盘 (即入场 bar 开盘前)
        k = f"{sym}_{t_snap}"
        # 检查是否已在 unified_records 中（根据 symbol 和大致进场时间）
        matched = False
        for rec in unified_records:
            if rec["symbol"] == sym and abs(rec.get("entry_time", 0) - t_in) <= 3600000:
                rec["has_truncated_twin"] = ot.get("has_truncated_twin", False)
                matched = True
                break

        if not matched:
            added_from_old += 1
            cache = data_cache.get(sym, {})
            df_m = cache.get("df_m", pd.DataFrame())
            df_f = cache.get("df_f", pd.DataFrame())
            df_1h = cache.get("df_1h", pd.DataFrame())
            df_15 = cache.get("df_15", pd.DataFrame())
            reg_ts = cache.get("reg_ts", np.array([]))
            reg_vals = cache.get("reg_vals", np.array([]))

            # 突破 1h K 线对齐 (取 t_in 之前最近闭合的 1h K 线)
            tb_ratio = np.nan
            tb_quote_ratio = np.nan
            taker_ls = np.nan
            upper_shadow = np.nan
            bar_ret = np.nan
            breakout_atr_dist = np.nan

            if not df_1h.empty:
                sub_1h = df_1h[df_1h["close_time"] <= t_in]
                if not sub_1h.empty:
                    last_1h = sub_1h.iloc[-1]
                    o_p = float(last_1h["open"])
                    h_p = float(last_1h["high"])
                    l_p = float(last_1h["low"])
                    c_p = float(last_1h["close"])
                    v_1h = float(last_1h["volume"])
                    qv_1h = float(last_1h.get("quote_volume", 0.0))
                    tb_v = float(last_1h.get("taker_buy_volume", np.nan))
                    tb_qv = float(last_1h.get("taker_buy_quote_volume", np.nan))

                    if v_1h > 0 and pd.notna(tb_v):
                        tb_ratio = tb_v / v_1h
                        taker_ls = tb_v / max(v_1h - tb_v, 1e-8)
                    if qv_1h > 0 and pd.notna(tb_qv):
                        tb_quote_ratio = tb_qv / qv_1h

                    hl_range = max(h_p - l_p, 1e-8)
                    upper_shadow = (h_p - max(o_p, c_p)) / hl_range
                    bar_ret = (c_p - o_p) / max(o_p, 1e-8)

            # As-of 对齐 Metrics
            m_retail_ratio = np.nan
            m_top_pos_ratio = np.nan
            m_top_acc_ratio = np.nan
            m_taker_vol_ratio = np.nan
            metrics_status = "MISSING"

            if not df_m.empty:
                m_times = df_m["ts"].to_numpy(np.int64)
                idx_m = np.searchsorted(m_times, t_in, side="right") - 1
                if idx_m >= 0:
                    m_match_t = m_times[idx_m]
                    if (t_in - m_match_t) <= METRICS_MAX_STALENESS_MS:
                        m_row = df_m.iloc[idx_m]
                        m_retail_ratio = float(m_row["retail_account_ls_ratio"]) if pd.notna(m_row["retail_account_ls_ratio"]) else np.nan
                        m_top_pos_ratio = float(m_row["top_position_ls_ratio"]) if pd.notna(m_row["top_position_ls_ratio"]) else np.nan
                        m_top_acc_ratio = float(m_row["count_toptrader_long_short_ratio"]) if pd.notna(m_row["count_toptrader_long_short_ratio"]) else np.nan
                        m_taker_vol_ratio = float(m_row["sum_taker_long_short_vol_ratio"]) if pd.notna(m_row["sum_taker_long_short_vol_ratio"]) else np.nan
                        metrics_status = "VALID"
                    else:
                        metrics_status = "STALE"

            # As-of 对齐 Funding
            funding_rate = np.nan
            funding_status = "MISSING"
            if not df_f.empty:
                f_times = df_f["f_time"].to_numpy(np.int64)
                idx_f = np.searchsorted(f_times, t_in, side="right") - 1
                if idx_f >= 0:
                    f_match_t = f_times[idx_f]
                    if (t_in - f_match_t) <= FUNDING_MAX_STALENESS_MS:
                        funding_rate = float(df_f.iloc[idx_f]["funding_rate"])
                        funding_status = "VALID"
                    else:
                        funding_status = "STALE"

            # 日线 Regime
            snap_day_ts = pd.to_datetime(t_in, unit="ms").floor("1D").value // 10**6
            idx_r = np.searchsorted(reg_ts, snap_day_ts, side="left") - 1
            market_regime = reg_vals[idx_r] if idx_r >= 0 else "UNKNOWN"

            # 前向收益
            fwd_ret_15m = np.nan
            fwd_ret_1h = np.nan
            fwd_ret_4h = np.nan
            if not df_15.empty:
                t15_times = df_15["open_time"].to_numpy(np.int64)
                idx_15 = np.searchsorted(t15_times, t_in, side="left")
                if idx_15 < len(df_15):
                    base_p = float(df_15.iloc[idx_15]["open"])
                    if base_p > 0:
                        if idx_15 + 1 < len(df_15):
                            fwd_ret_15m = (float(df_15.iloc[idx_15 + 1]["close"]) - base_p) / base_p
                        if idx_15 + 4 < len(df_15):
                            fwd_ret_1h = (float(df_15.iloc[idx_15 + 4]["close"]) - base_p) / base_p
                        if idx_15 + 16 < len(df_15):
                            fwd_ret_4h = (float(df_15.iloc[idx_15 + 16]["close"]) - base_p) / base_p

            # 微观路径与分类
            mfe_pct = float(ot.get("mfe", 0.0))
            mae_pct = float(ot.get("mae", 0.0))
            r_pct = float(ot.get("r_pct", 0.05))
            mfe_r = mfe_pct / max(r_pct, 1e-6)
            mae_r = mae_pct / max(r_pct, 1e-6)
            net_pnl = float(ot.get("net", 0.0))

            if mae_r >= 0.8 and mfe_r < 0.5:
                adverse_category = "EARLY_ADVERSE"
            elif mfe_r >= 1.5 and net_pnl <= 0:
                adverse_category = "MFE_PULLBACK"
            elif mfe_r >= 1.5 and net_pnl > 0:
                adverse_category = "TREND_WINNER"
            else:
                adverse_category = "CHOPPY_OR_OTHER"

            rec = {
                "event_id": f"{sym}_{t_in}",
                "candidate_id": f"old_cand_{sym}_{t_in}",
                "symbol": sym,
                "snapshot_time_ms": t_snap,
                "datetime_utc": pd.to_datetime(t_in, unit="ms").strftime("%Y-%m-%d %H:%M:%S"),
                "box_high": float(ot.get("p_in", 0.0)),
                "box_low": float(ot.get("stop_px", 0.0)),
                "box_atr_4h": np.nan,
                "box_er": np.nan,
                "volume_ratio_1h": np.nan,
                "oi_growth_6h": np.nan,
                "initial_stop_price": float(ot.get("stop_px", 0.0)),
                "initial_risk_r": float(ot.get("r_abs", 0.0)),
                "taker_buy_ratio_1h": tb_ratio,
                "taker_buy_quote_ratio_1h": tb_quote_ratio,
                "taker_ls_ratio_1h": taker_ls,
                "upper_shadow_ratio_1h": upper_shadow,
                "bar_return_1h": bar_ret,
                "breakout_atr_dist": breakout_atr_dist,
                "metrics_status": metrics_status,
                "retail_account_ls_ratio": m_retail_ratio,
                "top_position_ls_ratio": m_top_pos_ratio,
                "count_toptrader_ls_ratio": m_top_acc_ratio,
                "sum_taker_ls_vol_ratio_5m": m_taker_vol_ratio,
                "funding_status": funding_status,
                "funding_rate": funding_rate,
                "market_regime": market_regime,
                "exec_status": "FILLED",
                "reject_reason": "",
                "entry_time": t_in,
                "exit_time": int(ot.get("t_out", 0)),
                "p_entry": float(ot.get("p_in", 0.0)),
                "p_exit": float(ot.get("p_out", 0.0)),
                "net_pnl": net_pnl,
                "gross_pnl": float(ot.get("gross", 0.0)),
                "fees": float(ot.get("fees", 0.0)),
                "funding": float(ot.get("funding", 0.0)),
                "exit_why": str(ot.get("why", "")),
                "fwd_ret_15m": fwd_ret_15m,
                "fwd_ret_1h": fwd_ret_1h,
                "fwd_ret_4h": fwd_ret_4h,
                "trade_mfe_pct": mfe_pct,
                "trade_mae_pct": mae_pct,
                "mfe_r": mfe_r,
                "mae_r": mae_r,
                "adverse_category": adverse_category,
                "split_phase": get_split_phase(t_in),
                "has_truncated_twin": ot.get("has_truncated_twin", False)
            }
            unified_records.append(rec)

    print(f"Added {added_from_old} unique events from old sweep that were not in continuous candidates.")
    print(f"Total unified candidate/entry events: {len(unified_records)}")

    df_unified = pd.DataFrame(unified_records)
    # 按入场时间/快照时间排序
    df_unified["sort_t"] = df_unified["entry_time"].fillna(df_unified["snapshot_time_ms"])
    df_unified = df_unified.sort_values(["sort_t", "symbol"]).reset_index(drop=True)
    df_unified = df_unified.drop(columns=["sort_t"])

    # 落地最终的 candidate_features
    df_unified.to_parquet(OUT_DIR / "candidate_features.parquet", index=False)
    df_unified.to_csv(OUT_DIR / "candidate_features.csv", index=False)
    print(f"Successfully saved unified dataset to candidate_features.parquet and .csv ({len(df_unified)} rows)")

    # 打印覆盖与分布
    filled = df_unified[df_unified["exec_status"] == "FILLED"]
    print(f"Total Events: {len(df_unified)}")
    print(f"Filled Trades: {len(filled)}")
    print(f"Engine Rejects: {len(df_unified) - len(filled)}")
    print("Split Phase Distribution:")
    print(df_unified["split_phase"].value_counts())
    print("Market Regime Distribution:")
    print(df_unified["market_regime"].value_counts())
    print("Metrics Status Distribution:")
    print(df_unified["metrics_status"].value_counts())
    print("Adverse Category Distribution (Filled):")
    print(filled["adverse_category"].value_counts())


if __name__ == "__main__":
    enrich_all()
