# -*- coding: utf-8 -*-
"""FT-ENTRY-AUDIT-02 · 06 时间块不确定性、置换检验与多重比较

方法：
  A. 30 天日历块配对 Bootstrap（1000 次，种子 20261020）——同一块内两组共享同一段行情，
     按块配对重采样，保留跨币同期相关与组间相关。
  B. 块级配对置换检验（1000 次）——零假设下块内标签可交换，得到总差异的精确 p 值。
  C. 按标的聚类的 Bootstrap（对跨币相关做第二种口径）。
  D. Holm-Bonferroni 校正 4 个"实验组 vs 基准"比较。
  E. 对照：把逐笔当独立样本的朴素 t 检验（错误口径，仅用于展示被高估的显著性）。

只读 FT-ENTRY-01 逐笔。产出：
  out/24_时间块不确定性与多重比较_v1.csv
  out/25_块级差异分布_v1.csv
  out/26_朴素口径对照_v1.csv
"""
from __future__ import annotations

import csv
import os
import re

import numpy as np
import pandas as pd

csv.field_size_limit(10 ** 9)

BASE = "/Users/boos/Desktop/威科夫因子共振趋势"
ENTRY = os.path.join(BASE, "evidence/FT-ENTRY-01")
OUT = os.path.join(BASE, "evidence/FT-ENTRY-AUDIT-02/out")

ARMS = ["WyckoffBaseline", "WyckoffEntryA1_NearTop", "WyckoffEntryA2_AtBoxHigh",
        "WyckoffEntryC1_StopSweep", "WyckoffEntryC2_StopBar"]
TESTS = ARMS[1:]
SEED = 20261020
NREP = 1000
BLOCK_DAYS = 30


def load(arm):
    df = pd.read_csv(os.path.join(ENTRY, "out", f"trades_{arm}.csv"), encoding="utf-8-sig")
    if "pair" not in df.columns:
        df = df.rename(columns={df.columns[0]: "pair"})
    df["profit_abs"] = df["profit_abs"].astype(float)
    df["close_dt"] = pd.to_datetime(df["close_date"], utc=True).dt.tz_localize(None)
    return df


def block_series(df, start, end):
    """按平仓日聚合成日度 PnL，再切成 30 天块（块内求和）。"""
    s = df.groupby(df["close_dt"].dt.floor("D"))["profit_abs"].sum()
    idx = pd.date_range(start, end, freq="D")
    s = s.reindex(idx, fill_value=0.0)
    nb = int(np.ceil(len(s) / BLOCK_DAYS))
    vals = np.zeros(nb)
    for i in range(nb):
        vals[i] = s.iloc[i * BLOCK_DAYS:(i + 1) * BLOCK_DAYS].sum()
    return vals


def holm(pvals):
    m = len(pvals)
    order = np.argsort(pvals)
    adj = np.empty(m)
    running = 0.0
    for rank, i in enumerate(order):
        val = (m - rank) * pvals[i]
        running = max(running, val)
        adj[i] = min(1.0, running)
    return adj


def main():
    os.makedirs(OUT, exist_ok=True)
    T = {a: load(a) for a in ARMS}
    start = min(t["close_dt"].min() for t in T.values()).normalize()
    end = max(t["close_dt"].max() for t in T.values()).normalize()
    B = {a: block_series(T[a], start, end) for a in ARMS}
    nb = len(B["WyckoffBaseline"])
    rng = np.random.default_rng(SEED)

    # ---------- A. 块配对 Bootstrap ----------
    boot = {}
    for a in ARMS:
        draws = np.empty(NREP)
        for r in range(NREP):
            idx = rng.integers(0, nb, nb)
            draws[r] = B[a][idx].sum()
        boot[a] = draws

    rows = []
    for a in TESTS:
        base_draw = boot["WyckoffBaseline"]
        arm_draw = boot[a]
        # 配对：同一批块索引同时用于两组 —— 重新抽一次以保证配对
        d_draw = np.empty(NREP)
        r2 = np.random.default_rng(SEED + 1)
        for r in range(NREP):
            idx = r2.integers(0, nb, nb)
            d_draw[r] = B[a][idx].sum() - B["WyckoffBaseline"][idx].sum()
        point = float(B[a].sum() - B["WyckoffBaseline"].sum())
        lo, hi = np.percentile(d_draw, [2.5, 97.5])
        # 块级配对置换
        obs = point
        cnt = 0
        r3 = np.random.default_rng(SEED + 2)
        for _ in range(NREP):
            flip = r3.random(nb) < 0.5
            d = np.where(flip, B["WyckoffBaseline"] - B[a], B[a] - B["WyckoffBaseline"])
            if abs(d.sum()) >= abs(obs) - 1e-12:
                cnt += 1
        p_perm = (cnt + 1) / (NREP + 1)
        rows.append({
            "arm": a,
            "blocks": nb, "block_days": BLOCK_DAYS,
            "arm_total_u": round(float(B[a].sum()), 4),
            "baseline_total_u": round(float(B["WyckoffBaseline"].sum()), 4),
            "diff_u": round(point, 4),
            "diff_ci95_lo": round(float(lo), 4),
            "diff_ci95_hi": round(float(hi), 4),
            "ci_excludes_zero": bool(lo > 0 or hi < 0),
            "p_block_permutation": round(float(p_perm), 4),
            "arm_boot_ci95": f"[{np.percentile(arm_draw, 2.5):.2f}, {np.percentile(arm_draw, 97.5):.2f}]",
            "baseline_boot_ci95": f"[{np.percentile(base_draw, 2.5):.2f}, {np.percentile(base_draw, 97.5):.2f}]",
        })

    # ---------- C. 按标的聚类 Bootstrap ----------
    for r in rows:
        a = r["arm"]
        syms = sorted(set(T[a]["pair"]) | set(T["WyckoffBaseline"]["pair"]))
        d_draw = np.empty(NREP)
        r4 = np.random.default_rng(SEED + 3)
        for i in range(NREP):
            pick = r4.choice(len(syms), len(syms), replace=True)
            chosen = [syms[j] for j in pick]
            da = T[a][T[a]["pair"].isin(chosen)]["profit_abs"].sum()
            db = T["WyckoffBaseline"][T["WyckoffBaseline"]["pair"].isin(chosen)]["profit_abs"].sum()
            d_draw[i] = da - db
        lo, hi = np.percentile(d_draw, [2.5, 97.5])
        r["symbol_cluster_ci95"] = f"[{lo:.2f}, {hi:.2f}]"
        r["symbol_cluster_excludes_zero"] = bool(lo > 0 or hi < 0)

    pv = [r["p_block_permutation"] for r in rows]
    adj = holm(pv)
    for r, ad in zip(rows, adj):
        r["p_holm"] = round(float(ad), 4)
        r["significant_after_holm"] = bool(ad < 0.05)

    pd.DataFrame(rows).to_csv(os.path.join(OUT, "24_时间块不确定性与多重比较_v1.csv"),
                              index=False, encoding="utf-8-sig")

    # ---------- 25 块级差异分布 ----------
    blk = pd.DataFrame({"block_index": np.arange(nb)})
    for a in ARMS:
        blk[a] = np.round(B[a], 4)
    blk["diff_A1_minus_base"] = np.round(B["WyckoffEntryA1_NearTop"] - B["WyckoffBaseline"], 4)
    blk["diff_A2_minus_base"] = np.round(B["WyckoffEntryA2_AtBoxHigh"] - B["WyckoffBaseline"], 4)
    blk["diff_C1_minus_base"] = np.round(B["WyckoffEntryC1_StopSweep"] - B["WyckoffBaseline"], 4)
    blk["diff_C2_minus_base"] = np.round(B["WyckoffEntryC2_StopBar"] - B["WyckoffBaseline"], 4)
    blk.to_csv(os.path.join(OUT, "25_块级差异分布_v1.csv"), index=False, encoding="utf-8-sig")

    # ---------- 26 朴素口径对照（错误示范）----------
    naive = []
    for a in TESTS:
        x = T[a]["profit_abs"].to_numpy()
        y = T["WyckoffBaseline"]["profit_abs"].to_numpy()
        # Welch t
        def welch(u, v):
            nu, nv = len(u), len(v)
            mu, mv = u.mean(), v.mean()
            su, sv = u.var(ddof=1), v.var(ddof=1)
            se = np.sqrt(su / nu + sv / nv)
            if se == 0:
                return 0.0, 1.0
            t = (mu - mv) / se
            # 正态近似双侧 p
            from math import erfc, sqrt
            p = erfc(abs(t) / sqrt(2))
            return t, p
        t, p = welch(x, y)
        naive.append({
            "arm": a,
            "per_trade_mean_diff_u": round(float(x.mean() - y.mean()), 4),
            "welch_t": round(float(t), 4),
            "naive_p_two_sided": round(float(p), 4),
            "note": "把同币同期的相关交易当独立样本，p 被系统性低估；不可用于结论",
        })
    pd.DataFrame(naive).to_csv(os.path.join(OUT, "26_朴素口径对照_v1.csv"),
                               index=False, encoding="utf-8-sig")

    print(f"块数 = {nb}（{BLOCK_DAYS} 天/块，{start.date()} ~ {end.date()}）")
    print("\n=== 块配对 Bootstrap + 块级置换 + Holm ===")
    for r in rows:
        print(f"  {r['arm']:32s} 差={r['diff_u']:+9.2f} 95%CI[{r['diff_ci95_lo']:+9.2f},{r['diff_ci95_hi']:+9.2f}] "
              f"含0={'是' if not r['ci_excludes_zero'] else '否'} p_perm={r['p_block_permutation']:.3f} "
              f"p_holm={r['p_holm']:.3f} 显著={'是' if r['significant_after_holm'] else '否'}")
        print(f"      标的聚类CI {r['symbol_cluster_ci95']} 含0={'是' if not r['symbol_cluster_excludes_zero'] else '否'}")
    print("\n=== 朴素逐笔口径（错误示范）===")
    for r in naive:
        print(f"  {r['arm']:32s} 均值差={r['per_trade_mean_diff_u']:+8.3f} t={r['welch_t']:+6.3f} p={r['naive_p_two_sided']:.4f}")


if __name__ == "__main__":
    main()
