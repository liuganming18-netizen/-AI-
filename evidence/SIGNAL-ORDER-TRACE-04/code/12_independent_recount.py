# -*- coding: utf-8 -*-
"""12｜独立复算：**绕过本单所有中间产物**，直接用 sqlite 读隔离账本重算。

为什么必须有这一段（项目既有纪律）：
  05 段的对账是用**本单自己的** `paper_engine` 调用结果 + 自己写的投影算出来的。
  自己验自己，天然会「一致」。本段故意**不读** `out/05_*.json`、**不 import** 本单
  任何模块的计算函数，只用 `sqlite3` + SQL 从账本原始表重算，再与报告数字比。

  这是「机械复算」而不是「再跑一遍我的代码」：
    · 净额恒等式   net = gross − (entry_fee + exit_fee) − funding_paid
    · 交易层 Σ     == 逐笔 realized 的 Σ
    · 成交↔订单     同 order_id 的 fill_price / qty / fee 必须逐字段相等
    · 关联完整性    fill.position_id / order.order_id 不得指向不存在的行
    · 资金费两层   Σ paper_funding.amount == Σ paper_realized.funding_paid
    · 时间因果     exit_ts > entry_ts（不允许等于）

**本段不做的事**：不重算价格（那要重放撮合，等于再实现一遍引擎）；只校验账本
内部一致性 + 与报告数字的等价性。能力边界写在 `capability_boundary` 里。
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
cm = __import__("importlib").import_module("00_common")

LEDGERS = {
    "replay": cm.ISO_LEDGER_DIR / "replay",
    "prod_epoch": cm.ISO_LEDGER_DIR / "prod_epoch",
    "gate_closed": cm.ISO_LEDGER_DIR / "gate_closed",
}
TOL = 1e-9


def _conn(d: Path):
    p = d / f"paper_{cm.PROD_ARM}.sqlite3"
    if not p.exists():
        return None, p
    return sqlite3.connect(f"file:{p}?mode=ro", uri=True), p


def _one(c, sql, *a):
    r = c.execute(sql, a).fetchone()
    return r[0] if r else None


def recount(name: str, d: Path):
    c, p = _conn(d)
    if c is None:
        return {"ledger": str(p), "exists": False}
    out = {"ledger": str(p), "exists": True,
           "bytes": p.stat().st_size}
    tables = [r[0] for r in c.execute(
        "select name from sqlite_master where type='table' order by name")]
    out["tables"] = tables
    counts = {}
    for t in tables:
        if t == "sqlite_sequence":
            continue
        counts[t] = _one(c, f"select count(*) from {t}")
    out["row_counts"] = counts

    # ── 1. 逐笔 realized 汇总 + 净额恒等式 ──────────────────────────────
    row = c.execute("""select count(*), sum(net_pnl), sum(gross_pnl),
                              sum(entry_fee + exit_fee), sum(funding_paid)
                       from paper_realized""").fetchone()
    n, net, gross, fees, fund = row
    out["realized"] = {
        "n": n, "net_pnl_sum": net, "gross_pnl_sum": gross,
        "fee_sum": fees, "funding_sum": fund,
    }
    out["identity_violations"] = {
        "net_eq_gross_minus_fees_minus_funding": _one(c, """
            select count(*) from paper_realized
            where abs(net_pnl - (gross_pnl - (entry_fee + exit_fee) - funding_paid)) > ?""", TOL),
    }

    # ── 2. 时间因果 ────────────────────────────────────────────────────
    out["time_causality"] = {
        "n_closed_positions": _one(c, "select count(*) from paper_position where exit_ts is not null"),
        "n_exit_not_after_entry": _one(c, """
            select count(*) from paper_position
            where exit_ts is not null and exit_ts <= entry_ts"""),
        "n_realized_exit_not_after_entry": _one(c, """
            select count(*) from paper_realized where exit_ts <= entry_ts"""),
    }

    # ── 3. 成交 ↔ 订单 逐字段 ──────────────────────────────────────────
    rows = list(c.execute("select order_id, count(*) from paper_fill group by order_id"))
    hist = {}
    for _, cc in rows:
        hist[str(cc)] = hist.get(str(cc), 0) + 1
    out["fill_per_order_histogram"] = hist
    out["n_filled_orders"] = len(rows)
    out["fill_vs_order_mismatch"] = _one(c, """
        select count(*) from paper_fill f join paper_order o on o.order_id = f.order_id
        where abs(coalesce(f.fill_price,0) - coalesce(o.fill_price,0)) > ?
           or abs(coalesce(f.qty,0)       - coalesce(o.qty,0))       > ?
           or abs(coalesce(f.fee,0)       - coalesce(o.fee,0))       > ?""", TOL, TOL, TOL)

    # ── 4. 关联完整性（悬空外键）────────────────────────────────────────
    out["dangling"] = {
        "fill_position_not_exists": _one(c, """
            select count(*) from paper_fill f where f.position_id is not null
              and not exists (select 1 from paper_position p where p.position_id = f.position_id)"""),
        "fill_order_not_exists": _one(c, """
            select count(*) from paper_fill f
              where not exists (select 1 from paper_order o where o.order_id = f.order_id)"""),
        "filled_order_without_fill": _one(c, """
            select count(*) from paper_order o where o.status = 'filled'
              and not exists (select 1 from paper_fill f where f.order_id = o.order_id)"""),
        "realized_position_not_exists": _one(c, """
            select count(*) from paper_realized r
              where not exists (select 1 from paper_position p where p.position_id = r.position_id)"""),
        "fill_signal_uid_empty": _one(c, """
            select count(*) from paper_fill where signal_uid is null or signal_uid = ''"""),
        "order_event_uid_empty": _one(c, """
            select count(*) from paper_order where event_uid is null or event_uid = ''"""),
    }

    # ── 5. 资金费两层 ─────────────────────────────────────────────────
    a = _one(c, "select sum(amount) from paper_funding")
    b = _one(c, "select sum(funding_paid) from paper_realized")
    out["funding_two_layer"] = {
        "sum_paper_funding_amount": a,
        "sum_paper_realized_funding_paid": b,
        "diff": (None if a is None or b is None else a - b),
        "ok": (None if a is None or b is None else abs(a - b) <= TOL),
    }

    # ── 6. 拒单原因分布 ────────────────────────────────────────────────
    out["blocked_reasons"] = {r[0]: r[1] for r in
                              c.execute("select reason, count(*) from paper_blocked group by reason")}
    out["order_status"] = {r[0]: r[1] for r in
                           c.execute("select status, count(*) from paper_order group by status")}
    c.close()
    return out


def compare_with_report(res: dict):
    """与 05 段报告数字做等价性比对（只比，不修）。"""
    p = cm.OUT / "05_核对_replay.json"
    if not p.exists():
        return {"compared": False, "reason": "缺 out/05_核对_replay.json"}
    rep = json.loads(p.read_text(encoding="utf-8"))
    s = rep.get("summary") or rep
    got = res["replay"]["realized"]
    pairs = {
        "n": (got["n"], s.get("n_closed")),
        "net_pnl_sum": (got["net_pnl_sum"], s.get("net_pnl_sum")),
        "gross_pnl_sum": (got["gross_pnl_sum"], s.get("gross_pnl_sum")),
        "fee_sum": (got["fee_sum"], s.get("fee_sum")),
        "funding_sum": (got["funding_sum"], s.get("funding_sum")),
    }
    detail, ok_all = {}, True
    for k, (a, b) in pairs.items():
        same = (a == b) if (isinstance(a, int) or b is None) else (
            b is not None and abs(a - b) <= TOL)
        detail[k] = {"independent": a, "report": b, "same": same}
        ok_all = ok_all and same
    return {"compared": True, "all_same": ok_all, "detail": detail}


def main():
    res = {name: recount(name, d) for name, d in LEDGERS.items()}
    out = {
        "generated_at_utc": cm.utc_now(),
        "method": "sqlite3 只读直连账本，不读本单中间产物、不 import 本单计算函数",
        "ledgers": res,
        "vs_report": compare_with_report(res),
        "capability_boundary": [
            "只校验账本**内部一致性**与**与报告数字的等价性**；不重算撮合价格（那需要重放引擎，等于再实现一遍）。",
            "`paper_order` 无 `signal_uid` 列 ⇒ 订单→候选只能走 `event_uid`；本段不假装有这条边。",
            "无交易所订单号（模拟盘不下真单）⇒ 不与交易所对账，也不伪造。",
            "本段 PASS 只说明「账本自洽 + 与报告一致」，**不构成策略可交付**（那是 WX-GATE-v2.0 的事）。",
        ],
    }
    p = cm.OUT / "12_独立复算.json"
    cm.write_json(p, out)
    r = res["replay"]
    print("replay:", r["row_counts"].get("paper_order"), "orders /",
          r["realized"]["n"], "realized / net =", r["realized"]["net_pnl_sum"])
    print("  恒等式违规 =", out["ledgers"]["replay"]["identity_violations"],
          "| 悬空 =", out["ledgers"]["replay"]["dangling"])
    print("  资金费两层 =", r["funding_two_layer"])
    print("  vs 报告:", json.dumps(out["vs_report"], ensure_ascii=False)[:300])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
