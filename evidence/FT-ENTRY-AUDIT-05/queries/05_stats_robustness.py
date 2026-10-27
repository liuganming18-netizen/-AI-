# -*- coding: utf-8 -*-
"""05 统计检验：块级不确定�� + 多重比较 + 跨币跨时间 + 极端盈利单依赖。

方法完全复用 FT-ENTRY-AUDIT-04/queries/04_stats_A.py 与 03_robustness_A.py，
**不按结果更换统计方法**：
- 30 天日历块（2021-12-01 起，共 38 块）配对；
- 配对 Bootstrap 5000 次（种子 20261020）→ 95% 百分位 CI；
- 块级符号翻转置换 5000 次 → 单格 p；
- Holm-Bonferroni 校正；
- 单步 maxT 置换（同一组块符号同时施加到全部比较格）→ 控制族错误率。

比较对象（本轮工单的核心问题）：
- 主比较 = 04（成交约束）− 03（无约束），逐 A 格；
- 参照比较 = 03 A − 03 Baseline（沿用 AUDIT-04 的口径）。

额外稳健性：
- 分年 / 分半年；
- 跨币集中度（最大贡献币占比）；
- 极端盈利单依赖阶梯（去前 1 / 前 3 / 前 5 大盈利单）。

只读，不重跑回测。
"""
from __future__ import annotations

import os
import sys
from typing import List

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from _common import (  # noqa: E402
    BASELINE, BLOCK_DAYS, CELLS_A, OUT, OUT03, OUT04, REPS, SEED,
    block_permutation_p, block_series, holm, jdump, load_trades, maxt_fwer,
    n_blocks, paired_bootstrap, write_csv,
)

import datetime as _dt


def ts_to_date(ms: int) -> str:
    return _dt.datetime.fromtimestamp(ms / 1000, tz=_dt.timezone.utc).strftime("%Y-%m-%d")


def year_of(ts: int) -> int:
    return _dt.datetime.fromtimestamp(ts / 1000, tz=_dt.timezone.utc).year


def half_of(ts: int) -> str:
    d = _dt.datetime.fromtimestamp(ts / 1000, tz=_dt.timezone.utc)
    return f"{d.year}H{1 if d.month <= 6 else 2}"


def main() -> None:
    nb = n_blocks(BLOCK_DAYS)
    base3 = load_trades(BASELINE, OUT03)
    base4 = load_trades(BASELINE, OUT04)
    base3_blocks = block_series(base3, nb)
    base4_blocks = block_series(base4, nb)

    print(f"30 天块数 = {nb}")
    print(f"基线 03 成交 {len(base3)} 笔 / 04 成交 {len(base4)} 笔；"
          f"基线两批逐笔一致 = {len(base3) == len(base4)}")

    # ── 主比较：04 − 03（同一组 A）──
    diffs, rows = {}, []
    for code, strat, delta, kvol in CELLS_A:
        d3 = load_trades(strat, OUT03)
        d4 = load_trades(strat, OUT04)
        if d3.empty or d4.empty:
            continue
        b3 = block_series(d3, nb)
        b4 = block_series(d4, nb)
        d = b4 - b3
        diffs[code] = d
        lo, hi, sd = paired_bootstrap(d, REPS, SEED)
        p = block_permutation_p(d, REPS, SEED)
        p3 = d3["profit_abs"].to_numpy()
        p4 = d4["profit_abs"].to_numpy()
        t_stat = ((p4.mean() - p3.mean()) /
                  np.sqrt(p4.var(ddof=1) / len(p4) + p3.var(ddof=1) / len(p3)))
        rows.append({
            "code": code, "strategy": strat, "delta": delta, "k_vol": kvol,
            "trades_03": len(d3), "trades_04": len(d4),
            "diff_total_u": round(float(d.sum()), 4),
            "blocks_with_activity": int(((b3 != 0) | (b4 != 0)).sum()),
            "block_mean_u": round(float(d.mean()), 4),
            "block_sd_u": round(float(d.std(ddof=1)), 4),
            "boot_ci_lo": round(lo, 4), "boot_ci_hi": round(hi, 4),
            "boot_ci_excludes_0": bool(lo > 0 or hi < 0),
            "perm_p": round(p, 4),
            "naive_t": round(float(t_stat), 3),
            "win_rate_pp": (round(100 * (p4 > 0).mean() - 100 * (p3 > 0).mean(), 4)
                            if len(p3) and len(p4) else None),
            "expectancy_03": round(float(p3.mean()), 4),
            "expectancy_04": round(float(p4.mean()), 4),
        })

    res = pd.DataFrame(rows)
    if res.empty:
        print("无 04 结果，退出")
        return
    res["holm_p"] = [round(v, 4) for v in holm(res["perm_p"].tolist())]
    maxt_p, obs_max = maxt_fwer(diffs, REPS, SEED)
    res["maxt_fwer_p"] = round(maxt_p, 4)
    write_csv(res, "05_成交约束块级统计_v1.csv")
    print("\n== 主比较：04 − 03（成交约束效应）==")
    print(res.to_string(index=False))
    print(f"\n单步 maxT（{len(diffs)} 格族错误率）p = {maxt_p:.4f}；观测最大 |差异| = {obs_max:.2f} U")

    dist = pd.DataFrame(diffs)
    dist.index.name = "block_idx"
    write_csv(dist.reset_index(), "05_块级差异分布_04减03_v1.csv")

    # 块级符号统计
    sign_rows = []
    for code in diffs:
        d = diffs[code]
        sign_rows.append({
            "code": code,
            "blocks_positive": int((d > 0).sum()),
            "blocks_negative": int((d < 0).sum()),
            "blocks_zero": int((d == 0).sum()),
            "largest_positive_block_u": round(float(d.max()), 4),
            "largest_negative_block_u": round(float(d.min()), 4),
            "top_block_share_pct": (round(100 * float(np.max(d)) / float(d.sum()), 2)
                                    if d.sum() > 1e-9 else None),
        })
    signs = pd.DataFrame(sign_rows)
    write_csv(signs, "05_块级符号统计_v1.csv")

    # ── 参照比较：03 A − 03 Baseline（沿用 AUDIT-04 口径，用于对照）──
    ref_diffs, ref_rows = {}, []
    for code, strat, delta, kvol in CELLS_A:
        d3 = load_trades(strat, OUT03)
        if d3.empty:
            continue
        b = block_series(d3, nb)
        d = b - base3_blocks
        ref_diffs[code] = d
        lo, hi, sd = paired_bootstrap(d, REPS, SEED)
        p = block_permutation_p(d, REPS, SEED)
        ref_rows.append({
            "code": code, "strategy": strat,
            "diff_total_u": round(float(d.sum()), 4),
            "block_mean_u": round(float(d.mean()), 4),
            "boot_ci_lo": round(lo, 4), "boot_ci_hi": round(hi, 4),
            "boot_ci_excludes_0": bool(lo > 0 or hi < 0),
            "perm_p": round(p, 4),
        })
    ref = pd.DataFrame(ref_rows)
    if not ref.empty:
        ref["holm_p"] = [round(v, 4) for v in holm(ref["perm_p"].tolist())]
        ref_maxt, ref_obs = maxt_fwer(ref_diffs, REPS, SEED)
        ref["maxt_fwer_p"] = round(ref_maxt, 4)
        write_csv(ref, "05_参照比较_03A减03基线_v1.csv")
        print("\n== 参照比较：03 A − 03 Baseline ==")
        print(ref.to_string(index=False))
        print(f"单步 maxT p = {ref_maxt:.4f}")

    # ── 分年 / 分半年 ──
    period_rows = []
    for code, strat, delta, kvol in CELLS_A:
        d3 = load_trades(strat, OUT03)
        d4 = load_trades(strat, OUT04)
        if d3.empty or d4.empty:
            continue
        for label, fn in (("year", year_of), ("half", half_of)):
            m3 = d3.assign(period=d3["close_ts"].map(fn))
            m4 = d4.assign(period=d4["close_ts"].map(fn))
            for per in sorted(set(m3["period"]) | set(m4["period"])):
                g3 = m3[m3["period"] == per]
                g4 = m4[m4["period"] == per]
                period_rows.append({
                    "code": code, "strategy": strat, "granularity": label, "period": per,
                    "trades_03": len(g3), "net_03": round(float(g3["profit_abs"].sum()), 4),
                    "trades_04": len(g4), "net_04": round(float(g4["profit_abs"].sum()), 4),
                    "delta_u": round(float(g4["profit_abs"].sum() - g3["profit_abs"].sum()), 4),
                    "win_rate_03": (round(100 * (g3["profit_abs"] > 0).mean(), 2) if len(g3) else None),
                    "win_rate_04": (round(100 * (g4["profit_abs"] > 0).mean(), 2) if len(g4) else None),
                })
    per = pd.DataFrame(period_rows)
    write_csv(per, "05_分年与分半年_v1.csv")
    print("\n== 分年（04 − 03）==")
    if not per.empty:
        y = per[per["granularity"] == "year"]
        print(y[["code", "period", "trades_03", "trades_04", "net_03", "net_04", "delta_u"]].to_string(index=False))

    # ── 跨币集中度 ──
    conc_rows = []
    for code, strat, delta, kvol in CELLS_A:
        d3 = load_trades(strat, OUT03)
        d4 = load_trades(strat, OUT04)
        if d3.empty or d4.empty:
            continue
        for tag, df in (("03", d3), ("04", d4)):
            by = df.groupby("pair")["profit_abs"].sum().sort_values(ascending=False)
            tot = float(df["profit_abs"].sum())
            top1 = float(by.iloc[0]) if len(by) else 0.0
            top3 = float(by.head(3).sum()) if len(by) else 0.0
            conc_rows.append({
                "code": code, "strategy": strat, "batch": tag,
                "n_pairs_with_trades": int(len(by)),
                "net_u": round(tot, 4),
                "top1_pair": by.index[0] if len(by) else None,
                "top1_u": round(top1, 4),
                "top1_share_pct": (round(100 * top1 / tot, 2) if abs(tot) > 1e-9 else None),
                "top3_u": round(top3, 4),
                "top3_share_pct": (round(100 * top3 / tot, 2) if abs(tot) > 1e-9 else None),
            })
    conc = pd.DataFrame(conc_rows)
    write_csv(conc, "05_跨币集中度_v1.csv")
    print("\n== 跨币集中度 ==")
    if not conc.empty:
        print(conc.to_string(index=False))

    # ── 极端盈利单依赖阶梯 ──
    ladder_rows = []
    for code, strat, delta, kvol in CELLS_A:
        d3 = load_trades(strat, OUT03)
        d4 = load_trades(strat, OUT04)
        if d3.empty or d4.empty:
            continue
        for tag, df in (("03", d3), ("04", d4)):
            p = df["profit_abs"].to_numpy()
            for k in (0, 1, 3, 5):
                if k == 0:
                    keep = p
                else:
                    idx = np.argsort(-p)[:k]
                    mask = np.ones(len(p), dtype=bool)
                    mask[idx] = False
                    keep = p[mask]
                ladder_rows.append({
                    "code": code, "strategy": strat, "batch": tag, "drop_top_k": k,
                    "trades": int(len(keep)),
                    "net_u": round(float(keep.sum()), 4),
                    "expectancy_u": round(float(keep.mean()), 4) if len(keep) else None,
                    "win_rate_pct": (round(100 * (keep > 0).mean(), 4) if len(keep) else None),
                })
    lad = pd.DataFrame(ladder_rows)
    write_csv(lad, "05_极端盈利单剔除阶梯_v1.csv")
    print("\n== 极端盈利单依赖阶梯 ==")
    if not lad.empty:
        print(lad.to_string(index=False))

    jdump(os.path.join(OUT, "05_统计摘要_v1.json"), {
        "method": {
            "block_days": BLOCK_DAYS, "n_blocks": nb, "reps": REPS, "seed": SEED,
            "bootstrap": "配对 Bootstrap 5000 次，95% 百分位 CI",
            "permutation": "块级符号翻转置换 5000 次",
            "multiplicity": "Holm-Bonferroni + 单步 maxT 族错误率",
            "source": "与 FT-ENTRY-AUDIT-04/queries/04_stats_A.py 完全相同，未按结果更换检验",
        },
        "primary_04_minus_03": res.to_dict("records"),
        "reference_03A_minus_03base": ref.to_dict("records") if not ref.empty else [],
        "maxt_fwer_p_primary": maxt_p,
        "n_primary_ci_excluding_0": int(res["boot_ci_excludes_0"].sum()),
        "n_primary_holm_lt_005": int((res["holm_p"] < 0.05).sum()),
        "baseline_03_equals_04": bool(len(base3) == len(base4)),
        "note": "主比较检验的是『成交约束本身』的效应，不是 A 路线相对基线的效应；两者分开报",
    })
    print("\n完成")


if __name__ == "__main__":
    main()
