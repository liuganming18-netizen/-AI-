# -*- coding: utf-8 -*-
"""工单 FACTOR-ENTRY-01 - 第二阶段：唯一候选与入场事件池提取、微观特征计算与后验路径归因。

执行重点：
1. 建立唯一事件体系：消除旧 296 笔因重叠窗口产生的截断伪重复。
2. 提取同一冻结基线下 152 个标的的完整候选事件（含引擎撤单与未成交候选）。
3. 关联主动成交特征 (candles)、持仓与多空微观结构 (metrics as-of 对齐)、资金费率 (funding as-of 对齐)、日线趋势状态 (Regime)。
4. 计算入场后微观路径：15m/1h/4h 收益、真实 MAE/MFE 及先后顺序，解耦「入场即弱」与「浮盈回吐」。
5. 输出 candidate_features.parquet 和 candidate_features.csv 到本证据目录。
"""
from __future__ import annotations

import concurrent.futures as cf
import glob
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

ROOT = Path("/Users/boos/Desktop/威科夫因子共振趋势")
for p in (str(ROOT), str(ROOT / "策略")):
    if p not in sys.path:
        sys.path.insert(0, p)

from WyckoffMaster.event_trend_v0_1.config import EventTrendConfig
from WyckoffMaster.event_trend_v0_1.contracts import CandidateSnapshot
from WyckoffMaster.event_trend_v0_1.engine_adapter import run_event_trend_backtest
from WyckoffMaster.event_trend_v0_1.features import compute_wilder_atr_series
from WyckoffMaster.event_trend_v0_1.runner import EventTrendRunner

OUT_DIR = ROOT / "evidence" / "FACTOR-ENTRY-01"
PARQUET_BASE = ROOT / "数据" / "市场数据" / "parquet"

START_DATE = "2021-12-01"
END_DATE = "2026-10-18"

# 最大陈旧时间限制 (ms)
METRICS_MAX_STALENESS_MS = 1800000  # 30 分钟
FUNDING_MAX_STALENESS_MS = 43200000  # 12 小时


def compute_daily_regime(df_4h: pd.DataFrame) -> pd.DataFrame:
    """由 4h K 线聚合日线并计算 EMA20 与 EMA50 状态。"""
    df = df_4h.copy()
    df["date"] = pd.to_datetime(df["open_time"], unit="ms").dt.floor("1D")
    d_agg = df.groupby("date").agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
        "open_time": "first"
    }).reset_index()
    d_agg["ema20"] = d_agg["close"].ewm(span=20, adjust=False).mean()
    d_agg["ema50"] = d_agg["close"].ewm(span=50, adjust=False).mean()
    d_agg["regime"] = "SIDEWAYS"
    bull_mask = (d_agg["close"] > d_agg["ema20"]) & (d_agg["ema20"] > d_agg["ema50"])
    bear_mask = (d_agg["close"] < d_agg["ema20"]) & (d_agg["ema20"] < d_agg["ema50"])
    d_agg.loc[bull_mask, "regime"] = "BULL"
    d_agg.loc[bear_mask, "regime"] = "BEAR"
    return d_agg[["date", "close", "ema20", "ema50", "regime"]]


def extract_features_for_symbol(symbol: str) -> List[dict]:
    """单标的执行：连续时段回测、提取所有候选事件及微观特征。"""
    runner = EventTrendRunner()
    try:
        df_4h, df_1h, df_15m, oi_ts, oi_vals, funding = runner.load_symbol_data(
            symbol, start_date=START_DATE, end_date=END_DATE, allow_auto_window=False
        )
    except Exception as exc:
        return []

    if len(df_15m) < 500 or len(df_1h) < 120:
        return []

    # 1. 状态机提取候选事件
    try:
        sm = runner.run_state_machine_simulation(symbol, df_4h, df_1h, oi_ts, oi_vals)
    except Exception:
        return []

    if not sm.candidates:
        return []

    # 2. 驱动引擎撮合（获取真实入场成交与拒单）
    cfg = EventTrendConfig(initial_equity=2000.0)
    spec = runner._resolve_spec(symbol, df_15m, cfg)
    res = run_event_trend_backtest(
        symbol=symbol, df_1h=df_1h, df_15m=df_15m, candidates=sm.candidates,
        funding_events=funding, df_4h=df_4h, variant="A",
        initial_equity=float(cfg.initial_equity), config=cfg,
        instrument_spec=spec,
        price_step=(None if spec is not None else "0.000000000001")
    )

    er = res.get("engine_result") or {}
    trades = er.get("trades") or []
    strat_rejects = er.get("rejected_orders") or []
    exec_rejects = (er.get("precision") or {}).get("rejects") or []

    # 索引 trades 按 entry_time
    trade_map: Dict[int, dict] = {}
    for t in trades:
        trade_map[int(t["entry_time"])] = t

    # 索引 rejects 按 candidate snapshot_time_ms (大致对应)
    # 策略挂单取消
    strat_rej_map: Dict[int, str] = {}
    for r in strat_rejects:
        t_req = int(r.get("request_time", 0))
        strat_rej_map[t_req] = str(r.get("reason", "unknown_strat_reject"))

    # 3. 预先加载并索引该标的的 Metrics 数据 (5m)
    metrics_files = sorted(glob.glob(str(PARQUET_BASE / "metrics" / "binance" / "um" / symbol / "*.parquet")))
    df_metrics = pd.DataFrame()
    if metrics_files:
        try:
            df_metrics = pd.concat([pd.read_parquet(f) for f in metrics_files], ignore_index=True)
            df_metrics = df_metrics.sort_values("ts").drop_duplicates("ts").reset_index(drop=True)
        except Exception:
            df_metrics = pd.DataFrame()

    m_ts = df_metrics["ts"].to_numpy(np.int64) if not df_metrics.empty else np.array([], dtype=np.int64)

    # 4. 预先加载并索引 Funding 数据
    funding_arr_time = np.array([f.get("time_ms", f.get("funding_time", 0)) for f in funding], dtype=np.int64) if funding else np.array([], dtype=np.int64)
    funding_arr_rate = np.array([f.get("rate", f.get("funding_rate", 0.0)) for f in funding], dtype=np.float64) if funding else np.array([], dtype=np.float64)

    # 5. 计算日线 Regime
    df_regime = compute_daily_regime(df_4h)
    df_regime["date_ts"] = df_regime["date"].astype(np.int64) // 10**6
    regime_ts = df_regime["date_ts"].to_numpy(np.int64)
    regimes = df_regime["regime"].to_numpy()

    # 6. 1h K 线索引，用于提取突破根特征
    df_1h_indexed = df_1h.set_index("open_time")
    df_15m_indexed = df_15m.set_index("open_time")
    df_15m_times = df_15m["open_time"].to_numpy(np.int64)

    candidate_records: List[dict] = []

    for c in sm.candidates:
        t_snap = int(c.snapshot_time_ms)
        event_id = f"{symbol}_{t_snap}"

        # 突破 K 线对齐 (1h K 线，以 t_snap - 3600000 对应的 open_time 为突破根)
        t_breakout_open = t_snap - 3600000
        row_1h = df_1h_indexed.loc[t_breakout_open] if t_breakout_open in df_1h_indexed.index else None

        tb_vol = np.nan
        vol_1h = np.nan
        tb_quote = np.nan
        quote_vol_1h = np.nan
        tb_ratio = np.nan
        tb_quote_ratio = np.nan
        taker_ls = np.nan
        upper_shadow = np.nan
        bar_ret = np.nan
        breakout_atr_dist = np.nan

        if row_1h is not None:
            o_p = float(row_1h["open"])
            h_p = float(row_1h["high"])
            l_p = float(row_1h["low"])
            c_p = float(row_1h["close"])
            vol_1h = float(row_1h["volume"])
            quote_vol_1h = float(row_1h["quote_volume"]) if "quote_volume" in row_1h else np.nan
            tb_vol = float(row_1h["taker_buy_volume"]) if "taker_buy_volume" in row_1h and pd.notna(row_1h["taker_buy_volume"]) else np.nan
            tb_quote = float(row_1h["taker_buy_quote_volume"]) if "taker_buy_quote_volume" in row_1h and pd.notna(row_1h["taker_buy_quote_volume"]) else np.nan

            if vol_1h > 0 and pd.notna(tb_vol):
                tb_ratio = tb_vol / vol_1h
                taker_ls = tb_vol / max(vol_1h - tb_vol, 1e-8)
            if quote_vol_1h > 0 and pd.notna(tb_quote):
                tb_quote_ratio = tb_quote / quote_vol_1h

            hl_range = max(h_p - l_p, 1e-8)
            upper_shadow = (h_p - max(o_p, c_p)) / hl_range
            bar_ret = (c_p - o_p) / max(o_p, 1e-8)
            if c.box_atr_a > 0:
                breakout_atr_dist = (c_p - c.box_high_h) / c.box_atr_a

        # As-of 对齐 Metrics (t <= t_snap)
        m_retail_ratio = np.nan
        m_top_pos_ratio = np.nan
        m_top_acc_ratio = np.nan
        m_taker_vol_ratio = np.nan
        metrics_status = "MISSING"

        if len(m_ts) > 0:
            idx_m = np.searchsorted(m_ts, t_snap, side="right") - 1
            if idx_m >= 0:
                m_match_t = m_ts[idx_m]
                if (t_snap - m_match_t) <= METRICS_MAX_STALENESS_MS:
                    m_row = df_metrics.iloc[idx_m]
                    m_retail_ratio = float(m_row["retail_account_ls_ratio"]) if pd.notna(m_row["retail_account_ls_ratio"]) else np.nan
                    m_top_pos_ratio = float(m_row["top_position_ls_ratio"]) if pd.notna(m_row["top_position_ls_ratio"]) else np.nan
                    m_top_acc_ratio = float(m_row["count_toptrader_long_short_ratio"]) if pd.notna(m_row["count_toptrader_long_short_ratio"]) else np.nan
                    m_taker_vol_ratio = float(m_row["sum_taker_long_short_vol_ratio"]) if pd.notna(m_row["sum_taker_long_short_vol_ratio"]) else np.nan
                    metrics_status = "VALID"
                else:
                    metrics_status = "STALE"

        # As-of 对齐 Funding (funding_time <= t_snap)
        funding_rate = np.nan
        funding_status = "MISSING"
        if len(funding_arr_time) > 0:
            idx_f = np.searchsorted(funding_arr_time, t_snap, side="right") - 1
            if idx_f >= 0:
                f_match_t = funding_arr_time[idx_f]
                if (t_snap - f_match_t) <= FUNDING_MAX_STALENESS_MS:
                    funding_rate = float(funding_arr_rate[idx_f])
                    funding_status = "VALID"
                else:
                    funding_status = "STALE"

        # 日线 Regime 对齐 (取 t_snap 所在当前日的前一日闭合值)
        snap_day_ts = pd.to_datetime(t_snap, unit="ms").floor("1D").value // 10**6
        idx_r = np.searchsorted(regime_ts, snap_day_ts, side="left") - 1
        market_regime = regimes[idx_r] if idx_r >= 0 else "UNKNOWN"

        # 引擎成交与执行状态匹配
        # 挂单是在候选产生后的第一根 15m K 线进行 (通常为 t_snap)
        matched_trade = None
        for t_entry in trade_map:
            # 入场通常在 t_snap 之后的 15m ~ 60m 内
            if 0 <= (t_entry - t_snap) <= 7200000:
                matched_trade = trade_map[t_entry]
                break

        exec_status = "NOT_ENTERED"
        reject_reason = ""
        trade_net = 0.0
        trade_gross = 0.0
        trade_fees = 0.0
        trade_funding = 0.0
        exit_why = ""
        p_entry = np.nan
        p_exit = np.nan
        t_entry_act = np.nan
        t_exit_act = np.nan
        trade_mfe_pct = np.nan
        trade_mae_pct = np.nan

        if matched_trade is not None:
            exec_status = "FILLED"
            trade_net = float(matched_trade.get("net_pnl", 0.0))
            trade_gross = float(matched_trade.get("gross_pnl", 0.0))
            trade_fees = float(matched_trade.get("total_fees", 0.0))
            trade_funding = float(matched_trade.get("total_funding", 0.0))
            exit_why = str(matched_trade.get("exit_reason", ""))
            p_entry = float(matched_trade.get("entry_price", 0.0))
            p_exit = float(matched_trade.get("exit_price", 0.0))
            t_entry_act = int(matched_trade.get("entry_time", 0))
            t_exit_act = int(matched_trade.get("exit_time", 0))
        else:
            # 检查是否有策略拒单
            for t_req, reason in strat_rej_map.items():
                if 0 <= (t_req - t_snap) <= 7200000:
                    exec_status = "ENGINE_REJECTED"
                    reject_reason = reason
                    break
            if exec_status == "NOT_ENTERED":
                exec_status = "UNFILLED_TIMEOUT"

        # 计算未来 15m/1h/4h 前向收益以及真实 MAE/MFE 微观路径
        fwd_ret_15m = np.nan
        fwd_ret_1h = np.nan
        fwd_ret_4h = np.nan
        mae_r = np.nan
        mfe_r = np.nan
        adverse_category = "UNFILLED"

        idx_15m = np.searchsorted(df_15m_times, t_snap, side="left")
        if idx_15m < len(df_15m):
            base_p = float(df_15m.iloc[idx_15m]["open"])
            if base_p > 0:
                if idx_15m + 1 < len(df_15m):
                    fwd_ret_15m = (float(df_15m.iloc[idx_15m + 1]["close"]) - base_p) / base_p
                if idx_15m + 4 < len(df_15m):
                    fwd_ret_1h = (float(df_15m.iloc[idx_15m + 4]["close"]) - base_p) / base_p
                if idx_15m + 16 < len(df_15m):
                    fwd_ret_4h = (float(df_15m.iloc[idx_15m + 16]["close"]) - base_p) / base_p

        # 若真实成交，从实际 entry 到 exit 计算微观路径
        if exec_status == "FILLED" and pd.notna(p_entry) and p_entry > 0:
            idx_in = np.searchsorted(df_15m_times, t_entry_act, side="left")
            idx_out = np.searchsorted(df_15m_times, t_exit_act, side="right")
            sub_bars = df_15m.iloc[idx_in:idx_out]
            if len(sub_bars) > 0:
                h_max = sub_bars["high"].astype(float).max()
                l_min = sub_bars["low"].astype(float).min()
                mfe_pct = (h_max - p_entry) / p_entry
                mae_pct = (p_entry - l_min) / p_entry
                trade_mfe_pct = mfe_pct
                trade_mae_pct = mae_pct

                risk_r = float(c.initial_risk_r) if c.initial_risk_r > 0 else (c.box_atr_a if c.box_atr_a > 0 else p_entry * 0.05)
                mfe_r = (h_max - p_entry) / risk_r
                mae_r = (p_entry - l_min) / risk_r

                # 寻找 MFE 与 MAE 发生的先后顺序
                t_mfe = sub_bars.loc[sub_bars["high"].astype(float) == h_max, "open_time"].iloc[0]
                t_mae = sub_bars.loc[sub_bars["low"].astype(float) == l_min, "open_time"].iloc[0]

                # 路径归因解耦
                if t_mae <= t_mfe and mae_r >= 0.8 and mfe_r < 0.5:
                    adverse_category = "EARLY_ADVERSE"  # 入场即弱
                elif mfe_r >= 1.5 and trade_net <= 0:
                    adverse_category = "MFE_PULLBACK"   # 浮盈回吐
                elif mfe_r >= 1.5 and trade_net > 0:
                    adverse_category = "TREND_WINNER"   # 趋势盈利
                else:
                    adverse_category = "CHOPPY_OR_OTHER"

        rec = {
            "event_id": event_id,
            "candidate_id": c.candidate_id,
            "symbol": symbol,
            "snapshot_time_ms": t_snap,
            "datetime_utc": pd.to_datetime(t_snap, unit="ms").strftime("%Y-%m-%d %H:%M:%S"),
            "box_high": c.box_high_h,
            "box_low": c.box_low_l,
            "box_atr_4h": c.box_atr_a,
            "box_er": c.box_er,
            "volume_ratio_1h": c.volume_ratio,
            "oi_growth_6h": c.oi_growth_6h,
            "initial_stop_price": c.initial_stop_price,
            "initial_risk_r": c.initial_risk_r,
            # 微观成交流
            "taker_buy_ratio_1h": tb_ratio,
            "taker_buy_quote_ratio_1h": tb_quote_ratio,
            "taker_ls_ratio_1h": taker_ls,
            "upper_shadow_ratio_1h": upper_shadow,
            "bar_return_1h": bar_ret,
            "breakout_atr_dist": breakout_atr_dist,
            # 微观持仓结构
            "metrics_status": metrics_status,
            "retail_account_ls_ratio": m_retail_ratio,
            "top_position_ls_ratio": m_top_pos_ratio,
            "count_toptrader_ls_ratio": m_top_acc_ratio,
            "sum_taker_ls_vol_ratio_5m": m_taker_vol_ratio,
            # 资金费率与宏观状态
            "funding_status": funding_status,
            "funding_rate": funding_rate,
            "market_regime": market_regime,
            # 执行与撮合
            "exec_status": exec_status,
            "reject_reason": reject_reason,
            "entry_time": t_entry_act,
            "exit_time": t_exit_act,
            "p_entry": p_entry,
            "p_exit": p_exit,
            "net_pnl": trade_net,
            "gross_pnl": trade_gross,
            "fees": trade_fees,
            "funding": trade_funding,
            "exit_why": exit_why,
            # 微观路径与收益
            "fwd_ret_15m": fwd_ret_15m,
            "fwd_ret_1h": fwd_ret_1h,
            "fwd_ret_4h": fwd_ret_4h,
            "trade_mfe_pct": trade_mfe_pct,
            "trade_mae_pct": trade_mae_pct,
            "mfe_r": mfe_r,
            "mae_r": mae_r,
            "adverse_category": adverse_category
        }
        candidate_records.append(rec)

    return candidate_records


def run_pipeline() -> None:
    t0 = time.time()
    # 读入所有在 FULL_SWEEP 中产生过交易的 152 个标的
    with open(ROOT / "evidence/FULL_SWEEP_200x100/trades_rich.jsonl") as f:
        old_trades = [json.loads(line) for line in f]
    symbols = sorted(set(t["symbol"] for t in old_trades))

    print(f"Starting Stage 2 feature pipeline for {len(symbols)} symbols...")

    all_records: List[dict] = []
    # 使用 ProcessPoolExecutor 并发处理，控制并发度为 8 以充分利用多核
    with cf.ProcessPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(extract_features_for_symbol, sym): sym for sym in symbols}
        done_cnt = 0
        for fut in cf.as_completed(futures):
            sym = futures[fut]
            done_cnt += 1
            try:
                res = fut.result()
                all_records.extend(res)
                if done_cnt % 20 == 0 or done_cnt == len(symbols):
                    print(f"[{done_cnt}/{len(symbols)}] Processed {sym}, total candidates collected: {len(all_records)}")
            except Exception as e:
                print(f"Error on {sym}: {e}")

    df_out = pd.DataFrame(all_records)
    print(f"Pipeline complete in {time.time() - t0:.2f}s. Total candidate events: {len(df_out)}")

    # 排序与落地
    if not df_out.empty:
        df_out = df_out.sort_values(["snapshot_time_ms", "symbol"]).reset_index(drop=True)

    parquet_file = OUT_DIR / "candidate_features.parquet"
    csv_file = OUT_DIR / "candidate_features.csv"
    df_out.to_parquet(parquet_file, index=False)
    df_out.to_csv(csv_file, index=False)
    print(f"Saved {len(df_out)} candidate features to {parquet_file} and {csv_file}")

    # 打印关键统计
    if not df_out.empty:
        filled = df_out[df_out["exec_status"] == "FILLED"]
        print(f"Total candidates: {len(df_out)}")
        print(f"Filled trades: {len(filled)}")
        print(f"Engine rejects / unfilled: {len(df_out) - len(filled)}")
        print("Adverse category distribution in filled trades:")
        print(filled["adverse_category"].value_counts())


if __name__ == "__main__":
    run_pipeline()
