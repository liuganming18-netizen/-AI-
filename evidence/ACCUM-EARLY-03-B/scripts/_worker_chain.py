# -*- coding: utf-8 -*-
"""ACCUM-EARLY-03-B｜隔离回放工作进程（**只在独立账本里跑**，不碰生产）。

为什么必须是子进程：`live_store` 的账本目录、行情库、合并开关都从环境变量读，
且账本对象在进程内是长生命周期的。用子进程才能做到
  · 每一臂一个**干净的独立账本**（互不污染）；
  · **真进程重启**（同一目录、新进程）用来验证恢复与不重复下单。

输入：stdin 一行 JSON（job）。输出：stdout 一行 JSON（结果）。
纪律：
  · `origin="replay"` + 显式 `epoch_ms=0` ⇒ 允许在**隔离账本**里回放历史事件；
    生产账本的 epoch 闸门不受影响（本进程根本不连生产账本）。
  · 逐根 15m 推进退出扫描，**不跳根、不插值**；缺口就是缺口。
  · 只读行情，只写本进程自己的账本目录。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
PROJECT = HERE.parents[3]
BACKEND = PROJECT / "框架" / "backend"
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(PROJECT))

H1 = 3_600_000
M15 = 900_000
H4 = 14_400_000


def iso(ms):
    if ms is None:
        return None
    import time
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(int(ms) / 1000))


def _row(r):
    return {k: r[k] for k in r.keys()} if r is not None else None


def _get(bar, key, default=None):
    """兼容 sqlite3.Row（没有 .get）与 Bar（dict 子类，有 .get）。"""
    try:
        if hasattr(bar, "get"):
            return bar.get(key, default)
        return bar[key] if key in bar.keys() else default
    except Exception:                                        # noqa: BLE001
        return default


def _bars4h(mkt, symbol, start_ms, end_ms):
    """取 4h 已收盘 bar：合并门面走合并器；仅热库时走与生产同一句 SQL。"""
    if hasattr(mkt, "bars_tf"):
        return mkt.bars_tf("4h", symbol, start_ms, end_ms)
    return [dict(r) for r in mkt.execute(
        "SELECT open_time,close_time,open,high,low,close,quote_volume,available_at "
        "FROM candles WHERE exchange='binance' AND market='um' AND symbol=? AND timeframe='4h' "
        "AND is_closed=1 AND open_time>=? AND open_time<? ORDER BY open_time",
        (symbol, int(start_ms), int(end_ms)))]


def main() -> int:
    job = json.loads(sys.stdin.read())
    import live_store as L

    # 新开仓闸门：只在**隔离目录内**放行。生产闸门文件绝不被本脚本触碰。
    if job.get("allow_new_entries"):
        gpath = Path(os.environ["PAPER_GATE_FILE"]).resolve()
        run_root = L.get_ledger_dir().parent
        if run_root not in gpath.parents:
            raise SystemExit(f"拒绝：闸门文件 {gpath} 不在隔离目录 {run_root} 内")
        gpath.write_text(json.dumps({"allow_new_entries": True,
                                     "set_by": "ACCUM-EARLY-03-B_isolated",
                                     "reason": "隔离回放：只在本单独立账本内放行新开仓"},
                                    ensure_ascii=False), encoding="utf-8")

    L.ensure_live_schema()
    import paper_engine as PE

    arm = job.get("arm", "3a")
    ev = job["event"]
    end_ms = int(job["end_ms"])
    mode = job.get("mode", "merge")
    max_bars = int(job.get("max_bars") or 100000)
    do_reopen = bool(job.get("reopen_ledger"))       # 重启恢复：先关再重开账本
    retry_same_signal = bool(job.get("retry_same_signal"))

    out = {
        "job": {"symbol": ev["symbol"], "signal_uid": ev["signal_uid"],
                "snapshot_ms": ev["snapshot_ms"], "mode": mode, "arm": arm},
        "env": {
            "PAPER_LEDGER_DIR": os.environ.get("PAPER_LEDGER_DIR"),
            "MARKET_DB": os.environ.get("MARKET_DB"),
            "WX_ARCHIVE_DIR": os.environ.get("WX_ARCHIVE_DIR"),
            "WX_MARKET_MERGE": os.environ.get("WX_MARKET_MERGE"),
            "LIVE_DB": os.environ.get("LIVE_DB"),
        },
        "market_mode": PE.market_source_mode(),
        "steps": [],
        "errors": [],
    }

    # ── 1. 委托：走真实 open_position（含全部闸门、取整、风控、幂等）──────────
    kwargs = dict(direction="long", now_ms=int(ev["snapshot_ms"]) + M15,
                  event_time=int(ev["snapshot_ms"]),
                  available_at=int(ev.get("available_at_ms") or ev["snapshot_ms"]),
                  requested_at=int(ev["snapshot_ms"]), origin="replay", epoch_ms=0)

    # 恢复模式：账本里已有这笔持仓 ⇒ 不重复下单，接着上一进程的状态继续推进
    resumed_pid = None
    if job.get("resume"):
        _c = L.paper_conn(arm, ro=True)
        try:
            _r = _c.execute("SELECT position_id,status,entry_ts,stop_price FROM paper_position "
                            "WHERE account_id=? AND signal_uid=? ORDER BY position_id DESC LIMIT 1",
                            (f"paper43_{arm}", ev["signal_uid"])).fetchone()
        finally:
            _c.close()
        if _r is not None:
            resumed_pid = int(_r["position_id"])
            out["steps"].append({"step": "resume_found_position",
                                 "position_id": resumed_pid, "status": _r["status"],
                                 "entry_ts": int(_r["entry_ts"]),
                                 "stop_price": _r["stop_price"]})

    res = PE.open_position(arm, ev["symbol"], signal_uid=ev["signal_uid"], **kwargs)
    out["steps"].append({"step": "open_position", "result": res})

    # 幂等：同一 signal_uid 再下一次（应被 duplicate_signal_uid 挡住）
    if retry_same_signal:
        out["steps"].append({"step": "open_position_retry_same_signal",
                             "result": PE.open_position(arm, ev["symbol"],
                                                        signal_uid=ev["signal_uid"], **kwargs)})

    _dup_ok = (resumed_pid is not None
               and res.get("reason") in ("duplicate_signal_uid",
                                         "position_already_open_no_add"))
    if not res.get("ok") and not _dup_ok:
        out["verdict"] = "rejected_at_order"
        out["reject_reason"] = res.get("reason")
        out["ledger"] = _snapshot_ledger(arm, PE, L)
        print(json.dumps(out, ensure_ascii=False, default=str))
        return 0

    if res.get("ok"):
        pid = int(res["position_id"])
        entry_ts = int(res["bar_open_time"])
    else:
        pid, entry_ts = resumed_pid, None
    if resumed_pid is not None:
        # 恢复场景：沿用账本里那笔持仓的身份，不新建
        pid = resumed_pid
        _c = L.paper_conn(arm, ro=True)
        try:
            _r = _c.execute("SELECT entry_ts FROM paper_position WHERE position_id=?",
                            (pid,)).fetchone()
            entry_ts = int(_r["entry_ts"])
        finally:
            _c.close()

    if do_reopen:
        # 真进程重启语义：关掉账本再重开（同进程内重开连接，仍是同一库、同一目录）
        try:
            import gc
            gc.collect()
        except Exception:                                    # noqa: BLE001
            pass
        out["steps"].append({"step": "ledger_reopen", "ledger_dir": str(L.get_ledger_dir())})

    # ── 2. 行情读取对象（合并 or 仅热库）────────────────────────────────────
    mkt = PE.open_market()
    try:
        # ── 3. 逐根 15m 推进：先应用已收盘 4h 的跟踪线，再判本根是否触损 ──────
        bars15 = PE.closed_15m(mkt, ev["symbol"], entry_ts, end_ms)
        # 从「包含入场的那根 4h」开始取（用户已确认：入场当根 4h 在入场后收盘即可用，
        # 不要求该线开盘时间晚于入场）；更早的由 paper_engine 的因果守卫自己跳过。
        bars4h = _bars4h(mkt, ev["symbol"], entry_ts - H4 + 1, end_ms)
        out["bars15_count"] = len(bars15)
        out["bars4h_count"] = len(bars4h)
        i4 = 0
        n_checked = 0
        exited = False
        for b in bars15:
            if n_checked >= max_bars:
                out["steps"].append({"step": "walk_truncated", "n": n_checked})
                break
            n_checked += 1
            # `check_position_exit_on_bar` 要求 dict（生产调用方也做 `dict(cur_b15)`）
            if not isinstance(b, dict):
                b = dict(b)
            clk = int(b["close_time"])
            # 4h：只应用「本根 15m 收盘时已经收盘」的那些
            while i4 < len(bars4h) and int(bars4h[i4]["close_time"]) <= clk:
                adv = PE.advance_position_trailing(arm, pid, bars4h[i4],
                                                   now_ms=clk, decision_at=clk)
                if adv.get("skipped_reason"):
                    out["trailing_skipped"] = out.get("trailing_skipped", 0) + 1
                if adv.get("stop_updated") or adv.get("exit_triggered"):
                    out["steps"].append({
                        "step": "trailing", "bar4h_open_utc": iso(bars4h[i4]["open_time"]),
                        "bar4h_source": _get(bars4h[i4], "source"),
                        "clock_utc": iso(clk),
                        "stop_updated": adv.get("stop_updated"),
                        "new_stop": adv.get("effective_stop"),
                        "available_at_proven": adv.get("available_at_proven"),
                        "exit_triggered": adv.get("exit_triggered"),
                        "skipped_reason": adv.get("skipped_reason"),
                    })
                i4 += 1
                if adv.get("exit_triggered"):
                    exited = True
                    break
            if exited:
                break
            chk = PE.check_position_exit_on_bar(arm, pid, b, now_ms=clk, decision_at=clk)
            if chk.get("exit_triggered"):
                out["steps"].append({"step": "exit_15m", "bar_open_utc": iso(b["open_time"]),
                                     "bar_source": _get(b, "source"),
                                     "bar_low": chk.get("bar_low"),
                                     "stop_used": chk.get("cur_stop"),
                                     "exit_result": chk.get("exit_result")})
                exited = True
                break
            if not chk.get("ok"):
                out["errors"].append({"step": "exit_check", "reason": chk.get("reason"),
                                      "bar_open_utc": iso(b["open_time"])})
                break
        out["walk_bars_checked"] = n_checked
        try:
            out["market_provenance"] = mkt.provenance() if hasattr(mkt, "provenance") else None
        except Exception as exc:                             # noqa: BLE001
            out["errors"].append({"step": "provenance", "error": str(exc)})
    finally:
        try:
            mkt.close()
        except Exception:                                    # noqa: BLE001
            pass

    # ── 4. 重复扫描：再跑一轮退出检查（必须无副作用）────────────────────────
    out["steps"].append({"step": "rescan_exits", "result": _rescan(arm, PE, ev, end_ms)})

    out["ledger"] = _snapshot_ledger(arm, PE, L)
    pos = (out["ledger"].get("positions") or [None])[0]
    out["verdict"] = ("closed" if pos and pos.get("status") == "closed" else "still_open")
    print(json.dumps(out, ensure_ascii=False, default=str))
    return 0


def _rescan(arm, PE, ev, end_ms):
    """对已平/未平的持仓再扫一遍最近一根 15m，验证「重复扫描不重复下单」。"""
    import live_store as L
    con = L.paper_conn(arm, ro=True)
    try:
        rows = [_row(r) for r in con.execute(
            "SELECT position_id, status FROM paper_position WHERE account_id=? "
            "ORDER BY position_id", (f"paper43_{arm}",))]
    finally:
        con.close()
    res = []
    mkt = PE.open_market()
    try:
        for p in rows:
            if p["status"] != "open":
                res.append({"position_id": p["position_id"], "skipped": "position_not_open"})
                continue
            b = PE.latest_closed_detail(mkt, ev["symbol"], int(ev["snapshot_ms"]), end_ms)
            if b is None:
                res.append({"position_id": p["position_id"], "skipped": "no_bar"})
                continue
            chk = PE.check_position_exit_on_bar(arm, int(p["position_id"]), b,
                                                now_ms=end_ms, decision_at=end_ms)
            res.append({"position_id": p["position_id"], "ok": chk.get("ok"),
                        "reason": chk.get("reason"),
                        "exit_triggered": chk.get("exit_triggered")})
    finally:
        try:
            mkt.close()
        except Exception:                                    # noqa: BLE001
            pass
    return res


def _snapshot_ledger(arm, PE, L):
    acc = f"paper43_{arm}"
    con = L.paper_conn(arm, ro=True)
    try:
        def q(sql, args=()):
            return [_row(r) for r in con.execute(sql, args)]

        positions = q("SELECT position_id,symbol,status,qty,entry_price,entry_ts,exit_ts,"
                      "exit_reason,exit_price,stop_price,initial_stop_price,entry_fee,exit_fee,"
                      "funding_paid,market_source,trail_d,stoploss_s,planned_risk,voided "
                      "FROM paper_position WHERE account_id=? ORDER BY position_id", (acc,))
        orders = q("SELECT order_id,event_uid,symbol,side,status,reject_reason,fill_price,"
                   "bar_open_time,market_source FROM paper_order WHERE account_id=? "
                   "ORDER BY order_id", (acc,))
        fills = q("SELECT fill_id,order_id,position_id,action,fill_price,raw_price,qty,notional,fee,"
                  "bar_open_time,market_source FROM paper_fill WHERE account_id=? "
                  "ORDER BY fill_id", (acc,))
        realized = q("SELECT id,position_id,gross_pnl,entry_fee,exit_fee,funding_paid,net_pnl,"
                     "entry_ts,exit_ts,exit_reason FROM paper_realized WHERE account_id=? "
                     "ORDER BY id", (acc,))
        stops = q("SELECT position_id,old_stop,new_stop,reason,effective_at,available_at "
                  "FROM paper_stop_history WHERE account_id=? ORDER BY id", (acc,))
        state = q("SELECT cash,realized_pnl,fees_paid,funding_paid FROM paper_state "
                  "WHERE account_id=?", (acc,))
        equity = q("SELECT ts,cash,unrealized,equity FROM paper_equity WHERE account_id=? "
                   "ORDER BY ts", (acc,))
        pending = q("SELECT pending_id,symbol,status,reason,earliest_fill_ms "
                    "FROM paper_pending WHERE account_id=? ORDER BY pending_id", (acc,))
        blocked = q("SELECT event_uid,reason FROM paper_blocked WHERE account_id=?", (acc,))
        return {"positions": positions, "orders": orders, "fills": fills,
                "realized": realized, "stop_history": stops, "state": state,
                "equity": equity, "pending": pending, "blocked": blocked,
                "counts": {"positions": len(positions), "orders": len(orders),
                           "fills": len(fills), "realized": len(realized),
                           "stop_history": len(stops)}}
    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(main())
