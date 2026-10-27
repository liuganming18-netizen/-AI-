# -*- coding: utf-8 -*-
"""07 · 快速止损(quick_stop)口径缺陷审计与正确口径重算。

背景：对方 FT-ENTRY-07 的 quick_stop 定义为
    (trade_duration <= 720) & (profit_abs < 0) & (max_rate < 0.03)
其中 max_rate 是 freqtrade 的**持仓期最高成交价（绝对价格）**，不是收益率。
`max_rate < 0.03` 实际等价于「币价 < 0.03 USDT」的廉价币分档，与 MFE<3% 无关。

本脚本：
  1. 用对方 bug 口径在我方上游交易 CSV 上复现对方 01_全部运行索引与指标总表.csv 的
     quick_stop_count（27/27 格逐格一致 ⇒ 口径缺陷定位完成）；
  2. 给出正确口径（MFE = (max_rate-open_rate)/open_rate < 3%）下的笔数与占比；
  3. 复算对方报告中被该指标支撑的结论（84.7% 降幅、避免快速止损 152 笔等）；
  4. 量化对方 01CSV(209) 与 02表(179) 的自相矛盾。

只读上游，只在 evidence/FT-ENTRY-AUDIT-07/ 下新增产物。
"""
import os
import re
import sys

import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
AUDIT = os.path.dirname(BASE)
FT07 = os.path.join(os.path.dirname(AUDIT), "FT-ENTRY-07")
OUT = os.path.join(AUDIT, "out")
sys.path.insert(0, BASE)

GRID = ["Vs04_Z02", "Vs04_Z04", "Vs04_Z06",
        "Vs06_Z02", "Vs06_Z04", "Vs06_Z06",
        "Vs08_Z02", "Vs08_Z04", "Vs08_Z06"]
LANES = ["C0", "C1", "C2"]
TPL = {"C0": "Wyckoff_C_{g}", "C1": "Wyckoff_C1_{g}", "C2": "Wyckoff_C2_{g}"}
EVENT_RE = re.compile(r'(ev_FT_ENTRY_02_binance_[^_]+_\d+)')


def load_lane(lane, grid):
    p = os.path.join(FT07, "out", f"trades_{TPL[lane].format(g=grid)}.csv")
    if not os.path.exists(p):
        return pd.DataFrame()
    d = pd.read_csv(p, encoding="utf-8-sig")
    if d.empty:
        return d
    d = d.copy()
    d["grid"] = grid
    d["lane"] = lane
    d["pnl"] = d["profit_abs"].astype(float)
    d["dur"] = d["trade_duration"].astype(float)
    d["mr"] = d["max_rate"].astype(float)
    d["orate"] = d["open_rate"].astype(float)
    d["mfe"] = (d["mr"] - d["orate"]) / d["orate"]
    # 对方 bug 口径：max_rate 是绝对价格，这里按"绝对价格 < 0.03"计算
    d["qs_bug"] = (d["dur"] <= 720) & (d["pnl"] < 0) & (d["mr"] < 0.03)
    # 正确口径：MFE < 3%
    d["qs_ok"] = (d["dur"] <= 720) & (d["pnl"] < 0) & (d["mfe"] < 0.03)
    d["event_id"] = d["enter_tag"].apply(
        lambda s: (EVENT_RE.search(s).group(1) if isinstance(s, str) and EVENT_RE.search(s) else None))
    return d


def main():
    frames = {ln: [load_lane(ln, g) for g in GRID] for ln in LANES}

    # ---------- 1) 复现对方 01CSV ----------
    m01 = pd.read_csv(os.path.join(FT07, "01_全部运行索引与指标总表.csv"), encoding="utf-8-sig")
    m01 = m01.set_index("strategy")
    rows = []
    for grid in GRID:
        for lane in LANES:
            d = frames[lane][GRID.index(grid)]
            n_bug = int(d["qs_bug"].sum()) if not d.empty else 0
            n_ok = int(d["qs_ok"].sum()) if not d.empty else 0
            key = f"Wyckoff_{'C' if lane == 'C0' else lane}_{grid}"
            rows.append({
                "param_group": grid,
                "lane": lane,
                "trades_count": int(m01.loc[key, "trades_count"]),
                "对方01CSV_quick_stop": int(m01.loc[key, "quick_stop_count"]),
                "bug口径复现": n_bug,
                "复现一致": n_bug == int(m01.loc[key, "quick_stop_count"]),
                "正确口径_笔数": n_ok,
                "正确口径_占全部%": round(100.0 * n_ok / len(d), 2) if len(d) else 0.0,
                "倍数(正确/bug)": round(n_ok / n_bug, 1) if n_bug else None,
            })
    repro = pd.DataFrame(rows)
    repro.to_csv(os.path.join(OUT, "07_quickstop口径复现与正确重算_v1.csv"),
                 index=False, encoding="utf-8-sig")
    print("[1] 对方01CSV quick_stop_count 复现：")
    print(repro.to_string(index=False))
    n_match = int(repro["复现一致"].sum())
    print(f"\n  逐格一致: {n_match}/27")

    # ---------- 2) 集合构成：廉价币伪影 ----------
    allc0 = pd.concat([d for d in frames["C0"] if not d.empty], ignore_index=True)
    flagged = allc0[allc0["qs_bug"]]
    comp = {
        "对方C0标记笔数": int(len(flagged)),
        "涉及币种数": int(flagged["pair"].nunique()),
        "max_rate(绝对价格)下限": float(flagged["mr"].min()),
        "max_rate(绝对价格)上限": float(flagged["mr"].max()),
        "其中正确口径亦成立": int((flagged["qs_ok"]).sum()),
        "其中正确口径不成立(误检)": int((~flagged["qs_ok"]).sum()),
        "C0全部交易数": int(len(allc0)),
        "C0中max_rate<0.03的交易数": int((allc0["mr"] < 0.03).sum()),
    }
    print("\n[2] 对方标记集合构成：")
    for k, v in comp.items():
        print(f"  {k}: {v}")
    print("  涉及币种:", sorted(flagged["pair"].unique().tolist()))
    pd.DataFrame([comp]).to_csv(os.path.join(OUT, "07_对方标记集合构成_v1.csv"),
                               index=False, encoding="utf-8-sig")

    # ---------- 3) 正确口径下的漏检规模 ----------
    ok_all = allc0[allc0["qs_ok"]]
    leak = {
        "C0正确口径快速止损": int(len(ok_all)),
        "其中被对方口径捕获": int((ok_all["qs_bug"]).sum()),
        "其中被对方口径漏检": int((~ok_all["qs_bug"]).sum()),
        "漏检率%": round(100.0 * (~ok_all["qs_bug"]).sum() / len(ok_all), 2),
    }
    print("\n[3] 正确口径漏检：", leak)

    # ---------- 4) 重算对方报告被该指标支撑的结论 ----------
    lane_sum = []
    for lane in LANES:
        dd = pd.concat([d for d in frames[lane] if not d.empty], ignore_index=True)
        lane_sum.append({
            "lane": lane,
            "trades": int(len(dd)),
            "qs_bug": int(dd["qs_bug"].sum()),
            "qs_bug_占比%": round(100.0 * dd["qs_bug"].mean(), 2),
            "qs_ok": int(dd["qs_ok"].sum()),
            "qs_ok_占比%": round(100.0 * dd["qs_ok"].mean(), 2),
        })
    ls = pd.DataFrame(lane_sum)
    b0, b1, b2 = ls.loc[0, "qs_bug"], ls.loc[1, "qs_bug"], ls.loc[2, "qs_bug"]
    o0, o1, o2 = ls.loc[0, "qs_ok"], ls.loc[1, "qs_ok"], ls.loc[2, "qs_ok"]
    concl = {
        "对方称_C0快速止损": b0,
        "对方称_C1快速止损": b1,
        "对方称_降幅%_绝对笔数": round(100.0 * (b0 - b1) / b0, 1),
        "正确_C0快速止损": o0,
        "正确_C1快速止损": o1,
        "正确_C2快速止损": o2,
        "正确_绝对笔数降幅_C0到C1%": round(100.0 * (o0 - o1) / o0, 1),
        "正确_绝对笔数降幅_C0到C2%": round(100.0 * (o0 - o2) / o0, 1),
        "正确_发生率_C0%": ls.loc[0, "qs_ok_占比%"],
        "正确_发生率_C1%": ls.loc[1, "qs_ok_占比%"],
        "正确_发生率_C2%": ls.loc[2, "qs_ok_占比%"],
        "正确_发生率相对降幅_C0到C1%": round(
            100.0 * (ls.loc[0, "qs_ok_占比%"] - ls.loc[1, "qs_ok_占比%"]) / ls.loc[0, "qs_ok_占比%"], 1),
        "正确_发生率相对降幅_C0到C2%": round(
            100.0 * (ls.loc[0, "qs_ok_占比%"] - ls.loc[2, "qs_ok_占比%"]) / ls.loc[0, "qs_ok_占比%"], 1),
        "对方_发生率相对降幅_C0到C1%": round(
            100.0 * (ls.loc[0, "qs_bug_占比%"] - ls.loc[1, "qs_bug_占比%"]) / ls.loc[0, "qs_bug_占比%"], 1),
    }
    print("\n[4] 对方结论 vs 正确口径：")
    for k, v in concl.items():
        print(f"  {k}: {v}")
    pd.DataFrame([concl]).to_csv(os.path.join(OUT, "07_快速止损结论重算_v1.csv"),
                                 index=False, encoding="utf-8-sig")

    # ---------- 5) 事件级"避免快速止损"归因重算 ----------
    ev_rows = []
    for grid in GRID:
        c0 = frames["C0"][GRID.index(grid)]
        c1 = frames["C1"][GRID.index(grid)]
        c2 = frames["C2"][GRID.index(grid)]
        e0 = set(c0["event_id"].dropna()) if not c0.empty else set()
        e1 = set(c1["event_id"].dropna()) if not c1.empty else set()
        e2 = set(c2["event_id"].dropna()) if not c2.empty else set()
        q0_ok = set(c0.loc[c0["qs_ok"], "event_id"].dropna()) if not c0.empty else set()
        q0_bug = set(c0.loc[c0["qs_bug"], "event_id"].dropna()) if not c0.empty else set()
        loss0 = set(c0.loc[c0["pnl"] < 0, "event_id"].dropna()) if not c0.empty else set()
        ev_rows.append({
            "param_group": grid,
            "C0事件数": len(e0), "C1事件数": len(e1), "C2事件数": len(e2),
            "bug口径_避免快速止损_C1": len(q0_bug - e1),
            "bug口径_避免快速止损_C2": len(q0_bug - e2),
            "正确口径_避免快速止损_C1": len(q0_ok - e1),
            "正确口径_避免快速止损_C2": len(q0_ok - e2),
            "正确口径_避免普通亏损_C1": len((loss0 - q0_ok) - e1),
        })
    ev = pd.DataFrame(ev_rows)
    ev.to_csv(os.path.join(OUT, "07_事件级避免快速止损归因重算_v1.csv"),
              index=False, encoding="utf-8-sig")
    print("\n[5] 事件级归因重算（对方 bug 口径 vs 正确口径）：")
    print(ev.to_string(index=False))
    print("  合计：", {c: int(ev[c].sum()) for c in ev.columns if c.startswith(("bug", "正确"))})

    # ---------- 6) 01CSV 与 02表 自相矛盾 ----------
    t2 = pd.read_csv(os.path.join(FT07, "02_逐候选对照表.csv"), encoding="utf-8-sig")
    rows2 = []
    for grid in GRID:
        sub = t2[t2["param_group"] == grid]
        for lane in LANES:
            low = lane.lower()
            rows2.append({
                "param_group": grid, "lane": lane,
                "01CSV_quick_stop": int(m01.loc[f"Wyckoff_{'C' if lane == 'C0' else lane}_{grid}", "quick_stop_count"]),
                "01CSV_trades": int(m01.loc[f"Wyckoff_{'C' if lane == 'C0' else lane}_{grid}", "trades_count"]),
                "02表_flag": int((sub[f"{low}_quick_stop"].astype(str) == "True").sum()),
                "02表_filled": int((sub[f"{low}_filled"].astype(str) == "True").sum()),
            })
    tb = pd.DataFrame(rows2)
    tb["差_01CSV减02表"] = tb["01CSV_quick_stop"] - tb["02表_flag"]
    tb.to_csv(os.path.join(OUT, "07_01CSV与02表矛盾_v1.csv"), index=False, encoding="utf-8-sig")
    print("\n[6] 01CSV vs 02表 quick_stop：")
    for lane in LANES:
        s = tb[tb.lane == lane]
        print(f"  {lane}: 01CSV={int(s['01CSV_quick_stop'].sum())} "
              f"02表={int(s['02表_flag'].sum())} 差={int(s['差_01CSV减02表'].sum())} | "
              f"02表filled={int(s['02表_filled'].sum())} vs 01CSV trades={int(s['01CSV_trades'].sum())}")


if __name__ == "__main__":
    main()
