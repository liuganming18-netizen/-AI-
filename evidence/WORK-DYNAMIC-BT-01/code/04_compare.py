# -*- coding: utf-8 -*-
"""WORK-DYNAMIC-BT-01｜A/B 与 C 的配对比较（逐组配对，不把重叠交易加总当账户）。

原则
────
· 9 组入场参数**逐组配对**：同一 `param_group` 下 A/B/C 的入场候选完全相同
  （同一 C1 入场、同一准入窗口），因此可以按 `(pair, open_timestamp)` 一对一配对；
· **不**把 9 组重叠交易的利润相加冒充一个账户收益 —— 每个 param_group 单独看；
· 配对差异只回答「同一次入场，换一种跟踪距离，结果差多少」，不是独立样本外证据；
· 全部为**探索性**结果：区间已被前序研究接触过，不能当独立有效性结论。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List

import numpy as np
import pandas as pd

EXP = Path("/Users/boos/Desktop/威科夫因子共振趋势/evidence/WORK-DYNAMIC-BT-01")
OUT = EXP / "out"

METRIC_COLS = ["net_total", "net_realized", "net_unrealized_end", "max_dd_pct",
               "sharpe", "sortino", "calmar", "profit_factor", "win_rate_pct",
               "trades_total", "avg_holding_hours", "fees_abs", "funding_abs",
               "flat_day_ratio_pct", "avg_capital_usage_pct", "annual_return_pct"]


def load_trades(group: str, p: str) -> pd.DataFrame:
    prefix = {"A": "Wyckoff_C1_S05_D05", "B": "Wyckoff_C1_S05_D20",
              "C": "Wyckoff_C1_S05_DYN", "P1": "Wyckoff_C1_S05_D08",
              "P2": "Wyckoff_C1_S05_DYN999"}[group]
    f = OUT / f"trades_{prefix}_{p}.csv"
    if not f.exists():
        return pd.DataFrame()
    df = pd.read_csv(f)
    if len(df) == 0:
        return df
    df["pair"] = df["pair"].astype(str)
    return df


def paired(df_x: pd.DataFrame, df_y: pd.DataFrame, tag_x: str, tag_y: str) -> dict:
    """按 (pair, open_timestamp) 一对一配对，返回配对差异统计。"""
    if len(df_x) == 0 or len(df_y) == 0:
        return {"n_matched": 0, "note": "一侧零成交，无法配对"}
    kx = df_x.set_index(["pair", "open_timestamp"])
    ky = df_y.set_index(["pair", "open_timestamp"])
    common = kx.index.intersection(ky.index)
    only_x = kx.index.difference(ky.index)
    only_y = ky.index.difference(kx.index)
    if len(common) == 0:
        return {"n_matched": 0, "only_x": int(len(only_x)), "only_y": int(len(only_y)),
                "note": "无共同入场，无法配对"}
    d = (ky.loc[common, "profit_abs"] - kx.loc[common, "profit_abs"]).sort_values()
    return {
        "n_matched": int(len(common)),
        "only_x": int(len(only_x)),
        "only_y": int(len(only_y)),
        "delta_sum": round(float(d.sum()), 4),
        "delta_mean": round(float(d.mean()), 4),
        "delta_median": round(float(d.median()), 4),
        f"mean_delta_{tag_y}_minus_{tag_x}": round(float(d.mean()), 4),
        f"median_delta_{tag_y}_minus_{tag_x}": round(float(d.median()), 4),
        f"sum_delta_{tag_y}_minus_{tag_x}": round(float(d.sum()), 4),
        "n_delta_positive": int((d > 0).sum()),
        "n_delta_negative": int((d < 0).sum()),
        "n_delta_zero": int((d == 0).sum()),
        "worst_delta": round(float(d.iloc[0]), 4),
        "best_delta": round(float(d.iloc[-1]), 4),
    }


def segment_delta(df_x: pd.DataFrame, df_y: pd.DataFrame, by: str) -> pd.DataFrame:
    if len(df_x) == 0 or len(df_y) == 0:
        return pd.DataFrame()
    kx = df_x.set_index(["pair", "open_timestamp"])
    ky = df_y.set_index(["pair", "open_timestamp"])
    common = kx.index.intersection(ky.index)
    if len(common) == 0:
        return pd.DataFrame()
    d = pd.DataFrame({
        "pair": [i[0] for i in common],
        "open_timestamp": [i[1] for i in common],
        "delta": (ky.loc[common, "profit_abs"] - kx.loc[common, "profit_abs"]).to_numpy(),
    })
    if by == "pair":
        g = d.groupby("pair")["delta"].agg(["count", "sum", "mean"]).reset_index()
        return g.sort_values("sum")
    d["quarter"] = pd.to_datetime(d["open_timestamp"], unit="ms", utc=True).dt.to_period("Q").astype(str)
    g = d.groupby("quarter")["delta"].agg(["count", "sum", "mean"]).reset_index()
    return g.sort_values("quarter")


def main() -> None:
    metrics = pd.read_csv(OUT / "03_分组指标总表.csv")
    metrics = metrics[metrics["status"] == "OK"]

    params = ["Vs04_Z02", "Vs04_Z04", "Vs04_Z06", "Vs06_Z02", "Vs06_Z04",
              "Vs06_Z06", "Vs08_Z02", "Vs08_Z04", "Vs08_Z06"]

    # ── 1. 指标宽表：每个 param_group × 组别 ──
    wide_rows: List[dict] = []
    for p in params:
        sub = metrics[metrics["param_group"] == p]
        row = {"param_group": p}
        for _, r in sub.iterrows():
            for c in METRIC_COLS:
                row[f"{r['group']}__{c}"] = r.get(c)
        wide_rows.append(row)
    wide = pd.DataFrame(wide_rows)
    wide.to_csv(OUT / "07_指标宽表_按入场参数.csv", index=False, encoding="utf-8-sig")

    # ── 2. 配对差异（逐 param_group）──
    pair_rows: List[dict] = []
    seg_pair: List[pd.DataFrame] = []
    seg_qtr: List[pd.DataFrame] = []
    for p in params:
        dx = {g: load_trades(g, p) for g in ("A", "B", "C", "P1", "P2")}
        for base, tgt in (("A", "C"), ("B", "C"), ("A", "B"), ("P1", "P2")):
            st = paired(dx[base], dx[tgt], base, tgt)
            rec = {"param_group": p, "baseline": base, "treatment": tgt, **st}
            pair_rows.append(rec)
        for base, tgt in (("A", "C"), ("B", "C")):
            for by, bucket in (("pair", seg_pair), ("quarter", seg_qtr)):
                g = segment_delta(dx[base], dx[tgt], by)
                if len(g):
                    g["param_group"] = p
                    g["baseline"] = base
                    g["treatment"] = tgt
                    g["by"] = by
                    bucket.append(g)

    pairs = pd.DataFrame(pair_rows)
    pairs.to_csv(OUT / "08_配对差异_逐入场参数.csv", index=False, encoding="utf-8-sig")
    if seg_pair:
        pd.concat(seg_pair, ignore_index=True).to_csv(
            OUT / "09_配对差异_分币.csv", index=False, encoding="utf-8-sig")
    if seg_qtr:
        pd.concat(seg_qtr, ignore_index=True).to_csv(
            OUT / "10_配对差异_分季度.csv", index=False, encoding="utf-8-sig")

    # ── 3. 极端单贡献 ──
    ext_rows = []
    for _, r in metrics.iterrows():
        p, g = r["param_group"], r["group"]
        df = load_trades(g, p)
        if len(df) == 0:
            continue
        tot = float(df["profit_abs"].sum())
        top = df.reindex(df["profit_abs"].abs().sort_values(ascending=False).index).head(3)
        ext_rows.append({
            "param_group": p, "group": g,
            "net_total": round(tot, 4),
            "top1_abs_share_pct": round(float(top["profit_abs"].abs().iloc[0]) / abs(tot) * 100, 2) if tot else None,
            "top3_abs_share_pct": round(float(top["profit_abs"].abs().sum()) / abs(tot) * 100, 2) if tot else None,
            "top1_pair": str(top["pair"].iloc[0]),
            "top1_pnl": round(float(top["profit_abs"].iloc[0]), 4),
            "net_ex_top1": round(tot - float(top["profit_abs"].iloc[0]), 4),
        })
    pd.DataFrame(ext_rows).to_csv(OUT / "11_极端单贡献.csv", index=False, encoding="utf-8-sig")

    # ── 4. 一致性对照结论（P1 vs P2）──
    p1p2 = pairs[pairs["baseline"] == "P1"]
    identical = bool(len(p1p2) and (p1p2["n_delta_negative"].fillna(1).sum() == 0)
                     and (p1p2["n_delta_positive"].fillna(1).sum() == 0)
                     and (p1p2["n_matched"] > 0).all())
    (OUT / "12_一致性对照结论.json").write_text(json.dumps({
        "check": "固定 d=8%（基线类） vs 动态类 + 切档阈值不可达",
        "per_param": p1p2[["param_group", "n_matched", "n_delta_positive",
                           "n_delta_negative", "sum_delta_P2_minus_P1"]].to_dict("records"),
        "all_trades_identical": identical,
        "meaning": ("一致 ⇒ 动态覆盖只改了跟踪距离 d 的来源（按涨幅分档），"
                    "没有改动入场、仓位、止损上移规则、退出时机等其他行为。"),
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    # ── 5. 汇总口径：A/B/C 各组 9 参数的分布（不求和当账户）──
    agg = metrics.groupby("group")[METRIC_COLS].agg(["mean", "median", "min", "max"])
    agg.to_csv(OUT / "13_组别指标分布_9参数.csv", encoding="utf-8-sig")

    print("=== A/B/C 各 9 组参数的中位数 ===")
    print(metrics.groupby("group")[["net_total", "max_dd_pct", "sharpe", "profit_factor",
                                    "win_rate_pct", "trades_total"]].median().to_string())
    print("\n=== 一致性对照 P1 vs P2 ===")
    print(p1p2[["param_group", "n_matched", "n_delta_positive", "n_delta_negative",
                "sum_delta_P2_minus_P1"]].to_string())
    print("\nall_trades_identical =", identical)
    print("\n=== 配对差异 A→C / B→C（sum_delta 为 9 组内共同入场的差额合计）===")
    show = pairs[pairs["baseline"].isin(["A", "B"])]
    print(show[["param_group", "baseline", "treatment", "n_matched", "delta_sum",
                "delta_mean", "delta_median", "n_delta_positive", "n_delta_negative"]].to_string())


if __name__ == "__main__":
    main()
