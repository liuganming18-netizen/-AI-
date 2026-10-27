# -*- coding: utf-8 -*-
"""06｜把隔离回放注册进**既有工作台**（run 目录 + run.json），不碰生产 runs 目录。

工单第五条要求「将本单回放注册到已有工作台，验证对应K线可重开、订单可定位」。
本脚本只做「按既有产物契约落盘」这件事，**不新造契约**：

产物文件名与字段严格照 `框架/backend/ledger_links.py` 与
`backtest_api._generate_fallback_chart / _run_own_window` 的读取口径：
  `trades.jsonl` / `orders.jsonl` / `fills.jsonl` / `funding.jsonl` /
  `positions.jsonl` / `candidates.jsonl` / `equity_curve.jsonl` /
  `summary.json` / `inputs.json` / `run.json`

**不写 `chart.json`**：让 `GET /chart` 走 `_generate_fallback_chart`，用本 run 自己的
标的与窗口从冷层 parquet 现读真实 K 线 —— 手写一份 chart.json 反而是「自己造图」。

身份口径（与 01/05 一致）：
  · `trade_id = trd_paper_pos_<position_id>`：`position_id` 是模拟账本真实主键，
    这里做**显式投影**并同时保留 `paper_position_id` 原值，不冒充引擎 trade_id；
  · 订单号：`paper_order.order_id`（账本内唯一，跨库不可比）；
  · 交易所订单号：**不存在**，一律 `None` + 原因。
"""
from __future__ import annotations

import importlib
import json
import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
cm = importlib.import_module("00_common")

ARM = "3a"
ACCOUNT = f"paper43_{ARM}"
RUNS_DIR = cm.OUT / "iso_runs"
TAG = "replay"


def read_ledger(ledger_dir: Path) -> dict:
    p = ledger_dir / f"paper_{ARM}.sqlite3"
    con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    out = {}
    for t in ("paper_order", "paper_fill", "paper_position", "paper_realized",
              "paper_funding", "paper_stop_history", "paper_equity",
              "paper_blocked", "paper_processed"):
        try:
            out[t] = [dict(r) for r in con.execute(f"SELECT * FROM {t}")]
        except sqlite3.Error:
            out[t] = []
    con.close()
    return out


def _ms(iso_s):
    if not iso_s:
        return None
    from datetime import datetime, timezone
    return int(datetime.fromisoformat(str(iso_s).replace("Z", "+00:00")).timestamp() * 1000)


def _write_jsonl(p: Path, rows) -> int:
    n = 0
    with p.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
            n += 1
    return n


def build_symbol_run(sym: str, cases: list, led: dict, cand_by_id: dict) -> dict:
    """把某标的的全部已成交候选落成一个 run 目录。"""
    trades_src = []
    for c in cases:
        if c["symbol"] != sym or not c["open_ok"]:
            continue
        pid = c["position_id"]
        pos = next((p for p in led["paper_position"] if p["position_id"] == pid), None)
        real = next((r for r in led["paper_realized"] if r["position_id"] == pid), None)
        fills = [f for f in led["paper_fill"] if f.get("position_id") == pid]
        orders = [o for o in led["paper_order"] if o.get("event_uid") == c["candidate_id"]]
        exit_orders = [o for o in led["paper_order"]
                       if str(o.get("event_uid") or "").startswith(f"exit:{ARM}:{pid}:")]
        stops = [s for s in led["paper_stop_history"] if s.get("position_id") == pid]
        fund = [f for f in led["paper_funding"] if f.get("position_id") == pid]
        trades_src.append(dict(case=c, pos=pos, real=real, fills=fills, orders=orders,
                               exit_orders=exit_orders, stops=stops, fund=fund))

    # 只有拒单、没有成交的标的也要建 run：否则 14/17 笔真实拒单在工作台里查不到
    rej_cases = [c for c in cases if c["symbol"] == sym and not c["open_ok"]]
    if not trades_src and not rej_cases:
        return {}

    # ── trades.jsonl ────────────────────────────────────────────────────
    trades, orders_rows, fills_rows, positions_rows, funding_rows = [], [], [], [], []
    for t in trades_src:
        c, pos, real = t["case"], t["pos"], t["real"]
        pid = c["position_id"]
        tid = f"trd_paper_pos_{pid}"
        of = next((f for f in t["fills"] if f.get("action") == "open"), None)
        cf = next((f for f in t["fills"] if f.get("action") == "close"), None)
        # **时间取持仓自身的 entry_ts / exit_ts**，不取成交的 bar_open_time。
        # 为什么：`advance_position_trailing` 触发退出时 `close_at_price` 传的是
        # **4h bar** 的 open/close（paper_engine.py 中 `ref_open_ms/ref_close_ms`），
        # 而 4h bar 可能早于入场时刻 ⇒ 用 bar_open_time 当 exit_time 会算出**负持有期**
        # （本单实测 15/48 笔平仓走 4h 路径，其中 14 笔 bar_open < entry）。
        # `entry_ts`/`exit_ts` 是引擎自己落的权威时刻，且 exit_ts = max(decision, 4h close)。
        et = int(pos["entry_ts"]) if pos and pos.get("entry_ts") else \
            int((of or {}).get("fill_ts") or 0) or _ms(c.get("attempt_fill_bar_utc"))
        xt = int(pos["exit_ts"]) if pos and pos.get("exit_ts") else \
            int((cf or {}).get("fill_ts") or 0) or _ms(c.get("exit_ts_utc"))
        ent_oid = t["orders"][0]["order_id"] if t["orders"] else None
        ext_oid = t["exit_orders"][0]["order_id"] if t["exit_orders"] else None
        init_stop = next((s["new_stop"] for s in t["stops"]
                          if s.get("reason") == "initial_stop"), None)
        stop_trace = [{"t": int(s["ref_bar_open_ms"]) if s.get("ref_bar_open_ms") else None,
                       "stop": s.get("new_stop"), "reason": s.get("reason")}
                      for s in sorted(t["stops"], key=lambda x: x.get("ref_bar_open_ms") or 0)]
        stop_trace = [s for s in stop_trace if s["t"]]
        fund_sum = round(sum(float(f.get("amount") or 0) for f in t["fund"]), 10)
        trade = {
            "run_id": None,                     # 落盘后回填
            "trade_id": tid,
            "trade_id_source": "paper_realized.position_id（显式投影，非引擎 trade_id）",
            "paper_position_id": pid,
            "candidate_id": c["candidate_id"],
            "cross_run_key": c.get("cross_run_key"),
            "signal_id": c["candidate_id"],
            "symbol": sym,
            "direction": "long",
            "entry_time": et,
            "exit_time": xt,
            "entry_price": c.get("entry_price"),
            "exit_price": c.get("exit_price"),
            "qty": c.get("qty"),
            "holding_hours": (round((xt - et) / 3_600_000.0, 6) if et and xt else None),
            "entry_bar_open_time": (of or {}).get("bar_open_time"),
            "exit_bar_open_time": (cf or {}).get("bar_open_time"),
            "exit_path": ("4h_advance_position_trailing"
                          if (cf and int(cf.get("bar_close_time") or 0)
                              - int(cf.get("bar_open_time") or 0) > 1_000_000)
                          else "15m_check_position_exit_on_bar"),
            "gross_pnl": (real or {}).get("gross_pnl"),
            "entry_fee": (real or {}).get("entry_fee"),
            "exit_fee": (real or {}).get("exit_fee"),
            "fees": round(float((real or {}).get("entry_fee") or 0)
                          + float((real or {}).get("exit_fee") or 0), 10),
            "funding_fee": (real or {}).get("funding_paid"),
            "funding_fee_recomputed": fund_sum,
            "net_pnl": (real or {}).get("net_pnl"),
            "exit_reason": c.get("exit_reason"),
            "closed_by": c.get("closed_by"),
            "order_id": ent_oid,
            "exit_order_id": ext_oid,
            "initial_stop": init_stop,
            "initial_stop_source": "paper_stop_history.reason=initial_stop",
            "stop_trace": stop_trace,
            "signal_time_ms": _ms(c.get("snapshot_time_utc")),
            "available_at_ms": _ms(c.get("available_at_utc")),
            "execution_earliest_ms": _ms(c.get("execution_earliest_utc")),
            "order_time_ms": (t["orders"][0].get("ts") if t["orders"] else None),
            "order_time_basis": "paper_order.ts（隔离回放推进时钟，非真实挂钟）",
            "origin": "isolated_replay",
            "exchange_order_id": None,
            "exchange_order_absent_reason": "模拟盘不下真单，无交易所回报",
            "gap_cases": c.get("gap_cases") or [],
            "n_advance_4h": c.get("n_advance_4h"),
            "n_bar15": c.get("n_bar15"),
        }
        trades.append(trade)

        for o in t["orders"] + t["exit_orders"]:
            is_entry = o.get("event_uid") == c["candidate_id"]
            orders_rows.append({
                "run_id": None, "order_id": o["order_id"], "trade_id": tid,
                "signal_id": c["candidate_id"], "symbol": sym,
                "side": o.get("side"), "is_entry": is_entry,
                "phase": "entry" if is_entry else "exit",
                "ts_ms": o.get("ts"), "ts": o.get("ts"),
                "action": o.get("status"), "status": o.get("status"),
                "reason": o.get("reject_reason"),
                "price": o.get("fill_price") if o.get("fill_price") is not None else o.get("price"),
                "raw_price": o.get("price"),
                "qty": o.get("qty"), "fee": o.get("fee"), "notional": o.get("notional"),
                "bar_open_time": o.get("bar_open_time"),
                "stoploss_s": o.get("stoploss_s"), "trail_d": o.get("trail_d"),
                "stop_price": o.get("initial_stop_price"),
                "config_fingerprint": o.get("config_fingerprint"),
                "model_version": o.get("model_version"),
                "event_uid": o.get("event_uid"),
                "exchange_order_id": None,
            })
        # fills.jsonl **必须**是「事件 + payload」结构：`ledger_links.all_fills()`
        # 只从 `payload` 里取 order_id / fill_price / quantity / fee / liquidity，
        # 平铺字段会被读成 None（那正是本单要防的「字段在、读不到」）。
        for f in t["fills"]:
            fills_rows.append({
                "run_id": None, "event_type": "FILL",
                "event_id": f"paper_fill:{f['fill_id']}",
                "symbol": sym,
                "event_time": f.get("fill_ts"),
                "sequence": f["fill_id"],
                "payload": {
                    "order_id": f.get("order_id"),
                    "action": f.get("action"),
                    "fill_price": f.get("fill_price"),
                    "quantity": f.get("qty"),
                    "fee": f.get("fee"),
                    # 引擎不落撮合流动性 / 部分成交 ⇒ 如实 None，不编造
                    "liquidity": None,
                    "detail": {
                        "position_id": pid, "signal_uid": f.get("signal_uid"),
                        "raw_price": f.get("raw_price"), "notional": f.get("notional"),
                        "slippage_cost": f.get("slippage_cost"),
                        "bar_open_time": f.get("bar_open_time"),
                        "bar_close_time": f.get("bar_close_time"),
                        "model_version": f.get("model_version"),
                        "exchange_order_id": None,
                        "exchange_order_absent_reason": "模拟盘不下真单，无交易所回报",
                    },
                },
            })
        positions_rows.append({
            "run_id": None, "event_type": "POSITION_OPENED", "event_time": et,
            "symbol": sym, "trade_id": tid,
            "payload": {"trade_id": tid, "order_id": ent_oid, "position_id": pid,
                        "price": c.get("entry_price"), "qty": c.get("qty"),
                        "candidate_id": c["candidate_id"]},
        })
        if xt:
            positions_rows.append({
                "run_id": None, "event_type": "POSITION_CLOSED", "event_time": xt,
                "symbol": sym, "trade_id": tid,
                "payload": {"trade_id": tid, "order_id": ext_oid, "position_id": pid,
                            "price": c.get("exit_price"), "qty": c.get("qty"),
                            "reason": c.get("exit_reason")},
            })
        cum = 0.0
        for f in sorted(t["fund"], key=lambda x: x.get("funding_time") or 0):
            cum += float(f.get("amount") or 0)
            funding_rows.append({
                "run_id": None, "event_type": "FUNDING_FEE", "symbol": sym,
                "event_time": f.get("funding_time"),
                "event_id": f"paper_funding:{f.get('id')}",
                "sequence": f.get("id"),
                "payload": {"delta": -float(f.get("amount") or 0),
                            "cum_funding_fee": round(-cum, 10),
                            "rate": f.get("rate"), "mark": f.get("mark"),
                            "position_id": pid},
            })

    # ── 拒单也要进 run 目录（工单：「若被拒绝，记录具体原因」）───────────
    # 不加这一段，同一标的的「1 笔拒单 + 1 笔成交」在账本里有 3 张订单，
    # 而 run 目录只有 2 张 ⇒ `/orders` 与账本对不上（本单实测 3 个标的中招）。
    rejects_rows, rej_orders = [], []
    for c in rej_cases:
        rej = [o for o in led["paper_order"] if o.get("event_uid") == c["candidate_id"]]
        for o in rej:
            rej_orders.append({
                "run_id": None, "order_id": o["order_id"], "trade_id": None,
                "signal_id": c["candidate_id"], "symbol": sym,
                "side": o.get("side"), "is_entry": True, "phase": "entry",
                "ts_ms": o.get("ts"), "ts": o.get("ts"),
                "action": o.get("status"), "status": o.get("status"),
                "reason": o.get("reject_reason"),
                "price": o.get("price"), "raw_price": o.get("price"),
                "qty": o.get("qty"), "fee": o.get("fee"), "notional": o.get("notional"),
                "bar_open_time": o.get("bar_open_time"),
                "stoploss_s": o.get("stoploss_s"), "trail_d": o.get("trail_d"),
                "stop_price": o.get("initial_stop_price"),
                "config_fingerprint": o.get("config_fingerprint"),
                "model_version": o.get("model_version"),
                "event_uid": o.get("event_uid"),
                "exchange_order_id": None,
            })
        rejects_rows.append({
            "run_id": None, "ts_ms": (rej[0].get("ts") if rej else None),
            "symbol": sym, "phase": "entry",
            "reason": (rej[0].get("reject_reason") if rej else c.get("open_reason")),
            "event_uid": c["candidate_id"],
            "candidate_id": c["candidate_id"],
            "candidate_snapshot_utc": c.get("snapshot_time_utc"),
            "note": "隔离回放下的真实拒单，原因原样来自 paper_blocked/paper_order.reject_reason",
        })

    # ── run_id：标的 + 本 run 最后时刻的 epoch 秒（与既有命名一致）───────
    last_ms = max((t["exit_time"] for t in trades if t.get("exit_time")), default=0) or \
        max((t["entry_time"] for t in trades if t.get("entry_time")), default=0) or \
        max((r.get("ts_ms") for r in rejects_rows if r.get("ts_ms")), default=0)
    if not last_ms:
        return {}
    anchor_ms = (min([t.get("entry_time") for t in trades if t.get("entry_time")])
                 if any(t.get("entry_time") for t in trades)
                 else min([r.get("ts_ms") for r in rejects_rows if r.get("ts_ms")]))
    run_id = f"run_WX_EVENT_TREND_V0_1_{sym}_{last_ms // 1000}"
    d = RUNS_DIR / run_id
    d.mkdir(parents=True, exist_ok=True)

    for row in (trades + orders_rows + rej_orders + fills_rows + positions_rows
                + funding_rows + rejects_rows):
        row["run_id"] = run_id

    # ── candidates.jsonl（真实候选条件）────────────────────────────────
    cand_rows = []
    for t in trades_src:
        c = cand_by_id.get(t["case"]["candidate_id"])
        if c:
            cand_rows.append(dict(c, run_id=run_id))
    for c in rej_cases:                       # 被拒候选的信号条件也要能查回来
        cc = cand_by_id.get(c["candidate_id"])
        if cc and not any(x["candidate_id"] == cc["candidate_id"] for x in cand_rows):
            cand_rows.append(dict(cc, run_id=run_id, rejected=True))

    # ── equity_curve.jsonl ──────────────────────────────────────────────
    # **不用**账本 `paper_equity` 快照：那是**账户级**序列（96 条，覆盖全部 48 笔），
    # 塞进每个单笔 run 会变成「一笔交易却画出全账户 96 点曲线」——比空着更误导。
    # 这里按本 run 自己的已实现净额累计推导，并在 `summary.equity_curve_source` 里
    # 写清是推导值、不是逐 bar 权益。
    try:
        base = float(getattr(cm.config(), "initial_equity", 2000.0) or 2000.0)
    except Exception:                                       # noqa: BLE001
        base = 2000.0
    equity, cum = [], 0.0
    equity.append({"run_id": run_id, "t": anchor_ms,
                   "cash": round(base, 10), "unrealized": 0.0, "equity": round(base, 10),
                   "total_equity": round(base, 10), "open_positions_count": 0})
    for t in sorted([x for x in trades if x.get("exit_time")], key=lambda x: x["exit_time"]):
        cum += float(t.get("net_pnl") or 0)
        equity.append({"run_id": run_id, "t": t["exit_time"],
                       "cash": round(base + cum, 10), "unrealized": 0.0,
                       "equity": round(base + cum, 10),
                       "total_equity": round(base + cum, 10),
                       "open_positions_count": 0})
    for e in equity:
        e["ts_ms"] = e["t"]
        e["timestamp"] = e["t"]
    eq_src = ("derived_from_paper_realized_cumsum（本 run 自身已实现净额逐笔累计；"
              f"起点 = initial_equity {base:g}。**不是**逐 bar 权益，"
              "未含持仓期间浮盈亏）")
    account_equity_rows = len([e for e in led["paper_equity"] if not e.get("voided")])

    # ── 窗口与摘要 ──────────────────────────────────────────────────────
    sig_ms = [t["signal_time_ms"] for t in trades if t.get("signal_time_ms")]
    win_start = min([x for x in (sig_ms + [t["entry_time"] for t in trades]) if x]
                    or [anchor_ms])
    win_end = max([x for x in ([t["exit_time"] for t in trades]
                              + [t["entry_time"] for t in trades]) if x]
                  or [last_ms])
    # 图要看到「进场前形态 + 出场后确认」，两侧各留 3 天
    PAD = 3 * 86_400_000
    net = round(sum(float(t.get("net_pnl") or 0) for t in trades), 10)
    gross = round(sum(float(t.get("gross_pnl") or 0) for t in trades), 10)
    fees = round(sum(float(t.get("fees") or 0) for t in trades), 10)
    funding = round(sum(float(t.get("funding_fee") or 0) for t in trades), 10)

    summary = {
        "run_id": run_id, "symbol": sym,
        "strategy_id": "WX_EVENT_TREND_V0_1",
        "origin": "isolated_replay",
        "research_period": True,
        "n_trades": len(trades), "n_closed": len([t for t in trades if t.get("exit_time")]),
        "net_pnl": net, "gross_pnl": gross, "fees": fees, "funding_fee": funding,
        "win_rate_pct": (round(100.0 * sum(1 for t in trades
                                          if float(t.get("net_pnl") or 0) > 0)
                               / len(trades), 4) if trades else None),
        "equity_curve_source": eq_src,
        "account_equity_snapshot_rows": account_equity_rows,
        "note": ("本 run 由 SIGNAL-ORDER-TRACE-04 从隔离模拟账本投影而来，"
                 "**不是自然运行**；不得当作实盘或自然成交。"),
    }
    inputs = {
        "run_id": run_id, "symbol": sym, "symbols": [sym],
        "origin": "isolated_replay",
        "data_summary": {
            "time_range_1h": {"start_ms": win_start - PAD, "end_ms": win_end + PAD},
            "time_range_15m": {"start_ms": win_start - PAD, "end_ms": win_end + PAD},
            "market_db": str(cm.ISO_MARKET_DB),
            "market_db_role": "隔离行情库（本单自建，60 标的，2026-06-01~2026-10-25）",
        },
        "window": {"win_start_ms": win_start - PAD, "win_end_ms": win_end + PAD},
        "spec": {"strategy_id": "WX_EVENT_TREND_V0_1", "symbols": [sym],
                 "exec_timeframe": "15m", "signal_timeframe": "1h"},
    }

    n = {}
    n["trades"] = _write_jsonl(d / "trades.jsonl", trades)
    n["orders"] = _write_jsonl(d / "orders.jsonl", orders_rows + rej_orders)
    n["fills"] = _write_jsonl(d / "fills.jsonl", fills_rows)
    n["positions"] = _write_jsonl(d / "positions.jsonl", positions_rows)
    n["funding"] = _write_jsonl(d / "funding.jsonl", funding_rows)
    n["candidates"] = _write_jsonl(d / "candidates.jsonl", cand_rows)
    n["equity_curve"] = _write_jsonl(d / "equity_curve.jsonl", equity)
    n["rejects"] = _write_jsonl(d / "rejects.jsonl", rejects_rows)
    for name, obj in (("summary.json", summary), ("inputs.json", inputs)):
        (d / name).write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")

    return {"run_id": run_id, "symbol": sym, "dir": str(d), "files": n,
            "n_trades": len(trades), "n_rejected": len(rejects_rows),
            "net_pnl": net, "equity_curve_source": eq_src,
            "n_exit_4h_path": sum(1 for t in trades
                                  if str(t.get("exit_path", "")).startswith("4h")),
            "win_start_ms": win_start - PAD, "win_end_ms": win_end + PAD}


def main():
    os.environ["EVENT_TREND_RUNS_DIR"] = str(RUNS_DIR)   # 必须在 import 前
    sys.path.insert(0, str(cm.BASE / "框架" / "backend"))
    import backtest_store as S                              # noqa: E402

    d4 = cm.read_json(cm.OUT / f"04_{TAG}.json")
    led = read_ledger(Path(d4["isolated_paths"]["LEDGER_DIR"]))
    scan = cm.read_json(cm.OUT / "02_候选扫描.json")
    cand_by_id = {c["candidate_id"]: c for c in scan.get("candidates", [])}

    cases = d4["cases"]
    # 成交的标的 + 只被拒的标的都要建 run（后者用于「拒单可查」）
    syms = sorted({c["symbol"] for c in cases})
    built, errs = [], []
    for sym in syms:
        try:
            r = build_symbol_run(sym, cases, led, cand_by_id)
            if not r:
                continue
            doc = {
                "run_id": r["run_id"],
                "status": S.STATUS_SUCCEEDED,
                "strategy_id": "WX_EVENT_TREND_V0_1",
                "research_period": True,
                "origin": "isolated_replay",
                "not_natural_signal": True,
                "spec": {
                    "strategy_id": "WX_EVENT_TREND_V0_1",
                    "symbols": [sym],
                    "exec_timeframe": "15m",
                    "signal_timeframe": "1h",
                },
                "window": {"win_start_ms": r["win_start_ms"],
                           "win_end_ms": r["win_end_ms"]},
                "started_at": cm.iso(r["win_start_ms"]),
                "finished_at": cm.iso(r["win_end_ms"]),
                "summary": {"symbol": sym, "n_trades": r["n_trades"],
                            "net_pnl": r["net_pnl"]},
                "registration": {
                    "registered_by": "SIGNAL-ORDER-TRACE-04/06_workbench.py",
                    "note": ("隔离模拟账本回放投影；**非自然运行**。"
                             "trade_id 为 paper_realized.position_id 的显式投影。"),
                },
                "provenance": {
                    "source_ledger": d4["isolated_paths"]["LEDGER_DIR"],
                    "source_pass": TAG,
                    "source_ledger_sha256": cm.sha256_file(
                        Path(d4["isolated_paths"]["LEDGER_DIR"]) / f"paper_{ARM}.sqlite3"),
                    "market_db": d4["isolated_paths"].get("MARKET_DB"),
                    "production_touched": False,
                },
            }
            S.register_external_run(r["run_id"], doc, run_dir_path=Path(r["dir"]))
            built.append(r)
        except Exception as exc:                            # noqa: BLE001
            errs.append({"symbol": sym, "error": f"{type(exc).__name__}: {exc}"})

    report = {
        "generated_at_utc": cm.utc_now(),
        "runs_root": str(RUNS_DIR),
        "runs_root_is_isolated": str(RUNS_DIR).startswith(str(cm.ME)),
        "production_runs_root": str(S.PROJECT_ROOT / "回测日志" / "event_trend_v0_1" / "runs"),
        "n_symbols": len(syms), "n_built": len(built), "n_error": len(errs),
        "errors": errs,
        "runs": built,
        "chart_json_written": False,
        "chart_note": ("不写 chart.json：GET /chart 走 _generate_fallback_chart，"
                       "按本 run 的 summary.symbol + inputs.data_summary 窗口从冷层 parquet "
                       "现读真实 K 线（读接口不落盘）"),
    }
    cm.write_json("06_工作台注册.json", report)
    print(f"注册 {len(built)}/{len(syms)} 个 run 到 {RUNS_DIR}")
    for b in built:
        print(f"  {b['run_id']}  trades={b['n_trades']}  net={b['net_pnl']}")
    if errs:
        print("错误：", json.dumps(errs, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
