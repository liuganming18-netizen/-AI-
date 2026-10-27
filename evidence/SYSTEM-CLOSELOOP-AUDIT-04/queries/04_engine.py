# -*- coding: utf-8 -*-
"""04 第一关③④⑤：参数、周期分离、账本闭合 / 幂等 / 重启。

③ 初始止损与跟踪距离是否来自**明确配置**，两组参数是否产生**预期区别**，有无静默兜底；
④ 4h 跟踪更新与 15m 明细周期退出检查是否**分开**，止损生效是否**不追溯**；
⑤ 退出订单/成交/持仓/费用/权益是否**闭合**，幂等与重启恢复是否成立。

路径声明：本脚本用**引擎函数直调**（不是自动守护），因为 02 已证明自动链路在成交环节就断了；
直调只用于检验引擎本身的规则与记账，**不作为「自动闭环通过」的证据**。

产物：out/04_参数与闭合.json
"""
from __future__ import annotations

import json
import os
import pathlib
import sqlite3
import subprocess
import sys
import time

BASE = pathlib.Path("/Users/boos/Desktop/威科夫因子共振趋势")
EVID = BASE / "evidence/SYSTEM-CLOSELOOP-AUDIT-04"
OUT = EVID / "out"
BACKEND = BASE / "框架/backend"
ROOT = pathlib.Path("/tmp/wx_audit04")
PAPER = ROOT / "paper"
MARKET = ROOT / "market.sqlite3"
GATE = ROOT / "gate.json"
ARM = "3a"
SYM = "BTCUSDT"
M15 = 900_000
H4 = 14_400_000
PY = "/Users/boos/.workbuddy-ai/binaries/python/envs/default/bin/python"

ENV = {
    "PAPER_LEDGER_DIR": str(PAPER), "PAPER_LEDGER_ALLOW_INIT": "1",
    "LIVE_DIR": str(ROOT / "live"), "LIVE_DB": str(ROOT / "live" / "live_signals.sqlite3"),
    "MARKET_DB": str(MARKET), "PAPER_GATE_FILE": str(GATE),
    "PYTHONPATH": f"{BACKEND}:{BASE}",
}


def ledger():
    con = sqlite3.connect(PAPER / f"paper_{ARM}.sqlite3")
    con.row_factory = sqlite3.Row
    return con


def wipe():
    con = ledger()
    try:
        for t in ("paper_position", "paper_stop_history", "paper_order", "paper_fill",
                  "paper_pending", "paper_processed", "paper_blocked"):
            con.execute(f"DELETE FROM {t}")
        con.execute("UPDATE paper_state SET cash=5000.0, realized_pnl=0.0, fees_paid=0.0, funding_paid=0.0")
        con.commit()
    finally:
        con.close()


def fixture(entry=100.0, stop=85.0, qty=10.0, fee=0.05, entry_ts=None) -> int:
    con = ledger()
    try:
        now = int(time.time() * 1000)
        ets = entry_ts or (now - 6 * H4)
        con.execute(
            "INSERT INTO paper_position(account_id,symbol,qty,entry_price,entry_ts,status,"
            "signal_uid,arm,stop_price,atr_at_entry,entry_fee,model_version,origin,epoch_ms) "
            "VALUES(?,?,?,?,?,'open',?,?,?,?,?,'fixture','fixture',?)",
            (f"paper43_{ARM}", SYM, qty, entry, ets, "fx04", ARM, stop, 1.0, fee, 1791939600000))
        pid = con.execute("SELECT last_insert_rowid()").fetchone()[0]
        con.execute("INSERT INTO paper_stop_history(account_id,position_id,old_stop,new_stop,reason,"
                    "ref_bar_open_ms,available_at,decision_at,effective_at,created_at) "
                    "VALUES(?,?,NULL,?,'initial_stop',?,?,?,?,?)",
                    (f"paper43_{ARM}", pid, stop, ets, ets, ets, ets, now))
        # 夹具必须把开仓手续费真的从现金扣掉，否则「闭合残差」会等于 entry_fee（假失败）
        con.execute("UPDATE paper_state SET cash=cash-? WHERE account_id=?", (fee, f"paper43_{ARM}"))
        con.commit()
        return int(pid)
    finally:
        con.close()


def pos(pid: int) -> dict:
    con = ledger()
    try:
        r = con.execute("SELECT * FROM paper_position WHERE position_id=?", (pid,)).fetchone()
        return dict(r) if r else {}
    finally:
        con.close()


def hist(pid: int) -> list[dict]:
    con = ledger()
    try:
        return [dict(r) for r in con.execute(
            "SELECT * FROM paper_stop_history WHERE position_id=? ORDER BY id", (pid,))]
    finally:
        con.close()


def rows(sql: str, args=()) -> list[dict]:
    con = ledger()
    try:
        return [dict(r) for r in con.execute(sql, args)]
    finally:
        con.close()


def bar4h(low: float, open_time: int, symbol: str = SYM) -> dict:
    return {"symbol": symbol, "open_time": open_time, "close_time": open_time + H4 - 1,
            "open": 100.0, "high": 110.0, "low": low, "close": 105.0,
            "available_at": open_time + H4 - 1}


def bar15m(low: float, open_time: int, symbol: str = SYM) -> dict:
    return {"symbol": symbol, "open_time": open_time, "close_time": open_time + M15 - 1,
            "open": 100.0, "high": 105.0, "low": low, "close": 102.0,
            "available_at": open_time + M15 - 1}


def main() -> None:
    for k, v in ENV.items():
        os.environ[k] = v
    if str(BACKEND) not in sys.path:
        sys.path.insert(0, str(BACKEND))
    import paper_engine as PE

    wipe()
    now = int(time.time() * 1000)
    res: dict = {"生成时刻": time.strftime("%Y-%m-%dT%H:%M:%S%z")}

    # ── ③ 参数来源 ──────────────────────────────────────────────────────
    p = PE.approved_params()
    res["③_配置"] = {
        "approved_params._source": p.get("_source"),
        "含 stoploss_s": "stoploss_s" in p,
        "含 trail_d": "trail_d" in p,
        "uniform_stop_atr_mult": p.get("uniform_stop_atr_mult"),
        "键数": len([k for k in p if not k.startswith("_")]),
        "生效视图文件": str(PE.EFFECTIVE_VIEW),
        "生效视图存在": PE.EFFECTIVE_VIEW.exists(),
    }

    # ③-a 两组 trail_d 的预期区别（同一持仓、同一 4h bar）
    pid = fixture()
    b = bar4h(97.0, now - 2 * H4)
    r05 = PE.advance_position_trailing(ARM, pid, b, now_ms=now, decision_at=now, trail_d=0.05)
    h_after_05 = hist(pid)
    con = ledger()
    con.execute("UPDATE paper_position SET stop_price=85.0 WHERE position_id=?", (pid,))
    con.execute("DELETE FROM paper_stop_history WHERE position_id=? AND reason='ft_low4h_trail'", (pid,))
    con.commit()
    con.close()
    r10 = PE.advance_position_trailing(ARM, pid, b, now_ms=now, decision_at=now, trail_d=0.10)
    res["③_两组参数"] = {
        "d=0.05": {"candidate": r05.get("candidate_stop"), "effective": r05.get("effective_stop"),
                   "stop_updated": r05.get("stop_updated")},
        "d=0.10": {"candidate": r10.get("candidate_stop"), "effective": r10.get("effective_stop"),
                   "stop_updated": r10.get("stop_updated")},
        "区别成立": (r05.get("candidate_stop") != r10.get("candidate_stop")
                 and r05.get("stop_updated") and r10.get("stop_updated")),
        "轨迹条数_0.05": len([x for x in h_after_05 if x["reason"] == "ft_low4h_trail"]),
    }

    # ③-b 缺 trail_d 必须显式失败
    r_none = PE.advance_position_trailing(ARM, pid, b, now_ms=now, decision_at=now)
    res["③_缺参数"] = {"ok": r_none.get("ok"), "reason": r_none.get("reason"),
                     "止损是否被动": pos(pid).get("stop_price")}

    # ③-c 初始止损：open_position 在缺 stoploss_s 时的行为（干净账本上复现 02 的成交拒绝）
    wipe()
    # 02 号探针删过两根 bar 来制造挂单窗口，这里补回来，保证 ③-c 有可成交 bar
    envj = json.loads((OUT / "01_隔离环境.json").read_text(encoding="utf-8"))
    b0 = envj["合成行情"]["base"]
    con_mkt = sqlite3.connect(MARKET)
    for off in (2, 1):
        ot = b0 - off * M15
        con_mkt.execute("INSERT OR REPLACE INTO candle_versions VALUES"
                        "('binance','um',?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)",
                        (SYM, "15m", ot, ot + M15 - 1, "100.00", "100.50", "99.50", "100.00",
                         "1000", "100000", 10, "500", "50000", 1, ot + M15 - 1))
        con_mkt.execute("INSERT OR REPLACE INTO candle_heads VALUES('binance','um',?,?,?,?)",
                        (SYM, "15m", ot, 1))
    con_mkt.commit()
    con_mkt.close()
    # 尝试时钟用**真实现在**（否则会先被 bar_after_attempt_clock_no_lookahead 拦下），
    # 信号时钟回填 4 根 bar ⇒ 成交 bar 22:45 已存在、滞后 < 60min，才能走到「初始止损参数」那一步。
    t_sig = now - 4 * M15
    _c = ledger()
    _c.execute("DELETE FROM paper_pending")
    _c.execute("DELETE FROM paper_request_anchor")
    _c.commit()
    _c.close()
    r_open = PE.open_position(ARM, SYM, signal_uid="fx04_open", direction="long", atr=1.0,
                              now_ms=now, event_time=t_sig, available_at=t_sig,
                              requested_at=t_sig, origin="replay")
    print("[③-c] r_open =", json.dumps({k: r_open.get(k) for k in
                                       ("ok", "reason", "earliest_fill_ms", "pending_id")},
                                      ensure_ascii=False))
    con_mkt = sqlite3.connect(MARKET)
    _b = con_mkt.execute("SELECT open_time,close_time FROM candles WHERE symbol=? AND timeframe='15m' "
                         "AND open_time>=? ORDER BY open_time LIMIT 1",
                         (SYM, t_sig + M15)).fetchone()
    con_mkt.close()
    import live_store as LS
    _ro = LS.market_conn()
    _ro_bar = PE.bar_at_or_after(_ro, SYM, t_sig + M15)
    _ro.close()
    res["③_初始止损"] = {"ok": r_open.get("ok"), "reason": r_open.get("reason"),
                     "t_sig": t_sig, "earliest": t_sig + M15,
                     "可成交bar": list(_b) if _b else None,
                     "只读连接取bar": dict(_ro_bar) if _ro_bar else None,
                     "行情库": str(LS.MARKET_DB)}

    # ── ④ 周期分离 + 不追溯 ─────────────────────────────────────────────
    wipe()
    pid = fixture(entry=100.0, stop=85.0, qty=10.0, fee=0.05, entry_ts=now - 6 * H4)
    # 4h bar 是 3 天前闭合的（老 bar），low=90 ⇒ 候选 85.5（>85，会上移）
    old4h = bar4h(90.0, now - 3 * 86400_000)
    r_old = PE.advance_position_trailing(ARM, pid, old4h, now_ms=now, decision_at=now, trail_d=0.05)
    h = [x for x in hist(pid) if x["reason"] == "ft_low4h_trail"]
    res["④_不追溯"] = {
        "老4h_bar_close_time": old4h["close_time"], "决策时刻": now,
        "candidate": r_old.get("candidate_stop"), "effective_stop": r_old.get("effective_stop"),
        "stop_updated": r_old.get("stop_updated"),
        "轨迹": [{"old": x["old_stop"], "new": x["new_stop"], "ref_bar_open_ms": x["ref_bar_open_ms"],
                "decision_at": x["decision_at"], "effective_at": x["effective_at"]} for x in h],
        "effective_at_是否等于决策时刻": bool(h) and all(int(x["effective_at"]) >= now - 5 for x in h),
        "effective_at_是否被写成老bar收盘": bool(h) and any(int(x["effective_at"]) == int(old4h["close_time"]) for x in h),
    }

    # ④-b 分离：4h bar 的 low 高于当前止损（4h 不触发退出），15m bar 的 low 低于止损（15m 触发）
    cur_stop = float(pos(pid)["stop_price"])
    b4_no_exit = bar4h(cur_stop + 5.0, now - H4)      # 4h low 高于止损 ⇒ 4h 不退出
    r_no_exit = PE.advance_position_trailing(ARM, pid, b4_no_exit, now_ms=now, decision_at=now, trail_d=0.05)
    res["④_分离_4h不退出"] = {"cur_stop": cur_stop, "4h_low": b4_no_exit["low"],
                          "exit_triggered": r_no_exit.get("exit_triggered"),
                          "status": pos(pid).get("status")}
    b15 = bar15m(cur_stop - 5.0, now - M15)
    r_exit = PE.check_position_exit_on_bar(ARM, pid, b15, now_ms=now, decision_at=now)
    res["④_分离_15m退出"] = {"15m_low": b15["low"], "exit_triggered": r_exit.get("exit_triggered"),
                         "status": pos(pid).get("status"),
                         "exit_reason": pos(pid).get("exit_reason")}

    # ④-c 老 15m bar 能否追溯触发退出（潜在缺陷，自动路径只喂最新已闭合 bar）
    wipe()
    pid2 = fixture(entry=100.0, stop=85.0, qty=10.0, fee=0.05, entry_ts=now - 6 * H4)
    old15 = bar15m(50.0, now - 2 * 3600_000)
    r_old15 = PE.check_position_exit_on_bar(ARM, pid2, old15, now_ms=now, decision_at=now)
    res["④_老15m能否追溯"] = {
        "老bar_close_time": old15["close_time"], "决策时刻": now,
        "exit_triggered": r_old15.get("exit_triggered"),
        "status": pos(pid2).get("status"),
        "说明": "自动路径只会喂最新已闭合 15m，故当前不可由外部触发；属代码层缺守卫",
    }

    # ── ⑤ 闭合 / 幂等 / 重启 ────────────────────────────────────────────
    wipe()
    pid3 = fixture(entry=100.0, stop=85.0, qty=10.0, fee=0.05, entry_ts=now - 6 * H4)
    b_exit = bar15m(80.0, now - M15)
    r1 = PE.check_position_exit_on_bar(ARM, pid3, b_exit, now_ms=now, decision_at=now)
    time.sleep(0.05)
    r2 = PE.check_position_exit_on_bar(ARM, pid3, b_exit, now_ms=now + 1000, decision_at=now + 1000)
    p3 = pos(pid3)
    orders = rows("SELECT order_id,side,qty,price,status,fill_price,notional FROM paper_order WHERE event_uid IN "
                  "(SELECT signal_uid FROM paper_position WHERE position_id=?) OR order_id IN "
                  "(SELECT order_id FROM paper_fill WHERE position_id=?)", (pid3, pid3))
    fills = rows("SELECT fill_id,action,qty,raw_price,fill_price,notional,fee,slippage_cost,bar_open_time,"
                 "bar_close_time,fill_ts FROM paper_fill WHERE position_id=?", (pid3,))
    st = rows("SELECT * FROM paper_state WHERE account_id=?", (f"paper43_{ARM}",))
    cash = float(st[0]["cash"]) if st else None
    entry_fee = float(p3.get("entry_fee") or 0)
    exit_fee = float(p3.get("exit_fee") or 0)
    funding = float(p3.get("funding_paid") or 0)
    exit_price = float(p3.get("exit_price") or 0)
    qty = float(p3.get("qty") or 0)
    entry_price = float(p3.get("entry_price") or 0)
    expect_cash = 5000.0 + (exit_price - entry_price) * qty - entry_fee - exit_fee - funding
    res["⑤_闭合"] = {
        "第一次退出": {"exit_triggered": r1.get("exit_triggered"), "reason": r1.get("exit_result", {}).get("reason")},
        "第二次退出（幂等）": {"exit_triggered": r2.get("exit_triggered"), "ok": r2.get("ok"),
                        "reason": r2.get("reason")},
        "持仓": {"status": p3.get("status"), "exit_reason": p3.get("exit_reason"),
               "exit_price": p3.get("exit_price"), "entry_fee": entry_fee, "exit_fee": exit_fee,
               "funding_paid": funding},
        "订单": orders, "成交": fills,
        "现金": cash, "按逐笔重算应为": expect_cash, "残差": (None if cash is None else cash - expect_cash),
        "退出成交条数": len([f for f in fills if f.get("action") in ("close", "sell")]),
    }
    res["⑤_幂等成立"] = (res["⑤_闭合"]["退出成交条数"] == 1 and r2.get("ok") is False
                     and abs(res["⑤_闭合"]["残差"] or 0) < 1e-6)

    # 重启：新进程重新打开账本，看轨迹与持仓是否还在
    probe = (
        "import sqlite3,json;"
        f"con=sqlite3.connect({str(PAPER / f'paper_{ARM}.sqlite3')!r});"
        "con.row_factory=sqlite3.Row;"
        f"p=[dict(r) for r in con.execute('SELECT position_id,status,exit_reason,exit_price FROM paper_position WHERE position_id={pid3}')];"
        f"h=[dict(r) for r in con.execute('SELECT id,reason,new_stop,effective_at FROM paper_stop_history WHERE position_id={pid3} ORDER BY id')];"
        f"f=len([dict(r) for r in con.execute('SELECT fill_id FROM paper_fill WHERE position_id={pid3}')]);"
        "print(json.dumps({'持仓':p,'轨迹':h,'成交条数':f},ensure_ascii=False))"
    )
    out = subprocess.run([PY, "-c", probe], capture_output=True, text=True, timeout=60)
    try:
        restart = json.loads(out.stdout.strip().splitlines()[-1])
    except Exception:                                        # noqa: BLE001
        restart = {"_raw": out.stdout[-400:], "_err": out.stderr[-400:]}
    res["⑤_重启恢复"] = restart
    res["⑤_重启一致"] = (restart.get("成交条数") == len(fills)
                     and (restart.get("持仓") or [{}])[0].get("status") == p3.get("status"))

    # ── 判定 ──
    res["判定"] = {
        "③参数来自明确配置": ("通过" if (res["③_配置"]["含 stoploss_s"] and res["③_配置"]["含 trail_d"])
                       else "不通过"),
        "③两组参数有预期区别": "通过" if res["③_两组参数"]["区别成立"] else "不通过",
        "③无静默兜底": ("通过" if res["③_缺参数"]["reason"] == "missing_parameter_trail_d" else "不通过"),
        "④止损不追溯": "通过" if (res["④_不追溯"]["stop_updated"]
                            and res["④_不追溯"]["effective_at_是否等于决策时刻"]
                            and not res["④_不追溯"]["effective_at_是否被写成老bar收盘"]) else "不通过",
        "④4h与15m分离": ("通过" if (res["④_分离_4h不退出"]["exit_triggered"] is False
                             and res["④_分离_15m退出"]["exit_triggered"] is True) else "不通过"),
        "⑤账本闭合": ("通过" if (res["⑤_闭合"]["残差"] is not None
                             and abs(res["⑤_闭合"]["残差"]) < 1e-6) else "不通过"),
        "⑤幂等": "通过" if res["⑤_幂等成立"] else "不通过",
        "⑤重启恢复": "通过" if res["⑤_重启一致"] else "不通过",
    }
    (OUT / "04_参数与闭合.json").write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str),
                                     encoding="utf-8")
    print(json.dumps(res["③_配置"], ensure_ascii=False))
    print(json.dumps(res["③_两组参数"], ensure_ascii=False))
    print(json.dumps(res["③_缺参数"], ensure_ascii=False))
    print(json.dumps(res["③_初始止损"], ensure_ascii=False))
    print(json.dumps(res["④_不追溯"], ensure_ascii=False)[:600])
    print(json.dumps(res["④_分离_4h不退出"], ensure_ascii=False))
    print(json.dumps(res["④_分离_15m退出"], ensure_ascii=False))
    print(json.dumps(res["④_老15m能否追溯"], ensure_ascii=False))
    print(json.dumps(res["⑤_闭合"], ensure_ascii=False)[:900])
    print(json.dumps(res["⑤_重启恢复"], ensure_ascii=False)[:300])
    print("\n=== 判定 ===")
    for k, v in res["判定"].items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
