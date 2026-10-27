# -*- coding: utf-8 -*-
"""05｜逐笔订单 / 逐次成交 / 交易汇总核对 + 关联完整性 + 能力边界。

核对口径（全部机械复算，不抄写）：
  · **关联链**：`candidate_id` → `paper_order.event_uid`（开仓单）→ `paper_fill.order_id`
    → `paper_fill.position_id` → `paper_position.signal_uid` → `paper_realized.position_id`
    每一环都必须**唯一命中**；断链/多义一律记为 `broken`，不按顺序补。
  · **数量/价格/费用/盈亏**：成交 vs 持仓 vs 已实现三方逐字段对账（容差 1e-9 相对）。
  · **能力边界**：引擎**有**逐次成交表 `paper_fill`（不是订单级投影）；但它每单只落 1 条
    （无部分成交展开）、且**没有交易所订单号**；跳空止损按 `stop` 价成交（非 `open`）——
    这三条都如实写进结果，不修饰。
"""
from __future__ import annotations

import argparse
import csv
import importlib
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
cm = importlib.import_module("00_common")

ARM = "3a"
ACCOUNT = f"paper43_{ARM}"
TOL = 1e-9


def _close(a, b, tol=TOL) -> bool:
    if a is None or b is None:
        return a is None and b is None
    try:
        a, b = float(a), float(b)
    except (TypeError, ValueError):
        return False
    scale = max(1.0, abs(a), abs(b))
    return abs(a - b) <= tol * scale


def load_ledger(ledger_dir: Path) -> dict:
    p = ledger_dir / f"paper_{ARM}.sqlite3"
    con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    out = {}
    for t in ("paper_order", "paper_fill", "paper_position", "paper_realized",
              "paper_blocked", "paper_processed", "paper_pending", "paper_stop_history",
              "paper_funding", "paper_request_anchor"):
        try:
            out[t] = [dict(r) for r in con.execute(f"SELECT * FROM {t}")]
        except Exception:                                          # noqa: BLE001
            out[t] = []
    con.close()
    return out


def verify_case(case: dict, led: dict) -> dict:
    """单笔：关联链 + 三方对账。"""
    cid = case["candidate_id"]
    sym = case["symbol"]
    pid = case.get("position_id")
    chk = {"candidate_id": cid, "symbol": sym, "position_id": pid}

    if not case["open_ok"]:
        # 拒单：必须有拒单订单 + blocked 记录，且不得有成交/持仓
        o = [x for x in led["paper_order"] if x.get("event_uid") == cid]
        b = [x for x in led["paper_blocked"] if x.get("event_uid") == cid]
        f = [x for x in led["paper_fill"] if x.get("signal_uid") == cid]
        chk.update({
            "kind": "rejected",
            "reject_order_rows": len(o),
            "reject_order_status": sorted({x.get("status") for x in o}),
            "blocked_rows": len(b),
            "blocked_reasons": sorted({x.get("reason") for x in b}),
            "fills_for_this_candidate": len(f),
            "chain_ok": (len(o) == 1 and o[0].get("status") == "rejected"
                         and len(b) >= 1 and len(f) == 0),
            "note": "拒单不得产生成交/持仓（重复输入靠 `event_uid` UNIQUE 去重）",
        })
        return chk

    # 成交笔：真实关联
    pos = next((p for p in led["paper_position"] if p["position_id"] == pid), None)
    entry_orders = [x for x in led["paper_order"] if x.get("event_uid") == cid]
    exit_orders = [x for x in led["paper_order"]
                   if str(x.get("event_uid") or "").startswith(f"exit:{ARM}:{pid}:")]
    fills = [x for x in led["paper_fill"] if x.get("position_id") == pid]
    open_fills = [x for x in fills if x.get("action") == "open"]
    close_fills = [x for x in fills if x.get("action") == "close"]
    real = next((r for r in led["paper_realized"] if r.get("position_id") == pid), None)

    chk.update({
        "kind": "filled",
        "entry_order_rows": len(entry_orders),
        "exit_order_rows": len(exit_orders),
        "open_fill_rows": len(open_fills),
        "close_fill_rows": len(close_fills),
        "position_found": pos is not None,
        "realized_found": real is not None,
    })

    # ── ① 关联链逐环 ──────────────────────────────────────────────
    links = {}
    links["candidate_to_entry_order"] = bool(
        len(entry_orders) == 1 and entry_orders[0].get("status") == "filled")
    links["entry_order_to_open_fill"] = bool(
        len(open_fills) == 1
        and open_fills[0].get("order_id") == entry_orders[0].get("order_id")
        if entry_orders else False)
    links["position_to_candidate"] = bool(pos and pos.get("signal_uid") == cid)
    links["open_fill_to_position"] = bool(
        len(open_fills) == 1 and open_fills[0].get("position_id") == pid)
    links["exit_order_exists"] = bool(len(exit_orders) == 1)
    links["exit_order_to_close_fill"] = bool(
        len(close_fills) == 1 and exit_orders
        and close_fills[0].get("order_id") == exit_orders[0].get("order_id"))
    links["realized_to_position"] = bool(real and real.get("position_id") == pid)
    links["close_fill_signal_uid"] = bool(
        len(close_fills) == 1 and close_fills[0].get("signal_uid") == cid)
    chk["links"] = links
    chk["chain_ok"] = all(links.values())

    # ── ② 数量 / 价格 / 费用 / 盈亏 三方对账 ──────────────────────
    checks = {}
    if pos and real and open_fills and close_fills:
        of, cf = open_fills[0], close_fills[0]
        qty = float(pos["qty"])
        checks["qty_fill_vs_position"] = (_close(of["qty"], qty)
                                         and _close(cf["qty"], qty)
                                         and _close(real["qty"], qty))
        checks["entry_price_chain"] = (_close(of["fill_price"], pos["entry_price"])
                                       and _close(of["fill_price"], real["entry_price"]))
        checks["exit_price_chain"] = (_close(cf["fill_price"], pos["exit_price"])
                                      and _close(cf["fill_price"], real["exit_price"]))
        checks["fee_entry"] = _close(pos["entry_fee"], of["fee"])
        checks["fee_exit"] = _close(pos["exit_fee"], cf["fee"])
        checks["fee_sum_vs_realized"] = _close(
            float(real["entry_fee"]) + float(real["exit_fee"]),
            float(of["fee"]) + float(cf["fee"]))
        # 方向：`paper_position` 无 direction 列，只能由开仓订单 side 推断（'buy'=多头）
        is_long = (entry_orders[0].get("side") == "buy") if entry_orders else True
        chk["direction"] = "long" if is_long else "short"
        chk["direction_source"] = "paper_order.side（position 表无 direction 列）"
        gross = ((float(real["exit_price"]) - float(real["entry_price"])) if is_long
                 else (float(real["entry_price"]) - float(real["exit_price"]))) * qty
        checks["gross_pnl_recompute"] = _close(gross, real["gross_pnl"])
        net = (float(real["gross_pnl"]) - float(real["entry_fee"])
               - float(real["exit_fee"]) - float(real["funding_paid"]))
        checks["net_pnl_recompute"] = _close(net, real["net_pnl"])
        checks["notional_open"] = _close(of["notional"], qty * float(of["fill_price"]))
        checks["notional_close"] = _close(cf["notional"], qty * float(cf["fill_price"]))
        checks["slippage_open"] = _close(
            of["slippage_cost"], qty * abs(float(of["raw_price"]) - float(of["fill_price"])))
        checks["slippage_close"] = _close(
            cf["slippage_cost"], qty * abs(float(cf["raw_price"]) - float(cf["fill_price"])))
        # ── 时间因果：平仓必须晚于开仓（**硬判据**，之前漏了这一条）──────────
        # 用持仓自身的 entry_ts/exit_ts；成交的 bar_open_time 不能当时间轴 ——
        # `advance_position_trailing` 触发退出时传的是 **4h bar** 的 open，
        # 可能早于入场时刻（本单 15/48 笔走 4h 路径）。
        et, xt = int(pos["entry_ts"] or 0), int(pos["exit_ts"] or 0)
        checks["exit_after_entry"] = bool(et and xt and xt > et)
        chk["entry_ts_utc"] = cm.iso(et)
        chk["exit_ts_utc"] = cm.iso(xt)
        chk["holding_hours_from_position"] = (round((xt - et) / 3_600_000.0, 6)
                                              if et and xt else None)
        span = int(cf.get("bar_close_time") or 0) - int(cf.get("bar_open_time") or 0)
        chk["exit_path"] = ("4h_advance_position_trailing" if span > 1_000_000
                            else "15m_check_position_exit_on_bar")
        chk["close_fill_bar_open_ms"] = cf.get("bar_open_time")
        # 只记录不改：4h 路径的参考 bar 可能在入场前就已开始，其 low 含入场前价格
        chk["close_fill_bar_open_precedes_entry"] = bool(
            cf.get("bar_open_time") and int(cf["bar_open_time"]) < et)
        # 资金费：Σ paper_funding.amount == realized.funding_paid
        fund = [f for f in led["paper_funding"] if f.get("position_id") == pid]
        checks["funding_sum_vs_realized"] = _close(
            sum(float(f["amount"] or 0) for f in fund), real["funding_paid"])
        chk["funding_rows"] = len(fund)
        # R 倍数（开仓时权益 × 0.5%）
        chk["net_pnl"] = real["net_pnl"]
        chk["gross_pnl"] = real["gross_pnl"]
    chk["value_checks"] = checks
    chk["value_ok"] = all(checks.values()) if checks else False

    # ── ③ 跳空偏差（只记录，不改规则）──────────────────────────────
    chk["gap_cases"] = case.get("gap_cases") or []
    chk["exit_reason"] = case.get("exit_reason")
    chk["closed_by"] = case.get("closed_by")
    chk["exchange_order_id"] = None
    chk["exchange_order_absent_reason"] = "模拟盘不下真单，无交易所回报"
    return chk


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pass", dest="tag", default="replay")
    a = ap.parse_args()

    d = cm.read_json(cm.OUT / f"04_{a.tag}.json")
    led = load_ledger(Path(d["isolated_paths"]["LEDGER_DIR"]))
    cases = d["cases"]
    rows = [verify_case(c, led) for c in cases]

    # ── 逐笔订单 / 逐次成交 导出（真实字段，不用投影）──────────────
    orders = led["paper_order"]
    fills = led["paper_fill"]
    order_by_id = {o["order_id"]: o for o in orders}
    pid2cid = {}
    for p in led["paper_position"]:
        pid2cid[p["position_id"]] = p.get("signal_uid")

    with open(cm.OUT / f"05_逐笔订单_{a.tag}.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["order_id", "event_uid", "candidate_id", "position_id", "symbol", "side",
                    "status", "reject_reason", "qty", "price", "fill_price", "notional",
                    "fee", "ts_utc", "bar_open_utc", "market_source", "exchange_order_id",
                    "exchange_order_absent_reason"])
        for o in orders:
            euid = str(o.get("event_uid") or "")
            if euid.startswith(f"exit:{ARM}:"):
                pid = int(euid.split(":")[2])
                cid = pid2cid.get(pid)
            else:
                cid, pid = euid, None
            w.writerow([o["order_id"], euid, cid, pid, o.get("symbol"), o.get("side"),
                        o.get("status"), o.get("reject_reason"), o.get("qty"),
                        o.get("price"), o.get("fill_price"), o.get("notional"),
                        o.get("fee"), cm.iso(o.get("ts")), cm.iso(o.get("bar_open_time")),
                        o.get("market_source"), "",
                        "模拟盘不下真单，无交易所回报"])

    with open(cm.OUT / f"05_逐次成交_{a.tag}.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["fill_id", "order_id", "position_id", "signal_uid", "symbol", "action",
                    "qty", "raw_price", "fill_price", "notional", "fee", "slippage_cost",
                    "bar_open_utc", "bar_close_utc", "fill_ts_utc", "model_version",
                    "exchange_order_id"])
        for x in fills:
            w.writerow([x["fill_id"], x.get("order_id"), x.get("position_id"),
                        x.get("signal_uid"), x.get("symbol"), x.get("action"),
                        x.get("qty"), x.get("raw_price"), x.get("fill_price"),
                        x.get("notional"), x.get("fee"), x.get("slippage_cost"),
                        cm.iso(x.get("bar_open_time")), cm.iso(x.get("bar_close_time")),
                        cm.iso(x.get("fill_ts")), x.get("model_version"), ""])

    # ── 交易汇总 ────────────────────────────────────────────────────
    filled = [r for r in rows if r["kind"] == "filled"]
    closed = [r for r in filled if r.get("net_pnl") is not None]
    wins = [r for r in closed if float(r["net_pnl"]) > 0]
    losses = [r for r in closed if float(r["net_pnl"]) < 0]
    flats = [r for r in closed if float(r["net_pnl"]) == 0]
    real_rows = led["paper_realized"]
    summary = {
        "pass": a.tag,
        "generated_at_utc": cm.utc_now(),
        "n_candidates": len(cases),
        "n_open_ok": sum(1 for c in cases if c["open_ok"]),
        "n_rejected": sum(1 for c in cases if not c["open_ok"]),
        "n_filled_positions": len(filled),
        "n_closed": len(closed),
        "n_not_closed": len(filled) - len(closed),
        "wins": len(wins), "losses": len(losses), "flats": len(flats),
        "win_rate_pct": (round(100.0 * len(wins) / len(closed), 4) if closed else None),
        "net_pnl_sum": round(sum(float(r["net_pnl"]) for r in closed), 10),
        "gross_pnl_sum": round(sum(float(r["gross_pnl"]) for r in closed), 10),
        "fee_sum": round(sum(float(x["fee"] or 0) for x in fills), 10),
        "slippage_sum": round(sum(float(x["slippage_cost"] or 0) for x in fills), 10),
        "funding_sum": round(sum(float(x["amount"] or 0) for x in led["paper_funding"]), 10),
        "ledger_counts": {k: len(v) for k, v in led.items()},
        "chain_ok_all": all(r["chain_ok"] for r in rows),
        "value_ok_all": all(r.get("value_ok") for r in filled) if filled else None,
        "n_chain_broken": [r["candidate_id"] for r in rows if not r["chain_ok"]],
        "n_value_mismatch": [r["candidate_id"] for r in filled if not r.get("value_ok")],
        "gap_case_total": sum(len(r.get("gap_cases") or []) for r in filled),
        "exit_path_histogram": _hist([r.get("exit_path") for r in filled]),
        "n_close_bar_before_entry": sum(
            1 for r in filled if r.get("close_fill_bar_open_precedes_entry")),
        "n_exit_not_after_entry": [r["candidate_id"] for r in filled
                                   if not (r.get("value_checks") or {}).get("exit_after_entry")],
        "holding_hours_range": [
            min([r["holding_hours_from_position"] for r in filled
                 if r.get("holding_hours_from_position") is not None], default=None),
            max([r["holding_hours_from_position"] for r in filled
                 if r.get("holding_hours_from_position") is not None], default=None),
        ],
        "capability_boundary": {
            "has_per_fill_events": len(fills) > 0,
            "per_fill_rows": len(fills),
            "per_order_fill_rows_histogram": _hist([sum(
                1 for x in fills if x.get("order_id") == o["order_id"])
                for o in orders if o.get("status") == "filled"]),
            "exchange_order_id": None,
            "exchange_order_absent_reason": "模拟盘不下真单；本模块无任何交易所写接口",
            "partial_fill_supported": False,
            "partial_fill_note": ("引擎每张成交订单只落 **1 条** `paper_fill`（全量成交），"
                                  "不支持部分成交展开 ⇒ 逐次成交=逐订单成交，"
                                  "**不是订单级投影**，但也不能据此推断撮合深度。"),
            "gap_stop_note": ("15m 跳空穿线时 `check_position_exit_on_bar` 仍按 `cur_stop` 成交"
                              "（未用更差的 `open`）⇒ 对多头偏乐观。本单**只记录不改**"
                              "（改即改冻结出场规则）。"),
        },
    }
    with open(cm.OUT / f"05_交易汇总_{a.tag}.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["candidate_id", "symbol", "position_id", "chain_ok", "value_ok",
                    "entry_price", "exit_price", "qty", "exit_reason", "closed_by",
                    "exit_path", "entry_ts_utc", "exit_ts_utc", "holding_hours",
                    "gross_pnl", "net_pnl", "gap_cases"])
        for r in rows:
            c = next((x for x in cases if x["candidate_id"] == r["candidate_id"]), {})
            w.writerow([r["candidate_id"], r["symbol"], r["position_id"],
                        r["chain_ok"], r.get("value_ok"),
                        c.get("entry_price"), c.get("exit_price"), c.get("qty"),
                        r.get("exit_reason"), r.get("closed_by"),
                        r.get("exit_path"), r.get("entry_ts_utc"), r.get("exit_ts_utc"),
                        r.get("holding_hours_from_position"),
                        r.get("gross_pnl"), r.get("net_pnl"),
                        len(r.get("gap_cases") or [])])

    summary["rows"] = rows
    cm.write_json(f"05_核对_{a.tag}.json", summary)
    print(f"[{a.tag}] 候选 {summary['n_candidates']} / 开仓成功 {summary['n_open_ok']} / "
          f"已平 {summary['n_closed']}")
    print(f"  关联链全通={summary['chain_ok_all']}  数值全对={summary['value_ok_all']}  "
          f"断链={summary['n_chain_broken']}  数值不符={summary['n_value_mismatch']}")
    print(f"  盈亏 net={summary['net_pnl_sum']} gross={summary['gross_pnl_sum']} "
          f"fee={summary['fee_sum']} funding={summary['funding_sum']}")
    print(f"  胜/负/平 = {summary['wins']}/{summary['losses']}/{summary['flats']}  "
          f"跳空样本={summary['gap_case_total']}")
    print(f"  出场路径 {json.dumps(summary['exit_path_histogram'], ensure_ascii=False)}  "
          f"平仓bar早于入场={summary['n_close_bar_before_entry']}  "
          f"平仓不晚于开仓={summary['n_exit_not_after_entry']}")
    print(f"  持有小时区间={summary['holding_hours_range']}")
    print(f"  账本 {json.dumps(summary['ledger_counts'], ensure_ascii=False)}")
    return 0


def _hist(vals) -> dict:
    h = {}
    for v in vals:
        h[str(v)] = h.get(str(v), 0) + 1
    return dict(sorted(h.items()))


if __name__ == "__main__":
    raise SystemExit(main())
