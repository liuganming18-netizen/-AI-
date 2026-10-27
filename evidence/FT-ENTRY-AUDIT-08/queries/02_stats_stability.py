# -*- coding: utf-8 -*-
"""02 统计稳定性（工单第 1 项）。

复用 AUDIT-04/05/06/07 已预锁的块级方法，不按结果更换检验：
- 30 天日历块（2021-12-01 起，38 块）；
- 配对 Bootstrap 5000 次（seed=20261020）→ 95% 百分位 CI；
- 块级符号翻转置换 5000 次 → 单格 p；
- Holm-Bonferroni；单步 maxT（同一组块符号同时施加到全族）。

独立样本纪律：
- 物理候选事件键 = (symbol, event_id, box_ms)；
- 同网格同事件多笔成交合并为 1 个事件（C0 有 77 个候选/163 笔复入）；
- 跨参数网格重复出现的同一事件不再当作独立样本：池化按事件等权、跨 9 网格平均。

指标口径（分开报，不混用）：
- 净期望：ITT 候选级（C1/C2 未成交记 0），回答"换入场规则后每个候选的期望"；
- 胜率/快速止损率：仅在成交事件上定义（与交付/审计口径一致），
  配对检验用"C0 与 C1/C2 都成交"的匹配事件，避免把"不交易"当成亏损；
- 同时报候选级发生率作描述，不用它替代成交口径的胜率。
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from _common import (  # noqa: E402
    BLOCK_DAYS, GRIDS, OUT, REPS, SEED, block_bootstrap_mean, block_index,
    block_permutation_p, block_sums, event_panel, holm, jdump, load_trades,
    n_blocks, write_csv,
)

NB = n_blocks(BLOCK_DAYS)


def itt_grid(grid: str) -> pd.DataFrame:
    p = {m: event_panel(m, grid).set_index("event_key") for m in ("C", "C1", "C2")}
    keys = sorted(set().union(*[set(v.index) for v in p.values()]))
    base = pd.DataFrame(index=keys)
    sig, sym = {}, {}
    for m, v in p.items():
        base[f"pnl_{m}"] = v["pnl"].reindex(keys).fillna(0.0)
        base[f"win_{m}"] = v["win"].reindex(keys).fillna(0).astype(int)
        base[f"qs_{m}"] = v["qs"].reindex(keys).fillna(0).astype(int)
        base[f"n_{m}"] = v["n_fills"].reindex(keys).fillna(0).astype(int)
        sig[m] = v["sig_ms"].reindex(keys)
        sym[m] = v["symbol"].reindex(keys)
    base["symbol"] = sym["C"].fillna(sym["C1"]).fillna(sym["C2"])
    s = pd.concat(sig, axis=1).min(axis=1)
    base["sig_ms"] = s.astype("int64")
    base["block"] = [block_index(t) for t in base["sig_ms"]]
    base = base[(base["block"] >= 0) & (base["block"] < NB)]
    return base.reset_index().rename(columns={"index": "event_key"})


def _stat(d: np.ndarray, bid: np.ndarray):
    lo, hi = block_bootstrap_mean(d, bid, NB)
    p = block_permutation_p(d, bid, NB)
    return lo, hi, p


def summarise(df: pd.DataFrame, hi: str, lo: str) -> dict:
    ne = len(df)
    nhi = int((df[f"n_{hi}"] > 0).sum())
    nlo = int((df[f"n_{lo}"] > 0).sum())
    both = df[(df[f"n_{hi}"] > 0) & (df[f"n_{lo}"] > 0)]
    bid = df["block"].to_numpy(int)
    out = {"n_events": ne, f"n_traded_{hi}": nhi, f"n_traded_{lo}": nlo,
           "n_both_traded": int(len(both)),
           f"net_{hi}": round(float(df[f"pnl_{hi}"].sum()), 4),
           f"net_{lo}": round(float(df[f"pnl_{lo}"].sum()), 4)}
    # 净期望：ITT 候选级
    d = (df[f"pnl_{hi}"] - df[f"pnl_{lo}"]).to_numpy(float)
    lo_ci, hi_ci, p = _stat(d, bid)
    out.update({
        f"pnl_mean_{hi}": round(float(df[f"pnl_{hi}"].mean()), 4),
        f"pnl_mean_{lo}": round(float(df[f"pnl_{lo}"].mean()), 4),
        "pnl_diff": round(float(d.mean()), 4),
        "pnl_ci_lo": round(lo_ci, 4), "pnl_ci_hi": round(hi_ci, 4),
        "pnl_ci_excl0": bool(lo_ci > 0 or hi_ci < 0),
        "pnl_p": round(float(p), 4),
    })
    # 胜率 / 快速止损：成交事件口径；配对检验用双方均成交的匹配事件
    for metric in ("win", "qs"):
        dh = df[df[f"n_{hi}"] > 0][f"{metric}_{hi}"]
        dl = df[df[f"n_{lo}"] > 0][f"{metric}_{lo}"]
        out[f"{metric}_rate_{hi}"] = round(100 * float(dh.mean()), 4) if len(dh) else None
        out[f"{metric}_rate_{lo}"] = round(100 * float(dl.mean()), 4) if len(dl) else None
        # 候选级发生率（描述）
        out[f"{metric}_cand_{hi}"] = round(100 * float(df[f"{metric}_{hi}"].fillna(0).mean()), 4)
        out[f"{metric}_cand_{lo}"] = round(100 * float(df[f"{metric}_{lo}"].fillna(0).mean()), 4)
        if len(both):
            dm = (both[f"{metric}_{hi}"] - both[f"{metric}_{lo}"]).to_numpy(float)
            bm = both["block"].to_numpy(int)
            mlo, mhi, mp = _stat(dm, bm)
            out[f"{metric}_diff_matched"] = round(100 * float(dm.mean()), 4)
            out[f"{metric}_ci_lo"] = round(100 * mlo, 4)
            out[f"{metric}_ci_hi"] = round(100 * mhi, 4)
            out[f"{metric}_ci_excl0"] = bool(mlo > 0 or mhi < 0)
            out[f"{metric}_p"] = round(float(mp), 4)
            out[f"{metric}_matched_n"] = int(len(both))
        else:
            out.update({f"{metric}_diff_matched": None, f"{metric}_ci_lo": None,
                        f"{metric}_ci_hi": None, f"{metric}_ci_excl0": False,
                        f"{metric}_p": None, f"{metric}_matched_n": 0})
    return out


def main() -> None:
    print(f"30 天块数 = {NB}；REPS={REPS}；SEED={SEED}")

    # 0) 样本单位
    unit_rows = []
    for mode in ("C", "C1", "C2"):
        d = load_trades(mode)
        per_grid = d.groupby("grid")["event_key"].nunique()
        unit_rows.append({
            "mode": mode,
            "trades_as_reported": int(len(d)),
            "physical_events": int(d["event_key"].nunique()),
            "unique_events_per_grid_sum": int(per_grid.sum()),
            "within_grid_duplicate_candidates": int(
                d.groupby(["grid", "event_key"]).size().gt(1).sum()),
            "within_grid_extra_fills": int(len(d) - per_grid.sum()),
            "grids_covered": int(d["grid"].nunique()),
            "net_u_as_reported": round(float(d["profit_abs"].sum()), 4),
            "wins": int((d["profit_abs"] > 0).sum()),
            "pooled_win_rate_pct": round(100 * (d["profit_abs"] > 0).mean(), 4),
            "quick_stop_corrected": int(d["quick_stop"].sum()),
            "quick_stop_rate_pct": round(100 * d["quick_stop"].mean(), 4),
            "trades_per_event": round(len(d) / d["event_key"].nunique(), 3),
        })
    unit = pd.DataFrame(unit_rows)
    write_csv(unit, "02_样本单位与重复候选_v1.csv")
    print("\n== 样本单位 ==")
    print(unit.to_string(index=False))

    # 1) 逐网格 18 主族 + 9 次族
    rows, ext = [], []
    pooled_frames = {}
    for g, *_ in GRIDS:
        df = itt_grid(g)
        pooled_frames[g] = df
        for hi, lo, tag, fam in (("C1", "C", "C1−C0", "主族"),
                                 ("C2", "C", "C2−C0", "主族"),
                                 ("C2", "C1", "C2−C1", "次族")):
            r = summarise(df, hi, lo)
            r.update({"grid": g, "contrast": tag, "family": fam})
            rows.append(r)
            ext.append((g, df, hi, lo, tag, fam))
    res = pd.DataFrame(rows)
    for metric, _label in (("pnl", "净期望"), ("win", "胜率"), ("qs", "快速止损率")):
        mask = res["family"] == "主族"
        res.loc[mask, f"{metric}_holm"] = [
            round(v, 4) for v in holm(res.loc[mask, f"{metric}_p"].fillna(1.0).tolist())]
    # 单步 maxT：主族 18 个对照共用一组块符号
    for metric, _label in (("pnl", "净期望"), ("win", "胜率"), ("qs", "快速止损率")):
        series, bids = [], []
        for (g, df, hi, lo, tag, fam) in ext:
            if fam != "主族":
                continue
            if metric == "pnl":
                s = (df[f"pnl_{hi}"] - df[f"pnl_{lo}"]).to_numpy(float)
                b = df["block"].to_numpy(int)
            else:
                both = df[(df[f"n_{hi}"] > 0) & (df[f"n_{lo}"] > 0)]
                s = (both[f"{metric}_{hi}"] - both[f"{metric}_{lo}"]).to_numpy(float)
                b = both["block"].to_numpy(int)
            series.append(s)
            bids.append(b)
        mat = np.vstack([block_sums(s, b, NB) for s, b in zip(series, bids)])
        ns = np.array([len(s) for s in series], float)
        obs = float(np.abs(mat.sum(axis=1) / ns).max())
        rng = np.random.default_rng(SEED)
        hits = 0
        for _ in range(REPS):
            signs = rng.choice([-1.0, 1.0], size=NB)
            if float(np.abs((mat * signs).sum(axis=1) / ns).max()) >= obs - 1e-15:
                hits += 1
        p_maxt = (hits + 1) / (REPS + 1)
        res.loc[res["family"] == "主族", f"{metric}_maxt_fwer_p"] = round(p_maxt, 4)
        print(f"主族 maxT（{metric}，18 对照）p = {p_maxt:.4f}")

    # 次族 Holm
    for metric, _label in (("pnl", "净期望"), ("win", "胜率"), ("qs", "快速止损率")):
        mask = res["family"] == "次族"
        res.loc[mask, f"{metric}_holm"] = [
            round(v, 4) for v in holm(res.loc[mask, f"{metric}_p"].fillna(1.0).tolist())]

    cols = ["family", "grid", "contrast", "n_events", "n_traded_C", "n_traded_C1",
            "n_traded_C2", "n_both_traded", "net_C", "net_C1", "net_C2",
            "pnl_mean_C", "pnl_mean_C1", "pnl_mean_C2", "pnl_diff",
            "pnl_ci_lo", "pnl_ci_hi", "pnl_ci_excl0", "pnl_p", "pnl_holm",
            "pnl_maxt_fwer_p",
            "win_rate_C", "win_rate_C1", "win_rate_C2", "win_diff_matched",
            "win_ci_lo", "win_ci_hi", "win_ci_excl0", "win_p", "win_holm", "win_maxt_fwer_p",
            "win_cand_C", "win_cand_C1", "win_cand_C2", "win_matched_n",
            "qs_rate_C", "qs_rate_C1", "qs_rate_C2", "qs_diff_matched",
            "qs_ci_lo", "qs_ci_hi", "qs_ci_excl0", "qs_p", "qs_holm", "qs_maxt_fwer_p",
            "qs_cand_C", "qs_cand_C1", "qs_cand_C2", "qs_matched_n"]
    res = res[cols]
    write_csv(res, "02_逐格配对统计_全对照_v1.csv")
    print("\n== 逐网格主族（C1−C0 / C2−C0）==")
    print(res[res["family"] == "主族"][
        ["grid", "contrast", "n_events", "n_both_traded", "pnl_diff", "pnl_ci_lo",
         "pnl_ci_hi", "pnl_p", "pnl_holm", "win_rate_C", "win_rate_C1",
         "win_diff_matched", "win_p", "qs_rate_C", "qs_rate_C1", "qs_diff_matched",
         "qs_p"]].to_string(index=False))

    # 2) 池化：事件等权（跨 9 网格平均，未成交记 0）
    grids = [g for g, *_ in GRIDS]
    long = pd.concat([pooled_frames[g].assign(grid=g) for g in grids], ignore_index=True)
    agg = long.groupby("event_key", as_index=False).agg(
        symbol=("symbol", "first"), sig_ms=("sig_ms", "min"),
        n_grids=("grid", "nunique"),
        **{f"pnl_{m}": (f"pnl_{m}", "sum") for m in ("C", "C1", "C2")},
        **{f"win_{m}": (f"win_{m}", "sum") for m in ("C", "C1", "C2")},
        **{f"qs_{m}": (f"qs_{m}", "sum") for m in ("C", "C1", "C2")},
        **{f"n_{m}": (f"n_{m}", lambda s: int((s > 0).sum())) for m in ("C", "C1", "C2")},
    )
    # 净额：ITT，按 9 个参数组平均（未成交=0）
    # 胜率/快速止损：只在成交的参数组上定义（分母=该事件成交的网格数），
    # 否则"不交易"会被人为计成非胜/非快速止损，重复 02 早期版本的错误口径。
    for m in ("C", "C1", "C2"):
        agg[f"pnl_{m}"] = agg[f"pnl_{m}"] / float(len(grids))
        tr = agg[f"n_{m}"].replace(0, np.nan)
        agg[f"win_{m}"] = agg[f"win_{m}"] / tr
        agg[f"qs_{m}"] = agg[f"qs_{m}"] / tr
    pv = agg.copy()
    pv["block"] = [block_index(t) for t in pv["sig_ms"]]
    pv = pv[(pv["block"] >= 0) & (pv["block"] < NB)].reset_index(drop=True)

    pool_rows = []
    for hi, lo, tag in (("C1", "C", "C1−C0"), ("C2", "C", "C2−C0"), ("C2", "C1", "C2−C1")):
        r = summarise(pv, hi, lo)
        r["contrast"] = tag
        pool_rows.append(r)
    pooled = pd.DataFrame(pool_rows)
    write_csv(pooled, "02_池化事件级统计_v1.csv")
    print("\n== 池化（事件等权，跨 9 网格平均；净额为网格均值口径）==")
    print(pooled[["contrast", "n_events", "n_traded_C", "n_traded_C1", "n_traded_C2",
                  "n_both_traded", "pnl_mean_C", "pnl_mean_C1", "pnl_mean_C2", "pnl_diff",
                  "pnl_ci_lo", "pnl_ci_hi", "pnl_p",
                  "win_rate_C", "win_rate_C1", "win_rate_C2", "win_diff_matched", "win_p",
                  "qs_rate_C", "qs_rate_C1", "qs_rate_C2", "qs_diff_matched", "qs_p"]].to_string(index=False))

    # 2c) 改善来源分解：过滤掉的原 C0 盈亏 + 共同成交的延迟效应 + 释放槽位新增成交
    def decompose(df, hi, lo, label):
        t_hi = df[f"n_{hi}"] > 0
        t_lo = df[f"n_{lo}"] > 0
        # 原模式有、新模式无：新模式记 0，故差异 = -原模式盈亏（负数亏损被避免时为正贡献）
        a = float(-df.loc[t_lo & ~t_hi, f"pnl_{lo}"].sum())
        b = float((df.loc[t_hi & t_lo, f"pnl_{hi}"] - df.loc[t_hi & t_lo, f"pnl_{lo}"]).sum())
        c = float(df.loc[t_hi & ~t_lo, f"pnl_{hi}"].sum())      # 新模式有、原模式无 → 新增
        return {"scope": label, "contrast": f"{hi}−{lo}",
                "events_lo_only": int((t_lo & ~t_hi).sum()),
                "pnl_lo_only_u": round(a, 4),
                "events_both": int((t_hi & t_lo).sum()),
                "delay_effect_u": round(b, 4),
                "events_hi_only": int((t_hi & ~t_lo).sum()),
                "new_entries_u": round(c, 4),
                "total_diff_u": round(a + b + c, 4)}
    dec_rows = []
    for g, *_ in GRIDS:
        df = pooled_frames[g]
        dec_rows.append(decompose(df, "C1", "C", g))
        dec_rows.append(decompose(df, "C2", "C", g))
        dec_rows.append(decompose(df, "C2", "C1", g))
    dec_rows.append(decompose(pv, "C1", "C", "池化(网格均值)"))
    dec_rows.append(decompose(pv, "C2", "C", "池化(网格均值)"))
    dec_rows.append(decompose(pv, "C2", "C1", "池化(网格均值)"))
    dec = pd.DataFrame(dec_rows)
    write_csv(dec, "02_改善来源分解_v1.csv")
    print("\n== 改善来源分解（C1−C0 / C2−C0 / C2−C1）==")
    print(dec.to_string(index=False))

    # 3) 块级符号
    sign_rows = []
    for g, df, hi, lo, tag, fam in ext:
        d = (df[f"pnl_{hi}"] - df[f"pnl_{lo}"]).to_numpy(float)
        bs = block_sums(d, df["block"].to_numpy(int), NB)
        nz = bs[np.abs(bs) > 1e-12]
        sign_rows.append({
            "contrast": tag, "grid": g, "blocks_nonzero": int(len(nz)),
            "blocks_positive": int((nz > 0).sum()),
            "blocks_negative": int((nz < 0).sum()),
            "positive_share_pct": round(100 * (nz > 0).mean(), 2) if len(nz) else None,
            "largest_positive_block_u": round(float(bs.max()), 4),
            "largest_negative_block_u": round(float(bs.min()), 4),
            "top_block_share_of_total_pct": (round(100 * float(bs.max()) / float(bs.sum()), 2)
                                             if abs(bs.sum()) > 1e-9 else None),
        })
    signs = pd.DataFrame(sign_rows)
    write_csv(signs, "02_块级符号稳定性_v1.csv")
    print("\n== 块级符号（主族）==")
    print(signs[signs.contrast.isin(["C1−C0", "C2−C0"])].to_string(index=False))

    # 4) 跨币
    pv["diff"] = pv["pnl_C1"] - pv["pnl_C"]
    by = (pv.groupby("symbol").agg(n_events=("event_key", "size"),
                                   diff_u=("diff", "sum"),
                                   mean_diff_u=("diff", "mean"))
          .reset_index().sort_values("diff_u", ascending=False))
    by["share_of_improvement_pct"] = 100 * by["diff_u"] / by["diff_u"].sum()
    write_csv(by, "02_跨币贡献_v1.csv")
    rob_rows = []
    total = float(pv["diff"].mean())

    def _drop_and_test(drop_syms, label):
        sub = pv[~pv["symbol"].isin(drop_syms)]
        d = (sub["pnl_C1"] - sub["pnl_C"]).to_numpy(float)
        bid = sub["block"].to_numpy(int)
        lo_ci, hi_ci, p = _stat(d, bid)
        rob_rows.append({"scenario": label, "dropped_coins": ";".join(sorted(drop_syms)),
                         "n_events": int(len(sub)), "mean_diff_u": round(float(d.mean()), 4),
                         "ci_lo": round(lo_ci, 4), "ci_hi": round(hi_ci, 4),
                         "p": round(float(p), 4),
                         "delta_vs_all_pct": round(100 * (float(d.mean()) - total) / abs(total), 2)})
    _drop_and_test([], "全部币")
    _drop_and_test(by.head(1)["symbol"].tolist(), "剔除贡献最大 1 币")
    _drop_and_test(by.head(3)["symbol"].tolist(), "剔除贡献最大 3 币")
    loo = pd.DataFrame([{"dropped": s, "n_events": int((pv["symbol"] != s).sum()),
                         "mean_diff_u": round(float((pv[pv["symbol"] != s]["pnl_C1"]
                                                     - pv[pv["symbol"] != s]["pnl_C"]).mean()), 4)}
                        for s in by["symbol"]]).sort_values("mean_diff_u")
    write_csv(loo, "02_逐币留一_v1.csv")
    rob_rows.append({"scenario": "逐币留一范围（min~max）", "dropped_coins": "每币各一次",
                     "n_events": None, "mean_diff_u": round(float(loo["mean_diff_u"].min()), 4),
                     "ci_lo": round(float(loo["mean_diff_u"].max()), 4),
                     "ci_hi": None, "p": None, "delta_vs_all_pct": None})
    rob = pd.DataFrame(rob_rows)
    write_csv(rob, "02_跨币稳健性_v1.csv")
    print("\n== 跨币稳健性（C1−C0）==")
    print(rob.to_string(index=False))
    print("贡献前 5 币：")
    print(by.head(5).to_string(index=False))

    # 5) 跨时间
    def year_of(ms):
        return pd.Timestamp(int(ms), unit="ms", tz="UTC").year

    def half_of(ms):
        t = pd.Timestamp(int(ms), unit="ms", tz="UTC")
        return f"{t.year}H{1 if t.month <= 6 else 2}"
    trows = []
    for label, fn in (("year", year_of), ("half", half_of)):
        pv[label] = [fn(t) for t in pv["sig_ms"]]
        for per in sorted(pv[label].unique()):
            sub = pv[pv[label] == per]
            trows.append({
                "granularity": label, "period": per, "n_events": int(len(sub)),
                "mean_net_C": round(float(sub["pnl_C"].mean()), 4),
                "mean_net_C1": round(float(sub["pnl_C1"].mean()), 4),
                "mean_net_C2": round(float(sub["pnl_C2"].mean()), 4),
                "diff_C1_C0": round(float((sub["pnl_C1"] - sub["pnl_C"]).mean()), 4),
                "diff_C2_C0": round(float((sub["pnl_C2"] - sub["pnl_C"]).mean()), 4),
                "win_C": round(100 * float(sub["win_C"].mean()), 4),
                "win_C1": round(100 * float(sub["win_C1"].mean()), 4),
                "win_C2": round(100 * float(sub["win_C2"].mean()), 4),
            })
    tdf = pd.DataFrame(trows)
    write_csv(tdf, "02_分年与分半年_v1.csv")
    print("\n== 跨时间（事件等权）==")
    print(tdf.to_string(index=False))

    # 6) 极端盈利单依赖
    ex_rows = []
    for k in (0, 1, 3, 5):
        idx_c1 = np.argsort(-pv["pnl_C1"].to_numpy(float))[:k] if k else np.array([], int)
        idx_c0 = np.argsort(-pv["pnl_C"].to_numpy(float))[:k] if k else np.array([], int)
        drop = set(idx_c1.tolist()) | set(idx_c0.tolist())
        sub = pv.drop(index=list(drop))
        d1 = (sub["pnl_C1"] - sub["pnl_C"]).to_numpy(float)
        d2 = (sub["pnl_C2"] - sub["pnl_C"]).to_numpy(float)
        hi1 = sub[sub["n_C1"] > 0]
        lo0 = sub[sub["n_C"] > 0]
        ex_rows.append({
            "drop_top_k_each_mode": k, "n_events": int(len(sub)),
            "net_C": round(float(sub["pnl_C"].sum()), 4),
            "net_C1": round(float(sub["pnl_C1"].sum()), 4),
            "net_C2": round(float(sub["pnl_C2"].sum()), 4),
            "mean_diff_C1_C0": round(float(d1.mean()), 4),
            "mean_diff_C2_C0": round(float(d2.mean()), 4),
            "win_C": round(100 * float(lo0["win_C"].mean()), 4),
            "win_C1": round(100 * float(hi1["win_C1"].mean()), 4),
        })
    ex = pd.DataFrame(ex_rows)
    write_csv(ex, "02_极端盈利单依赖_v1.csv")
    print("\n== 极端盈利单依赖（事件级，k=0/1/3/5）==")
    print(ex.to_string(index=False))

    # 7) 摘要
    prim = res[res["family"] == "主族"]
    summary = {
        "method": {"block_days": BLOCK_DAYS, "n_blocks": NB, "reps": REPS, "seed": SEED,
                   "bootstrap": "30 天块重采样 5000 次，95% 百分位 CI",
                   "permutation": "块级符号翻转 5000 次",
                   "multiplicity": "Holm-Bonferroni + 单步 maxT FWER",
                   "source": "常数与方法复用 FT-ENTRY-AUDIT-04/05/06/07（未按结果更换）"},
        "sample_unit": unit.to_dict("records"),
        "primary_family_tests": int(len(prim)),
        "secondary_family_tests": int((res["family"] == "次族").sum()),
        "primary_holm_lt_005": {m: int((prim[m + "_holm"] < 0.05).sum()) for m in ("pnl", "win", "qs")},
        "primary_ci_excl0": {m: int(prim[m + "_ci_excl0"].sum()) for m in ("pnl", "win", "qs")},
        "primary_positive_grids": {
            "C1−C0_pnl": int(((prim["contrast"] == "C1−C0") & (prim["pnl_diff"] > 0)).sum()),
            "C2−C0_pnl": int(((prim["contrast"] == "C2−C0") & (prim["pnl_diff"] > 0)).sum()),
            "C1−C0_win_matched": int(((prim["contrast"] == "C1−C0") & (prim["win_diff_matched"] > 0)).sum()),
            "C2−C0_win_matched": int(((prim["contrast"] == "C2−C0") & (prim["win_diff_matched"] > 0)).sum()),
            "C1−C0_qs_matched": int(((prim["contrast"] == "C1−C0") & (prim["qs_diff_matched"] < 0)).sum()),
            "C2−C0_qs_matched": int(((prim["contrast"] == "C2−C0") & (prim["qs_diff_matched"] < 0)).sum()),
        },
        "pooled": pooled.to_dict("records"),
        "cross_coin": rob.to_dict("records"),
        "cross_time": tdf.to_dict("records"),
        "extreme_profit": ex.to_dict("records"),
        "decomposition": dec.to_dict("records"),
        "note": "逐格 ITT 配对：C1/C2 未成交事件记 0（政策口径）；胜率/快速止损仅在成交事件上定义，"
                "配对 p 值来自双方均成交的匹配事件。池化净额为跨 9 网格均值口径（=网格汇总/9），"
                "不是账户复利。",
    }
    jdump(os.path.join(OUT, "02_统计摘要_v1.json"), summary)
    print("\n完成 02")


if __name__ == "__main__":
    main()
