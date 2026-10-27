# -*- coding: utf-8 -*-
"""FT-ENTRY-AUDIT-02 · 08 FT-ENTRY-02（A/C 有限阈值探索）网格完整性与相邻参数稳定性

只读 FT-ENTRY-02。该批次在审计时仍在运行，本脚本按"快照"口径记录：
  - 已完成的组合、缺失的组合、每个组合的文件指纹与时间戳
  - 每个组合的指标、对基准事件的召回率、被丢弃基准事件的盈亏
  - 相邻参数梯度（Δ_entry × k_vol / k_Vs × Δ_zone）
产出：
  out/30_FT-ENTRY-02快照与组合完整性_v1.csv
  out/31_FT-ENTRY-02网格指标_v1.csv
  out/32_FT-ENTRY-02相邻参数梯度_v1.csv
  out/33_FT-ENTRY-02同事件对照_v1.csv
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import re
from datetime import datetime, timezone

import numpy as np
import pandas as pd

csv.field_size_limit(10 ** 9)

BASE = "/Users/boos/Desktop/威科夫因子共振趋势"
E2 = os.path.join(BASE, "evidence/FT-ENTRY-02")
OUT = os.path.join(BASE, "evidence/FT-ENTRY-AUDIT-02/out")

A_CELLS = [(f"L{int(round(l / 10)):02d}", f"V{v:02d}", l / 100.0, v / 10.0)
           for l in (60, 40, 20) for v in (8, 10, 12)]
C_CELLS = [(f"Vs{v:02d}", f"Z{z:02d}", v / 10.0, z / 10.0)
           for v in (4, 6, 8) for z in (2, 4, 6)]

TAG_RE = re.compile(r"^cand_(?:(A|C|A1|A2|C1|C2)_)?(ev_.+)_(\d{13})$")


def sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def load(name):
    p = os.path.join(E2, "out", f"trades_{name}.csv")
    if not os.path.exists(p):
        return None
    df = pd.read_csv(p, encoding="utf-8-sig")
    if "pair" not in df.columns:
        df = df.rename(columns={df.columns[0]: "pair"})
    df["event_id"] = df["enter_tag"].astype(str).str.extract(TAG_RE)[1]
    df["profit_abs"] = df["profit_abs"].astype(float)
    df["open_dt"] = pd.to_datetime(df["open_date"], utc=True)
    return df


def main():
    os.makedirs(OUT, exist_ok=True)
    snap_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    planned = ["WyckoffBaseline"]
    planned += [f"Wyckoff_A_{a}_{b}" for a, b, _, _ in A_CELLS]
    planned += [f"Wyckoff_C_{a}_{b}" for a, b, _, _ in C_CELLS]

    base = load("WyckoffBaseline")
    base_ev = base.groupby("event_id").first() if base is not None else pd.DataFrame()
    base_net = float(base["profit_abs"].sum()) if base is not None else 0.0

    comp_rows, grid_rows, ev_rows = [], [], []
    data = {}
    for name in planned:
        p = os.path.join(E2, "out", f"trades_{name}.csv")
        zp = os.path.join(E2, "out", f"backtest-result-{name}.zip")
        present = os.path.exists(p)
        row = {
            "cell": name,
            "present": present,
            "csv_mtime_utc": datetime.fromtimestamp(os.path.getmtime(p), timezone.utc).strftime("%Y-%m-%d %H:%M:%S") if present else "",
            "csv_sha256": sha256(p) if present else "",
            "zip_present": os.path.exists(zp),
            "status": "已完成" if present else "审计快照时无结果",
        }
        if present:
            df = load(name)
            data[name] = df
            row["trades"] = len(df)
            row["events"] = int(df["event_id"].nunique())
            row["symbols"] = int(df["pair"].nunique())
        else:
            row["trades"] = 0
            row["events"] = 0
            row["symbols"] = 0
        comp_rows.append(row)

    for name, df in data.items():
        p = df["profit_abs"]
        net = float(p.sum())
        ev = df.groupby("event_id").first()
        shared = ev.index.intersection(base_ev.index) if len(base_ev) else []
        dropped = base_ev.loc[~base_ev.index.isin(ev.index)] if len(base_ev) else pd.DataFrame()
        unmatched = ev.loc[~ev.index.isin(base_ev.index)]
        sorted_p = p.sort_values(ascending=False).reset_index(drop=True)
        by_sym = df.groupby("pair")["profit_abs"].sum().sort_values(ascending=False)
        grid_rows.append({
            "cell": name, "trades": len(df), "events": len(ev),
            "net_u": round(net, 4),
            "win_rate_pct": round(float((p > 0).mean() * 100), 2),
            "expectancy_u": round(net / len(df), 4),
            "profit_factor": round(float(p[p > 0].sum() / abs(p[p < 0].sum())), 4) if (p < 0).any() else float("inf"),
            "max_win_u": round(float(p.max()), 4),
            "net_ex_top1": round(net - float(sorted_p.iloc[0]), 4),
            "net_ex_top3": round(net - float(sorted_p.head(3).sum()), 4),
            "top1_symbol": by_sym.index[0] if len(by_sym) else "",
            "top1_symbol_u": round(float(by_sym.iloc[0]), 4) if len(by_sym) else 0.0,
            "net_ex_top1_symbol": round(net - float(by_sym.iloc[0]), 4) if len(by_sym) else net,
            "baseline_events_recalled": len(shared),
            "baseline_recall_pct": round(len(shared) / len(base_ev) * 100, 2) if len(base_ev) else 0.0,
            "dropped_baseline_events": len(dropped),
            "dropped_baseline_pnl_u": round(float(dropped["profit_abs"].sum()), 4) if len(dropped) else 0.0,
            "unmatched_events": len(unmatched),
            "unmatched_pnl_u": round(float(unmatched["profit_abs"].sum()), 4),
            "shared_events_pnl_u": round(float(ev.loc[shared, "profit_abs"].sum()), 4) if len(shared) else 0.0,
            "net_vs_baseline_u": round(net - base_net, 4),
        })
        if len(shared):
            for e in shared:
                ev_rows.append({
                    "cell": name, "event_id": e, "pair": ev.loc[e, "pair"],
                    "open_date": ev.loc[e, "open_date"],
                    "cell_pnl_u": round(float(ev.loc[e, "profit_abs"]), 4),
                    "baseline_pnl_u": round(float(base_ev.loc[e, "profit_abs"]), 4),
                    "diff_u": round(float(ev.loc[e, "profit_abs"] - base_ev.loc[e, "profit_abs"]), 4),
                })

    pd.DataFrame(comp_rows).to_csv(os.path.join(OUT, "30_FT-ENTRY-02快照与组合完整性_v1.csv"),
                                   index=False, encoding="utf-8-sig")
    gdf = pd.DataFrame(grid_rows)
    if len(gdf):
        gdf.to_csv(os.path.join(OUT, "31_FT-ENTRY-02网格指标_v1.csv"),
                   index=False, encoding="utf-8-sig")
    if ev_rows:
        pd.DataFrame(ev_rows).to_csv(os.path.join(OUT, "33_FT-ENTRY-02同事件对照_v1.csv"),
                                     index=False, encoding="utf-8-sig")

    # ---------- 相邻参数梯度 ----------
    grad = []
    gmap = {r["cell"]: r for r in grid_rows}

    def add_grad(route, varying, fixed_label, fixed_val, cells, values):
        for i in range(len(cells) - 1):
            a, b = cells[i], cells[i + 1]
            if a in gmap and b in gmap:
                grad.append({
                    "route": route, "fixed_param": f"{fixed_label}={fixed_val}",
                    "varying_param": varying,
                    "from_cell": a, "to_cell": b,
                    "from_value": values[i], "to_value": values[i + 1],
                    "delta_param": round(values[i + 1] - values[i], 2),
                    "from_net_u": gmap[a]["net_u"], "to_net_u": gmap[b]["net_u"],
                    "net_change_u": round(gmap[b]["net_u"] - gmap[a]["net_u"], 4),
                })

    L_VALS = [0.6, 0.4, 0.2]
    V_VALS = [0.8, 1.0, 1.2]
    Z_VALS = [0.2, 0.4, 0.6]
    VS_VALS = [0.4, 0.6, 0.8]

    for lo in L_VALS:
        tag = f"L{int(round(lo * 10)):02d}"
        add_grad("A", "k_vol", "Δ_entry", f"{lo}A",
                 [f"Wyckoff_A_{tag}_V{int(round(v * 10)):02d}" for v in V_VALS], V_VALS)
    for vo in V_VALS:
        tag = f"V{int(round(vo * 10)):02d}"
        add_grad("A", "Δ_entry", "k_vol", vo,
                 [f"Wyckoff_A_L{int(round(l * 10)):02d}_{tag}" for l in L_VALS], L_VALS)
    for vo in VS_VALS:
        tag = f"Vs{int(round(vo * 10)):02d}"
        add_grad("C", "Δ_zone", "k_Vs", vo,
                 [f"Wyckoff_C_{tag}_Z{int(round(z * 10)):02d}" for z in Z_VALS], Z_VALS)
    for zo in Z_VALS:
        tag = f"Z{int(round(zo * 10)):02d}"
        add_grad("C", "k_Vs", "Δ_zone", zo,
                 [f"Wyckoff_C_Vs{int(round(v * 10)):02d}_{tag}" for v in VS_VALS], VS_VALS)
    if grad:
        pd.DataFrame(grad).to_csv(os.path.join(OUT, "32_FT-ENTRY-02相邻参数梯度_v1.csv"),
                                  index=False, encoding="utf-8-sig")

    # ---------- 控制台 ----------
    done = [r for r in comp_rows if r["present"]]
    print(f"审计快照时间(UTC): {snap_ts}")
    print(f"计划组合 {len(planned)} 个（1 基准 + 9 A + 9 C）；快照时已完成 {len(done)} 个")
    for r in comp_rows:
        if not r["present"]:
            print(f"  缺失: {r['cell']}")
    print("\n=== 已完成组合 ===")
    for r in grid_rows:
        print(f"  {r['cell']:28s} n={r['trades']:3d} net={r['net_u']:+9.2f} wr={r['win_rate_pct']:6.2f}% "
              f"PF={r['profit_factor']:6.3f} 去最大单={r['net_ex_top1']:+9.2f} 去前三={r['net_ex_top3']:+9.2f} "
              f"基准召回={r['baseline_recall_pct']:5.1f}% 丢弃={r['dropped_baseline_events']:2d}({r['dropped_baseline_pnl_u']:+.2f}) "
              f"vs基准={r['net_vs_baseline_u']:+9.2f}")
    print("\n=== 相邻参数梯度（|Δnet| 从大到小）===")
    for r in sorted(grad, key=lambda x: -abs(x["net_change_u"]))[:20]:
        print(f"  {r['route']} {r['varying_param']:10s} {r['from_cell']:26s}→{r['to_cell']:26s} "
              f"{r['from_value']}→{r['to_value']} Δnet={r['net_change_u']:+9.2f}")


if __name__ == "__main__":
    main()
