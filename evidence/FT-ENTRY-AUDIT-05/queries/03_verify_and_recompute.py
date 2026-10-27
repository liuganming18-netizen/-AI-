# -*- coding: utf-8 -*-
"""03 FT-ENTRY-04 独立重算 + 成交约束真实性核验（工单第 2、3 条）。

核验目标：
A. 成交约束真实性（工单第 2 条）
   A1. H 在信号时冻结：逐笔核对「成交时所用的 H」与「信号时刻 4h 箱顶 H」是否同源；
   A2. 成交时可知：H 必须来自已闭合 4h K 线，成交时刻晚于信号时刻；
   A3. 新结果全部成交严格低于 H（不出现 == H 或 > H）；
   A4. 拒单不伪装成成交：被拒候选不得出现在逐笔成交里；成交笔数 = 候选数 − 拒单数。

B. 独立重算（工单第 3 条）
   从 FT-ENTRY-04 的逐笔 CSV 独立重算胜负平、扣费后胜率、平均盈亏、盈亏比、
   净期望、净收益、真实平仓权益回撤，并与 02 的静态预测对账。

只读，不重跑回测。
"""
from __future__ import annotations

import json
import os
import sys
import zipfile
from typing import List, Optional

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from _common import (  # noqa: E402
    AUDIT04_OUT, BASELINE, CELLS_A, ENTRY04, OUT, OUT03, OUT04,
    jdump, load_trades, metrics, sha256_file, wilson_ci, write_csv,
)

H_TABLE = os.path.join(AUDIT04_OUT, "01_A入场价与箱顶H逐笔_v1.csv")
START_MS = 1638316800000
END_MS = 1735689600000


def load_archive_trades(strategy: str) -> Optional[pd.DataFrame]:
    """从 FT-ENTRY-04 归档 zip 读 trades（与逐笔 CSV 交叉对账用）。"""
    path = os.path.join(OUT04, f"backtest-result-{strategy}.zip")
    if not os.path.isfile(path):
        return None
    try:
        with zipfile.ZipFile(path) as z:
            name = next(n for n in z.namelist() if n.endswith(".json") and "config" not in n)
            with z.open(name) as f:
                data = json.loads(f.read().decode("utf-8"))
        trades = data["strategy"][strategy].get("trades", [])
        return pd.DataFrame(trades)
    except Exception as e:  # noqa: BLE001
        print(f"  [warn] 读归档失败 {strategy}: {e}")
        return None


def main() -> None:
    h = pd.read_csv(H_TABLE, encoding="utf-8-sig")
    h["code"] = h["code"].astype(str)

    # ── A. 成交约束真实性核验 ──
    audit_rows: List[dict] = []
    fill_rows: List[dict] = []
    for code, strat, delta, kvol in [("BASE", BASELINE, 0.0, 0.0)] + CELLS_A:
        df = load_trades(strat, OUT04)
        if df.empty:
            audit_rows.append({"code": code, "strategy": strat,
                               "__missing__": f"未找到 FT-ENTRY-04 逐笔 {strat}.csv"})
            continue
        df3 = load_trades(strat, OUT03)
        hh = h[h["code"] == code][["pair", "open_rate", "H", "sig_close_ms", "box_ms",
                                   "A", "at_or_above_H"]].rename(
            columns={"H": "frozen_H", "A": "box_atr", "sig_close_ms": "h_sig_close_ms",
                     "box_ms": "h_box_ms", "at_or_above_H": "h_at_or_above_H"})
        m = df.merge(hh, on=["pair", "open_rate"], how="left", validate="one_to_one")

        # A1/A3：成交价 vs 冻结 H
        viol = m[m["frozen_H"].notna() & (m["open_rate"] >= m["frozen_H"])]
        # A2：因果性——成交时刻必须晚于信号时刻；H 的 box 必须已闭合
        m["exec_after_signal"] = m["open_ts"] > m["sig_close_ms"]
        m["h_from_closed_box"] = m["h_box_ms"].notna() & (m["h_box_ms"] < m["open_ts"])
        causal_bad = m[~(m["exec_after_signal"] & m["h_from_closed_box"])]

        is_a = code != "BASE"
        audit_rows.append({
            "code": code, "strategy": strat, "route": "Baseline" if code == "BASE" else "A",
            "trades_04": len(m),
            "trades_03": len(df3),
            "h_matched_rows": int(m["frozen_H"].notna().sum()),
            "h_unmatched_rows": int(m["frozen_H"].isna().sum()),
            "fills_at_or_above_H": int(len(viol)),
            "fills_at_or_above_H_pct": round(100.0 * len(viol) / len(m), 4) if len(m) else None,
            "all_fills_strictly_below_H": bool(len(viol) == 0) if is_a else None,
            "causal_violations": int(len(causal_bad)),
            "min_exec_minus_signal_ms": int((m["open_ts"] - m["sig_close_ms"]).min()) if len(m) else None,
            "max_price_minus_h": round(float((m["open_rate"] - m["frozen_H"]).max()), 8) if len(m) else None,
            "h_source_note": "H 取自 AUDIT-04 用 4h feather 独立回算的冻结箱顶",
        })

        for _, r in m.iterrows():
            fill_rows.append({
                "code": code, "strategy": strat, "pair": r["pair"],
                "event_id": r["event_id"], "enter_tag": r["enter_tag"],
                "open_ts": r["open_ts"], "close_ts": r["close_ts"],
                "open_rate": r["open_rate"], "frozen_H": r["frozen_H"],
                "price_minus_h": (round(float(r["open_rate"] - r["frozen_H"]), 8)
                                  if pd.notna(r["frozen_H"]) else None),
                "price_minus_h_in_atr": (round(float((r["open_rate"] - r["frozen_H"]) / r["box_atr"]), 6)
                                         if pd.notna(r["frozen_H"]) and pd.notna(r["box_atr"]) and r["box_atr"] else None),
                "sig_close_ms": r["sig_close_ms"],
                "exec_after_signal": bool(r["exec_after_signal"]),
                "h_from_closed_box": bool(r["h_from_closed_box"]),
                "profit_abs": r["profit_abs"], "is_win": bool(r["is_win"]),
            })

    aud = pd.DataFrame(audit_rows)
    write_csv(aud, "03_成交约束真实性核验_v1.csv")
    print("== A. 成交约束真实性核验 ==")
    print(aud.to_string(index=False))

    fills = pd.DataFrame(fill_rows)
    write_csv(fills, "03_新结果逐笔成交与H对照_v1.csv")

    # A4：拒单不伪装成成交 —— 用 04 的订单审计日志核对
    audit_log = os.path.join(OUT04, "audit_orders_WyckoffParamExplorationBase.csv")
    rej_rows: List[dict] = []
    if os.path.isfile(audit_log):
        al = pd.read_csv(audit_log, encoding="utf-8-sig")
        al["strategy"] = al.get("strategy", "")
        rejected = al[al["decision"] == "REJECTED"].copy()
        for _, r in rejected.iterrows():
            rej_rows.append({
                "strategy": r.get("strategy"), "pair": r.get("pair"),
                "candidate_id": r.get("candidate_id"),
                "signal_time_ms": r.get("signal_time_ms"),
                "planned_execution_time_ms": r.get("planned_execution_time_ms"),
                "fill_price": r.get("fill_price"), "frozen_H": r.get("frozen_H"),
                "price_minus_h": r.get("price_minus_h"), "reason": r.get("reason"),
            })
        rej = pd.DataFrame(rej_rows)
        write_csv(rej, "03_被拒订单审计明细_v1.csv")
        # 被拒候选是否出现在 04 逐笔成交里
        if not rej.empty and not fills.empty:
            traded_tags = set(fills["enter_tag"].dropna().astype(str))
            rej["appears_in_trades"] = rej["candidate_id"].astype(str).isin(traded_tags)
            write_csv(rej, "03_被拒订单审计明细_v1.csv")
            n_leak = int(rej["appears_in_trades"].sum())
            print(f"\n== A4. 拒单伪装成交检查 ==")
            print(f"审计日志被拒记录 {len(rej)} 条；其中出现在逐笔成交里的 = {n_leak} 条")
            by_reason = rej.groupby("reason").agg(
                n=("candidate_id", "size"), leaked=("appears_in_trades", "sum")).reset_index()
            write_csv(by_reason, "03_拒单伪装成交核验_v1.csv")
            print(by_reason.to_string(index=False))
        else:
            print("\n== A4 == 审计日志缺失或无被拒记录，无法完成伪装成交核验")
    else:
        print("\n== A4 == 未找到订单审计日志，跳过（记为证据缺口）")

    # ── B. 独立重算 04 指标，并与 02 静态预测对账 ──
    metric_rows: List[dict] = []
    for code, strat, delta, kvol in [("BASE", BASELINE, 0.0, 0.0)] + CELLS_A:
        df = load_trades(strat, OUT04)
        if df.empty:
            continue
        m = metrics(df)
        lo, hi = wilson_ci(m["wins"], m["trades"])
        arc = load_archive_trades(strat)
        arc_n = len(arc) if arc is not None else None
        arc_pnl = (round(float(arc["profit_abs"].sum()), 4)
                   if arc is not None and "profit_abs" in arc.columns else None)
        metric_rows.append({
            "code": code, "strategy": strat, "route": "Baseline" if code == "BASE" else "A",
            "delta_atr": delta, "k_vol": kvol,
            "trades": m["trades"], "wins": m["wins"], "losses": m["losses"], "flat": m["flat"],
            "win_rate_pct": m["win_rate_pct"],
            "win_rate_ci95_lo": lo, "win_rate_ci95_hi": hi,
            "avg_win_u": m["avg_win_u"], "avg_loss_u": m["avg_loss_u"],
            "payoff_ratio": m["payoff_ratio"],
            "expectancy_u": m["expectancy_u"], "net_pnl_u": m["net_pnl_u"],
            "profit_factor": m["profit_factor"],
            "max_dd_realized": m["max_dd_realized"], "final_equity": m["final_equity"],
            "gross_pnl_u": m["gross_pnl_u"], "cost_total_u": m["cost_total_u"],
            "fee_u": m["fee_u"], "funding_u": m["funding_u"],
            "archive_trades": arc_n, "archive_net_pnl_u": arc_pnl,
            "csv_archive_trade_count_match": (arc_n == m["trades"]) if arc_n is not None else None,
            "csv_archive_pnl_match": (abs((arc_pnl or 0) - (m["net_pnl_u"] or 0)) < 1e-3)
            if arc_pnl is not None else None,
        })
    mm = pd.DataFrame(metric_rows)
    write_csv(mm, "03_独立重算指标_v1.csv")
    print("\n== B. FT-ENTRY-04 独立重算指标 ==")
    print(mm[["code", "trades", "wins", "losses", "win_rate_pct", "avg_win_u", "avg_loss_u",
              "payoff_ratio", "expectancy_u", "net_pnl_u", "profit_factor",
              "max_dd_realized", "csv_archive_trade_count_match"]].to_string(index=False))

    # 与 02 静态预测对账
    pred_path = os.path.join(OUT, "02_静态反事实预测_v1.csv")
    if os.path.isfile(pred_path):
        pred = pd.read_csv(pred_path, encoding="utf-8-sig")
        cmp = mm.merge(pred, on="code", how="outer", suffixes=("", "_pred"))
        cmp["trades_pred"] = cmp["trades_04_expected"]
        cmp["pnl_pred"] = cmp["net_pnl_u_04_expected"]
        cmp["exp_pred"] = cmp["expectancy_u_04_expected"]
        cmp["trades_match"] = cmp["trades"] == cmp["trades_pred"]
        cmp["pnl_diff_u"] = (cmp["net_pnl_u"] - cmp["pnl_pred"]).round(4)
        cmp["exp_diff_u"] = (cmp["expectancy_u"] - cmp["exp_pred"]).round(4)
        cols = ["code", "trades", "trades_pred", "trades_match",
                "net_pnl_u", "pnl_pred", "pnl_diff_u", "expectancy_u", "exp_pred", "exp_diff_u"]
        write_csv(cmp[cols], "03_实际回测与静态预测对账_v1.csv")
        print("\n== B2. 实际回测 vs 静态预测对账 ==")
        print(cmp[cols].to_string(index=False))

    jdump(os.path.join(OUT, "03_核验摘要_v1.json"), {
        "constraint_audit": aud.to_dict("records"),
        "metrics_04": mm.to_dict("records"),
        "notes": [
            "A1/A2：H 来自已闭合 4h 箱体，成交时刻晚于信号时刻（逐笔核验 causal_violations）",
            "A3：新结果 A 路线全部成交严格 < 冻结 H",
            "A4：被拒候选不得出现在逐笔成交中（用订单审计日志核对）",
            "B：全部指标由本审核从逐笔 CSV 独立重算，不引用对方自报值",
        ],
    })
    print("\n完成")


if __name__ == "__main__":
    main()
