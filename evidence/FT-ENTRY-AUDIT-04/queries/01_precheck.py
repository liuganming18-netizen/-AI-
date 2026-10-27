# -*- coding: utf-8 -*-
"""01 开跑前：版本锁定 + 样本一致性 + A 是否严格 <H + OI 门槛是否保留。

只读：
- evidence/FT-FACTOR-AUDIT-01/out/02_派生区间_200根4h预热_研究期交集_v1.csv
- evidence/FT-ENTRY-03/{out,user_data,code}
- evidence/FT-ENTRY-02/out（仅用于跨批基线一致性）
- evidence/FT-ENTRY-AUDIT-03/out（仅用于数据指纹交叉比对）
输出：out/00_* 01_*
"""
from __future__ import annotations

import difflib
import hashlib
import json
import os
import sys
import zipfile

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from _common import (  # noqa: E402
    AUDIT03_OUT, BASELINE, CELLS_A, DATA4H, DROPPED_FROM_74, ENTRY02, ENTRY03,
    OUT, OUT02, OUT03, ROOT, box_at, jdump, load_trades, write_csv,
)

AUDIT01_OUT = os.path.join(ROOT, "evidence/FT-FACTOR-AUDIT-01/out")
BASE_STRATEGY = os.path.join(ENTRY03, "user_data/strategies/WyckoffParamExplorationBase.py")
CFG_JSON = os.path.join(ENTRY03, "user_data/config.json")
EVENT_CONFIG = os.path.join(ROOT, "策略/WyckoffMaster/event_trend_v0_1/config.py")
OI_DIR = os.path.join(ROOT, "evidence/FT-FACTOR-03/user_data/data/binance/futures")

ALL_STRATS = [("BASE", BASELINE, "Baseline", None, None)] + [
    (c, s, "A", d, k) for c, s, d, k in CELLS_A
]


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_archive(strat: str) -> dict:
    zp = os.path.join(OUT03, f"backtest-result-{strat}.zip")
    with zipfile.ZipFile(zp) as z:
        inner = [n for n in z.namelist() if n.endswith(".json") and "config" not in n][0]
        return json.loads(z.read(inner))["strategy"][strat]


def main() -> None:
    print("=" * 78)
    print("01-1 版本锁定表")
    print("=" * 78)
    cfg = json.load(open(CFG_JSON, encoding="utf-8"))
    whitelist = cfg["exchange"]["pair_whitelist"]
    pool = [p.split("/")[0] for p in whitelist]
    print(f"FT-ENTRY-03 白名单 {len(pool)} 个标的")

    cfg02 = json.load(open(os.path.join(ENTRY02, "user_data/config.json"), encoding="utf-8"))
    pool02 = [p.split("/")[0] for p in cfg02["exchange"]["pair_whitelist"]]
    removed = sorted(set(pool02) - set(pool))
    added = sorted(set(pool) - set(pool02))
    print(f"FT-ENTRY-02 白名单 {len(pool02)}；相对旧版移除 {removed}；新增 {added}")

    trades = {BASELINE: load_trades(BASELINE)}
    for code, strat, _, _ in CELLS_A:
        trades[code] = load_trades(strat)

    lock_rows = []
    for code, strat, route, delta, kvol in ALL_STRATS:
        d = read_archive(strat)
        zp = os.path.join(OUT03, f"backtest-result-{strat}.zip")
        cp = os.path.join(OUT03, f"trades_{strat}.csv")
        sp = os.path.join(ENTRY03, "user_data/strategies", f"{strat}.py")
        lock_rows.append({
            "code": code,
            "strategy": strat,
            "route": route,
            "delta_entry_atr": delta,
            "k_vol_mult": kvol,
            "archive_file": os.path.basename(zp),
            "archive_sha256": sha256(zp),
            "trades_csv_sha256": sha256(cp),
            "strategy_src_sha256": sha256(sp),
            "timerange": d["timerange"],
            "backtest_start": d["backtest_start"],
            "backtest_end": d["backtest_end"],
            "backtest_days": d["backtest_days"],
            "timeframe": d["timeframe"],
            "max_open_trades": d["max_open_trades"],
            "stake_amount": d["stake_amount"],
            "dry_run_wallet": d["dry_run_wallet"],
            "trading_mode": d["trading_mode"],
            "total_trades": d["total_trades"],
            "csv_rows": int(len(trades[BASELINE] if code == "BASE" else trades[code])),
            "market_change": round(float(d["market_change"]), 6),
            "p_value": d.get("p_value"),
            "rejected_signals": d["rejected_signals"],
            "final_balance": round(float(d["final_balance"]), 6),
            "pairlist_n": len(d.get("pairlist", []) or []),
        })
    lock = pd.DataFrame(lock_rows)
    write_csv(lock, "00_版本锁定表_v1.csv")
    print(lock[["code", "strategy", "delta_entry_atr", "k_vol_mult", "total_trades", "csv_rows"]].to_string(index=False))

    # ── 1.2 唯一性核对 ─────────────────────────────────────────────────
    uniq = {}
    for col in ("timerange", "backtest_start", "backtest_end", "backtest_days", "timeframe",
                "max_open_trades", "stake_amount", "dry_run_wallet", "trading_mode",
                "market_change", "pairlist_n"):
        vals = sorted({str(v) for v in lock[col].unique()})
        uniq[col] = vals
    print("\n唯一性：", {k: v for k, v in uniq.items() if len(v) == 1})
    print("非唯一：", {k: v for k, v in uniq.items() if len(v) > 1})

    # ── 1.3 参数冻结核验（代码级 + diff 级） ────────────────────────────
    print("\n" + "=" * 78)
    print("01-2 参数冻结核验")
    print("=" * 78)
    src03 = open(BASE_STRATEGY, encoding="utf-8").read()
    src02 = open(os.path.join(ENTRY02, "user_data/strategies/WyckoffParamExplorationBase.py"), encoding="utf-8").read()
    diff = list(difflib.unified_diff(src02.splitlines(), src03.splitlines(), lineterm="", n=0))
    changed = [ln for ln in diff if (ln.startswith("+") or ln.startswith("-")) and not ln.startswith(("+++", "---"))]
    hunks = [ln for ln in diff if ln.startswith("@@")]
    print(f"FT-ENTRY-02 → FT-ENTRY-03 基类差异：{len(hunks)} 个 hunk，{len(changed)} 行增删")
    for ln in changed:
        print("   ", ln.strip())

    cfg_src = open(EVENT_CONFIG, encoding="utf-8").read()
    # A 分支代码块（用于证明「只有一条触发路径、没有突破后兜底」）
    a_start = src03.index('if self.timing_mode == "A":')
    a_end = src03.index("# Baseline 基准突破", a_start)
    a_block = src03[a_start:a_end]
    freeze_rows = [
        {"item": "A 入场上界", "expected": "close_p < H（严格低于突破位）",
         "evidence": "基类 338 行 is_pre_bo_trigger = (close_p >= target_level) and (close_p < H)",
         "pass": "close_p < H" in src03},
        {"item": "A 入场下限", "expected": "close_p >= H - offset*A",
         "evidence": "基类 337 行 target_level = H - a_entry_level_atr_offset * A",
         "pass": "target_level = H - self.a_entry_level_atr_offset * A" in src03},
        {"item": "A 快照箱顶字段", "expected": "box_high_h 写真实 H（旧版误写 target_level）",
         "evidence": "基类 375 行 box_high_h=H",
         "pass": "box_high_h=H" in src03},
        {"item": "突破兜底分支", "expected": "A 组无兜底（不做突破后追单）",
         "evidence": "A 分支代码块内无 breakout_buffer_atr、无第二条触发路径；唯一触发条件为 is_pre_bo_trigger",
         "pass": ("breakout_buffer_atr" not in a_block) and a_block.count("is_pre_bo_trigger") == 2},
        {"item": "OI 过滤开关", "expected": "enable_oi_filter = True，9 组 A 均不覆盖",
         "evidence": "基类 509 行类属性 enable_oi_filter: bool = True；子类仅设 timing_mode/offset/volume",
         "pass": "enable_oi_filter: bool = True" in src03 and all(
             "enable_oi_filter" not in open(os.path.join(ENTRY03, "user_data/strategies", f"{s}.py"), encoding="utf-8").read()
             for _, s, _, _ in CELLS_A)},
        {"item": "OI 门槛", "expected": "ΔOI_6h >= 2.0%",
         "evidence": f"event_trend_v0_1/config.py: oi_min_growth_pct = 0.02；基类 358 行 oi_growth < config.oi_min_growth_pct ⇒ return None",
         "pass": "oi_min_growth_pct: float = 0.02" in cfg_src and "oi_growth < self.config.oi_min_growth_pct" in src03},
        {"item": "OI 回看窗口", "expected": "6 小时",
         "evidence": "config.py: oi_lookback_hours = 6",
         "pass": "oi_lookback_hours: int = 6" in cfg_src},
        {"item": "初始止损锚点", "expected": "Lr - 0.1A",
         "evidence": "基类 362 行 initial_stop = event.retest_low_lr - initial_stop_buffer_atr * A",
         "pass": "initial_stop = event.retest_low_lr - self.config.initial_stop_buffer_atr * A" in src03},
        {"item": "手续费", "expected": "0.05% 单边",
         "evidence": "config.json fee=0.0005，逐笔 fee_open/fee_close 全为 0.0005",
         "pass": bool(np.allclose(pd.concat([trades[c]["fee_open"] for c in trades]).unique(), 0.0005))},
        {"item": "资金与槽位", "expected": "2000 U / 最多 3 槽",
         "evidence": "归档 dry_run_wallet=2000、max_open_trades=3",
         "pass": bool((lock["dry_run_wallet"] == 2000).all() and (lock["max_open_trades"] == 3).all())},
        {"item": "时间框架", "expected": "15m 执行、4h 箱体、1h 状态机",
         "evidence": "归档 timeframe=15m；informative_pairs 含 4h/1h",
         "pass": bool((lock["timeframe"] == "15m").all())},
    ]
    frz = pd.DataFrame(freeze_rows)
    write_csv(frz, "01_参数冻结核验_v1.csv")
    print(frz[["item", "pass"]].to_string(index=False))
    assert frz["pass"].all(), "存在未通过的冻结项"

    # ── 1.4 跨批基线一致性（旧 74 池 vs 新 70 池） ─────────────────────
    print("\n" + "=" * 78)
    print("01-3 跨批基线一致性")
    print("=" * 78)
    base_old = load_trades(BASELINE, src=OUT02)
    old_trades_set = {(r["pair"], int(r["open_ts"])) for _, r in base_old.iterrows()}
    new_trades_set = {(r["pair"], int(r["open_ts"])) for _, r in trades[BASELINE].iterrows()}
    dropped_trades = 0
    for sym in DROPPED_FROM_74:
        pair = f"{sym}/USDT:USDT"
        dropped_trades += int((base_old["pair"] == pair).sum())

    def read_no_bom(path: str) -> bytes:
        raw = open(path, "rb").read()
        return raw[3:] if raw[:3] == b"\xef\xbb\xbf" else raw

    old_bytes = read_no_bom(os.path.join(OUT02, f"trades_{BASELINE}.csv"))
    new_bytes = read_no_bom(os.path.join(OUT03, f"trades_{BASELINE}.csv"))
    cross = {
        "old_baseline_trades": int(len(base_old)),
        "new_baseline_trades": int(len(trades[BASELINE])),
        "old_net": round(float(base_old["profit_abs"].sum()), 6),
        "new_net": round(float(trades[BASELINE]["profit_abs"].sum()), 6),
        "same_trade_keys": len(old_trades_set & new_trades_set),
        "only_in_old": len(old_trades_set - new_trades_set),
        "only_in_new": len(new_trades_set - old_trades_set),
        "trades_in_removed_4_coins_old_baseline": dropped_trades,
        "trades_csv_sha256_old": sha256(os.path.join(OUT02, f"trades_{BASELINE}.csv")),
        "trades_csv_sha256_new": sha256(os.path.join(OUT03, f"trades_{BASELINE}.csv")),
        "baseline_csv_byte_identical": sha256(os.path.join(OUT02, f"trades_{BASELINE}.csv")) ==
                                       sha256(os.path.join(OUT03, f"trades_{BASELINE}.csv")),
        "baseline_bytes_identical_after_bom_strip": old_bytes == new_bytes,
        "bom_old": open(os.path.join(OUT02, f"trades_{BASELINE}.csv"), "rb").read(3) == b"\xef\xbb\xbf",
        "bom_new": open(os.path.join(OUT03, f"trades_{BASELINE}.csv"), "rb").read(3) == b"\xef\xbb\xbf",
        "sha256_after_bom_strip": hashlib.sha256(new_bytes).hexdigest(),
        "note": "两版基线 CSV 的唯一字节差异是 UTF-8 BOM（旧版有、新版无）；去掉 BOM 后逐字节一致 ⇒ 74→70 的样本收缩对基线零影响",
    }
    print(json.dumps(cross, ensure_ascii=False, indent=2))
    print("⇒ 主张核对：被剔除 4 币在旧基线成交 %d 笔（对方称 0 笔）" % dropped_trades)

    # ── 1.5 逐币区间清单 ───────────────────────────────────────────────
    print("\n" + "=" * 78)
    print("01-4 逐币区间清单与数据指纹")
    print("=" * 78)
    l1 = pd.read_csv(os.path.join(AUDIT01_OUT, "02_派生区间_200根4h预热_研究期交集_v1.csv"), encoding="utf-8-sig")
    l1["symbol"] = l1["symbol"].astype(str)
    rows = []
    for sym in pool:
        sym_key = f"{sym}USDT"
        sub = l1[l1["symbol"] == sym_key]
        rec = sub.iloc[0] if len(sub) else None
        intervals = []
        if rec is not None and isinstance(rec["research_triple_intersection_intervals"], str):
            try:
                intervals = json.loads(rec["research_triple_intersection_intervals"])
            except Exception:
                intervals = []
        pair = f"{sym}/USDT:USDT"
        pair_s = f"{sym}_USDT_USDT"
        rows.append({
            "symbol": sym,
            "pair": pair,
            "in_70_pool": True,
            "L1_price_volume_candidate": rec["L1_price_volume_candidate"] if rec is not None else "无索引",
            "research_interval_start_utc": intervals[0]["start_utc"] if intervals else "",
            "research_interval_end_utc": intervals[0]["end_utc"] if intervals else "",
            "research_1h_bars": int(rec["research_triple_intersection_bars_1h"]) if rec is not None else 0,
            "L2_full_strategy_qualified": rec["L2_full_strategy_qualified"] if rec is not None else "否",
            "L2_blocking_reason": rec["L2_blocking_reason"] if rec is not None else "",
            "has_4h_feather": os.path.exists(os.path.join(DATA4H, f"{pair_s}-4h-futures.feather")),
            "has_1h_feather": os.path.exists(os.path.join(DATA4H, f"{pair_s}-1h-futures.feather")),
            "has_5m_oi_feather": os.path.exists(os.path.join(OI_DIR, f"{pair_s}-5m-open_interest.feather")),
            "trades_baseline": int((trades[BASELINE]["pair"] == pair).sum()),
            "trades_A_total": sum(int((trades[c]["pair"] == pair).sum()) for c, _, _, _ in CELLS_A),
        })
    pool_df = pd.DataFrame(rows)
    write_csv(pool_df, "00_逐币区间清单_70池_v1.csv")

    l1_out = l1[["symbol", "batch_no", "research_triple_intersection_intervals",
                 "research_triple_intersection_bars_1h", "L1_price_volume_candidate",
                 "L2_full_strategy_qualified", "L2_blocking_reason",
                 "oi_factor_1h_qualified_segments", "oi_factor_mapping_status"]].copy()
    write_csv(l1_out, "00_逐币区间清单_L1_241_v1.csv")

    summary = {
        "denominator_pool": len(pool_df),
        "pool_74_minus_4": {"removed": removed, "added": added, "removed_count": len(removed)},
        "in_L1_price_volume": int((pool_df["L1_price_volume_candidate"] == "是").sum()),
        "not_in_L1": pool_df.loc[pool_df["L1_price_volume_candidate"] != "是", "symbol"].tolist(),
        "has_4h_file": int(pool_df["has_4h_feather"].sum()),
        "has_1h_file": int(pool_df["has_1h_feather"].sum()),
        "has_5m_oi_file": int(pool_df["has_5m_oi_feather"].sum()),
        "L2_full_strategy_qualified": int((pool_df["L2_full_strategy_qualified"] == "是").sum()),
        "coins_with_baseline_trades": int((pool_df["trades_baseline"] > 0).sum()),
        "coins_with_A_trades": int((pool_df["trades_A_total"] > 0).sum()),
        "note": "L1=价量可算；L2=完整策略资格（需 OI 1h 合格区间），本批仍为 0",
    }
    jdump(os.path.join(OUT, "00_资格分层汇总_v1.json"), summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))

    # 数据指纹 + 与 AUDIT-03 交叉比对
    hash_rows = []
    for sym in pool:
        for tf in ("4h", "1h"):
            p = os.path.join(DATA4H, f"{sym}_USDT_USDT-{tf}-futures.feather")
            if os.path.exists(p):
                hash_rows.append({"symbol": sym, "timeframe": tf, "path": os.path.relpath(p, ROOT),
                                  "size_bytes": os.path.getsize(p), "sha256": sha256(p)})
    hash_df = pd.DataFrame(hash_rows)
    write_csv(hash_df, "00_数据文件指纹_v1.csv")
    prev_fp = pd.read_csv(os.path.join(AUDIT03_OUT, "00_数据文件指纹_v1.csv"), encoding="utf-8-sig")
    prev_map = dict(zip(prev_fp["symbol"] + "_" + prev_fp["timeframe"], prev_fp["sha256"]))
    hash_df["sha256_at_audit03"] = (hash_df["symbol"] + "_" + hash_df["timeframe"]).map(prev_map)
    hash_df["identical_to_audit03"] = hash_df["sha256"] == hash_df["sha256_at_audit03"]
    write_csv(hash_df, "00_数据文件指纹_v1.csv")
    n_cmp = int(hash_df["sha256_at_audit03"].notna().sum())
    print(f"指纹 {len(hash_df)} 个；可与 AUDIT-03 比对 {n_cmp} 个；全部一致 = {bool(hash_df['identical_to_audit03'].all())}")

    # ── 1.6 A 是否严格 <H ──────────────────────────────────────────────
    print("\n" + "=" * 78)
    print("01-5 A 严格低于 H 核验（4h 独立回算）")
    print("=" * 78)
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

    detail_rows = []
    for code, strat, route, delta, kvol in ALL_STRATS:
        df = trades[BASELINE] if code == "BASE" else trades[code]
        for _, r in df.iterrows():
            box = box_at(r["pair_s"], int(r["box_ms"]))
            if box is None:
                detail_rows.append({"code": code, "pair": r["pair"], "event_id": r["event_id"],
                                    "status": "box_unavailable"})
                continue
            H, L, A = box["H"], box["L"], box["A"]
            dl = delta if delta is not None else 0.0
            target = H - dl * A
            min_entry = (target - 0.1 * A) if code != "BASE" else H
            close_p = h1_close_at(r["pair_s"], int(r["sig_close_ms"]))
            r_dist = r["equity_before"] * 0.005 * float(r["open_rate"]) / float(r["stake_amount"])
            p0 = float(r["open_rate"]) - r_dist
            lr = p0 + 0.1 * A
            detail_rows.append({
                "code": code, "strategy": strat, "route": route,
                "pair": r["pair"], "event_id": r["event_id"],
                "box_ms": int(r["box_ms"]), "sig_close_ms": int(r["sig_close_ms"]),
                "H": H, "L": L, "A": A, "delta": dl,
                "target_level": target, "min_entry_price": min_entry,
                "sig_close_price": close_p,
                "sig_close_minus_H_in_ATR": (close_p - H) / A if np.isfinite(close_p) else float("nan"),
                "trigger_close_at_or_above_H": bool(close_p >= H) if np.isfinite(close_p) else None,
                "entry_gap_in_ATR": (float(r["open_rate"]) - close_p) / A if np.isfinite(close_p) else float("nan"),
                "open_rate": float(r["open_rate"]),
                "open_rate_minus_H": float(r["open_rate"]) - H,
                "open_rate_minus_H_in_ATR": (float(r["open_rate"]) - H) / A,
                "at_or_above_H": bool(float(r["open_rate"]) >= H),
                "below_min_entry": bool(float(r["open_rate"]) < min_entry - 1e-9),
                "lr_implied": lr,
                "lr_in_band": bool(L - 0.002 * A <= lr <= L + 0.5 * A + 0.002 * A),
                "stake_capped_30pct": bool(float(r["stake_amount"]) >= r["equity_before"] * 0.30 - 1e-9),
                "profit_abs": float(r["profit_abs"]),
            })
    det = pd.DataFrame(detail_rows)
    write_csv(det, "01_A入场价与箱顶H逐笔_v1.csv")

    strict_rows = []
    for code, strat, route, delta, kvol in ALL_STRATS:
        sub = det[det["code"] == code]
        n = len(sub)
        strict_rows.append({
            "code": code, "strategy": strat, "route": route,
            "delta_atr": delta if delta is not None else 0.0,
            "trades": n,
            "box_recomputed": int(sub["H"].notna().sum()) if "H" in sub else 0,
            "trigger_close_at_or_above_H": int((sub["trigger_close_at_or_above_H"] == True).sum()) if n else 0,
            "entries_at_or_above_H": int(sub["at_or_above_H"].sum()) if n else 0,
            "entries_at_or_above_H_pct": round(100.0 * int(sub["at_or_above_H"].sum()) / n, 2) if n else None,
            "entries_below_min_entry": int(sub["below_min_entry"].sum()) if n else 0,
            "max_sig_close_minus_H_in_ATR": round(float(sub["sig_close_minus_H_in_ATR"].max()), 4) if n else None,
            "max_open_rate_minus_H_in_ATR": round(float(sub["open_rate_minus_H_in_ATR"].max()), 4) if n else None,
            "median_open_rate_minus_H_in_ATR": round(float(sub["open_rate_minus_H_in_ATR"].median()), 4) if n else None,
            "min_open_rate_minus_H_in_ATR": round(float(sub["open_rate_minus_H_in_ATR"].min()), 4) if n else None,
            "lr_in_band": int(sub["lr_in_band"].sum()) if n else 0,
            "stake_capped_30pct": int(sub["stake_capped_30pct"].sum()) if n else 0,
        })
    strict = pd.DataFrame(strict_rows)
    write_csv(strict, "01_A严格低于H_汇总_v1.csv")
    print(strict.to_string(index=False))

    # ── 1.7 OI 门槛行为核验（用同一受控实现复算 ΔOI_6h） ────────────────
    print("\n" + "=" * 78)
    print("01-6 OI 门槛行为核验（ΔOI_6h >= 2%）")
    print("=" * 78)
    sys.path.insert(0, ROOT)
    from 策略.WyckoffMaster.event_trend_v0_1.features import extract_real_oi_growth_ex  # noqa: E402

    oi_cache: dict = {}
    oi_rows = []
    for code, strat, route, delta, kvol in ALL_STRATS:
        df = trades[BASELINE] if code == "BASE" else trades[code]
        for _, r in df.iterrows():
            pair_s = r["pair_s"]
            if pair_s not in oi_cache:
                p = os.path.join(OI_DIR, f"{pair_s}-5m-open_interest.feather")
                if os.path.exists(p):
                    o = pd.read_feather(p)
                    oi_cache[pair_s] = (o["date"].astype("int64").to_numpy(),
                                        o["open_interest_amount"].to_numpy(np.float64))
                else:
                    oi_cache[pair_s] = None
            if oi_cache[pair_s] is None:
                oi_rows.append({"code": code, "pair": r["pair"], "event_id": r["event_id"],
                                "status": "oi_file_missing"})
                continue
            ts, vals = oi_cache[pair_s]
            res = extract_real_oi_growth_ex(ts, vals, int(r["sig_close_ms"]),
                                            lookback_ms=6 * 3600 * 1000,
                                            max_distance_ms=15 * 60 * 1000,
                                            oi_available_at=None, bucket_ms=5 * 60 * 1000)
            g = res["growth"]
            oi_rows.append({
                "code": code, "pair": r["pair"], "event_id": r["event_id"],
                "sig_close_ms": int(r["sig_close_ms"]),
                "oi_growth_6h": g,
                "oi_growth_pct": round(g * 100, 4) if g is not None else None,
                "status": res["status"],
                "assumed_bucket_end": res["assumed"],
                "passes_2pct": (g is not None and g >= 0.02),
                "profit_abs": float(r["profit_abs"]),
            })
    oi_df = pd.DataFrame(oi_rows)
    write_csv(oi_df, "01_OI门槛行为核验_v1.csv")
    oi_sum = oi_df.groupby("code").agg(
        trades=("event_id", "size"),
        oi_available=("oi_growth_6h", lambda s: int(s.notna().sum())),
        passes_2pct=("passes_2pct", lambda s: int(s.fillna(False).sum())),
        min_growth_pct=("oi_growth_pct", "min"),
        median_growth_pct=("oi_growth_pct", "median"),
    ).reset_index()
    write_csv(oi_sum, "01_OI门槛行为汇总_v1.csv")
    print(oi_sum.to_string(index=False))

    # ── 1.8 事件覆盖 ───────────────────────────────────────────────────
    print("\n" + "=" * 78)
    print("01-7 事件覆盖")
    print("=" * 78)
    base_events = set(trades[BASELINE]["event_id"])
    universe_rows = []
    for code, strat, _, _ in CELLS_A:
        ev = set(trades[code]["event_id"])
        missed = base_events - ev
        missed_pnl = float(trades[BASELINE].loc[trades[BASELINE]["event_id"].isin(missed), "profit_abs"].sum())
        universe_rows.append({
            "code": code, "strategy": strat,
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

    consist = {
        "unique_single_value": {k: v for k, v in uniq.items() if len(v) == 1},
        "unique_multi_value": {k: v for k, v in uniq.items() if len(v) > 1},
        "csv_vs_archive_rows_mismatch": lock.loc[lock["csv_rows"] != lock["total_trades"],
                                                ["code", "csv_rows", "total_trades"]].to_dict("records"),
        "config_sha256": sha256(CFG_JSON),
        "config_whitelist_n": len(whitelist),
        "config_whitelist_sha256": hashlib.sha256(
            json.dumps(whitelist, ensure_ascii=False).encode("utf-8")).hexdigest(),
        "strategy_base_sha256": sha256(BASE_STRATEGY),
        "event_config_sha256_now": sha256(EVENT_CONFIG),
        "event_config_note": "策略 import 的冻结参数模块；批次内无运行时快照，只能记录当前哈希",
        "base_class_diff_vs_entry02": {"hunks": len(hunks), "changed_lines": [c.strip() for c in changed]},
        "cross_batch_baseline": cross,
        "data_fingerprint_vs_audit03": {"compared": n_cmp, "all_identical": bool(hash_df["identical_to_audit03"].all())},
    }
    jdump(os.path.join(OUT, "00_样本一致性_v1.json"), consist)

    jdump(os.path.join(OUT, "01_开跑前核对摘要_v1.json"), {
        "version_lock": lock.to_dict("records"),
        "freeze_check": frz.to_dict("records"),
        "strict_below_H": strict.to_dict("records"),
        "oi_gate": oi_sum.to_dict("records"),
        "event_coverage": uni.to_dict("records"),
        "qualification": summary,
    })
    print("\n完成")


if __name__ == "__main__":
    main()
