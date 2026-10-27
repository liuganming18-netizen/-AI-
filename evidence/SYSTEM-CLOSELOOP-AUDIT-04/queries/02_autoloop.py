# -*- coding: utf-8 -*-
"""02 第一关①：从运行入口启动后，系统是否**自动**完成 挂单→成交→止损上移→退出。

工单原文：「从运行入口启动后，系统自动完成挂单、成交、止损上移和退出；审核脚本不得手工推进交易。」

做法（审核脚本只做三件事，均不属于「推进交易」）：
  1. 造一条**信号到达**（引擎自己的 `open_position` 登记挂单，等价于上游信号入库）；
  2. 造**行情到达**（往合成行情库插入一根已闭合 15m bar / 4h bar）；
  3. 启动**运行入口**（`workers/paper_execution_worker.py`，交付方声明的执行守护），
     然后**只看账本**，不调用 `advance_pending` / `advance_position_trailing` / `check_position_exit_on_bar`。

产物：out/02_自动闭环.json
"""
from __future__ import annotations

import datetime as dt
import json
import os
import pathlib
import signal
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
PY = "/Users/boos/.workbuddy-ai/binaries/python/envs/default/bin/python"
M15 = 900_000

ENV = {
    "PAPER_LEDGER_DIR": str(PAPER),
    "PAPER_LEDGER_ALLOW_INIT": "1",
    "LIVE_DIR": str(ROOT / "live"),
    "LIVE_DB": str(ROOT / "live" / "live_signals.sqlite3"),
    "MARKET_DB": str(MARKET),
    "PAPER_GATE_FILE": str(GATE),
    "PYTHONPATH": f"{BACKEND}:{BASE}",
}

TIMELINE: list[dict] = []


def t() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def note(step: str, detail) -> None:
    TIMELINE.append({"时刻": t(), "步骤": step, "详情": detail})
    print(f"[{t()[11:]}] {step}: {json.dumps(detail, ensure_ascii=False, default=str)[:300]}")


def paper_conn(arm: str = "3a"):
    p = PAPER / f"paper_{arm}.sqlite3"
    if not p.exists():
        cand = sorted(PAPER.glob("*.sqlite3"))
        p = cand[0] if cand else p
    con = sqlite3.connect(p)
    con.row_factory = sqlite3.Row
    return con


def snapshot(arm: str = "3a") -> dict:
    con = paper_conn(arm)
    try:
        def rows(sql, args=()):
            try:
                return [dict(r) for r in con.execute(sql, args).fetchall()]
            except sqlite3.Error as exc:
                return [{"_错误": str(exc)}]
        return {
            "pending": rows("SELECT pending_id,symbol,status,reason,requested_at,first_request_ms,"
                            "earliest_fill_ms,event_time FROM paper_pending ORDER BY pending_id"),
            "orders": rows("SELECT order_id,event_uid,symbol,side,qty,price,status,reject_reason,ts,"
                           "fill_price FROM paper_order ORDER BY order_id"),
            "positions": rows("SELECT position_id,symbol,qty,entry_price,entry_ts,stop_price,status,"
                              "exit_ts,exit_reason,entry_fee FROM paper_position ORDER BY position_id"),
            "fills": rows("SELECT fill_id,position_id,order_id,symbol,action,qty,fill_price,fee,fill_ts "
                          "FROM paper_fill ORDER BY fill_id"),
            "stops": rows("SELECT * FROM paper_stop_history ORDER BY position_id, effective_at"),
            "blocked": rows("SELECT * FROM paper_blocked ORDER BY rowid"),
        }
    finally:
        con.close()


def insert_bar(symbol: str, open_time: int, ohlc, tf: str = "15m") -> int:
    span = {"15m": M15, "1h": 3_600_000, "4h": 14_400_000}[tf]
    close_time = open_time + span - 1
    o, h, l, c = ohlc
    con = sqlite3.connect(MARKET)
    try:
        con.execute("INSERT OR REPLACE INTO candle_versions VALUES"
                    "('binance','um',?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)",
                    (symbol, tf, open_time, close_time, f"{o:.2f}", f"{h:.2f}", f"{l:.2f}", f"{c:.2f}",
                     "1000", "100000", 10, "500", "50000", 1, close_time))
        con.execute("INSERT OR REPLACE INTO candle_heads VALUES('binance','um',?,?,?,?)",
                    (symbol, tf, open_time, 1))
        con.commit()
    finally:
        con.close()
    return close_time


def del_bars_from(symbol: str, open_time: int, tf: str = "15m") -> int:
    con = sqlite3.connect(MARKET)
    try:
        n = con.execute("DELETE FROM candle_versions WHERE symbol=? AND timeframe=? AND open_time>=?",
                        (symbol, tf, open_time)).rowcount
        con.execute("DELETE FROM candle_heads WHERE symbol=? AND timeframe=? AND open_time>=?",
                    (symbol, tf, open_time))
        con.commit()
    finally:
        con.close()
    return n


def start_worker(tag: str, interval: float = 1.0) -> tuple[subprocess.Popen, pathlib.Path]:
    log = OUT / f"02_worker_{tag}.log"
    env = dict(os.environ)
    env.update(ENV)
    f = open(log, "w", encoding="utf-8")
    p = subprocess.Popen([PY, str(BACKEND / "workers/paper_execution_worker.py"),
                          "--arm", "3a", "--interval", str(interval)],
                         cwd=str(BACKEND), env=env, stdout=f, stderr=subprocess.STDOUT,
                         start_new_session=True)
    return p, log


def main() -> None:
    envj = json.loads((OUT / "01_隔离环境.json").read_text(encoding="utf-8"))
    base = envj["合成行情"]["base"]
    sym = envj["合成行情"]["主标的"]
    T_sig = base - 3 * M15

    for k, v in ENV.items():
        os.environ[k] = v
    if str(BACKEND) not in sys.path:
        sys.path.insert(0, str(BACKEND))
    import paper_engine as PE

    res: dict = {"生成时刻": t(), "入口": "workers/paper_execution_worker.py --arm 3a --interval 1",
                 "隔离根": str(ROOT), "base": base, "T_sig": T_sig}

    # ── 步骤 0：把「成交时刻之后才该到达」的 bar 先撤掉，制造真实挂单窗口 ──
    n_del = del_bars_from(sym, base - 2 * M15)
    note("撤掉尚未到达的 15m bar（制造挂单窗口）", {"删除根数": n_del, "自": base - 2 * M15})

    # ── 步骤 1：信号到达 → 引擎登记挂单（不是推进） ──
    r = PE.open_position("3a", sym, signal_uid="sig_auto04_01", direction="long", atr=1.0,
                         now_ms=T_sig, event_time=T_sig, available_at=T_sig,
                         requested_at=T_sig, origin="replay")
    note("信号到达（引擎 open_position，origin=replay 指定信号时钟）",
         {"ok": r.get("ok"), "reason": r.get("reason"), "pending_id": r.get("pending_id"),
          "earliest_fill_ms": r.get("earliest_fill_ms")})
    res["步骤1_信号登记"] = {"ok": r.get("ok"), "reason": r.get("reason"),
                        "pending_id": r.get("pending_id")}
    snap0 = snapshot()
    res["账本_启动前"] = snap0

    # ── 步骤 2：启动运行入口 ──
    w1, log1 = start_worker("w1", 1.0)
    note("启动执行守护（运行入口）", {"pid": w1.pid, "日志": str(log1)})
    time.sleep(5)
    snap1 = snapshot()
    note("守护跑 5 秒后账本", {"pending状态": [p.get("status") for p in snap1["pending"]],
                        "持仓数": len(snap1["positions"])})
    res["账本_守护首轮后"] = snap1

    # ── 步骤 3：行情到达（插入成交 bar） ──
    ct = insert_bar(sym, base - 2 * M15, (100.0, 100.5, 99.5, 100.0))
    note("行情到达：插入成交 bar", {"open_time": base - 2 * M15, "close_time": ct})
    time.sleep(8)
    snap2 = snapshot()
    note("行情到达 8 秒后账本", {
        "pending": [(p.get("status"), p.get("reason")) for p in snap2["pending"]],
        "订单": [(o.get("side"), o.get("status"), o.get("reject_reason")) for o in snap2["orders"]],
        "持仓数": len(snap2["positions"]),
        "成交数": len(snap2["fills"]),
        "止损轨迹数": len(snap2["stops"]),
    })
    res["账本_行情到达后"] = snap2

    filled = [p for p in snap2["positions"]]
    res["自动成交"] = bool(filled)
    res["未成交原因"] = None if filled else {
        "pending_status": [(p.get("status"), p.get("reason")) for p in snap2["pending"]],
        "order_reject": [(o.get("status"), o.get("reject_reason")) for o in snap2["orders"]],
        "blocked": snap2["blocked"][-3:],
    }

    # ── 步骤 4：若已成交，继续验证「止损自动上移」与「自动退出」 ──
    steps = {}
    if filled:
        pos_id = filled[0]["position_id"]
        # 4h 跟踪：插入一根更晚的已闭合 4h bar，low 高于当前止损
        h4 = (base // 14_400_000) * 14_400_000
        insert_bar(sym, h4 - 14_400_000, (100.0, 103.0, 97.0, 102.0), tf="4h")
        time.sleep(6)
        snap3 = snapshot()
        note("插入 4h bar 后账本", {"止损价": [p.get("stop_price") for p in snap3["positions"]],
                              "止损轨迹数": len(snap3["stops"])})
        steps["止损上移"] = {"止损价": [p.get("stop_price") for p in snap3["positions"]],
                        "轨迹": snap3["stops"]}
        # 15m 明细退出：插入一根 low 远低于止损的已闭合 bar
        insert_bar(sym, base - M15, (100.0, 100.5, 60.0, 62.0))
        time.sleep(6)
        snap4 = snapshot()
        note("插入跌破 bar 后账本", {
            "持仓状态": [(p.get("position_id"), p.get("status"), p.get("exit_reason")) for p in snap4["positions"]],
            "成交数": len(snap4["fills"]), "止损轨迹数": len(snap4["stops"])})
        steps["自动退出"] = {"持仓": snap4["positions"], "成交": snap4["fills"], "轨迹": snap4["stops"]}
        res["账本_退出后"] = snap4
    else:
        steps["止损上移"] = "未执行：无持仓（成交环节已断）"
        steps["自动退出"] = "未执行：无持仓（成交环节已断）"
    res["步骤4"] = steps

    # ── 步骤 5：单实例锁（同时起第二个守护） ──
    w2, log2 = start_worker("w2", 1.0)
    time.sleep(4)
    alive2 = w2.poll() is None
    log2_txt = log2.read_text(encoding="utf-8", errors="ignore") if log2.exists() else ""
    note("并发起第二个守护", {"仍在运行": alive2, "退出码": w2.poll(),
                        "日志尾部": log2_txt.strip().splitlines()[-3:] if log2_txt else []})
    res["单实例锁"] = {"第二实例仍在运行": alive2, "退出码": w2.poll(),
                    "日志尾部": log2_txt.strip().splitlines()[-5:]}
    if alive2:
        w2.terminate()
        try:
            w2.wait(timeout=5)
        except Exception:                                        # noqa: BLE001
            w2.kill()

    # ── 步骤 6：重启恢复（杀掉再起，看是否重复成交/重复止损） ──
    before_restart = snapshot()
    w1.terminate()
    try:
        w1.wait(timeout=5)
    except Exception:                                            # noqa: BLE001
        w1.kill()
    time.sleep(1)
    w3, log3 = start_worker("w3", 1.0)
    time.sleep(6)
    after_restart = snapshot()
    dup = {
        "成交数_重启前": len(before_restart["fills"]), "成交数_重启后": len(after_restart["fills"]),
        "止损轨迹_重启前": len(before_restart["stops"]), "止损轨迹_重启后": len(after_restart["stops"]),
        "持仓数_重启前": len(before_restart["positions"]), "持仓数_重启后": len(after_restart["positions"]),
    }
    note("重启恢复", dup)
    res["重启恢复"] = dup
    res["重启后无重复"] = (dup["成交数_重启前"] == dup["成交数_重启后"]
                       and dup["止损轨迹_重启前"] == dup["止损轨迹_重启后"]
                       and dup["持仓数_重启前"] == dup["持仓数_重启后"])
    w3.terminate()
    try:
        w3.wait(timeout=5)
    except Exception:                                            # noqa: BLE001
        w3.kill()

    # ── 守护日志（状态暴露能力） ──
    res["守护日志_w1尾部"] = (log1.read_text(encoding="utf-8", errors="ignore").strip().splitlines()[-8:]
                           if log1.exists() else [])
    res["时间线"] = TIMELINE

    # ── 判定 ──
    res["判定"] = {
        "①挂单": "通过" if snap1["pending"] and any(p.get("status") == "pending" for p in snap1["pending"]) else "不通过",
        "②成交": "通过" if res["自动成交"] else "不通过",
        "③止损自动上移": ("通过" if filled and len([s for s in steps["止损上移"].get("轨迹", [])
                                              if s.get("reason") == "ft_low4h_trail"]) > 0
                    else ("未执行" if not filled else "不通过")),
        "④自动退出": ("通过" if filled and any(p.get("status") == "closed" for p in
                                          [x for x in steps["自动退出"].get("持仓", [])])
                   else ("未执行" if not filled else "不通过")),
        "⑤单实例锁": "通过" if not alive2 else "不通过",
        "⑥重启恢复": "通过" if res["重启后无重复"] else "不通过",
    }
    (OUT / "02_自动闭环.json").write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str),
                                     encoding="utf-8")
    print("\n=== 判定 ===")
    for k, v in res["判定"].items():
        print(f"  {k}: {v}")
    print(f"未成交原因: {json.dumps(res['未成交原因'], ensure_ascii=False, default=str)[:600]}")


if __name__ == "__main__":
    main()
