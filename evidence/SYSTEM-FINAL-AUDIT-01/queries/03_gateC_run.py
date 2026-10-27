# -*- coding: utf-8 -*-
"""03 C 关（自动运行关）单配置执行器 —— 由 03_gateC.py 以子进程方式调用。

一个进程只跑一套配置（`PAPER_LEDGER_DIR` 是模块级常量，切换必须换进程），
避免两组配置在同一账本里互相污染。

审核脚本只做三件事（都不属于「推进交易」）：
  ① 用引擎自己的 `open_position` 登记一条信号（回放时钟，只登记挂单）；
  ② 往隔离合成行情库插入**已闭合** bar（= 行情到达）；
  ③ 起停 `workers/paper_execution_worker.py`。
**不调用** advance_pending / advance_position_trailing / check_position_exit_on_bar，**不写**账本。

产物：out/03_<tag>.json
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import sqlite3
import subprocess
import sys
import time

BASE = pathlib.Path("/Users/boos/Desktop/威科夫因子共振趋势")
BACKEND = BASE / "框架/backend"
HERE = BASE / "evidence/SYSTEM-FINAL-AUDIT-01"
OUT = HERE / "out"
ROOT = pathlib.Path("/tmp/wx_final01")
MARKET = ROOT / "market.sqlite3"
GATE = ROOT / "gate.json"
PY = "/Users/boos/.workbuddy-ai/binaries/python/envs/default/bin/python"
M15 = 900_000
H4 = 14_400_000

TIMELINE: list[dict] = []


def t() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def note(step: str, detail) -> None:
    TIMELINE.append({"时刻": t(), "步骤": step, "详情": detail})
    print(f"[{t()[11:]}] {step}: {json.dumps(detail, ensure_ascii=False, default=str)[:300]}", flush=True)


def insert_bar(symbol: str, open_time: int, ohlc, tf: str = "15m") -> int:
    span = {"15m": M15, "1h": 3_600_000, "4h": H4}[tf]
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


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--view", required=True)
    ap.add_argument("--tag", required=True)
    ap.add_argument("--paper", required=True)
    ap.add_argument("--symbol", required=True)
    a = ap.parse_args()

    PAPER = pathlib.Path(a.paper)
    PAPER.mkdir(parents=True, exist_ok=True)
    env = {
        "PAPER_LEDGER_DIR": str(PAPER),
        "PAPER_LEDGER_ALLOW_INIT": "1",
        "LIVE_DIR": str(ROOT / "live"),
        "LIVE_DB": str(ROOT / "live" / "live_signals.sqlite3"),
        "MARKET_DB": str(MARKET),
        "PAPER_GATE_FILE": str(GATE),
        "PAPER_LOCK_DIR": str(ROOT / f"locks_{a.tag}"),
        "EFFECTIVE_VIEW": a.view,
        "PYTHONPATH": f"{BACKEND}:{BASE}",
        "PYTHONUNBUFFERED": "1",
    }
    os.environ.update(env)
    if str(BACKEND) not in sys.path:
        sys.path.insert(0, str(BACKEND))

    import live_store as L
    L.ensure_live_schema()
    L.ensure_paper_epoch_ms()
    import paper_engine as PE

    envj = json.loads((OUT / "01_隔离环境.json").read_text(encoding="utf-8"))
    base = envj["合成行情"]["base"]
    sym = a.symbol
    T_sig = base - 3 * M15
    T_entry = base - 2 * M15

    res: dict = {"生成时刻": t(), "配置": a.view, "标签": a.tag, "标的": sym,
                 "隔离账本": str(PAPER), "base": base, "T_sig": T_sig,
                 "入口": "workers/paper_execution_worker.py --arm 3a --interval 1",
                 "加速回放声明": "信号与行情用回放时钟压缩；推进全部由守护按真实挂钟完成；工程测试，不计策略成绩"}

    p0 = PE.approved_params()
    res["生效参数"] = {"source": p0.get("source") or p0.get("_source"),
                   "stoploss_s": p0.get("stoploss_s"), "trail_d": p0.get("trail_d"),
                   "max_holding_hours": p0.get("max_holding_hours"),
                   "risk_per_trade": p0.get("risk_per_trade_equity_fraction"),
                   "uniform_stop_atr_mult": p0.get("uniform_stop_atr_mult"),
                   "portfolio_initial_stop_risk_cap": p0.get("portfolio_initial_stop_risk_cap"),
                   "model_version": p0.get("model_version"),
                   "view_sha256": (p0.get("view_sha256") or "")[:16] or None}
    note("生效参数", res["生效参数"])

    def snap() -> dict:
        # 必须锁定 3a：目录里同时有 3a~3d 四个库，glob 取第一个会读到空库（已踩过）
        p = PAPER / "paper_3a.sqlite3"
        if not p.exists():
            return {"pending": [], "orders": [], "fills": [], "positions": [], "stops": [], "state": []}
        con = sqlite3.connect(p)
        con.row_factory = sqlite3.Row

        def rows(sql):
            try:
                return [dict(r) for r in con.execute(sql).fetchall()]
            except sqlite3.Error as exc:                          # 出错返回空，绝不占位
                return [{"_错误": str(exc)}]
        out = {
            "pending": rows("SELECT pending_id,status,reason,stoploss_s,trail_d,config_fingerprint,"
                            "earliest_fill_ms FROM paper_pending"),
            "orders": rows("SELECT order_id,side,status,fill_price,fee FROM paper_order"),
            "fills": rows("SELECT fill_id,position_id,action,fill_price,fee FROM paper_fill"),
            "positions": rows("SELECT position_id,status,entry_price,stop_price,qty,entry_fee,exit_fee,"
                              "exit_price,exit_reason,stoploss_s,trail_d,config_fingerprint FROM paper_position"),
            "stops": rows("SELECT position_id,old_stop,new_stop,reason,effective_at FROM paper_stop_history"),
            "state": rows("SELECT cash FROM paper_state"),
        }
        con.close()
        return out

    # ── 0. 把该标的的 4h 历史归一到「不会一开仓就触发退出」的水平 ──
    # 原因：01_env 播的 4h low=97.0，若某配置 s=0.03（止损≈97.04），这根历史 4h 会
    # 恰好压在止损下方 ⇒ 守护一开仓就判退出。那是测试数据撞车，不是引擎缺陷，
    # 所以这里显式把 4h low 抬到 99.0（高于任何 s 下的初始止损），再驱动上移。
    n4 = del_bars_from(sym, 0, tf="4h")
    h4b0 = (base // H4) * H4
    for k in range(-4, 0):
        insert_bar(sym, h4b0 + k * H4, (100.0, 101.0, 99.0, 100.0), tf="4h")
    note("归一 4h 历史（避免历史 low 直接击穿初始止损）", {"删除根数": n4, "新 low": 99.0})

    # ── 1. 制造挂单窗口 + 信号登记 ──
    n_del = del_bars_from(sym, T_entry)
    note("撤掉尚未到达的 15m bar（制造真实挂单窗口）", {"删除根数": n_del, "自": T_entry})
    r = PE.open_position("3a", sym, signal_uid=f"sig_final_{a.tag}", direction="long", atr=1.0,
                         now_ms=T_sig, event_time=T_sig, available_at=T_sig,
                         requested_at=T_sig, origin="replay")
    note("信号登记（引擎 open_position，只登记挂单）",
         {"reason": r.get("reason"), "pending_id": r.get("pending_id"),
          "earliest_fill_ms": r.get("earliest_fill_ms")})
    s0 = snap()
    res["1_挂单"] = {"open_position": {"reason": r.get("reason"), "pending_id": r.get("pending_id")},
                   "挂单行": s0["pending"], "持仓数": len(s0["positions"])}

    # ── 2. 起守护 + 行情到达 → 自动成交 ──
    logf = OUT / f"03_worker_{a.tag}.log"
    fh = open(logf, "w", encoding="utf-8")
    w = subprocess.Popen([PY, str(BACKEND / "workers/paper_execution_worker.py"),
                          "--arm", "3a", "--interval", "1.0"],
                         cwd=str(BACKEND), env=dict(os.environ), stdout=fh, stderr=subprocess.STDOUT,
                         start_new_session=True)
    time.sleep(3)
    res["2_守护"] = {"pid": w.pid, "存活": w.poll() is None}
    ct = insert_bar(sym, T_entry, (100.0, 100.5, 99.5, 100.0))
    note("行情到达：插入已闭合成交 bar", {"open_time": T_entry, "close_time": ct})
    time.sleep(8)
    s1 = snap()
    res["2_成交"] = {"持仓": s1["positions"], "订单": s1["orders"], "成交": s1["fills"],
                   "挂单": s1["pending"]}
    filled = [p for p in s1["positions"] if p.get("status") in ("open", "closed")]
    res["2_判定"] = ("通过" if filled else "不通过(成交数为 0 ⇒ 自动闭环失败)")
    res["2_持仓状态"] = [p.get("status") for p in s1["positions"]]
    if filled and filled[0].get("status") == "closed":
        res["2_警告"] = "开仓后立即被退出（检查测试数据的 4h low 是否击穿初始止损）"

    pos_id = filled[0]["position_id"] if filled else None
    entry = float(filled[0]["entry_price"]) if filled else None
    stop0 = float(filled[0]["stop_price"]) if filled else None
    qty = float(filled[0]["qty"]) if filled else None

    # ── 3. 4h 跟踪上移 ≥2 次 + 中途重启 ──
    trail: list[dict] = []
    restart: dict = {}
    if pos_id is not None and filled[0].get("status") == "open":
        h4_base = (base // H4) * H4 - H4
        plan = [(1, 102.0, False), (2, 104.0, True), (3, 106.0, False)]
        for i, low, do_restart in plan:
            ot = h4_base + i * 5 * 60_000
            insert_bar(sym, ot, (low, low + 2.0, low, low + 1.0), tf="4h")
            time.sleep(5)
            sn = snap()
            cur = [p for p in sn["positions"] if p["position_id"] == pos_id]
            trail.append({"轮次": i, "4h_low": low,
                          "止损价": float(cur[0]["stop_price"]) if cur else None,
                          "轨迹条数": len([x for x in sn["stops"] if x["position_id"] == pos_id])})
            if do_restart:
                before = snap()
                w.terminate()
                try:
                    w.wait(timeout=6)
                except Exception:                                # noqa: BLE001
                    w.kill()
                time.sleep(1)
                fh2 = open(OUT / f"03_worker_{a.tag}_restart.log", "w", encoding="utf-8")
                w = subprocess.Popen([PY, str(BACKEND / "workers/paper_execution_worker.py"),
                                      "--arm", "3a", "--interval", "1.0"],
                                     cwd=str(BACKEND), env=dict(os.environ), stdout=fh2,
                                     stderr=subprocess.STDOUT, start_new_session=True)
                time.sleep(6)
                after = snap()
                restart = {
                    "重启前止损": [float(p["stop_price"]) for p in before["positions"]],
                    "重启后止损": [float(p["stop_price"]) for p in after["positions"]],
                    "重启前成交数": len(before["fills"]), "重启后成交数": len(after["fills"]),
                    "重启前轨迹数": len(before["stops"]), "重启后轨迹数": len(after["stops"]),
                    "重启前状态": [p["status"] for p in before["positions"]],
                    "重启后状态": [p["status"] for p in after["positions"]],
                }
        distinct = len({x["止损价"] for x in trail if x["止损价"] is not None})
        res["3_上移"] = {"轨迹": trail, "不同止损价个数": distinct, "重启": restart,
                       "判定": "通过" if distinct >= 2 else "不通过"}

        # ── 4. 自动退出 + 费用闭合 + 幂等 ──
        cur_stop = [float(p["stop_price"]) for p in snap()["positions"] if p["position_id"] == pos_id][0]
        brk = round(cur_stop - 2.0, 2)
        insert_bar(sym, base - M15, (cur_stop, cur_stop + 0.5, brk, brk + 0.2))
        note("行情到达：插入跌破生效止损的 15m bar", {"low": brk, "生效止损": cur_stop})
        time.sleep(8)
        s2 = snap()
        closed = [p for p in s2["positions"] if p.get("status") == "closed"]
        pos = (closed or [{}])[0]
        fees = sum(float(f.get("fee") or 0) for f in s2["fills"])
        cash = float((s2["state"] or [{}])[0].get("cash") or 0)
        entry_fee = float(pos.get("entry_fee") or 0)
        exit_fee = float(pos.get("exit_fee") or 0)
        res["4_退出"] = {
            "退出": "通过" if closed else "不通过",
            "退出原因": [p.get("exit_reason") for p in closed],
            "退出价": [float(p.get("exit_price") or 0) for p in closed],
            "订单": [(o["side"], o["status"], o["fill_price"], o["fee"]) for o in s2["orders"]],
            "成交": [(f["action"], f["fill_price"], f["fee"]) for f in s2["fills"]],
            "entry_fee": entry_fee, "exit_fee": exit_fee, "成交费合计": round(fees, 10),
            "费用自洽": abs(fees - (entry_fee + exit_fee)) < 1e-9,
            "现金": cash, "现金为正": cash > 0,
        }
        insert_bar(sym, base - M15, (cur_stop, cur_stop + 0.5, brk, brk + 0.2))
        time.sleep(5)
        s3 = snap()
        res["5_幂等"] = {"成交数前": len(s2["fills"]), "成交数后": len(s3["fills"]),
                      "持仓数前": len(s2["positions"]), "持仓数后": len(s3["positions"]),
                      "判定": "通过" if len(s2["fills"]) == len(s3["fills"]) else "不通过"}

    # ── 5. 运行身份：状态接口 vs 进程 ──
    try:
        import paper_loop as PL
        st = PL.get_daemon_status("3a")
        res["6_运行身份"] = {"get_daemon_status": st, "worker_pid": w.pid,
                          "worker存活": w.poll() is None,
                          "一致": (bool(st.get("running")) == (w.poll() is None))}
    except Exception as exc:                                     # noqa: BLE001
        res["6_运行身份"] = {"错误": str(exc)}

    # ── 6. 风险独立重算（B 关用）──
    if filled:
        risk_actual = qty * (entry - stop0)
        eq0 = 5000.0
        budget = eq0 * float(p0.get("risk_per_trade_equity_fraction") or 0)
        atr_risk = qty * (float(p0.get("uniform_stop_atr_mult") or 0) * 1.0)
        res["7_风险重算"] = {
            "成交价": entry, "初始止损": stop0, "数量": qty,
            "实际风险_数量×(成交价-止损)": round(risk_actual, 8),
            "预算_初始权益×单笔比例": round(budget, 8),
            "实际/预算": round(risk_actual / budget, 6) if budget else None,
            "若按 3×ATR 定量口径的风险": round(atr_risk, 8),
            "名义": round(qty * entry, 8),
            "止损距离": round(entry - stop0, 8),
            "s 反推": round((entry - stop0) / entry, 8),
        }
        note("风险独立重算", res["7_风险重算"])

    w.terminate()
    try:
        w.wait(timeout=6)
    except Exception:                                            # noqa: BLE001
        w.kill()
    res["7_结束"] = {"worker已停": w.poll() is not None}
    res["时间线"] = TIMELINE

    outp = OUT / f"03_{a.tag}.json"
    outp.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[{a.tag}] 写出 {outp}", flush=True)


if __name__ == "__main__":
    main()
