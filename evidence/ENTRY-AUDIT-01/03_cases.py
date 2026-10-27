# -*- coding: utf-8 -*-
"""ENTRY-AUDIT-01 / 03 案例抽取 + 事实与解释分离。

案例抽取规则在脚本顶部常量中**先声明后执行**，排序键完全确定，可复现。
产出：
  03_cases.json       8 笔案例（含 16 根 K 线逐根明细与计算过程）
  03_cases.md         人读版
  03_fact_vs_cause.json  「先跌」与「亏损」的混淆矩阵及分组统计
"""
from __future__ import annotations

import glob
import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path("/Users/boos/Desktop/威科夫因子共振趋势")
OUT = ROOT / "evidence" / "ENTRY-AUDIT-01"
BASE = ROOT / "数据" / "市场数据" / "parquet" / "candles" / "binance" / "um"

# ============ 案例抽取规则（先声明，后执行） ============
SELECTION_RULE = [
    {"slot": "A1", "stratum": "早期走弱-漏判",
     "rule": "42条回退ATR组中，原判非EARLY_WEAKNESS、独立ATR下变为EARLY_WEAKNESS者；"
             "按 (mae_r_snap / mae_r_theirs) 降序取第1，并列取 event_id 字典序最小"},
    {"slot": "A2", "stratum": "早期走弱-深跌",
     "rule": "原判EARLY_WEAKNESS且独立ATR下仍为EARLY_WEAKNESS；按 mae_r_snap 降序取第1，"
             "并列取 event_id 字典序最小"},
    {"slot": "A3", "stratum": "早期走弱-顺序不可判",
     "rule": "原判EARLY_WEAKNESS、独立ATR下首触为UNKNOWN_SAME_BAR；取 event_id 字典序最小"},
    {"slot": "B1", "stratum": "浮盈回吐-最大浮盈",
     "rule": "分类为MFE_PULLBACK；按 mfe_r_full 降序取第1，并列取 event_id 字典序最小"},
    {"slot": "B2", "stratum": "浮盈回吐-4h内浮盈最大却亏",
     "rule": "net_pnl<0；按 mfe_r_4h_theirs 降序取第1，并列取 event_id 字典序最小"},
    {"slot": "C1", "stratum": "盈利-最大净利",
     "rule": "分类为SUSTAINED_TREND；按 net_pnl 降序取第1"},
    {"slot": "C2", "stratum": "盈利-标签与结果背离",
     "rule": "原判EARLY_WEAKNESS但 net_pnl>0；按 net_pnl 降序取第1"},
    {"slot": "D1", "stratum": "缺失/边界",
     "rule": "exit_why=force_close_eod（数据末端强平）优先；若无则取42条回退ATR组中 "
             "mae_r_snap-mae_r_theirs 最大者"},
]


def pick(df: pd.DataFrame, cond, sort_col, ascending=False):
    pool = df[cond]
    if pool.empty:
        return None
    return pool.sort_values([sort_col, "event_id"], ascending=[ascending, True]).iloc[0]


def main() -> None:
    pr = pd.read_csv(OUT / "02_per_record.csv")
    feat = pd.read_parquet(ROOT / "evidence/FACTOR-ENTRY-01/candidate_features.parquet")
    feat = feat.set_index("event_id")

    picks = []
    picks.append(("A1", pick(pr, (pr["atr_box"].isna()) & (pr["cat_theirs"] != "EARLY_WEAKNESS")
                             & (pr["cat_mine_snap"] == "EARLY_WEAKNESS"), "atr_ratio_report_over_snap")))
    picks.append(("A2", pick(pr, (pr["cat_theirs"] == "EARLY_WEAKNESS")
                             & (pr["cat_mine_snap"] == "EARLY_WEAKNESS"), "mae_r_4h_mine_snap")))
    picks.append(("A3", pick(pr, (pr["cat_theirs"] == "EARLY_WEAKNESS")
                             & (pr["first_move_mine_snap"] == "UNKNOWN_SAME_BAR"), "event_id", True)))
    picks.append(("B1", pick(pr, pr["cat_theirs"] == "MFE_PULLBACK", "mfe_r_full")))
    picks.append(("B2", pick(pr, pr["net_pnl"] < 0, "mfe_r_4h_theirs")))
    picks.append(("C1", pick(pr, pr["cat_theirs"] == "SUSTAINED_TREND", "net_pnl")))
    picks.append(("C2", pick(pr, (pr["cat_theirs"] == "EARLY_WEAKNESS") & (pr["net_pnl"] > 0), "net_pnl")))
    d1 = pick(pr, pr["event_id"].isin(
        feat[feat["exit_why"] == "force_close_eod"].index), "event_id", True)
    if d1 is None:
        pr["_d"] = pr["mae_r_4h_mine_snap"] - pr["mae_r_4h_theirs"]
        d1 = pick(pr, pr["atr_box"].isna(), "_d")
    picks.append(("D1", d1))

    cases = []
    for slot, row in picks:
        if row is None:
            cases.append({"slot": slot, "error": "该层无满足条件的样本"})
            continue
        eid = row["event_id"]
        sym = row["symbol"]
        t_in = int(row["entry_time_ms"])
        p = float(row["p_entry"])

        # 逐根 15m
        fs = sorted(glob.glob(str(BASE / sym / "15m" / "*.parquet")))
        d = pd.concat([pd.read_parquet(f, columns=["open_time", "open", "high", "low", "close"])
                       for f in fs], ignore_index=True)
        d = d.sort_values("open_time").drop_duplicates("open_time").reset_index(drop=True)
        for c in ("open", "high", "low", "close"):
            d[c] = pd.to_numeric(d[c], errors="coerce")
        win = d[(d["open_time"] >= t_in) & (d["open_time"] < t_in + 4 * 3600 * 1000)].reset_index(drop=True)

        atr_rep = float(row["atr_report"])
        atr_snap = float(row["atr_snap"])
        bars = []
        for i, b in win.iterrows():
            bars.append({
                "i": i, "open_time_ms": int(b["open_time"]),
                "utc": str(pd.to_datetime(int(b["open_time"]), unit="ms")),
                "open": float(b["open"]), "high": float(b["high"]),
                "low": float(b["low"]), "close": float(b["close"]),
                "touch_fav_0.3ATR_report": bool(b["high"] >= p + 0.3 * atr_rep),
                "touch_adv_0.3ATR_report": bool(b["low"] <= p - 0.3 * atr_rep),
                "touch_fav_0.3ATR_snap": bool(b["high"] >= p + 0.3 * atr_snap),
                "touch_adv_0.3ATR_snap": bool(b["low"] <= p - 0.3 * atr_snap),
            })
        i_max = int(win["high"].to_numpy().argmax())
        i_min = int(win["low"].to_numpy().argmin())

        f_row = feat.loc[eid]
        # 窗口内 15m 文件覆盖
        years = sorted({str(pd.to_datetime(int(t), unit="ms").year)
                        for t in win["open_time"].tolist()})
        files15 = [str(Path("数据/市场数据/parquet/candles/binance/um") / sym / "15m" / f"{y}.parquet")
                   for y in years]
        years4 = sorted({str(pd.to_datetime(int(t), unit="ms").year) for t in [t_in]})
        files4 = [str(Path("数据/市场数据/parquet/candles/binance/um") / sym / "4h" / f"{y}.parquet")
                  for y in years4]

        cases.append({
            "slot": slot,
            "stratum": next(s for s in SELECTION_RULE if s["slot"] == slot)["stratum"],
            "event_id": eid, "symbol": sym,
            "entry_time_ms": t_in,
            "entry_utc": str(pd.to_datetime(t_in, unit="ms")),
            "raw_files_15m": files15,
            "raw_files_4h": files4,
            "p_entry": p,
            "atr_report": atr_rep, "atr_source": row["atr_source"],
            "atr_snap": atr_snap,
            "box_high": float(f_row["box_high"]), "box_low": float(f_row["box_low"]),
            "box_atr_4h": None if pd.isna(f_row["box_atr_4h"]) else float(f_row["box_atr_4h"]),
            "box_width_over_atr": (float(f_row["box_high"]) - float(f_row["box_low"])) / atr_rep,
            "initial_risk_r": float(f_row["initial_risk_r"]),
            "net_pnl": float(f_row["net_pnl"]),
            "exit_why": f_row["exit_why"],
            "exit_time_utc": str(pd.to_datetime(int(f_row["exit_time"]), unit="ms")),
            "mfe_r_full": float(f_row["mfe_r"]), "mae_r_full": float(f_row["mae_r"]),
            "bars_in_window": int(len(win)),
            "window": bars,
            "calc": {
                "window_high": float(win["high"].max()), "window_high_bar_i": i_max,
                "window_high_utc": str(pd.to_datetime(int(win["open_time"].iloc[i_max]), unit="ms")),
                "window_low": float(win["low"].min()), "window_low_bar_i": i_min,
                "window_low_utc": str(pd.to_datetime(int(win["open_time"].iloc[i_min]), unit="ms")),
                "mfe_pct": float(row["mfe_pct_4h"]), "mae_pct": float(row["mae_pct_4h"]),
                "mfe_r_report": float(row["mfe_r_4h_mine_rep"]), "mae_r_report": float(row["mae_r_4h_mine_rep"]),
                "mfe_r_snap": float(row["mfe_r_4h_mine_snap"]), "mae_r_snap": float(row["mae_r_4h_mine_snap"]),
                "fwd_ret_4h_idx16": float(row["fwd_ret_4h_mine_their_idx16"]),
                "fwd_ret_4h_window_end": float(row["fwd_ret_4h_mine_end"]),
                "first_move_theirs": row["first_move_theirs"],
                "first_move_mine_report": row["first_move_mine"],
                "first_move_mine_snap": row["first_move_mine_snap"],
                "cat_theirs": row["cat_theirs"], "cat_mine_report": row["cat_mine_rep"],
                "cat_mine_snap": row["cat_mine_snap"],
            },
        })

    (OUT / "03_cases.json").write_text(
        json.dumps({"ticket": "ENTRY-AUDIT-01", "selection_rule": SELECTION_RULE, "cases": cases},
                   ensure_ascii=False, indent=1), encoding="utf-8")

    # ---------- 事实与解释分离 ----------
    ew = pr[pr["cat_theirs"] == "EARLY_WEAKNESS"]
    ne = pr[pr["cat_theirs"] != "EARLY_WEAKNESS"]
    fa = pr[pr["first_move_theirs"] == "FIRST_ADVERSE"]
    nfa = pr[pr["first_move_theirs"] != "FIRST_ADVERSE"]

    def stat(d):
        return {
            "n": int(len(d)),
            "win_rate_pct": round(float((d["net_pnl"] > 0).mean() * 100), 2),
            "total_net": round(float(d["net_pnl"].sum()), 2),
            "avg_net": round(float(d["net_pnl"].mean()), 2),
            "median_net": round(float(d["net_pnl"].median()), 2),
        }

    fv = {
        "ticket": "ENTRY-AUDIT-01",
        "section": "事实与解释分离",
        "说明": "「先跌」是路径事实；「亏损」是结果。二者不是同一件事，也不能互相证明。",
        "分组统计_按EARLY_WEAKNESS标签": {"早期走弱组": stat(ew), "其余组": stat(ne)},
        "分组统计_按FIRST_ADVERSE标签": {"先不利组": stat(fa), "其余组": stat(nfa)},
        "混淆矩阵_先跌vs亏损": {
            "先不利且亏损": int(((pr["first_move_theirs"] == "FIRST_ADVERSE") & (pr["net_pnl"] <= 0)).sum()),
            "先不利但盈利": int(((pr["first_move_theirs"] == "FIRST_ADVERSE") & (pr["net_pnl"] > 0)).sum()),
            "非先不利且亏损": int(((pr["first_move_theirs"] != "FIRST_ADVERSE") & (pr["net_pnl"] <= 0)).sum()),
            "非先不利但盈利": int(((pr["first_move_theirs"] != "FIRST_ADVERSE") & (pr["net_pnl"] > 0)).sum()),
        },
        "早期走弱组内仍盈利笔数": int(((pr["cat_theirs"] == "EARLY_WEAKNESS") & (pr["net_pnl"] > 0)).sum()),
        "早期走弱组内最大盈利": round(float(ew["net_pnl"].max()), 2),
        "早期走弱组内最大亏损": round(float(ew["net_pnl"].min()), 2),
        "盈亏分组重叠区间": {
            "早期走弱组净盈亏分位": {k: round(float(v), 2) for k, v in
                              ew["net_pnl"].describe(percentiles=[.1, .5, .9]).items()},
            "其余组净盈亏分位": {k: round(float(v), 2) for k, v in
                            ne["net_pnl"].describe(percentiles=[.1, .5, .9]).items()},
        },
        "可支持的表述": "入场后 4 小时内先跌破 -0.3ATR 的笔数占比（路径事实）。",
        "不可支持的表述": "该现象证明「追高导致亏损」；或「早期走弱」等价于「亏钱」。",
    }
    (OUT / "03_fact_vs_cause.json").write_text(
        json.dumps(fv, ensure_ascii=False, indent=1), encoding="utf-8")

    # ---------- md ----------
    lines = ["# ENTRY-AUDIT-01 · 8 笔案例（按预先声明的规则抽取）", "",
             "## 抽取规则（先声明后执行）", ""]
    for s in SELECTION_RULE:
        lines.append(f"- **{s['slot']} {s['stratum']}**：{s['rule']}")
    lines += ["", "## 案例总表", "",
              "| 槽位 | 层 | 标的 | 入场UTC | 入场价 | 4h最高 | 4h最低 | MAE(R,原) | MAE(R,独立) | 首触(原) | 首触(独立) | 分类(原) | 分类(独立) | 净盈亏 |",
              "| :-- | :-- | :-- | :-- | --: | --: | --: | --: | --: | :-- | :-- | :-- | :-- | --: |"]
    for c in cases:
        if "error" in c:
            lines.append(f"| {c['slot']} | — | — | — | — | — | — | — | — | — | — | — | — | — |")
            continue
        k = c["calc"]
        lines.append(
            f"| {c['slot']} | {c['stratum']} | {c['symbol']} | {c['entry_utc']} | {c['p_entry']:.6g} | "
            f"{k['window_high']:.6g} | {k['window_low']:.6g} | {k['mae_r_report']:.3f} | {k['mae_r_snap']:.3f} | "
            f"{k['first_move_theirs']} | {k['first_move_mine_snap']} | {k['cat_theirs']} | {k['cat_mine_snap']} | "
            f"{c['net_pnl']:+.2f} |")
    lines.append("")
    for c in cases:
        if "error" in c:
            continue
        k = c["calc"]
        lines += [f"### {c['slot']} {c['stratum']} · {c['symbol']}", "",
                  f"- 事件ID：`{c['event_id']}`",
                  f"- 入场：{c['entry_utc']} UTC，价 {c['p_entry']:.8g}",
                  f"- 原始 15m：`{c['raw_files_15m']}`；4h：`{c['raw_files_4h']}`",
                  f"- ATR：原口径 {c['atr_report']:.8g}（来源 {c['atr_source']}）；独立口径 {c['atr_snap']:.8g}",
                  f"- 箱体：上沿 {c['box_high']:.8g} / 下沿 {c['box_low']:.8g}；宽度÷ATR = {c['box_width_over_atr']:.3f}",
                  f"- 窗口：{c['bars_in_window']} 根；最高 {k['window_high']:.8g}（第 {k['window_high_bar_i']} 根 {k['window_high_utc']}）；"
                  f"最低 {k['window_low']:.8g}（第 {k['window_low_bar_i']} 根 {k['window_low_utc']}）",
                  f"- 计算：MFE%={k['mfe_pct']*100:.3f}，MAE%={k['mae_pct']*100:.3f}；"
                  f"MAE(R)=原 {k['mae_r_report']:.3f} / 独立 {k['mae_r_snap']:.3f}；"
                  f"MFE(R)=原 {k['mfe_r_report']:.3f} / 独立 {k['mfe_r_snap']:.3f}",
                  f"- 4h 收益：第17根收盘口径 {k['fwd_ret_4h_idx16']*100:.3f}%；窗口末根口径 {k['fwd_ret_4h_window_end']*100:.3f}%",
                  f"- 首触：原 {k['first_move_theirs']}；独立 {k['first_move_mine_snap']}",
                  f"- 分类：原 {k['cat_theirs']}；独立 {k['cat_mine_snap']}",
                  f"- 结果：净盈亏 {c['net_pnl']:+.2f} USDT，离场 {c['exit_why']} @ {c['exit_time_utc']}；"
                  f"全周期 MFE {c['mfe_r_full']:.3f}R / MAE {c['mae_r_full']:.3f}R", ""]
    (OUT / "03_cases.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(fv, ensure_ascii=False, indent=1))
    print("\ncases:", [c.get("slot") for c in cases])


if __name__ == "__main__":
    main()
