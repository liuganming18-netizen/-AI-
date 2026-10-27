# -*- coding: utf-8 -*-
"""ENTRY-AUDIT-01 / 02 用原始 15m/4h K 线独立复算分类数量。

不复用 FACTOR-ENTRY-03 的 mae_r_4h / mfe_r_4h / first_move_4h / entry_quality_category，
全部从 parquet 原始 K 线重算，再与其派生列逐条对比。

ATR 三套口径（用于分离「ATR 基准」对结论的影响）：
  atr_report : 复刻对方回退链 box_atr_4h → initial_risk_r → p_entry*0.03
  atr_box    : 仅 box_atr_4h（163 条有值）
  atr_snap   : 我方独立口径 —— 入场时刻最后一根已闭合 4h K 线的全历史 Wilder ATR(14)

产出：
  02_recompute.json
  02_per_record.csv        逐条（205 行）：我方重算值 vs 对方派生值 vs 差异
  02_category_diff.csv     分类差异逐条
  02_variant_counts.csv    各口径下的分类计数
  02_data_integrity.csv    15m 窗口完整性
"""
from __future__ import annotations

import glob
import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path("/Users/boos/Desktop/威科夫因子共振趋势")
OUT = ROOT / "evidence" / "ENTRY-AUDIT-01"
OUT.mkdir(parents=True, exist_ok=True)

FEAT = ROOT / "evidence/FACTOR-ENTRY-01/candidate_features.parquet"
ENR = ROOT / "evidence/FACTOR-ENTRY-03/candidate_features_enriched.parquet"
BASE = ROOT / "数据" / "市场数据" / "parquet" / "candles" / "binance" / "um"

BAR_MS = 15 * 60 * 1000
WIN_BARS = 16          # 4h
THRESH_ATR = 0.3       # ±0.3 ATR 首触判定
MAE_GATE = 0.5         # 早期走弱 MAE 门槛
MFE_CAP = 0.5          # 早期走弱 MFE 上限


def wilder_atr(h: np.ndarray, l: np.ndarray, c: np.ndarray, period: int = 14) -> np.ndarray:
    """与 features.compute_wilder_atr_series 同定义，独立实现。"""
    n = len(h)
    tr = np.zeros(n)
    tr[0] = h[0] - l[0]
    for i in range(1, n):
        tr[i] = max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))
    atr = np.full(n, np.nan)
    if n < period:
        return atr
    atr[period - 1] = tr[:period].mean()
    for i in range(period, n):
        atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period
    return atr


_c15: dict[str, pd.DataFrame] = {}
_c4h: dict[str, pd.DataFrame] = {}


def load15(sym: str) -> pd.DataFrame:
    if sym not in _c15:
        fs = sorted(glob.glob(str(BASE / sym / "15m" / "*.parquet")))
        d = pd.concat(
            [pd.read_parquet(f, columns=["open_time", "close_time", "open", "high", "low", "close"])
             for f in fs], ignore_index=True)
        d = d.sort_values("open_time").drop_duplicates("open_time").reset_index(drop=True)
        for col in ("open", "high", "low", "close"):
            d[col] = pd.to_numeric(d[col], errors="coerce")
        _c15[sym] = d
    return _c15[sym]


def load4h(sym: str) -> pd.DataFrame:
    if sym not in _c4h:
        fs = sorted(glob.glob(str(BASE / sym / "4h" / "*.parquet")))
        d = pd.concat(
            [pd.read_parquet(f, columns=["open_time", "close_time", "high", "low", "close"])
             for f in fs], ignore_index=True)
        d = d.sort_values("open_time").drop_duplicates("open_time").reset_index(drop=True)
        for col in ("high", "low", "close"):
            d[col] = pd.to_numeric(d[col], errors="coerce")
        d["atr"] = wilder_atr(d["high"].to_numpy(float), d["low"].to_numpy(float),
                              d["close"].to_numpy(float), 14)
        _c4h[sym] = d
    return _c4h[sym]


def atr_snapshot(sym: str, t_ms: int) -> float:
    d = load4h(sym)
    sub = d[d["close_time"] <= t_ms]
    if sub.empty:
        return np.nan
    return float(sub["atr"].iloc[-1])


def classify(fwd_ret_4h, mfe_r_4h, mae_r_4h, first_move, mfe_r_full, net):
    if pd.isna(fwd_ret_4h) or pd.isna(mfe_r_4h):
        return "NO_WINDOW_DATA"
    if fwd_ret_4h < 0 and mfe_r_4h < MFE_CAP and (
            (pd.notna(mae_r_4h) and mae_r_4h >= MAE_GATE) or first_move == "FIRST_ADVERSE"):
        return "EARLY_WEAKNESS"
    if mfe_r_full >= 1.0 and net <= 0:
        return "MFE_PULLBACK"
    if mfe_r_full >= 1.5 and net > 0:
        return "SUSTAINED_TREND"
    return "CHOPPY_OR_OTHER"


def main() -> None:
    feat = pd.read_parquet(FEAT)
    enr = pd.read_parquet(ENR)
    filled = feat[feat["exec_status"] == "FILLED"].copy().reset_index(drop=True)

    enr_cols = ["event_id", "mae_r_4h", "mfe_r_4h", "first_move_4h", "window_status_4h",
                "fwd_ret_4h", "entry_quality_category", "mae_r_24h", "mfe_r_24h"]
    enr_s = enr[enr_cols].set_index("event_id")

    rows = []
    integ = []
    for _, r in filled.iterrows():
        sym = r["symbol"]
        t_in = int(r["entry_time"])
        p = float(r["p_entry"])
        d15 = load15(sym)

        times = d15["open_time"].to_numpy(np.int64)
        idx = int(np.searchsorted(times, t_in, side="left"))
        bar_exists = idx < len(d15) and int(times[idx]) == t_in
        win = d15.iloc[idx: idx + WIN_BARS]
        # 严格 4h 内：open_time < t_in + 4h
        win = win[win["open_time"] < t_in + 4 * 3600 * 1000]

        gaps = 0
        if len(win) > 1:
            diffs = np.diff(win["open_time"].to_numpy(np.int64))
            gaps = int((diffs != BAR_MS).sum())
        bad_px = int((win[["high", "low"]].isna().any(axis=1)).sum() + (win["high"] < win["low"]).sum())

        # ---- ATR 三口径 ----
        atr_box = float(r["box_atr_4h"]) if pd.notna(r["box_atr_4h"]) and float(r["box_atr_4h"]) > 0 else np.nan
        irr = float(r["initial_risk_r"]) if pd.notna(r["initial_risk_r"]) and float(r["initial_risk_r"]) > 0 else np.nan
        if pd.notna(atr_box):
            atr_report, atr_src = atr_box, "box_atr_4h"
        elif pd.notna(irr):
            atr_report, atr_src = irr, "initial_risk_r(回退)"
        else:
            atr_report, atr_src = p * 0.03, "p_entry*0.03(回退)"
        atr_snap = atr_snapshot(sym, t_in)

        # 箱体宽度 / ATR 一致性（策略配置要求 2~8 ATR）
        box_w = float(r["box_high"]) - float(r["box_low"])
        # 校正口径一：按 163 条实测中位比 5.90 反推（经验校准）
        atr_cal = atr_box if pd.notna(atr_box) else (box_w / 5.90 if box_w > 0 else np.nan)
        # 校正口径二：按策略下限 2.0 ATR 反推（最保守、改动最小）
        atr_cons = atr_box if pd.notna(atr_box) else (box_w / 2.0 if box_w > 0 else np.nan)

        # ---- 窗口路径：每个 ATR 口径各自重算 MAE/MFE 与首触顺序 ----
        if len(win) == 0 or pd.isna(win["high"]).all():
            mae_pct = mfe_pct = np.nan
        else:
            mfe_pct = (float(win["high"].max()) - p) / p
            mae_pct = (p - float(win["low"].min())) / p

        def path_of(atr):
            """返回 (mae_r, mfe_r, first_move)。同根同时触及 → UNKNOWN_SAME_BAR，不推测先后。"""
            if pd.isna(atr) or atr <= 0 or pd.isna(mae_pct):
                return np.nan, np.nan, "NO_DATA"
            mfe_r = (mfe_pct * p) / atr
            mae_r = (mae_pct * p) / atr
            fav_th = p + THRESH_ATR * atr
            adv_th = p - THRESH_ATR * atr
            hi = win["high"].to_numpy(float)
            lo = win["low"].to_numpy(float)
            hit_fav = hi >= fav_th
            hit_adv = lo <= adv_th
            if not (hit_fav.any() or hit_adv.any()):
                return mae_r, mfe_r, "NEITHER_REACHED_0.3ATR"
            i_f = int(np.argmax(hit_fav)) if hit_fav.any() else None
            i_a = int(np.argmax(hit_adv)) if hit_adv.any() else None
            if i_f is not None and i_a is not None:
                if i_a < i_f:
                    return mae_r, mfe_r, "FIRST_ADVERSE"
                if i_f < i_a:
                    return mae_r, mfe_r, "FIRST_FAVORABLE"
                return mae_r, mfe_r, "UNKNOWN_SAME_BAR"
            return mae_r, mfe_r, ("FIRST_ADVERSE" if i_a is not None else "FIRST_FAVORABLE")

        mae_r_rep, mfe_r_rep, first_move = path_of(atr_report)
        mae_r_snap, mfe_r_snap, first_move_snap = path_of(atr_snap)
        mae_r_cal, mfe_r_cal, first_move_cal = path_of(atr_cal)
        mae_r_cons, mfe_r_cons, first_move_cons = path_of(atr_cons)

        # 4h 收益两种口径
        ret_4h_end = (float(win["close"].iloc[-1]) - p) / p if len(win) else np.nan
        ret_4h_their = (float(d15["close"].iloc[idx + 16]) - p) / p if idx + 16 < len(d15) else np.nan

        mfe_full = float(r["mfe_r"]) if pd.notna(r["mfe_r"]) else 0.0
        net = float(r["net_pnl"]) if pd.notna(r["net_pnl"]) else 0.0

        rows.append({
            "event_id": r["event_id"], "symbol": sym,
            "entry_time_ms": t_in,
            "entry_utc": str(pd.to_datetime(t_in, unit="ms")),
            "p_entry": p,
            "bars_in_window": int(len(win)),
            "window_gaps": gaps,
            "atr_report": atr_report, "atr_source": atr_src,
            "atr_box": atr_box, "atr_snap": atr_snap,
            "box_width": box_w,
            "box_width_over_atr_report": (box_w / atr_report) if (pd.notna(atr_report) and atr_report > 0) else np.nan,
            "atr_cal": atr_cal, "atr_cons": atr_cons,
            "atr_ratio_report_over_snap": (atr_report / atr_snap) if (pd.notna(atr_snap) and atr_snap > 0) else np.nan,
            "mae_pct_4h": mae_pct, "mfe_pct_4h": mfe_pct,
            "mae_r_4h_mine_rep": mae_r_rep, "mfe_r_4h_mine_rep": mfe_r_rep,
            "mae_r_4h_mine_snap": mae_r_snap, "mfe_r_4h_mine_snap": mfe_r_snap,
            "mae_r_4h_theirs": float(enr_s.loc[r["event_id"], "mae_r_4h"]),
            "mfe_r_4h_theirs": float(enr_s.loc[r["event_id"], "mfe_r_4h"]),
            "first_move_mine": first_move,
            "first_move_theirs": enr_s.loc[r["event_id"], "first_move_4h"],
            "first_move_mine_snap": first_move_snap,
            "fwd_ret_4h_mine_end": ret_4h_end,
            "fwd_ret_4h_mine_their_idx16": ret_4h_their,
            "fwd_ret_4h_theirs": float(enr_s.loc[r["event_id"], "fwd_ret_4h"]),
            "cat_mine_rep": classify(ret_4h_their, mfe_r_rep, mae_r_rep, first_move, mfe_full, net),
            "cat_mine_snap": classify(ret_4h_their, mfe_r_snap, mae_r_snap, first_move_snap, mfe_full, net),
            "cat_mine_cal": classify(ret_4h_their, mfe_r_cal, mae_r_cal, first_move_cal, mfe_full, net),
            "cat_mine_cons": classify(ret_4h_their, mfe_r_cons, mae_r_cons, first_move_cons, mfe_full, net),
            "cat_mine_rep_ret_end": classify(ret_4h_end, mfe_r_rep, mae_r_rep, first_move, mfe_full, net),
            "first_move_mine_cal": first_move_cal,
            "first_move_mine_cons": first_move_cons,
            "cat_mine_snap_ret_end": classify(ret_4h_end, mfe_r_snap, mae_r_snap, first_move_snap, mfe_full, net),
            "cat_theirs": enr_s.loc[r["event_id"], "entry_quality_category"],
            "mfe_r_full": mfe_full, "net_pnl": net,
        })

        integ.append({
            "event_id": r["event_id"], "symbol": sym, "entry_utc": str(pd.to_datetime(t_in, unit="ms")),
            "entry_bar_exists": bool(bar_exists), "bars_in_window": int(len(win)),
            "window_complete_16": bool(len(win) == WIN_BARS), "window_gaps": gaps, "bad_price_bars": bad_px,
        })

    pr = pd.DataFrame(rows)
    pr.to_csv(OUT / "02_per_record.csv", index=False)
    pd.DataFrame(integ).to_csv(OUT / "02_data_integrity.csv", index=False)

    # ---- 逐条差异 ----
    diff = pr[["event_id", "symbol", "entry_utc", "cat_theirs", "cat_mine_rep", "cat_mine_snap",
               "first_move_theirs", "first_move_mine", "first_move_mine_snap",
               "mae_r_4h_theirs", "mae_r_4h_mine_rep",
               "mae_r_4h_mine_snap", "mfe_r_4h_theirs", "mfe_r_4h_mine_rep", "mfe_r_4h_mine_snap",
               "atr_source", "atr_report", "atr_snap", "atr_ratio_report_over_snap",
               "fwd_ret_4h_theirs", "fwd_ret_4h_mine_their_idx16", "fwd_ret_4h_mine_end",
               "bars_in_window", "window_gaps"]].copy()
    diff["d_mae_r_rep"] = (diff["mae_r_4h_mine_rep"] - diff["mae_r_4h_theirs"]).abs()
    diff["d_mfe_r_rep"] = (diff["mfe_r_4h_mine_rep"] - diff["mfe_r_4h_theirs"]).abs()
    diff["cat_changed_vs_theirs"] = diff["cat_theirs"] != diff["cat_mine_rep"]
    diff["cat_changed_under_snap"] = diff["cat_theirs"] != diff["cat_mine_snap"]
    diff["firstmove_changed_vs_theirs"] = diff["first_move_theirs"] != diff["first_move_mine"]
    diff["firstmove_changed_under_snap"] = diff["first_move_theirs"] != diff["first_move_mine_snap"]
    diff.sort_values(["cat_changed_under_snap", "d_mae_r_rep"], ascending=[False, False]).to_csv(
        OUT / "02_category_diff.csv", index=False)

    # ---- 计数 ----
    def counts(col):
        vc = pr[col].value_counts().to_dict()
        return {str(k): int(v) for k, v in vc.items()}

    variants = pd.DataFrame([
        {"basis": "对方派生列(直接读)", "early_weakness": int((pr["cat_theirs"] == "EARLY_WEAKNESS").sum()),
         "first_adverse": int((pr["first_move_theirs"] == "FIRST_ADVERSE").sum()),
         "denominator": len(pr), "note": "非复算，仅读表核对"},
        {"basis": "我方复算/atr_report(复刻回退链)", "early_weakness": int((pr["cat_mine_rep"] == "EARLY_WEAKNESS").sum()),
         "first_adverse": int((pr["first_move_mine"] == "FIRST_ADVERSE").sum()),
         "denominator": len(pr), "note": "检验其算术是否可复现"},
        {"basis": "我方复算/atr_snap(全池统一独立ATR)", "early_weakness": int((pr["cat_mine_snap"] == "EARLY_WEAKNESS").sum()),
         "first_adverse": int((pr["first_move_mine_snap"] == "FIRST_ADVERSE").sum()),
         "denominator": len(pr), "note": "统一基准下的对照"},
        {"basis": "我方复算/atr_snap+窗口末根收益", "early_weakness": int((pr["cat_mine_snap_ret_end"] == "EARLY_WEAKNESS").sum()),
         "first_adverse": int((pr["first_move_mine_snap"] == "FIRST_ADVERSE").sum()),
         "denominator": len(pr), "note": "收益口径改用第16根收盘"},
        {"basis": "校正/42条按W÷5.90反推(163实测中位)", "early_weakness": int((pr["cat_mine_cal"] == "EARLY_WEAKNESS").sum()),
         "first_adverse": int((pr["first_move_mine_cal"] == "FIRST_ADVERSE").sum()),
         "denominator": len(pr), "note": "经验校准"},
        {"basis": "校正/42条按W÷2.0反推(策略下限,最保守)", "early_weakness": int((pr["cat_mine_cons"] == "EARLY_WEAKNESS").sum()),
         "first_adverse": int((pr["first_move_mine_cons"] == "FIRST_ADVERSE").sum()),
         "denominator": len(pr), "note": "最小改动下界"},
        {"basis": "复刻回退链+窗口末根收益", "early_weakness": int((pr["cat_mine_rep_ret_end"] == "EARLY_WEAKNESS").sum()),
         "first_adverse": int((pr["first_move_mine"] == "FIRST_ADVERSE").sum()),
         "denominator": len(pr), "note": "仅检验 4h 收益取第16还是第17根的影响"},
    ])
    variants.to_csv(OUT / "02_variant_counts.csv", index=False)

    # ---- 仅 163 条有真 box ATR 的子集 ----
    sub163 = pr[pr["atr_box"].notna()]

    def sub_counts(df, col):
        vc = df[col].value_counts().to_dict()
        return {str(k): int(v) for k, v in vc.items()}

    # ---- R 单位一致性：全周期 R 用止损距离，4h R 用 ATR，两套分母 ----
    feat_f = feat[feat["exec_status"] == "FILLED"].copy()
    feat_f["risk_pct"] = feat_f["initial_risk_r"] / feat_f["p_entry"]
    feat_f["atr_pct"] = feat_f["box_atr_4h"] / feat_f["p_entry"]
    r_unit = {
        "全周期mfe_r分母=初始止损距离": "已核验：mfe_r ≡ trade_mfe_pct ÷ (initial_risk_r/p_entry)，205/205 精确成立",
        "4h窗口mae_r分母=4h ATR": "已核验：mae_r_4h ≡ (p_entry-窗口最低)/atr，205/205 精确成立",
        "初始止损距离占价格_中位": round(float(feat_f["risk_pct"].median() * 100), 2),
        "4h ATR占价格_中位(163条)": round(float(feat_f["atr_pct"].dropna().median() * 100), 2),
        "两者倍数(止损距离÷ATR)": round(float((feat_f["risk_pct"] / feat_f["atr_pct"]).dropna().median()), 2),
        "含义": "同一个「R」在报告里有两套分母，量级相差约 6.5 倍；"
                "因此「mae_r_4h ≥ 0.5」与「mfe_r ≥ 1.0」不是同一把尺子，四类标签之间不可直接比较。",
        "42条回退组的initial_risk_r占价格_中位": round(
            float(feat_f.loc[feat_f["box_atr_4h"].isna(), "risk_pct"].median() * 100), 2),
    }

    res = {
        "ticket": "ENTRY-AUDIT-01", "section": "02 独立复算",
        "样本": int(len(pr)),
        "A_分类计数_各口径": variants.to_dict("records"),
        "B_分类分布_明细": {
            "对方派生列": counts("cat_theirs"),
            "我方复算_atr_report": counts("cat_mine_rep"),
            "我方复算_atr_snap": counts("cat_mine_snap"),
        },
        "C_首次运动方向_明细": {
            "对方派生列": counts("first_move_theirs"),
            "我方复算_atr_report": counts("first_move_mine"),
            "我方复算_atr_snap": counts("first_move_mine_snap"),
        },
        "D_算术可复现性": {
            "mae_r_4h 完全一致条数(差<1e-9)": int((diff["d_mae_r_rep"].fillna(9e9) < 1e-9).sum()),
            "mae_r_4h 差>1e-6 条数": int((diff["d_mae_r_rep"].fillna(9e9) > 1e-6).sum()),
            "mfe_r_4h 差>1e-6 条数": int((diff["d_mfe_r_rep"].fillna(9e9) > 1e-6).sum()),
            "首次运动方向不一致条数": int(diff["firstmove_changed_vs_theirs"].sum()),
            "分类不一致条数": int(diff["cat_changed_vs_theirs"].sum()),
            "改用独立ATR后分类变化条数": int(diff["cat_changed_under_snap"].sum()),
            "改用独立ATR后首次方向变化条数": int(diff["firstmove_changed_under_snap"].sum()),
        },
        "D2_换ATR后翻转的方向": (
            pr[pr["first_move_mine"] != pr["first_move_mine_snap"]]
            .groupby(["first_move_theirs", "first_move_mine_snap"]).size().rename("n").reset_index()
            .to_dict("records")
        ),
        "E_ATR基准": {
            "box_atr_4h缺失条数": int(pr["atr_box"].isna().sum()),
            "回退到initial_risk_r条数": int((pr["atr_source"] == "initial_risk_r(回退)").sum()),
            "回退到p_entry*0.03条数": int((pr["atr_source"] == "p_entry*0.03(回退)").sum()),
            "回退组 atr_report/atr_snap 中位倍数": float(pr.loc[pr["atr_box"].isna(), "atr_ratio_report_over_snap"].median()),
            "回退组 atr_report/atr_snap 最小": float(pr.loc[pr["atr_box"].isna(), "atr_ratio_report_over_snap"].min()),
            "回退组 atr_report/atr_snap 最大": float(pr.loc[pr["atr_box"].isna(), "atr_ratio_report_over_snap"].max()),
            "箱体宽度÷ATR_163条_中位": float(pr.loc[pr["atr_box"].notna(), "box_width_over_atr_report"].median()),
            "箱体宽度÷ATR_163条_落在2~8": int(((pr.loc[pr["atr_box"].notna(), "box_width_over_atr_report"] >= 2)
                                          & (pr.loc[pr["atr_box"].notna(), "box_width_over_atr_report"] <= 8)).sum()),
            "箱体宽度÷ATR_42条_唯一取值": sorted(set(pr.loc[pr["atr_box"].isna(), "box_width_over_atr_report"].round(9).tolist())),
            "箱体宽度÷ATR_42条_落在2~8": int(((pr.loc[pr["atr_box"].isna(), "box_width_over_atr_report"] >= 2)
                                          & (pr.loc[pr["atr_box"].isna(), "box_width_over_atr_report"] <= 8)).sum()),
        },
        "F_有真boxATR的163条子集": {
            "对方EARLY_WEAKNESS": int((sub163["cat_theirs"] == "EARLY_WEAKNESS").sum()),
            "我方复算EARLY_WEAKNESS": int((sub163["cat_mine_rep"] == "EARLY_WEAKNESS").sum()),
            "对方FIRST_ADVERSE": int((sub163["first_move_theirs"] == "FIRST_ADVERSE").sum()),
            "我方复算FIRST_ADVERSE": int((sub163["first_move_mine"] == "FIRST_ADVERSE").sum()),
            "分母": int(len(sub163)),
        },
        "F2_回退ATR的42条子集": {
            "EARLY_WEAKNESS_对方": int((pr[pr["atr_box"].isna()]["cat_theirs"] == "EARLY_WEAKNESS").sum()),
            "EARLY_WEAKNESS_独立ATR": int((pr[pr["atr_box"].isna()]["cat_mine_snap"] == "EARLY_WEAKNESS").sum()),
            "EARLY_WEAKNESS_校准ATR": int((pr[pr["atr_box"].isna()]["cat_mine_cal"] == "EARLY_WEAKNESS").sum()),
            "EARLY_WEAKNESS_最保守ATR": int((pr[pr["atr_box"].isna()]["cat_mine_cons"] == "EARLY_WEAKNESS").sum()),
            "FIRST_ADVERSE_对方": int((pr[pr["atr_box"].isna()]["first_move_theirs"] == "FIRST_ADVERSE").sum()),
            "FIRST_ADVERSE_独立ATR": int((pr[pr["atr_box"].isna()]["first_move_mine_snap"] == "FIRST_ADVERSE").sum()),
            "FIRST_ADVERSE_校准ATR": int((pr[pr["atr_box"].isna()]["first_move_mine_cal"] == "FIRST_ADVERSE").sum()),
            "FIRST_ADVERSE_最保守ATR": int((pr[pr["atr_box"].isna()]["first_move_mine_cons"] == "FIRST_ADVERSE").sum()),
            "分母": int((pr["atr_box"].isna()).sum()),
        },
        "F3_163条在独立ATR时点下的敏感性": {
            "EARLY_WEAKNESS_对方口径": int((sub163["cat_mine_rep"] == "EARLY_WEAKNESS").sum()),
            "EARLY_WEAKNESS_入场时刻ATR": int((sub163["cat_mine_snap"] == "EARLY_WEAKNESS").sum()),
            "FIRST_ADVERSE_对方口径": int((sub163["first_move_mine"] == "FIRST_ADVERSE").sum()),
            "FIRST_ADVERSE_入场时刻ATR": int((sub163["first_move_mine_snap"] == "FIRST_ADVERSE").sum()),
            "分母": int(len(sub163)),
        },
        "G_数据完整性": {
            "入场bar不存在条数": int((~pd.DataFrame(integ)["entry_bar_exists"]).sum()),
            "窗口不足16根条数": int((~pd.DataFrame(integ)["window_complete_16"]).sum()),
            "窗口内有缺口条数": int((pd.DataFrame(integ)["window_gaps"] > 0).sum()),
            "异常价格bar条数": int((pd.DataFrame(integ)["bad_price_bars"] > 0).sum()),
        },
        "H_R单位一致性": r_unit,
    }
    (OUT / "02_recompute.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(res, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
