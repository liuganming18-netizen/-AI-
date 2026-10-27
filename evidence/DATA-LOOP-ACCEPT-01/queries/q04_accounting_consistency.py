# -*- coding: utf-8 -*-
"""q04｜「页面同源显示」口径一致性复算：接口汇总 / 账本 state / 接口明细 三方对齐（只读）。

背景（代码事实，行号见报告）：
  · `paper_engine.account_view()` 默认口径 = initial_equity + Σ(net_pnl WHERE voided=0)，
    即**重算式**，把作废的测试记录剔除 ⇒ 3a 得到 5000.00；
  · `paper_state` 表是**累计式**，测试亏损没回滚 ⇒ 4995.65；
  · `/paper/accounts` 的 `realized_history` 与 `n_closed` **未按 voided 过滤** ⇒ 明细里仍有 3 笔作废记录。
本脚本把三处数字并列，输出差值与明细中作废条数，作为「同一响应内部口径冲突」的机械证据。

HTTP 走 `ProxyHandler({})`，避免本机代理把 127.0.0.1 请求转走（本工程已知坑）。
"""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parents[1] / "out"
TZ = dt.timezone(dt.timedelta(hours=8))
BASE = "http://127.0.0.1:8787"
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def get(path):
    with OPENER.open(BASE + path, timeout=15) as r:
        return json.loads(r.read().decode("utf-8"))


def bj(ms):
    return dt.datetime.fromtimestamp(ms / 1000, TZ).strftime("%Y-%m-%d %H:%M:%S") if ms else None


def main():
    now = dt.datetime.now(TZ)
    acc_env = get("/api/v1/paper/accounts")
    acc = (acc_env.get("items") or [acc_env.get("item")])[0]
    pos_env = get("/api/v1/paper/positions?arm=3a")
    pos = pos_env.get("items") if isinstance(pos_env.get("items"), list) else (pos_env.get("item") or [])

    db = ROOT / "数据" / "paper" / "paper_3a.sqlite3"
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    st = dict(con.execute("SELECT * FROM paper_state WHERE account_id='paper43_3a'").fetchone())
    real = [dict(r) for r in con.execute(
        "SELECT id,position_id,net_pnl,entry_fee,exit_fee,voided,exit_reason FROM paper_realized "
        "WHERE account_id='paper43_3a' ORDER BY id")]
    posrows = [dict(r) for r in con.execute(
        "SELECT position_id,status,origin,voided,exit_reason,signal_uid FROM paper_position "
        "WHERE account_id='paper43_3a' ORDER BY position_id")]
    con.close()

    hist = acc.get("realized_history") or []
    hist_voided = [h for h in hist if int(h.get("voided") or 0) == 1]
    live_real = [r for r in real if int(r.get("voided") or 0) == 0]

    out = {
        "checked_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "api": {
            "cash": acc.get("cash"), "equity": acc.get("equity"),
            "realized_pnl_total": acc.get("realized_pnl_total"),
            "fees_paid_total": acc.get("fees_paid_total"),
            "initial_equity": acc.get("initial_equity"),
            "n_orders": acc.get("n_orders"), "n_rejected": acc.get("n_rejected"),
            "n_closed": acc.get("n_closed"),
            "positions_len": len(pos or []), "pending_len": len(acc.get("pending") or []),
            "realized_history_len": len(hist),
            "realized_history_voided_len": len(hist_voided),
            "realized_history_voided_ids": [h.get("id") for h in hist_voided],
            "quality": acc_env.get("quality"),
            "version_fields": {k: acc_env.get(k) for k in
                               ("mode", "account_id", "run_id", "strategy_version",
                                "rule_version", "param_hash", "data_version")},
        },
        "ledger_state": {k: st[k] for k in ("cash", "realized_pnl", "fees_paid", "funding_paid")},
        "ledger_state_updated_bj": bj(st["updated_at"]),
        "ledger_realized_rows": real,
        "ledger_realized_voided_rows": len([r for r in real if int(r.get("voided") or 0) == 1]),
        "ledger_realized_live_rows": len(live_real),
        "ledger_positions": posrows,
        "diffs": {
            "api_cash_minus_state_cash": round((acc.get("cash") or 0) - st["cash"], 8),
            "api_realized_minus_state_realized": round((acc.get("realized_pnl_total") or 0) - st["realized_pnl"], 8),
            "api_fees_minus_state_fees": round((acc.get("fees_paid_total") or 0) - st["fees_paid"], 8),
            "history_rows_minus_live_rows": len(hist) - len(live_real),
            "n_closed_minus_live_rows": (acc.get("n_closed") or 0) - len(live_real),
            "initial_equity_vs_frozen_2000": (acc.get("initial_equity") or 0) - 2000.0,
        },
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "q04_accounting_consistency.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: out[k] for k in ("checked_at", "api", "ledger_state",
                                          "ledger_state_updated_bj", "diffs")},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
