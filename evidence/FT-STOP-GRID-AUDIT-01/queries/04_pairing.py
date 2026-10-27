# -*- coding: utf-8 -*-
"""FT-STOP-GRID-AUDIT-01 / 04_配对比较与统计

严格按冻结计划第三、五、六节：
  · 配对键 (pair, 事件ID)；P1=四档齐全、P2=两两交集、P3=全样本
  · 时间块=13 个自然季度；块内一票，避免逐笔伪独立
  · 块 bootstrap 10,000 次（种子固定）给差值 95% 区间
  · 6 个两两比较 × 9 个入场组 = 54 个检验，Benjamini-Hochberg FDR q=0.05
  · 同时把「总 R 差」拆成「都已实现部分」与「一档已实现/另一档期末」两部分
"""
from __future__ import annotations

import json
import math
from itertools import combinations

import numpy as np
import pandas as pd

from _lib import OUT, TIERS, equity_curve, load_all

RNG_SEED = 20261022
N_BOOT = 10000
PAIRS = list(combinations(TIERS, 2))


def quarter_of(ms: int) -> str:
    ts = pd.Timestamp(ms, unit="ms", tz="UTC")
    q = (ts.month - 1) // 3 + 1
    return f"{ts.year}Q{q}"


def bh_fdr(pvals: list[float]) -> list[float]:
    m = len(pvals)
    if m == 0:
        return []
    order = np.argsort(pvals)
    q = np.empty(m, dtype=float)
    prev = 1.0
    for rank in range(m, 0, -1):
        i = order[rank - 1]
        val = pvals[i] * m / rank
        prev = min(prev, val)
        q[i] = prev
    return list(q)


def binom_two_sided(k: int, n: int) -> float:
    """精确二项双侧检验 p（p0=0.5），n 很小，直接算。"""
    if n == 0:
        return 1.0
    probs = [math.comb(n, i) * 0.5 ** n for i in range(n + 1)]
    obs = probs[k]
    return float(min(1.0, sum(p for p in probs if p <= obs * 1.0000001)))


def block_bootstrap_median(vals: np.ndarray, blocks: np.ndarray, rng: np.random.Generator) -> tuple[float, float]:
    uniq = np.unique(blocks)
    idx_by_block = {b: np.where(blocks == b)[0] for b in uniq}
    meds = np.empty(N_BOOT)
    for i in range(N_BOOT):
        pick = rng.choice(uniq, size=len(uniq), replace=True)
        sel = np.concatenate([idx_by_block[b] for b in pick])
        meds[i] = np.median(vals[sel])
    return float(np.percentile(meds, 2.5)), float(np.percentile(meds, 97.5))


def event_table(d: pd.DataFrame) -> pd.DataFrame:
    """事件级：同一 (档位, 参数组, pair, 事件ID) 若有多笔则 R 求和并标记。"""
    key = ["档位", "参数组", "pair", "事件ID"]
    g = d.groupby(key, as_index=False).agg(
        R=("R", "sum"),
        已实现R=("R", lambda x: float(x[d.loc[x.index, "是否期末结算"].eq(False)].sum())),
        期末R=("R", lambda x: float(x[d.loc[x.index, "是否期末结算"].eq(True)].sum())),
        笔数=("R", "size"),
        期末笔数=("是否期末结算", "sum"),
        open_ms=("open_ms", "min"),
    )
    g["季度"] = g["open_ms"].map(quarter_of)
    return g


def compare(sub: pd.DataFrame, a: str, b: str, rng: np.random.Generator) -> dict:
    """在给定配对集上比较档位 a 与 b。sub 已按事件对齐成宽表。"""
    diffs = (sub[a] - sub[b]).to_numpy(dtype=float)
    n = len(diffs)
    if n == 0:
        return {"n": 0, "差值中位": None, "差值均值": None, "差值合计": None,
                "a更好事件数": 0, "b更好事件数": 0, "持平事件数": 0, "块数": 0,
                "a更好的块数": 0, "b更好的块数": 0, "块符号检验p": 1.0,
                "bootstrap中位差值_2.5%": None, "bootstrap中位差值_97.5%": None,
                "bootstrap区间不含0": False, "都已实现子集n": 0,
                "都已实现子集中位差": None, "一档期末另一档已实现事件数": 0}
    blocks = sub["季度"].to_numpy()
    uniq = sorted(set(blocks))
    per_block = {q: float(diffs[blocks == q].sum()) for q in uniq}
    pos_blocks = sum(1 for v in per_block.values() if v > 0)
    neg_blocks = sum(1 for v in per_block.values() if v < 0)
    lo, hi = block_bootstrap_median(diffs, blocks, rng)
    # 都已实现的子集
    both_real = sub[(sub["期末笔数_" + a] == 0) & (sub["期末笔数_" + b] == 0)]
    d_real = (both_real[a] - both_real[b]).to_numpy(dtype=float) if len(both_real) else np.array([])
    return {
        "n": int(n),
        "差值中位": round(float(np.median(diffs)), 6),
        "差值均值": round(float(diffs.mean()), 6),
        "差值合计": round(float(diffs.sum()), 6),
        "a更好事件数": int((diffs > 0).sum()),
        "b更好事件数": int((diffs < 0).sum()),
        "持平事件数": int((diffs == 0).sum()),
        "块数": len(uniq),
        "a更好的块数": pos_blocks,
        "b更好的块数": neg_blocks,
        "块符号检验p": round(binom_two_sided(max(pos_blocks, neg_blocks), pos_blocks + neg_blocks), 6),
        "bootstrap中位差值_2.5%": round(lo, 6),
        "bootstrap中位差值_97.5%": round(hi, 6),
        "bootstrap区间不含0": bool(lo > 0 or hi < 0),
        "都已实现子集n": int(len(both_real)),
        "都已实现子集中位差": round(float(np.median(d_real)), 6) if len(d_real) else None,
        "一档期末另一档已实现事件数": int(n - len(both_real)),
    }


def build_wide(ev: pd.DataFrame, suffix: str, tiers: list[str]) -> pd.DataFrame:
    """把同一参数组内指定档位对齐到 (pair, 事件ID) 宽表；只保留所有指定档位都有的行。"""
    sub = ev[ev["参数组"] == suffix]
    parts = []
    for t in tiers:
        g = sub[sub["档位"] == t][["pair", "事件ID", "R", "期末笔数", "季度"]].rename(
            columns={"R": t, "期末笔数": "期末笔数_" + t})
        parts.append(g)
    wide = parts[0]
    for nxt in parts[1:]:
        wide = wide.merge(nxt.drop(columns="季度"), on=["pair", "事件ID"], how="inner")
    return wide.reset_index(drop=True)


def main() -> dict:
    df, missing = load_all()
    d = equity_curve(df)
    ev = event_table(d)

    rng = np.random.default_rng(RNG_SEED)
    tests = []

    # ---------- P1 四档齐全 ----------
    for s in sorted(ev["参数组"].unique()):
        wide = build_wide(ev, s, TIERS)
        for a, b in PAIRS:
            r = compare(wide, a, b, rng)
            r.update({"参数组": s, "比较": f"{a}-{b}", "配对集": "P1四档齐全"})
            tests.append(r)
    p1 = pd.DataFrame(tests)
    p1["q值"] = bh_fdr(list(p1["块符号检验p"])) if len(p1) else []

    # ---------- P2 两两交集 ----------
    tests2 = []
    for s in sorted(ev["参数组"].unique()):
        for a, b in PAIRS:
            wide = build_wide(ev, s, [a, b])
            r = compare(wide, a, b, rng)
            r.update({"参数组": s, "比较": f"{a}-{b}", "配对集": "P2两两交集"})
            tests2.append(r)
    p2 = pd.DataFrame(tests2)

    # ---------- P3 全样本（不配对） ----------
    p3 = []
    for t in TIERS:
        g = ev[ev["档位"] == t]
        p3.append({"档位": t, "事件数": int(len(g)), "R合计": round(float(g["R"].sum()), 6),
                   "R中位": round(float(g["R"].median()), 6),
                   "已实现R合计": round(float(g["已实现R"].sum()), 6),
                   "期末R合计": round(float(g["期末R"].sum()), 6)})

    # ---------- 9 组稳健性：各档在 P1 上的中位 R ----------
    p1_wide_all = {}
    for s in sorted(ev["参数组"].unique()):
        wide = build_wide(ev, s, TIERS)
        for t in TIERS:
            p1_wide_all.setdefault(t, []).append(float(wide[t].median()) if len(wide) else None)
    robust = {t: {"组数": len([x for x in v if x is not None]),
                  "中位R的九组中位": round(float(np.median([x for x in v if x is not None])), 6) if any(x is not None for x in v) else None,
                  "中位R的九组均值": round(float(np.mean([x for x in v if x is not None])), 6) if any(x is not None for x in v) else None,
                  "逐组": [None if x is None else round(x, 6) for x in v]}
              for t, v in p1_wide_all.items()}

    # ---------- P1 样本量 ----------
    p1_n = {}
    for s in sorted(ev["参数组"].unique()):
        wide = build_wide(ev, s, TIERS)
        p1_n[s] = int(len(wide))

    p1.to_csv(OUT / "04_P1配对比较.csv", index=False, encoding="utf-8-sig")
    p2.to_csv(OUT / "04_P2配对比较.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(p3).to_csv(OUT / "04_P3全样本.csv", index=False, encoding="utf-8-sig")

    sig = p1[(p1["q值"] < 0.05) & (p1["bootstrap区间不含0"])] if len(p1) else p1
    return {
        "一_P1四档齐全": p1.to_dict("records"),
        "二_P2两两交集": p2.to_dict("records"),
        "三_P3全样本": p3,
        "四_九组稳健": robust,
        "五_P1样本量": p1_n,
        "六_通过FDR且区间不含0的检验数": int(len(sig)),
        "七_检验族大小": int(len(p1)),
        "八_随机种子": RNG_SEED,
        "九_bootstrap次数": N_BOOT,
        "十_缺失组": missing,
    }


if __name__ == "__main__":
    r = main()
    p = OUT / "04_配对与统计.json"
    with open(p, "w", encoding="utf-8") as f:
        json.dump(r, f, ensure_ascii=False, indent=2, default=str)
    print(f"已写出 {p} ({p.stat().st_size} 字节)")
    print("P1 样本量：", r["五_P1样本量"])
    print("检验族大小：", r["七_检验族大小"], " 通过FDR且区间不含0：", r["六_通过FDR且区间不含0的检验数"])
    print("\nP3 全样本：")
    print(pd.DataFrame(r["三_P3全样本"]).to_string(index=False))
    print("\n九组稳健：")
    print(json.dumps(r["四_九组稳健"], ensure_ascii=False, indent=1))
