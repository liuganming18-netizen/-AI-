# -*- coding: utf-8 -*-
"""07 Q1 权威核验（工单第 2 条）：用 FT-ENTRY-04 自身在回测运行时写出的
audit_orders_*.csv 作为权威证据，逐笔交叉验证成交约束真实性。

核验逻辑：
1. 每个 A 路线成交（trades CSV 的 enter_tag = candidate_id）必须能在审计日志里
   找到对应 PASSED 记录，且其 fill_price < frozen_H（price_minus_h < 0）。
2. 所有 REJECTED 候选（含 fill>=H / retry / outside_bounds）不得出现在 trades CSV
   中（拒单不伪装成交）。
3. 审计日志自陈的 fill_price 必须与 freqtrade trades CSV 的 open_rate 一致（交叉校验，
   防止“代码自证”循环：trades CSV 是引擎独立产物）。
4. 同时核对 AUDIT-04 旧 H 表（01_A入场价与箱顶H逐笔_v1.csv）的覆盖缺口，作为
   独立核验源的局限记录，但不据此判 FAIL。

只读，不重跑回测。
"""
from __future__ import annotations

import os
import sys
from typing import List

import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from _common import BASELINE, CELLS_A, OUT, OUT04, write_csv  # noqa: E402


def load_trades(strat: str) -> pd.DataFrame:
    p = os.path.join(OUT04, f"trades_{strat}.csv")
    return pd.read_csv(p, encoding="utf-8-sig") if os.path.isfile(p) else pd.DataFrame()


def main() -> None:
    H_OLD = os.path.join(OUT, "..", "FT-ENTRY-AUDIT-04", "out", "01_A入场价与箱顶H逐笔_v1.csv")
    h_old = pd.read_csv(H_OLD, encoding="utf-8-sig") if os.path.isfile(H_OLD) else pd.DataFrame()
    if not h_old.empty:
        h_old["code"] = h_old["code"].astype(str)

    rows: List[dict] = []
    global_viol: List[dict] = []

    for code, strat, _d, _k in [("BASE", BASELINE, 0.0, 0.0)] + CELLS_A:
        is_a = code != "BASE"
        tr = load_trades(strat)
        aud_p = os.path.join(OUT04, f"audit_orders_{strat}.csv")
        aud = pd.read_csv(aud_p, encoding="utf-8-sig") if os.path.isfile(aud_p) else pd.DataFrame()

        # 审计日志：candidate_id -> 真实决策（剔除 retry_prohibited，每个候选只有一次真实决策）
        if not aud.empty:
            real = aud[aud["reason"] != "retry_prohibited_already_evaluated"]
            aud_map = real.groupby("candidate_id").first()
            n_pass = int((aud["decision"] == "PASSED").sum())
            n_rej = int((aud["decision"] == "REJECTED").sum())
            # 核心约束拒绝（fill >= H）
            n_rej_ge = int(aud["reason"].isin(
                ["fill_price_ge_frozen_h_greater", "fill_price_ge_frozen_h_equal"]).sum())
            n_rej_retry = int((aud["reason"] == "retry_prohibited_already_evaluated").sum())
            n_rej_bounds = int((aud["reason"] == "outside_min_max_bounds").sum())
            # 审计日志自陈：所有 PASSED 必须 fill<H
            pass_rec = aud[aud["decision"] == "PASSED"]
            n_pass_ge_h = int((pass_rec["price_minus_h"] >= 0).sum()) if not pass_rec.empty else 0
        else:
            aud_map = None
            n_pass = n_rej = n_rej_ge = n_rej_retry = n_rej_bounds = n_pass_ge_h = 0

        # 成交 ↔ 审计日志 交叉校验
        n_trades = len(tr)
        n_trade_in_audit = 0
        n_trade_pass_lt_h = 0
        n_trade_mismatch_fill = 0
        n_rejected_in_trades = 0
        if not tr.empty and aud_map is not None:
            tr["_tag"] = tr["enter_tag"].astype(str)
            for _, r in tr.iterrows():
                cid = r["_tag"]
                if cid in aud_map.index:
                    rec = aud_map.loc[cid]
                    n_trade_in_audit += 1
                    if rec["decision"] == "PASSED" and rec["price_minus_h"] < 0:
                        n_trade_pass_lt_h += 1
                    # 交叉校验 fill 一致性
                    ap = float(rec["fill_price"])
                    trp = float(r["open_rate"])
                    if abs(ap - trp) > 1e-9:
                        n_trade_mismatch_fill += 1
                # REJECTED 候选不应出现在 trades
            rej_ids = set(aud[aud["decision"] == "REJECTED"]["candidate_id"].astype(str))
            traded_ids = set(tr["_tag"])
            n_rejected_in_trades = len(rej_ids & traded_ids)

        # AUDIT-04 旧 H 表覆盖缺口（仅记录，不判 FAIL）
        old_gap = 0
        if is_a and not h_old.empty and not tr.empty:
            hh = h_old[h_old["code"] == code]
            for _, r in tr.iterrows():
                key = (r["pair"], round(float(r["open_rate"]), 8))
                sub = hh[(hh["pair"] == r["pair"])]
                if sub.empty or not ((sub["open_rate"].round(8) == key[1]).any()):
                    old_gap += 1

        q1_ok = (is_a == False) or (
            n_trade_in_audit == n_trades
            and n_trade_pass_lt_h == n_trades
            and n_trade_mismatch_fill == 0
            and n_rejected_in_trades == 0
            and n_pass_ge_h == 0
        )
        rows.append({
            "code": code, "strategy": strat, "route": "Baseline" if not is_a else "A",
            "trades": n_trades,
            "audit_total": len(aud) if not aud.empty else 0,
            "audit_passed": n_pass, "audit_rejected": n_rej,
            "rej_fill_ge_H": n_rej_ge, "rej_retry": n_rej_retry, "rej_bounds": n_rej_bounds,
            "pass_ge_H_selfclaim": n_pass_ge_h,
            "trade_in_audit": n_trade_in_audit, "trade_pass_lt_H": n_trade_pass_lt_h,
            "trade_fill_mismatch": n_trade_mismatch_fill,
            "rejected_in_trades": n_rejected_in_trades,
            "old_Htable_gap": old_gap,
            "Q1_pass": bool(q1_ok),
        })

    res = pd.DataFrame(rows)
    write_csv(res, "07_Q1审计日志权威核验_v1.csv")
    print("== Q1 权威核验（审计日志 × trades CSV 交叉） ==")
    print(res[["code", "route", "trades", "audit_passed", "rej_fill_ge_H",
               "trade_in_audit", "trade_pass_lt_H", "trade_fill_mismatch",
               "rejected_in_trades", "old_Htable_gap", "Q1_pass"]].to_string(index=False))

    a = res[res["route"] == "A"]
    print(f"\nA 路线 Q1 全部通过: {bool(a['Q1_pass'].all())}")
    print(f"A 路线成交总数: {int(a['trades'].sum())}")
    print(f"A 路线被约束拒绝(fill>=H)候选总数: {int(a['rej_fill_ge_H'].sum())}")
    print(f"A 路线被拒候选伪装成交数: {int(a['rejected_in_trades'].sum())}")
    print(f"A 路线审计自陈 PASSED 但 fill>=H 数: {int(a['pass_ge_H_selfclaim'].sum())}")
    print(f"A 路线成交与审计 fill 不一致数: {int(a['trade_fill_mismatch'].sum())}")
    print(f"AUDIT-04 旧 H 表覆盖缺口(仅记录): {int(a['old_Htable_gap'].sum())}")
    print("\n完成")


if __name__ == "__main__":
    main()
