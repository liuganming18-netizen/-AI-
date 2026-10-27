# -*- coding: utf-8 -*-
"""SIGNAL-HISTORY-FIX-01｜07 执行链路核对（信号→订单→成交，只读）。

工单要求「明确区分：历史候选、当前新增候选、执行拒绝、订单和成交」，
并且「没有成交就明确记录原因」「订单级记录不能冒充逐次成交事件」。

本脚本只读纸面账本（`PAPER_LEDGER_DIR` 下的 sqlite），逐表点数并**逐行核对来源**：
  · `live_signal`      信号落盘（真实候选有没有进过这里）
  · `paper_pending`    待成交挂单（被拒的也在这里，`status='rejected'`）
  · `paper_order`      订单
  · `paper_position`   持仓
  · `paper_fill`       成交事件（**逐次成交**的唯一事实源）
  · `paper_realized`   已实现盈亏
  · `paper_blocked`    风控/闸门拒绝
逐条判定：该行的 `signal_uid` 是否等于**引擎真实候选**的 `candidate_id`
（`cand_ev_WX_EVENT_TREND_V0_1_...`）。是 ⇒ 真实信号驱动；否 ⇒ 测试驱动。

产出：out/07_执行链路核对.json、out/07_账本逐行.csv
"""
from __future__ import annotations

import importlib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
cm = importlib.import_module("00_common")

ARM = "3a"
#: 引擎真实候选 id：`cand_ev_WX_EVENT_TREND_V0_1_<venue>_<sym>_<...>`
REAL_CAND = re.compile(r"^cand_ev_WX_EVENT_TREND_V0_1_")
#: 引擎真实事件 id（候选的父事件）：`ev_WX_EVENT_TREND_V0_1_<venue>_<sym>_<...>`
#: 注意：`paper_order` / `paper_blocked` 用的是 `event_uid`（事件级），
#: 不是 `signal_uid`（候选级）⇒ 订单只能追溯到事件，不能追溯到候选。
REAL_EVENT = re.compile(r"^ev_WX_EVENT_TREND_V0_1_")

TABLES = ["live_signal", "paper_pending", "paper_order", "paper_position",
          "paper_fill", "paper_realized", "paper_blocked", "paper_processed",
          "paper_request_anchor", "paper_stop_history", "paper_equity"]


def main():
    import live_store as L

    out = {"ticket": "SIGNAL-HISTORY-FIX-01", "stage": "ledger_audit",
           "generated_at_utc": cm.utc_now(), "arm": ARM,
           "ledger_dir": str(getattr(L, "PAPER_LEDGER_DIR", None)),
           "ledger_db": None, "tables": {}, "rows": []}

    con = L.paper_conn(ARM, ro=True)
    con.row_factory = __import__("sqlite3").Row
    try:
        out["ledger_db"] = str(con.execute("PRAGMA database_list").fetchone()[2])
        exist = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        for t in TABLES:
            if t not in exist:
                out["tables"][t] = {"exists": False, "n": 0}
                continue
            n = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            out["tables"][t] = {"exists": True, "n": n}

        # ── 逐行核对来源 ────────────────────────────────────────────────
        schemas = {}
        for t in TABLES:
            if t in exist:
                schemas[t] = [r[1] for r in con.execute(f"PRAGMA table_info({t})").fetchall()]
        out["schemas"] = schemas

        def dump(table, cols):
            """按**实际表结构**取列：请求列与真实列取交集，永不猜列名。"""
            if table not in exist:
                return
            real = schemas[table]
            use = [c for c in cols if c in real]
            sel = ", ".join(use) if use else "*"
            for r in con.execute(f"SELECT {sel} FROM {table}").fetchall():
                d = {c: r[c] for c in use}
                uid = (d.get("signal_uid") or d.get("candidate_id")
                       or d.get("event_uid") or "")
                d["_table"] = table
                d["_uid"] = uid
                if not uid:
                    kind = "empty"
                elif REAL_CAND.match(str(uid)):
                    kind = "real_candidate"
                elif REAL_EVENT.match(str(uid)):
                    kind = "real_event"
                else:
                    kind = "test_or_other"
                d["_uid_kind"] = kind
                d["_uid_is_real_candidate"] = (kind == "real_candidate")
                d["_cols_used"] = use
                out["rows"].append(d)

        dump("live_signal", ["signal_uid", "symbol", "origin", "event_time",
                             "available_at", "executable_state", "first_seen_at"])
        dump("paper_pending", ["pending_id", "signal_uid", "symbol", "status", "reason",
                               "earliest_fill_ms", "created_at"])
        dump("paper_order", ["order_id", "event_uid", "symbol", "side", "status",
                             "reject_reason", "event_time", "first_request_ms"])
        dump("paper_position", ["position_id", "signal_uid", "symbol", "status",
                                "exit_reason", "entry_ts", "exit_ts"])
        dump("paper_fill", ["fill_id", "order_id", "position_id", "signal_uid", "symbol",
                            "action", "fill_price", "qty", "fill_ts", "available_at"])
        dump("paper_realized", ["realized_id", "position_id", "symbol", "pnl_abs",
                                "pnl_r", "closed_at"])
        dump("paper_blocked", ["id", "event_uid", "symbol", "reason", "ts"])
    finally:
        con.close()

    # ── 汇总：真实信号 vs 测试信号 ─────────────────────────────────────
    per_table = {}
    for r in out["rows"]:
        t = r["_table"]
        d = per_table.setdefault(t, {"rows": 0, "real_candidate": 0, "real_event": 0,
                                     "test_or_other": 0, "empty": 0})
        d["rows"] += 1
        d[r["_uid_kind"]] += 1
    out["per_table_source"] = per_table

    # ── 逐条状态分布 ──────────────────────────────────────────────────
    def dist(table, field):
        rows = [r for r in out["rows"] if r["_table"] == table]
        c = {}
        for r in rows:
            k = str(r.get(field))
            c[k] = c.get(k, 0) + 1
        return c

    out["distributions"] = {
        "paper_pending.status": dist("paper_pending", "status"),
        "paper_pending.reason": dist("paper_pending", "reason"),
        "paper_order.status": dist("paper_order", "status"),
        "paper_order.reject_reason": dist("paper_order", "reject_reason"),
        "paper_position.status": dist("paper_position", "status"),
        "paper_position.exit_reason": dist("paper_position", "exit_reason"),
        "paper_blocked.reason": dist("paper_blocked", "reason"),
        "paper_fill.action": dist("paper_fill", "action"),
    }

    real_anywhere = sum(v["real_candidate"] for v in per_table.values())
    event_anywhere = sum(v["real_event"] for v in per_table.values())
    out["verdict"] = {
        "real_candidate_signals_in_ledger": per_table.get("live_signal", {}).get(
            "real_candidate", 0),
        "rows_attributable_to_real_candidate_anywhere": real_anywhere,
        "rows_attributable_to_real_event_only": event_anywhere,
        "fills_total": per_table.get("paper_fill", {}).get("rows", 0),
        "conclusion": (
            "账本内没有任何一行由**引擎真实候选**驱动" if real_anywhere == 0
            else f"账本内有 {real_anywhere} 行可追溯到真实候选"),
        "identity_gap": ("`paper_order` / `paper_blocked` 只有 `event_uid`（事件级），"
                         "没有 `signal_uid`（候选级）⇒ 即使有真实候选，订单表也无法"
                         "按候选逐条对账；逐次成交事件只在 `paper_fill` 里，"
                         "两者不可互相代替。"),
        "discipline": ("订单级记录（paper_order）与逐次成交事件（paper_fill）分开计数；"
                       "`paper_position` 全部为测试驱动时，不得表述为「成交链路已打通」。"),
    }

    cm.write_json("07_执行链路核对.json", out)

    import csv
    keys = ["_table", "_uid", "_uid_kind", "symbol", "status", "reject_reason",
            "reason", "action", "fill_price", "qty", "fill_ts", "entry_ts", "exit_ts",
            "exit_reason"]
    with open(cm.OUT / "07_账本逐行.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(keys)
        for r in out["rows"]:
            w.writerow([r.get(k) for k in keys])

    print("ledger:", out["ledger_db"])
    for t, v in out["tables"].items():
        print(f"  {t:24s} n={v['n']}")
    print("per_table_source:", json.dumps(per_table, ensure_ascii=False))
    print("distributions:", json.dumps(out["distributions"], ensure_ascii=False))
    print("verdict:", json.dumps(out["verdict"], ensure_ascii=False))


if __name__ == "__main__":
    main()
