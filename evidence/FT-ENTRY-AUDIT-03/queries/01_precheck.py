# -*- coding: utf-8 -*-
"""01 开跑前核对：可复用逐币区间 + 样本一致性 + A 是否严格 <H。

只读：
- evidence/FT-FACTOR-AUDIT-01/out/02_派生区间_200根4h预热_研究期交集_v1.csv
- evidence/FT-FACTOR-AUDIT-01/out/04_冻结研究期输入清单_v1.csv
- evidence/FT-ENTRY-02/{out,user_data}
输出：out/00_*.csv|json
"""
from __future__ import annotations

import hashlib
import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from _common import (  # noqa: E402
    AUDIT, BASELINE, CELLS_A, CELLS_C, DATA4H, ENTRY02, OUT, OUT02, ROOT, box_at,
    jdump, load_4h, load_trades, write_csv,
)

AUDIT01_OUT = os.path.join(ROOT, "evidence/FT-FACTOR-AUDIT-01/out")


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> None:
    print("== 1. 读取冻结研究期清单 ==")
    l1 = pd.read_csv(os.path.join(AUDIT01_OUT, "02_派生区间_200根4h预热_研究期交集_v1.csv"), encoding="utf-8-sig")
    l1["symbol"] = l1["symbol"].astype(str)
    print(f"全池索引 {len(l1)} 行；L1 标记 是={int((l1['L1_price_volume_candidate']=='是').sum())}")

    cfg = json.load(open(os.path.join(ENTRY02, "user_data/config.json"), encoding="utf-8"))
    whitelist = cfg["exchange"]["pair_whitelist"]
    pool = [p.split("/")[0] for p in whitelist]
    print(f"本批 74 池：{len(pool)}")

    trades = {BASELINE: load_trades(BASELINE)}
    for code, strat, _, _ in CELLS_A:
        trades[code] = load_trades(strat)
    for code, strat, _, _ in CELLS_C:
        trades[code] = load_trades(strat)

    # ── 1.1 逐币区间清单 ────────────────────────────────────────────────
    rows = []
    for sym in pool:
        sym_key = f"{sym}USDT"
        sub = l1[l1["symbol"] == sym_key]
        rec = sub.iloc[0] if len(sub) else None
        intervals = []
        if rec is not None:
            try:
                intervals = json.loads(rec["research_triple_intersection_intervals"]) if isinstance(
                    rec["research_triple_intersection_intervals"], str) else []
            except Exception:
                intervals = []
        pair = f"{sym}/USDT:USDT"
        pair_s = f"{sym}_USDT_USDT"
        n_a_trades = sum(int((trades[c]["pair"] == pair).sum()) for c, _, _, _ in CELLS_A)
        rows.append({
            "symbol": sym,
            "pair": pair,
            "in_74_pool": True,
            "L1_price_volume_candidate": (rec["L1_price_volume_candidate"] if rec is not None else "无索引"),
            "research_interval_start_utc": intervals[0]["start_utc"] if intervals else "",
            "research_interval_end_utc": intervals[0]["end_utc"] if intervals else "",
            "research_1h_bars": int(rec["research_triple_intersection_bars_1h"]) if rec is not None else 0,
            "L2_full_strategy_qualified": (rec["L2_full_strategy_qualified"] if rec is not None else "否"),
            "L2_blocking_reason": (rec["L2_blocking_reason"] if rec is not None else ""),
            "has_4h_feather": os.path.exists(os.path.join(DATA4H, f"{pair_s}-4h-futures.feather")),
            "has_1h_feather": os.path.exists(os.path.join(DATA4H, f"{pair_s}-1h-futures.feather")),
            "trades_baseline": int((trades[BASELINE]["pair"] == pair).sum()),
            "trades_A_total": n_a_trades,
        })
    pool_df = pd.DataFrame(rows)
    write_csv(pool_df, "00_逐币区间清单_74池_v1.csv")

    l1_out = l1[["symbol", "batch_no", "research_triple_intersection_intervals",
                 "research_triple_intersection_bars_1h", "L1_price_volume_candidate",
                 "L2_full_strategy_qualified", "L2_blocking_reason",
                 "oi_factor_1h_qualified_segments", "oi_factor_mapping_status"]].copy()
    write_csv(l1_out, "00_逐币区间清单_L1_241_v1.csv")

    in_l1 = int((pool_df["L1_price_volume_candidate"] == "是").sum())
    has_4h = int(pool_df["has_4h_feather"].sum())
    has_1h = int(pool_df["has_1h_feather"].sum())
    l2_pass = int((pool_df["L2_full_strategy_qualified"] == "是").sum())
    summary = {
        "denominator_pool": len(pool_df),
        "in_L1_price_volume": in_l1,
        "not_in_L1": pool_df.loc[pool_df["L1_price_volume_candidate"] != "是", "symbol"].tolist(),
        "has_4h_file": has_4h,
        "has_1h_file": has_1h,
        "L2_full_strategy_qualified": l2_pass,
        "L2_reason_set": sorted(set(pool_df["L2_blocking_reason"].fillna(""))),
        "coins_with_baseline_trades": int((pool_df["trades_baseline"] > 0).sum()),
        "coins_with_A_trades": int((pool_df["trades_A_total"] > 0).sum()),
        "note": "L1=价量数据可算；L2=完整策略资格（需 OI 1h 因子合格区间），本批 0 个标的满足",
    }
    jdump(os.path.join(OUT, "00_资格分层汇总_v1.json"), summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))

    # ── 1.2 样本一致性：配置 / 归档元数据 ────────────────────────────────
    print("== 2. 归档元数据一致性 ==")
    import zipfile
    meta_rows = []
    for code, strat, _, _ in [("BASE", BASELINE, 0, 0)] + CELLS_A + CELLS_C:
        zp = os.path.join(OUT02, f"backtest-result-{strat}.zip")
        z = zipfile.ZipFile(zp)
        inner = [n for n in z.namelist() if n.endswith(".json") and "config" not in n][0]
        d = json.loads(z.read(inner))["strategy"][strat]
        meta_rows.append({
            "code": code,
            "strategy": strat,
            "archive_sha256": sha256(zp),
            "strategy_name": d["strategy_name"],
            "timerange": d["timerange"],
            "backtest_start": d["backtest_start"],
            "backtest_end": d["backtest_end"],
            "backtest_days": d["backtest_days"],
            "timeframe": d["timeframe"],
            "trading_mode": d["trading_mode"],
            "max_open_trades": d["max_open_trades"],
            "max_open_trades_setting": d["max_open_trades_setting"],
            "stake_amount": d["stake_amount"],
            "dry_run_wallet": d["dry_run_wallet"],
            "stake_currency": d["stake_currency"],
            "total_trades": d["total_trades"],
            "rejected_signals": d["rejected_signals"],
            "market_change": round(float(d["market_change"]), 6),
            "p_value": d.get("p_value"),
            "final_balance": round(float(d["final_balance"]), 6),
            "starting_balance": d["starting_balance"],
            "pairlist_n": len(d.get("pairlist", []) or []),
            "trade_csv_rows": int(len(trades[BASELINE] if code == "BASE" else trades[code])),
            "archive_trades": len(d["trades"]),
        })
    meta_df = pd.DataFrame(meta_rows)
    write_csv(meta_df, "00_归档元数据一致性_v1.csv")

    consist = {
        "timerange_unique": sorted(meta_df["timerange"].unique().tolist()),
        "backtest_start_unique": sorted(meta_df["backtest_start"].unique().tolist()),
        "backtest_end_unique": sorted(meta_df["backtest_end"].unique().tolist()),
        "timeframe_unique": sorted(meta_df["timeframe"].unique().tolist()),
        "max_open_trades_unique": sorted(meta_df["max_open_trades"].unique().tolist()),
        "stake_amount_unique": sorted(meta_df["stake_amount"].unique().tolist()),
        "dry_run_wallet_unique": sorted(meta_df["dry_run_wallet"].unique().tolist()),
        "market_change_unique": sorted(meta_df["market_change"].unique().tolist()),
        "pairlist_n_unique": sorted(meta_df["pairlist_n"].unique().tolist()),
        "csv_vs_archive_rows_mismatch": meta_df.loc[
            meta_df["trade_csv_rows"] != meta_df["archive_trades"], ["code", "trade_csv_rows", "archive_trades"]
        ].to_dict("records"),
        "config_sha256": sha256(os.path.join(ENTRY02, "user_data/config.json")),
        "config_whitelist_n": len(whitelist),
        "config_whitelist_sha256": hashlib.sha256(
            json.dumps(whitelist, ensure_ascii=False).encode("utf-8")).hexdigest(),
        "strategy_base_sha256": sha256(os.path.join(ENTRY02, "user_data/strategies/WyckoffParamExplorationBase.py")),
    }
    jdump(os.path.join(OUT, "00_样本一致性_v1.json"), consist)
    print(json.dumps(consist, ensure_ascii=False, indent=2))

    # ── 1.3 数据文件哈希（74 池 4h/1h） ────────────────────────────────
    print("== 3. 数据文件指纹（74 池 4h+1h） ==")
    hash_rows = []
    for sym in pool:
        for tf in ("4h", "1h"):
            p = os.path.join(DATA4H, f"{sym}_USDT_USDT-{tf}-futures.feather")
            if os.path.exists(p):
                hash_rows.append({"symbol": sym, "timeframe": tf, "path": os.path.relpath(p, ROOT),
                                  "size_bytes": os.path.getsize(p), "sha256": sha256(p)})
    hash_df = pd.DataFrame(hash_rows)
    write_csv(hash_df, "00_数据文件指纹_v1.csv")
    print(f"指纹文件 {len(hash_df)} 个")

    # ── 1.4 A 是否严格 <H（用 4h/1h 数据独立回算 H/L/A 与触发收盘价） ────
    print("== 4. A 严格 <H 核验 ==")
    h1_cache: dict = {}

    def h1_close_at(pair_s: str, close_ms: int) -> float:
        if pair_s not in h1_cache:
            p = os.path.join(DATA4H, f"{pair_s}-1h-futures.feather")
            if not os.path.exists(p):
                h1_cache[pair_s] = None
            else:
                d1 = pd.read_feather(p)
                d1["open_ms"] = pd.to_datetime(d1["date"], utc=True).astype("int64")
                h1_cache[pair_s] = d1.set_index("open_ms")["close"]
        s = h1_cache[pair_s]
        if s is None:
            return float("nan")
        key = close_ms - 3600 * 1000
        return float(s.loc[key]) if key in s.index else float("nan")

    strict_rows = []
    detail_rows = []
    for code, strat, delta, kvol in [("BASE", BASELINE, 0.0, 1.5)] + CELLS_A:
        df = trades[BASELINE] if code == "BASE" else trades[code]
        for _, r in df.iterrows():
            box = box_at(r["pair_s"], int(r["box_ms"]))
            if box is None:
                detail_rows.append({"code": code, "pair": r["pair"], "event_id": r["event_id"],
                                    "status": "box_unavailable"})
                continue
            H, L, A = box["H"], box["L"], box["A"]
            target = H - delta * A
            min_entry = (target - 0.1 * A) if code != "BASE" else H
            close_p = h1_close_at(r["pair_s"], int(r["sig_close_ms"]))
            # P0 反推：stake = risk_budget / r_dist * entry  =>  r_dist = risk_budget*entry/stake
            r_dist = r["equity_before"] * 0.005 * float(r["open_rate"]) / float(r["stake_amount"])
            p0 = float(r["open_rate"]) - r_dist
            lr = p0 + 0.1 * A
            detail_rows.append({
                "code": code,
                "strategy": strat,
                "pair": r["pair"],
                "event_id": r["event_id"],
                "box_ms": int(r["box_ms"]),
                "sig_close_ms": int(r["sig_close_ms"]),
                "H": H, "L": L, "A": A,
                "delta": delta,
                "target_level": target,
                "min_entry_price": min_entry,
                "sig_close_price": close_p,
                "sig_close_minus_H_in_ATR": (close_p - H) / A if np.isfinite(close_p) else float("nan"),
                "entry_gap_in_ATR": (float(r["open_rate"]) - close_p) / A if np.isfinite(close_p) else float("nan"),
                "entry_beyond_max_entry": bool((float(r["open_rate"]) - close_p) / A > 0.5 + 1e-6) if np.isfinite(close_p) else None,
                "trigger_close_at_or_above_H": bool(close_p >= H) if np.isfinite(close_p) else None,
                "open_rate": float(r["open_rate"]),
                "open_rate_minus_H": float(r["open_rate"]) - H,
                "open_rate_minus_H_in_ATR": (float(r["open_rate"]) - H) / A,
                "at_or_above_H": bool(float(r["open_rate"]) >= H),
                "above_breakout_line": bool(float(r["open_rate"]) > H + 0.1 * A),
                "below_min_entry": bool(float(r["open_rate"]) < min_entry - 1e-9),
                "p0_implied": p0,
                "lr_implied": lr,
                "lr_in_band": bool(L - 0.002 * A <= lr <= L + 0.5 * A + 0.002 * A),
                "stake_capped_30pct": bool(float(r["stake_amount"]) >= r["equity_before"] * 0.30 - 1e-9),
                "profit_abs": float(r["profit_abs"]),
            })
    det = pd.DataFrame(detail_rows)
    write_csv(det, "01_A入场价与箱顶H逐笔_v1.csv")

    for code, strat, delta, kvol in [("BASE", BASELINE, 0.0, 1.5)] + CELLS_A:
        sub = det[det["code"] == code]
        n = len(sub)
        strict_rows.append({
            "code": code,
            "strategy": strat,
            "delta_atr": delta,
            "trades": n,
            "box_recomputed": int(sub["H"].notna().sum()) if "H" in sub else 0,
            "trigger_close_at_or_above_H": int((sub["trigger_close_at_or_above_H"] == True).sum()) if n else 0,
            "entries_at_or_above_H": int(sub["at_or_above_H"].sum()) if n else 0,
            "entries_above_breakout_line": int(sub["above_breakout_line"].sum()) if n else 0,
            "entries_below_min_entry": int(sub["below_min_entry"].sum()) if n else 0,
            "entries_beyond_max_entry": int((sub["entry_beyond_max_entry"] == True).sum()) if n else 0,
            "entries_at_or_above_H_pct": round(100.0 * int(sub["at_or_above_H"].sum()) / n, 2) if n else None,
            "lr_in_band": int(sub["lr_in_band"].sum()) if n else 0,
            "stake_capped_30pct": int(sub["stake_capped_30pct"].sum()) if n else 0,
            "max_sig_close_minus_H_in_ATR": round(float(sub["sig_close_minus_H_in_ATR"].max()), 4) if n else None,
            "max_open_rate_minus_H_in_ATR": round(float(sub["open_rate_minus_H_in_ATR"].max()), 4) if n else None,
            "median_open_rate_minus_H_in_ATR": round(float(sub["open_rate_minus_H_in_ATR"].median()), 4) if n else None,
            "min_open_rate_minus_H_in_ATR": round(float(sub["open_rate_minus_H_in_ATR"].min()), 4) if n else None,
        })
    strict = pd.DataFrame(strict_rows)
    write_csv(strict, "01_A严格低于H_汇总_v1.csv")
    print(strict.to_string(index=False))

    # ── 1.5 事件宇宙一致性 ──────────────────────────────────────────────
    print("== 5. 事件宇宙一致性 ==")
    base_events = set(trades[BASELINE]["event_id"])
    universe_rows = []
    for code, strat, _, _ in CELLS_A:
        ev = set(trades[code]["event_id"])
        missed = base_events - ev
        missed_pnl = float(trades[BASELINE].loc[trades[BASELINE]["event_id"].isin(missed), "profit_abs"].sum())
        universe_rows.append({
            "code": code,
            "strategy": strat,
            "unique_events": len(ev),
            "baseline_events_covered": len(base_events & ev),
            "baseline_coverage_pct": round(100.0 * len(base_events & ev) / len(base_events), 2),
            "baseline_events_missed": len(missed),
            "missed_baseline_pnl_u": round(missed_pnl, 4),
            "extra_events_not_in_baseline": len(ev - base_events),
        })
    uni = pd.DataFrame(universe_rows)
    write_csv(uni, "01_A事件覆盖_v1.csv")
    print(uni.to_string(index=False))

    jdump(os.path.join(OUT, "01_开跑前核对摘要_v1.json"), {
        "pool": summary,
        "consistency": consist,
        "strict_below_H": strict.to_dict("records"),
        "event_coverage": uni.to_dict("records"),
    })
    print("完成")


if __name__ == "__main__":
    main()
