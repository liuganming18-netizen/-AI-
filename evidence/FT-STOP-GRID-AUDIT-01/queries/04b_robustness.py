# -*- coding: utf-8 -*-
"""FT-STOP-GRID-AUDIT-01 / 04b_稳定性判据机械裁决

把冻结计划第八节的 7 条判据逐条算成布尔值，不靠人眼判断。
  C1 P1 四档齐全集上已实现 R 中位数优于其余三档
  C2 块 bootstrap 差值 95% 区间不含 0（对全部三个对手）
  C3 9 个入场参数组中 ≥7 组方向一致
  C4 13 个时间块中 ≥9 块方向一致
  C5 缩尾 ±5R 与剔除首尾 1% 后排序不变
  C6 剔除贡献最大的 3 个币后排序不变
  C7 已实现 R > 0 且期末浮盈占总 R < 30%
另按冻结计划第七节给出极端值依赖的三套口径对照。
"""
from __future__ import annotations

import json
from itertools import combinations

import numpy as np
import pandas as pd

from _lib import OUT, TIERS, equity_curve, load_all

PAIRS = list(combinations(TIERS, 2))


def agg(sub: pd.DataFrame) -> dict:
    real = sub[~sub["是否期末结算"]]
    tot = float(sub["R"].sum())
    return {
        "笔数": int(len(sub)),
        "已实现R": round(float(real["R"].sum()), 6),
        "期末R": round(float(sub[sub["是否期末结算"]]["R"].sum()), 6),
        "总R": round(tot, 6),
        "全口径净额USDT": round(float(sub["profit_abs"].sum()), 4),
        "已实现中位R": round(float(real["R"].median()), 6) if len(real) else None,
    }


def rank_of(d: pd.DataFrame, key: str) -> dict:
    return {t: agg(d[d["档位"] == t])[key] for t in TIERS}


def main() -> dict:
    df, missing = load_all()
    d = equity_curve(df)
    d = d.sort_values("open_ms").reset_index(drop=True)

    # ---------- 基准 ----------
    base = {t: agg(d[d["档位"] == t]) for t in TIERS}

    # ---------- C1 已实现中位 R 最高者 ----------
    med = {t: base[t]["已实现中位R"] for t in TIERS}
    best_c1 = max(med, key=lambda t: med[t])
    c1 = {"各档已实现中位R": med, "最高档": best_c1,
          "通过": bool(med[best_c1] > max(v for k, v in med.items() if k != best_c1)),
          "说明": "中位R 被止损机制锁在 −1R 附近，四档差异仅 0.018R，虽有一档最高但实务上不可区分"}

    # ---------- C2/C3/C4 基于 P1 配对 ----------

    p1 = pd.read_csv(OUT / "04_P1配对比较.csv", encoding="utf-8-sig")

    def verdict(tier: str, key: str) -> dict:
        rows = p1[p1["比较"].str.contains(tier)]
        rows = rows[(rows["比较"].str.startswith(tier)) | (rows["比较"].str.endswith(tier))]
        # 该档相对对手是否更好：差值为 a-b，若 tier 是 b 则取负
        better = []
        for _, r in rows.iterrows():
            a, b = r["比较"].split("-")
            v = r[key]
            if pd.isna(v):
                continue
            better.append((v < 0) if a == tier else (v > 0))
        return {"组数": len(better), "更好组数": int(sum(better))}

    c2 = {}
    for t in TIERS:
        rows = p1[(p1["比较"].str.startswith(t + "-")) | (p1["比较"].str.endswith("-" + t))]
        opp = sorted({r["比较"].replace(t, "").strip("-") for _, r in rows.iterrows()})
        per_opp = {}
        for o in opp:
            sub = rows[rows["比较"].isin([f"{t}-{o}", f"{o}-{t}"])]
            per_opp[o] = int(sub["bootstrap区间不含0"].sum())
        c2[t] = {"各对手区间不含0组数": per_opp, "通过": all(v >= 7 for v in per_opp.values())}

    # 重新用干净的算法算 C3/C4（上面的写法在混合方向时不可靠，这里以对手为单位重算）
    c3, c4 = {}, {}
    for t in TIERS:
        c3_ok, c4_ok, det3, det4 = True, True, {}, {}
        for o in TIERS:
            if o == t:
                continue
            a, b = (t, o) if (t, o) in PAIRS else (o, t)
            sub = p1[p1["比较"] == f"{a}-{b}"]
            if sub.empty:
                continue
            # 该档更好 ⇔ 若它是 a，差值应为正；若它是 b，差值应为负
            sign = 1 if a == t else -1
            n_better = int((sub["差值中位"] * sign > 0).sum())
            det3[o] = n_better
            c3_ok = c3_ok and n_better >= 7
            # 块：该档更好的块数（9 组求和后与 9×13/2 比）
            bw = int(((sub["a更好的块数"] if a == t else sub["b更好的块数"])).sum())
            tot_blk = int((sub["a更好的块数"] + sub["b更好的块数"]).sum())
            det4[o] = f"{bw}/{tot_blk}"
            c4_ok = c4_ok and tot_blk > 0 and bw / tot_blk >= 9 / 13
        c3[t] = {"各对手9组中方向一致的组数": det3, "通过": c3_ok}
        c4[t] = {"各对手该档更好的块数占比": det4, "通过": c4_ok}

    # ---------- C5 缩尾 / 剔除首尾 1% ----------
    wins = d.copy()
    wins["R"] = wins["R"].clip(-5, 5)
    wins["profit_abs"] = wins["profit_abs"] * np.where(
        d["R"].abs() > 5, np.where(d["R"].abs() > 1e-12, 5 / d["R"].abs(), 1.0), 1.0)
    w_rank = rank_of(wins, "总R")

    trim = []
    for t in TIERS:
        g = d[d["档位"] == t].copy()
        lo, hi = g["R"].quantile(0.01), g["R"].quantile(0.99)
        trim.append(g[(g["R"] >= lo) & (g["R"] <= hi)])
    td = pd.concat(trim, ignore_index=True)
    t_rank = rank_of(td, "总R")

    # ---------- C6 剔除贡献最大的 3 个币 ----------
    contrib = {t: d[d["档位"] == t].groupby("pair")["R"].sum().sort_values(ascending=False) for t in TIERS}
    drop = pd.concat([d[(d["档位"] == t) & (~d["pair"].isin(contrib[t].head(3).index))] for t in TIERS],
                     ignore_index=True)
    dr_rank = rank_of(drop, "总R")

    order_base = sorted(TIERS, key=lambda t: -base[t]["总R"])
    c5 = {"基准排序": order_base,
          "缩尾±5R后": sorted(TIERS, key=lambda t: -w_rank[t]),
          "剔除首尾1%后": sorted(TIERS, key=lambda t: -t_rank[t]),
          "缩尾后数值": w_rank, "剔除后数值": t_rank}
    c5["通过"] = (c5["基准排序"] == c5["缩尾±5R后"] == c5["剔除首尾1%后"])
    c6 = {"基准排序": order_base, "剔除前3币后": sorted(TIERS, key=lambda t: -dr_rank[t]),
          "剔除后数值": dr_rank,
          "被剔除的币": {t: list(contrib[t].head(3).index) for t in TIERS}}
    c6["通过"] = c6["基准排序"] == c6["剔除前3币后"]

    # ---------- C7 ----------
    c7 = {}
    for t in TIERS:
        b = base[t]
        share = (b["期末R"] / b["总R"]) if abs(b["总R"]) > 1e-12 else None
        c7[t] = {"已实现R": b["已实现R"], "期末R占总R": round(share, 6) if share is not None else None,
                 "通过": bool(b["已实现R"] > 0 and share is not None and share < 0.30)}

    verdicts = {}
    for t in TIERS:
        conds = {
            "C1_中位R最优": bool(t == best_c1),
            "C2_区间不含0": c2[t]["通过"],
            "C3_9组方向一致": c3[t]["通过"],
            "C4_13块方向一致": c4[t]["通过"],
            "C5_去极值排序不变": c5["通过"],
            "C6_剔3币排序不变": c6["通过"],
            "C7_已实现为正且浮盈占比<30%": c7[t]["通过"],
        }
        verdicts[t] = {"逐条": conds, "满足条数": int(sum(conds.values())), "全部满足": all(conds.values())}

    return {
        "一_C1": c1, "二_C2": c2, "三_C3": c3, "四_C4": c4,
        "五_C5": c5, "六_C6": c6, "七_C7": c7,
        "八_逐档裁决": verdicts,
        "九_基准各档": base,
        "十_缺失组": missing,
    }


if __name__ == "__main__":
    r = main()
    p = OUT / "04b_稳定性判据裁决.json"
    with open(p, "w", encoding="utf-8") as f:
        json.dump(r, f, ensure_ascii=False, indent=2, default=str)
    print(f"已写出 {p} ({p.stat().st_size} 字节)")
    for t, v in r["八_逐档裁决"].items():
        print(f"\n{t}: 满足 {v['满足条数']}/7  全部满足={v['全部满足']}")
        for k, ok in v["逐条"].items():
            print("   ", "PASS" if ok else "FAIL", k)
    print("\n基准各档：")
    print(pd.DataFrame(r["九_基准各档"]).T.to_string())
    print("\nC5:", json.dumps({k: v for k, v in r["五_C5"].items() if k != "缩尾后数值"}, ensure_ascii=False))
    print("C6:", json.dumps({k: v for k, v in r["六_C6"].items() if k != "剔除后数值"}, ensure_ascii=False)[:600])
