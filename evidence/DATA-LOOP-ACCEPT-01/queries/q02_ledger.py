# -*- coding: utf-8 -*-
"""q02｜原始账本活性、信号身份链与逐笔权益守恒（只读）。

复算口径（全部取账本原始字段，不引用任何汇总报告）：
  gross_pnl = (exit_price - entry_price) * qty                    （多单）
  entry_fee = entry_notional * 5bps        exit_fee = exit_notional * 5bps
  slippage  = notional * 3bps              （taker 5bps / 滑点 3bps 为冻结参数）
  net_pnl   = gross_pnl - entry_fee - exit_fee - funding_paid
  cash_{t+1} = cash_t + gross_pnl - fees - funding                （平仓链）
  equity    = cash + unrealized
"""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
PAPER_DIR = ROOT / "数据" / "paper"
OUT = Path(__file__).resolve().parents[1] / "out"
TZ = dt.timezone(dt.timedelta(hours=8))
FEE_BPS = 5.0
SLIP_BPS = 3.0
INIT_EQ = 2000.0


def bj(ms):
    if ms is None:
        return None
    return dt.datetime.fromtimestamp(ms / 1000, TZ).strftime("%Y-%m-%d %H:%M:%S")


def q(con, sql, args=()):
    return con.execute(sql, args).fetchall()


def main():
    now_ms = int(dt.datetime.now(TZ).timestamp() * 1000)
    out = {"measured_at_ms": now_ms, "measured_at_bj": bj(now_ms), "arms": {}}

    for arm in ("3a", "3b", "3c", "3d"):
        db = PAPER_DIR / f"paper_{arm}.sqlite3"
        if not db.exists():
            continue
        st = db.stat()
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        tables = [r[0] for r in q(con, "SELECT name FROM sqlite_master WHERE type='table'")]
        counts = {}
        for t in tables:
            try:
                counts[t] = q(con, f'SELECT COUNT(*) FROM "{t}"')[0][0]
            except Exception:
                counts[t] = None
        rec = {
            "db": str(db), "size": st.st_size,
            "mtime_bj": dt.datetime.fromtimestamp(st.st_mtime, TZ).strftime("%Y-%m-%d %H:%M:%S"),
            "counts": counts, "positions": [], "fills": [], "equity": [], "state": None,
            "recompute": {}, "identity_chain": {},
        }

        if counts.get("paper_position"):
            for r in q(con, "SELECT position_id,symbol,qty,entry_price,exit_price,entry_fee,exit_fee,"
                           "funding_paid,status,exit_reason,origin,signal_uid,voided,void_reason,"
                           "entry_ts,exit_ts,planned_risk,initial_stop_price,config_fingerprint "
                           "FROM paper_position ORDER BY position_id"):
                d = dict(r)
                d["entry_bj"] = bj(d["entry_ts"]); d["exit_bj"] = bj(d["exit_ts"])
                if d["entry_price"] and d["exit_price"] and d["qty"]:
                    d["gross_recalc"] = round((d["exit_price"] - d["entry_price"]) * d["qty"], 8)
                    d["entry_notional"] = round(d["entry_price"] * d["qty"], 8)
                    d["exit_notional"] = round(d["exit_price"] * d["qty"], 8)
                    d["entry_fee_recalc"] = round(d["entry_notional"] * FEE_BPS / 10000, 8)
                    d["exit_fee_recalc"] = round(d["exit_notional"] * FEE_BPS / 10000, 8)
                    d["net_recalc"] = round(d["gross_recalc"] - d["entry_fee_recalc"]
                                            - d["exit_fee_recalc"] - (d["funding_paid"] or 0), 8)
                    d["fee_diff"] = round((d["entry_fee"] or 0) - d["entry_fee_recalc"], 10)
                    d["exit_fee_diff"] = round((d["exit_fee"] or 0) - d["exit_fee_recalc"], 10)
                rec["positions"].append(d)

        if counts.get("paper_fill"):
            for r in q(con, "SELECT fill_id,position_id,signal_uid,action,qty,raw_price,fill_price,"
                           "notional,fee,slippage_cost,bar_open_time,bar_close_time,fill_ts,voided "
                           "FROM paper_fill ORDER BY fill_id"):
                d = dict(r)
                d["fill_bj"] = bj(d["fill_ts"]); d["bar_close_bj"] = bj(d["bar_close_time"])
                if d["notional"]:
                    d["fee_recalc"] = round(d["notional"] * FEE_BPS / 10000, 8)
                    d["slip_recalc"] = round(d["notional"] * SLIP_BPS / 10000, 8)
                    d["fee_diff"] = round((d["fee"] or 0) - d["fee_recalc"], 10)
                    d["slip_diff"] = round((d["slippage_cost"] or 0) - d["slip_recalc"], 10)
                    if d["raw_price"]:
                        d["fill_minus_raw"] = round(d["fill_price"] - d["raw_price"], 8)
                        d["expected_slip"] = round(d["raw_price"] * SLIP_BPS / 10000, 8)
                rec["fills"].append(d)

        if counts.get("paper_equity"):
            for r in q(con, "SELECT ts,cash,unrealized,equity,voided FROM paper_equity ORDER BY ts"):
                d = dict(r); d["bj"] = bj(d["ts"])
                d["equity_recalc"] = round(d["cash"] + d["unrealized"], 8)
                d["equity_diff"] = round(d["equity"] - d["equity_recalc"], 10)
                rec["equity"].append(d)

        if counts.get("paper_state"):
            r = q(con, "SELECT * FROM paper_state LIMIT 1")
            if r:
                rec["state"] = dict(r[0])
                rec["state"]["updated_bj"] = bj(rec["state"]["updated_at"])

        # ── 现金守恒：按时间序把每笔平仓现金流加回 ──
        if rec["equity"]:
            first, last = rec["equity"][0], rec["equity"][-1]
            rec["recompute"]["equity_first"] = first["equity"]
            rec["recompute"]["equity_last"] = last["equity"]
            rec["recompute"]["equity_delta"] = round(last["equity"] - first["equity"], 8)
            rec["recompute"]["equity_internal_consistency_max_diff"] = max(
                abs(e["equity_diff"]) for e in rec["equity"])
        if rec["positions"] and rec["state"]:
            total_net = sum(p.get("net_recalc", 0.0) for p in rec["positions"] if p.get("exit_price"))
            total_fee = sum((p.get("entry_fee") or 0) + (p.get("exit_fee") or 0)
                            for p in rec["positions"] if p.get("exit_price"))
            rec["recompute"]["sum_net_recalc"] = round(total_net, 8)
            rec["recompute"]["sum_fees_from_positions"] = round(total_fee, 8)
            rec["recompute"]["state_realized_pnl"] = rec["state"]["realized_pnl"]
            rec["recompute"]["state_fees_paid"] = rec["state"]["fees_paid"]
            rec["recompute"]["realized_diff_vs_sum"] = round(rec["state"]["realized_pnl"] - total_net, 8)
            rec["recompute"]["fees_diff_vs_sum"] = round(rec["state"]["fees_paid"] - total_fee, 8)
            rec["recompute"]["cash_minus_init_plus_realized"] = round(
                rec["state"]["cash"] - (INIT_EQ + rec["state"]["realized_pnl"]), 8)
            rec["recompute"]["cash_state_vs_last_equity_cash"] = round(
                rec["state"]["cash"] - (rec["equity"][-1]["cash"] if rec["equity"] else 0), 8)

        # ── 同一信号身份链：order -> pending -> position -> fill -> processed ──
        if counts.get("paper_order"):
            for r in q(con, "SELECT event_uid, order_id, status, reject_reason, ts FROM paper_order ORDER BY order_id"):
                uid = r["event_uid"]
                chain = {"event_uid": uid, "order_id": r["order_id"], "order_status": r["status"],
                         "reject_reason": r["reject_reason"], "order_bj": bj(r["ts"])}
                chain["pending"] = [dict(x) for x in q(
                    con, "SELECT pending_id,status,reason,created_at,resolved_at FROM paper_pending WHERE signal_uid=?", (uid,))]
                chain["positions"] = [dict(x) for x in q(
                    con, "SELECT position_id,status,origin,voided FROM paper_position WHERE signal_uid=?", (uid,))]
                chain["fills"] = [dict(x) for x in q(
                    con, "SELECT fill_id,action,fill_price FROM paper_fill WHERE signal_uid=?", (uid,))]
                chain["processed"] = [dict(x) for x in q(
                    con, "SELECT action,detail FROM paper_processed WHERE signal_uid=?", (uid,))]
                for k in ("pending",):
                    for it in chain[k]:
                        it["created_bj"] = bj(it.get("created_at")); it["resolved_bj"] = bj(it.get("resolved_at"))
                rec["identity_chain"][uid] = chain

        con.close()
        out["arms"][arm] = rec

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "q02_ledger.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    # 控制台只打摘要
    for arm, r in out["arms"].items():
        print(f"[{arm}] counts={r['counts']} mtime={r['mtime_bj']}")
        print(f"     recompute={json.dumps(r['recompute'], ensure_ascii=False)}")
        print(f"     state={json.dumps(r['state'], ensure_ascii=False)}")
        for p in r["positions"]:
            print(f"     pos{p['position_id']} {p['symbol']} {p['status']} origin={p['origin']} "
                  f"voided={p['voided']} reason={p['exit_reason']} net_recalc={p.get('net_recalc')} "
                  f"fee_diff={p.get('fee_diff')} uid={p['signal_uid']}")


if __name__ == "__main__":
    main()
