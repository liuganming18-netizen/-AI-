# -*- coding: utf-8 -*-
"""04 A 组时间块不确定性与多重比较（本轮实际 9 个 A 对照，不复用上一轮统计）。

方法：
- 30 天日历块（2021-12-01 起，共 38 块）配对：同一块索引同时取 A 与基线；
- 配对 Bootstrap 5000 次（种子 20261020）→ 95% 百分位 CI；
- 块级符号翻转置换 5000 次 → 单格 p；
- Holm-Bonferroni 校正 9 个比较；
- 单步 maxT 置换（同一组块符号同时施加到 9 格）→ 控制族错误率，且保留格间相关性。
输出：out/04_*.csv
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from _common import (  # noqa: E402
    BASELINE, CELLS_A, OUT, block_permutation_p, block_series, holm, jdump,
    load_trades, n_blocks, paired_bootstrap, write_csv,
)

REPS = 5000
SEED = 20261020


def main() -> None:
    nb = n_blocks(30)
    base = load_trades(BASELINE)
    base_blocks = block_series(base, nb)

    diffs = {}
    rows = []
    for code, strat, delta, kvol in CELLS_A:
        df = load_trades(strat)
        b = block_series(df, nb)
        d = b - base_blocks
        diffs[code] = d
        lo, hi, sd = paired_bootstrap(d, REPS, SEED)
        p = block_permutation_p(d, REPS, SEED)
        # 朴素逐笔口径（错误但常见），用于说明结论对方法不敏感
        a_vals = df["profit_abs"].to_numpy()
        b_vals = base["profit_abs"].to_numpy()
        t_stat = (a_vals.mean() - b_vals.mean()) / np.sqrt(
            a_vals.var(ddof=1) / len(a_vals) + b_vals.var(ddof=1) / len(b_vals))
        rows.append({
            "code": code, "strategy": strat, "delta": delta, "k_vol": kvol,
            "diff_total_u": round(float(d.sum()), 4),
            "blocks_with_activity": int(((b != 0) | (base_blocks != 0)).sum()),
            "block_mean_u": round(float(d.mean()), 4),
            "block_sd_u": round(float(d.std(ddof=1)), 4),
            "boot_ci_lo": round(lo, 4),
            "boot_ci_hi": round(hi, 4),
            "boot_ci_excludes_0": bool(lo > 0 or hi < 0),
            "perm_p": round(p, 4),
            "naive_t": round(float(t_stat), 3),
            "naive_p_two_sided": round(float(2 * (1 - _norm_cdf(abs(t_stat)))), 4),
        })
    res = pd.DataFrame(rows)
    res["holm_p"] = [round(v, 4) for v in holm(res["perm_p"].tolist())]

    # ── 单步 maxT（保留格间相关） ──────────────────────────────────────
    mat = np.vstack([diffs[c] for c in res["code"]])  # 9 x nb
    obs_max = float(np.abs(mat.sum(axis=1)).max())
    rng = np.random.default_rng(SEED)
    hits = 0
    for _ in range(REPS):
        signs = rng.choice([-1.0, 1.0], size=nb)
        perm = np.abs((mat * signs).sum(axis=1)).max()
        if perm >= obs_max:
            hits += 1
    maxt_p = hits / REPS

    res["maxt_fwer_p"] = round(maxt_p, 4)
    write_csv(res, "04_时间块与多重比较_v1.csv")
    print(res.to_string(index=False))
    print(f"\n单步 maxT（9 格族错误率）p = {maxt_p:.4f}；观测最大 |差异| = {obs_max:.2f} U")

    # 块级差异分布（用于报告展示离散度）
    dist = pd.DataFrame({c: diffs[c] for c in diffs})
    dist.index.name = "block_idx"
    write_csv(dist.reset_index(), "04_块级差异分布_v1.csv")

    jdump(os.path.join(OUT, "04_统计摘要_v1.json"), {
        "block_days": 30,
        "n_blocks": nb,
        "reps": REPS,
        "seed": SEED,
        "results": res.to_dict("records"),
        "maxt_fwer_p": maxt_p,
        "observed_max_abs_diff_u": obs_max,
        "note": "块内同时包含 A 与基线的已实现盈亏；配对差分按块求和后做 Bootstrap/置换",
    })
    print("完成")


def _norm_cdf(x: float) -> float:
    from math import erf, sqrt
    return 0.5 * (1 + erf(x / sqrt(2)))


if __name__ == "__main__":
    main()
