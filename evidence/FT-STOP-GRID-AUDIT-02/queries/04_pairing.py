# -*- coding: utf-8 -*-
"""04 配对比较 + 与上一轮（无跟踪）的 Δ 分解。

冻结口径：
  配对键 = (参数组, pair, 事件ID)；不含档位
  P1 = 四档齐全的事件；P2 = 两两交集；P3 = 各档全样本
  块   = 季度（13 块）；块 bootstrap 10000 次，种子 20261022
  Δ 分解（同档同参数组对齐 GRID-02 vs GRID-01）：
        Δ总R = Δ两边都成交 + Δ仅02新增 + Δ仅01减少
  判据 C8：Δ总R > 0 时，Δ两边都成交 / Δ总R ≥ 50% ⇒ 改善主要来自盈利保护
"""
from __future__ import annotations

import json
import math

import numpy as np
import pandas as pd

import _lib as L

OUT = L.OUT
SEED = 20261022
NBOOT = 10000
UP01 = L.BASE_DIR / "evidence/FT-STOP-GRID-01"

# ---------- 载入 ----------
def load_side(root, prefix="trades_") -> pd.DataFrame:
    frames = []
    for n in L.ALL:
        p = root / f"out/{prefix}{n}.csv"
        if not p.exists() or p.stat().st_size == 0:
            continue
        df = pd.read_csv(p, encoding="utf-8-sig")
        if df.empty:
            continue
        df["策略"] = n
        df["档位"] = L.tier_of(n)
        df["参数组"] = L.suffix_of(n)
        df["事件ID"] = df["enter_tag"].map(L.event_id)
        df["open_ms"] = pd.to_numeric(df["open_timestamp"], errors="coerce").astype("int64")
        df["close_ms"] = pd.to_numeric(df["close_timestamp"], errors="coerce").astype("int64")
        frames.append(df)
    out = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    return out


def add_r(df: pd.DataFrame) -> pd.DataFrame:
    """按组重算权益与 R（与 03 同一口径）。"""
    parts = []
    for _, g in df.groupby("策略", sort=False):
        g = g.sort_values(["close_ms", "open_ms"]).reset_index(drop=True)
        closed = g.loc[g["exit_reason"].astype(str) != "force_exit", ["close_ms", "profit_abs"]]
        closed = closed.groupby("close_ms", as_index=False)["profit_abs"].sum()
        cum_ms = closed["close_ms"].to_numpy()
        cum_val = L.INIT_EQUITY + np.cumsum(closed["profit_abs"].to_numpy())
        idx = np.searchsorted(cum_ms, g["open_ms"].to_numpy(), side="right") - 1
        eq = np.where(idx >= 0, cum_val[np.clip(idx, 0, None)], L.INIT_EQUITY)
        g["equity_at_entry"] = eq
        g["R"] = g["profit_abs"] / (eq * L.RISK_RATE)
        g["季度"] = pd.to_datetime(g["open_ms"], unit="ms", utc=True).dt.tz_localize(None) \
            .dt.to_period("Q").astype(str)
        parts.append(g)
    return pd.concat(parts, ignore_index=True)


def boot_median_by_block(vals: np.ndarray, blocks: np.ndarray, rng) -> tuple[float, float, float]:
    """块 bootstrap 的中位差值的 95% 区间。"""
    uniq = np.unique(blocks)
    idx_by = {b: np.where(blocks == b)[0] for b in uniq}
    n = len(uniq)
    meds = np.empty(NBOOT)
    for i in range(NBOOT):
        pick = rng.integers(0, n, n)
        sel = np.concatenate([idx_by[uniq[k]] for k in pick])
        meds[i] = np.median(vals[sel])
    return float(np.median(vals)), float(np.percentile(meds, 2.5)), float(np.percentile(meds, 97.5))


def sign_test_p(pos: int, neg: int) -> float | None:
    """精确二项符号检验（两侧，忽略打平块）。"""
    n = pos + neg
    if n == 0:
        return None
    k = min(pos, neg)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n
    return float(min(1.0, 2 * tail))


def bh_fdr(p: np.ndarray, q: float = 0.05) -> np.ndarray:
    n = len(p)
    order = np.argsort(p)
    ranked = p[order] * n / (np.arange(n) + 1)
    # 单调化
    for i in range(n - 2, -1, -1):
        ranked[i] = min(ranked[i], ranked[i + 1])
    out = np.empty(n, dtype=bool)
    out[order] = ranked <= q
    return out


def main() -> None:
    d2 = add_r(load_side(L.UP))
    d1 = add_r(load_side(UP01))
    have2 = sorted(d2["策略"].unique()) if not d2.empty else []
    res: dict = {
        "工单": "FT-STOP-GRID-AUDIT-02 / 04 配对比较与 Δ 分解",
        "GRID02 已落盘组数": len(have2),
        "GRID02 逐笔数": int(len(d2)),
        "GRID01 逐笔数": int(len(d1)),
        "种子": SEED, "bootstrap次数": NBOOT,
    }

    key = ["参数组", "pair", "事件ID"]
    d2v = d2[d2["事件ID"].notna()]
    d1v = d1[d1["事件ID"].notna()]
    res["事件ID缺失笔数"] = {"GRID02": int(len(d2) - len(d2v)), "GRID01": int(len(d1) - len(d1v))}

    # ---------- P1：四档齐全 ----------
    cnt = d2v.groupby(key + ["档位"]).size().unstack("档位")
    p1_keys = cnt.dropna().index
    p1 = d2v.set_index(key).loc[p1_keys].reset_index()
    res["P1"] = {
        "事件数": int(len(p1_keys)),
        "说明": "同一参数组内四档都成交的事件（不含档位）",
        "各档已实现R合计": {t: float(p1[(p1["档位"] == t) & (p1["exit_reason"] != "force_exit")]["R"].sum())
                       for t in L.TIERS if t in set(p1["档位"])},
        "各档已实现R中位": {t: float(p1[(p1["档位"] == t) & (p1["exit_reason"] != "force_exit")]["R"].median())
                       for t in L.TIERS if t in set(p1["档位"])},
        "各档总R合计": {t: float(p1[p1["档位"] == t]["R"].sum()) for t in L.TIERS if t in set(p1["档位"])},
    }

    # ---------- 两两配对（按参数组，块 bootstrap） ----------
    rng = np.random.default_rng(SEED)
    rows = []
    for tier_a, tier_b in [(a, b) for i, a in enumerate(L.TIERS) for b in L.TIERS[i + 1:]]:
        for grp in L.SUFFIXES:
            ga = d2v[(d2v["档位"] == tier_a) & (d2v["参数组"] == grp)].set_index(key)
            gb = d2v[(d2v["档位"] == tier_b) & (d2v["参数组"] == grp)].set_index(key)
            common = ga.index.intersection(gb.index)
            if len(common) < 10:
                rows.append({"档位A": tier_a, "档位B": tier_b, "参数组": grp, "共同笔数": len(common),
                             "状态": "样本不足"})
                continue
            a = ga.loc[common]
            b = gb.loc[common]
            # 同键去重（极端情况下同键多笔）
            a = a[~a.index.duplicated()]
            b = b[~b.index.duplicated()]
            common = a.index.intersection(b.index)
            a, b = a.loc[common], b.loc[common]
            diff = (a["R"] - b["R"]).to_numpy()
            blocks = a["季度"].to_numpy()
            med, lo, hi = boot_median_by_block(diff, blocks, rng)
            # 块符号检验
            s = pd.DataFrame({"q": blocks, "d": diff}).groupby("q")["d"].median()
            pos = int((s > 0).sum())
            neg = int((s < 0).sum())
            rows.append({
                "档位A": tier_a, "档位B": tier_b, "参数组": grp,
                "共同笔数": int(len(diff)),
                "A已实现R合计": float(a.loc[a["exit_reason"] != "force_exit", "R"].sum()),
                "B已实现R合计": float(b.loc[b["exit_reason"] != "force_exit", "R"].sum()),
                "ΔR均值": float(diff.mean()), "ΔR中位": med,
                "CI下": lo, "CI上": hi,
                "区间不含0": bool(lo > 0 or hi < 0),
                "块正": pos, "块负": neg,
                "块符号p": sign_test_p(pos, neg),
                "状态": "ok",
            })
    pair_df = pd.DataFrame(rows)
    pair_df.to_csv(OUT / "04_两两配对.csv", index=False, encoding="utf-8-sig")

    ok = pair_df[pair_df["状态"] == "ok"]
    # BH-FDR：按档位对分组（每档 3 个对手 × 9 参数组 = 27 个检验）
    if len(ok):
        ok = ok.copy()
        p = ok["块符号p"].fillna(1.0).to_numpy()
        ok["BH通过"] = bh_fdr(p, 0.05)
        ok.to_csv(OUT / "04_两两配对_BH.csv", index=False, encoding="utf-8-sig")

    # 每档 vs 三个对手：满足"区间不含0"的参数组个数
    c2 = {}
    for t in L.TIERS:
        sub = ok[((ok["档位A"] == t) | (ok["档位B"] == t))]
        if sub.empty:
            continue
        # 方向：该档更好则记正
        sub = sub.copy()
        sub["该档更好"] = np.where(sub["档位A"] == t, sub["ΔR中位"] > 0, sub["ΔR中位"] < 0)
        sub["区间排除0"] = sub["区间不含0"]
        c2[t] = {
            "比较次数": int(len(sub)),
            "该档更好且区间排除0": int((sub["该档更好"] & sub["区间排除0"]).sum()),
            "按参数组（3 个对手全胜的组数）": int(
                sub.groupby("参数组").apply(lambda g: bool((g["该档更好"] & g["区间排除0"]).all()),
                                          include_groups=False).sum()),
            "中位ΔR中位": float(sub["ΔR中位"].median()),
        }
    res["C2_配对显著性"] = c2

    # ---------- Δ 分解：GRID-02 vs GRID-01 ----------
    dec = []
    for grp in L.SUFFIXES:
        for t in L.TIERS:
            a = d2v[(d2v["档位"] == t) & (d2v["参数组"] == grp)].set_index(key)
            b = d1v[(d1v["档位"] == t) & (d1v["参数组"] == grp)].set_index(key)
            a = a[~a.index.duplicated()]
            b = b[~b.index.duplicated()]
            common = a.index.intersection(b.index)
            only_a = a.index.difference(b.index)
            only_b = b.index.difference(a.index)
            both = float((a.loc[common, "R"] - b.loc[common, "R"]).sum())
            new_a = float(a.loc[only_a, "R"].sum())
            lost_b = float(-b.loc[only_b, "R"].sum())
            dec.append({
                "档位": t, "参数组": grp,
                "02笔数": int(len(a)), "01笔数": int(len(b)),
                "两边都成交笔数": int(len(common)),
                "仅02新增笔数": int(len(only_a)), "仅01减少笔数": int(len(only_b)),
                "Δ两边都成交": both, "Δ仅02新增": new_a, "Δ仅01减少": lost_b,
                "Δ总R": both + new_a + lost_b,
                "02总R": float(a["R"].sum()), "01总R": float(b["R"].sum()),
                "02已实现R": float(a.loc[a["exit_reason"] != "force_exit", "R"].sum()),
                "01已实现R": float(b.loc[b["exit_reason"] != "force_exit", "R"].sum()),
                "02期末R": float(a.loc[a["exit_reason"] == "force_exit", "R"].sum()),
                "01期末R": float(b.loc[b["exit_reason"] == "force_exit", "R"].sum()),
            })
    dec_df = pd.DataFrame(dec)
    dec_df["Δ两边都成交占Δ总R"] = np.where(dec_df["Δ总R"].abs() > 1e-12,
                                    dec_df["Δ两边都成交"] / dec_df["Δ总R"], np.nan)
    dec_df["C8_盈利保护为主"] = np.where(dec_df["Δ总R"] > 0,
                                  dec_df["Δ两边都成交占Δ总R"] >= 0.5, np.nan)
    dec_df.to_csv(OUT / "04_Δ分解.csv", index=False, encoding="utf-8-sig")

    res["Δ分解"] = {
        "恒等式校验最大误差": float((dec_df["Δ总R"] -
                                (dec_df["02总R"] - dec_df["01总R"])).abs().max()),
        "分档汇总": {},
    }
    for t in L.TIERS:
        s = dec_df[dec_df["档位"] == t]
        if s.empty:
            continue
        res["Δ分解"]["分档汇总"][t] = {
            "组数": int(len(s)),
            "Δ总R中位": float(s["Δ总R"].median()),
            "Δ两边都成交中位": float(s["Δ两边都成交"].median()),
            "Δ仅02新增中位": float(s["Δ仅02新增"].median()),
            "Δ仅01减少中位": float(s["Δ仅01减少"].median()),
            "Δ两边都成交占Δ总R中位": float(s["Δ两边都成交占Δ总R"].median()),
            "C8满足组数": int((s["C8_盈利保护为主"] == True).sum()),
            "Δ已实现R中位": float((s["02已实现R"] - s["01已实现R"]).median()),
            "Δ期末R中位": float((s["02期末R"] - s["01期末R"]).median()),
            "笔数变化中位": float((s["02笔数"] - s["01笔数"]).median()),
        }

    # ---------- 成交率 / 标的数 / 槽位 / 时长 ----------
    meta = []
    for grp in L.SUFFIXES:
        for t in L.TIERS:
            a = d2v[(d2v["档位"] == t) & (d2v["参数组"] == grp)]
            b = d1v[(d1v["档位"] == t) & (d1v["参数组"] == grp)]
            if a.empty and b.empty:
                continue
            meta.append({
                "档位": t, "参数组": grp,
                "02笔数": int(len(a)), "01笔数": int(len(b)),
                "02标的数": int(a["pair"].nunique()) if len(a) else 0,
                "01标的数": int(b["pair"].nunique()) if len(b) else 0,
                "02中位持仓小时": float((a["trade_duration"] / 60).median()) if len(a) else None,
                "01中位持仓小时": float((b["trade_duration"] / 60).median()) if len(b) else None,
                "02资金费": float(a["funding_fees"].sum()) if len(a) else 0.0,
                "01资金费": float(b["funding_fees"].sum()) if len(b) else 0.0,
                "02期末结算笔数": int((a["exit_reason"] == "force_exit").sum()) if len(a) else 0,
                "01期末结算笔数": int((b["exit_reason"] == "force_exit").sum()) if len(b) else 0,
                "02盈利笔数": int((a["profit_abs"] > 0).sum()) if len(a) else 0,
                "01盈利笔数": int((b["profit_abs"] > 0).sum()) if len(b) else 0,
            })
    meta_df = pd.DataFrame(meta)
    meta_df.to_csv(OUT / "04_路径变化.csv", index=False, encoding="utf-8-sig")
    if len(meta_df):
        res["路径变化（分档中位）"] = {
            t: {
                "笔数02": float(s["02笔数"].median()), "笔数01": float(s["01笔数"].median()),
                "标的数02": float(s["02标的数"].median()), "标的数01": float(s["01标的数"].median()),
                "持仓小时02": float(s["02中位持仓小时"].median()),
                "持仓小时01": float(s["01中位持仓小时"].median()),
                "资金费02": float(s["02资金费"].median()), "资金费01": float(s["01资金费"].median()),
                "期末结算笔数02": float(s["02期末结算笔数"].median()),
                "期末结算笔数01": float(s["01期末结算笔数"].median()),
                "盈利笔数02": float(s["02盈利笔数"].median()),
                "盈利笔数01": float(s["01盈利笔数"].median()),
            } for t in L.TIERS for s in [meta_df[meta_df["档位"] == t]] if len(s)
        }

    with open(OUT / "04_配对与分解.json", "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=2, default=str)

    print("GRID02 组数", len(have2), " 笔数", len(d2), " GRID01 笔数", len(d1))
    print("P1 事件数", res["P1"]["事件数"])
    if len(dec_df):
        print("Δ分解 恒等式误差", res["Δ分解"]["恒等式校验最大误差"])
        for t, v in res["Δ分解"]["分档汇总"].items():
            print(f"  {t}: Δ总R中位={v['Δ总R中位']:.2f} 两边都成交={v['Δ两边都成交中位']:.2f} "
                  f"新增={v['Δ仅02新增中位']:.2f} 减少={v['Δ仅01减少中位']:.2f} "
                  f"C8满足={v['C8满足组数']}/{v['组数']}")


if __name__ == "__main__":
    main()
