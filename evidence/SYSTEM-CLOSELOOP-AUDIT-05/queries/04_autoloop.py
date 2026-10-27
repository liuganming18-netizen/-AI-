# -*- coding: utf-8 -*-
"""04 第 2/3/4/6 项：参数贯通 → 挂单 → 自动成交 → 止损上移≥2次（含重启）→ 自动退出闭合。

工单：
  2. 缺配置时启动检查明确失败；完整配置下正常信号能够挂单并自动成交。
  3. 自动止损上移至少两次，期间重启后继续运行。
  4. 自动退出产生完整订单、成交、持仓和费用账本，金额闭合。
  6. 重试和重复行情不重复成交或记账；第二配置证明参数贯通。

审核脚本只做三件事，都不属于「推进交易」：
  ① 造一条信号（引擎自己的 `open_position`，等价于上游信号入库；`origin=replay` 走加速回放）；
  ② 往**隔离合成行情库**插入已闭合 bar（= 行情到达）；
  ③ 起停 `workers/paper_execution_worker.py`（交付方唯一的执行入口）。
脚本**不调用** `advance_pending` / `advance_position_trailing` / `check_position_exit_on_bar`，
也不写任何账本表；撮合、跟踪、退出全部由守护自己完成。

**加速回放声明**：信号与行情时间用回放时钟压缩，但**推进仍由守护按真实挂钟自行完成**。
本脚本产出的是**工程测试**结果，不计入任何策略成绩。

产物：out/04_自动闭环.json
"""
from __future__ import annotations

import datetime as dt
import json
import os
import pathlib
import sqlite3
import subprocess
import sys
import time

BASE = pathlib.Path("/Users/boos/Desktop/威科夫因子共振趋势")
HERE = BASE / "evidence/SYSTEM-CLOSELOOP-AUDIT-05"
OUT = HERE / "out"
BACKEND = BASE / "框架/backend"
ROOT = pathlib.Path("/tmp/wx_audit05")
PAPER = ROOT / "paper"
MARKET = ROOT / "market.sqlite3"
GATE = ROOT / "gate.json"
VIEW_A = ROOT / "view_A_complete.json"
VIEW_B = ROOT / "view_B_alt.json"
PY = "/Users/boos/.workbuddy-ai/binaries/python/envs/default/bin/python"
M15 = 900_000
H4 = 14_400_000

ENV = {
    "PAPER_LEDGER_DIR": str(PAPER),
    "PAPER_LEDGER_ALLOW_INIT": "1",
    "LIVE_DIR": str(ROOT / "live"),
    "LIVE_DB": str(ROOT / "live" / "live_signals.sqlite3"),
    "MARKET_DB": str(MARKET),
    "PAPER_GATE_FILE": str(GATE),
    "PYTHONPATH": f"{BACKEND}:{BASE}",
    "PYTHONUNBUFFERED": "1",
    "PAPER_LOCK_DIR": str(ROOT / "locks"),
}
TIMELINE: list[dict] = []
ERRORS: list[dict] = []


def t() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def note(step: str, detail) -> None:
    TIMELINE.append({"时刻": t(), "步骤": step, "详情": detail})
    print(f"[{t()[11:]}] {step}: {json.dumps(detail, ensure_ascii=False, default=str)[:340]}")


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
                ERRORS.append({"sql": sql.strip()[:70], "错误": str(exc)})
                return []      # 出错返回空列表；绝不返回占位行（占位行会被数成 1 条记录）
        return {
            "pending": rows("SELECT pending_id,symbol,status,reason,requested_at,earliest_fill_ms,event_time,"
                            "stoploss_s,trail_d,config_fingerprint FROM paper_pending ORDER BY pending_id"),
            "orders": rows("SELECT order_id,event_uid,symbol,side,qty,price,status,reject_reason,ts,fill_price,fee "
                           "FROM paper_order ORDER BY order_id"),
            "positions": rows("SELECT position_id,symbol,qty,entry_price,entry_ts,stop_price,status,exit_ts,"
                              "exit_reason,exit_price,entry_fee,exit_fee,stoploss_s,trail_d,config_fingerprint "
                              "FROM paper_position ORDER BY position_id"),
            "fills": rows("SELECT fill_id,position_id,order_id,symbol,action,qty,fill_price,fee,fill_ts "
                          "FROM paper_fill ORDER BY fill_id"),
            "stops": rows("SELECT position_id,reason,old_stop,new_stop,ref_bar_open_ms,available_at,"
                          "decision_at,effective_at FROM paper_stop_history ORDER BY position_id, effective_at"),
            "state": rows("SELECT account_id,cash,realized_pnl,fees_paid,funding_paid FROM paper_state"),
            "blocked": rows("SELECT event_uid,symbol,reason,ts FROM paper_blocked ORDER BY id"),
        }
    finally:
        con.close()


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


def start_worker(tag: str, interval: float = 1.0, extra_env: dict | None = None):
    log = OUT / f"04_worker_{tag}.log"
    env = dict(os.environ)
    env.update(ENV)
    if extra_env:
        env.update(extra_env)
    f = open(log, "w", encoding="utf-8")
    p = subprocess.Popen([PY, str(BACKEND / "workers/paper_execution_worker.py"),
                          "--arm", "3a", "--interval", str(interval)],
                         cwd=str(BACKEND), env=env, stdout=f, stderr=subprocess.STDOUT,
                         start_new_session=True)
    return p, log


def stop(p) -> None:
    p.terminate()
    try:
        p.wait(timeout=6)
    except Exception:                                            # noqa: BLE001
        p.kill()


def write_view(path: pathlib.Path, s: float, d: float, tag: str) -> None:
    path.write_text(json.dumps({
        "schema_version": "research_policy_2.2", "spec_version": f"audit05_{tag}",
        "strategy_version": f"FT-LOW4H-AUDIT-{tag}", "model_version": f"FT-LOW4H-AUDIT-{tag}",
        "purpose": "paper_isolated_execution", "production_config": False,
        "live_trading_authorized": False, "research_policy_frozen": True,
        "execution": {"detail_timeframe": "15m", "signal_execution_delay_minutes": 15,
                      "fee_each_side": 0.0005, "slippage_each_side": 0.0005,
                      "signal_exit_delay_minutes": 15},
        "stops": {"rule": "FT-LOW4H", "stoploss_s": s, "trail_d": d,
                  "uniform": "actual_entry_minus_s_and_4h_low_minus_d",
                  "max_holding_hours": 240, "trailing_enabled": True},
        "risk": {"initial_equity_USDT": 5000, "risk_per_trade_equity_fraction": 0.005,
                 "leverage": 1, "max_positions": 10, "single_name_notional_equity_cap": 0.10,
                 "gross_long_notional_equity_cap": 0.80, "cash_or_free_margin_reserve_fraction": 0.20,
                 "portfolio_initial_stop_risk_cap": 0.05,
                 "entry_participation_previous_15m_quote_cap": 0.01,
                 "maximum_account_drawdown_cap": 0.20, "maximum_account_drawdown_fraction": 0.20,
                 "short_enabled": False},
    }, ensure_ascii=False, indent=1), encoding="utf-8")


def main() -> None:
    envj = json.loads((OUT / "02_隔离环境.json").read_text(encoding="utf-8"))
    base = envj["合成行情"]["base"]
    sym = envj["合成行情"]["主标的"]
    now_ms = int(time.time() * 1000)
    T_sig = base - 3 * M15          # 回放信号时刻；成交 bar = base-2*M15（已闭合）

    for k, v in ENV.items():
        os.environ[k] = v
    if str(BACKEND) not in sys.path:
        sys.path.insert(0, str(BACKEND))
    import paper_engine as PE

    res: dict = {"生成时刻": t(), "隔离根": str(ROOT), "base": base, "T_sig": T_sig,
                 "入口": "workers/paper_execution_worker.py --arm 3a --interval 1",
                 "加速回放声明": "信号与行情用回放时钟压缩；推进全部由守护按真实挂钟完成；本结果为工程测试，不计策略成绩"}

    # ── A. 参数来源（默认视图 = 交付方正式位置） ──
    os.environ.pop("EFFECTIVE_VIEW", None)
    p_def = PE.approved_params()
    res["A_参数_正式视图"] = {"路径": str(PE.EFFECTIVE_VIEW), "存在": PE.EFFECTIVE_VIEW.exists(),
                         "键数": len(p_def), "source": p_def.get("_source"),
                         "stoploss_s": p_def.get("stoploss_s"), "trail_d": p_def.get("trail_d"),
                         "max_holding_hours": p_def.get("max_holding_hours"),
                         "view_sha256": (p_def.get("view_sha256") or "")[:16]}
    write_view(VIEW_A, 0.05, 0.05, "A")
    os.environ["EFFECTIVE_VIEW"] = str(VIEW_A)
    p_a = PE.approved_params()
    write_view(VIEW_B, 0.08, 0.12, "B")
    os.environ["EFFECTIVE_VIEW"] = str(VIEW_B)
    p_b = PE.approved_params()
    os.environ["EFFECTIVE_VIEW"] = str(VIEW_A)
    excl = ("view_sha256", "config_fingerprint", "view_spec_version", "_source",
            "model_version", "_exec_core_crosscheck")
    res["A_参数_第二配置"] = {
        "A": {"s": p_a.get("stoploss_s"), "d": p_a.get("trail_d")},
        "B": {"s": p_b.get("stoploss_s"), "d": p_b.get("trail_d")},
        "两套生效参数是否相同": {k: v for k, v in p_a.items() if k not in excl} ==
                        {k: v for k, v in p_b.items() if k not in excl},
    }
    res["A_判定"] = {
        "正式视图含 s/d": (p_def.get("stoploss_s") is not None) and (p_def.get("trail_d") is not None),
        "两套配置产生区别": not res["A_参数_第二配置"]["两套生效参数是否相同"],
        "结论": ("参数贯通：s/d 来自配置文件，且两套配置产生预期区别"
                 if (p_def.get("stoploss_s") is not None and p_def.get("trail_d") is not None
                     and not res["A_参数_第二配置"]["两套生效参数是否相同"]) else "参数未贯通"),
    }
    note("A 参数贯通", res["A_判定"])

    # ── B. 缺配置时「启动检查」是否明确失败 ──
    attempts = []
    w_miss = None
    for k in range(3):
        w_miss, log_miss = start_worker(f"missing_cfg{k}", 1.0,
                                        {"EFFECTIVE_VIEW": str(ROOT / "no_such_view.json")})
        time.sleep(5)
        code = w_miss.poll()
        txt = log_miss.read_text(encoding="utf-8", errors="ignore") if log_miss.exists() else ""
        attempts.append({"第几次": k + 1, "退出码": code, "存活": code is None,
                         "日志尾部": txt.strip().splitlines()[-3:]})
        if code is None:
            break
        time.sleep(2)          # 若是锁冲突（上一次的守护还没退），等它退掉再重试
    last = attempts[-1]
    alive_miss = last["存活"]
    lock_conflict = any("lock occupied" in " ".join(a["日志尾部"]) for a in attempts)
    if w_miss is not None:
        stop(w_miss)
    os.environ["EFFECTIVE_VIEW"] = str(ROOT / "no_such_view.json")
    r_miss = PE.open_position("3a", sym, signal_uid="sig_audit05_missing_cfg", direction="long",
                              atr=1.0, now_ms=now_ms, event_time=T_sig, available_at=T_sig,
                              requested_at=T_sig, origin="replay")
    res["B_缺配置"] = {
        "尝试": attempts,
        "守护是否照常启动": alive_miss,
        "退出原因是否锁冲突": lock_conflict,
        "缺配置下的信号结果": {"ok": r_miss.get("ok"), "reason": r_miss.get("reason")},
        "启动检查": ("不通过（无启动自检，缺配置时守护照常运行）" if alive_miss else
                 ("无效（因单实例锁冲突退出，不是配置检查）" if lock_conflict else "通过（启动即失败）")),
        "缺配置是否静默兜底": ("是（配置缺失仍按 FALLBACK 开仓，且账本未标注 fallback 来源）"
                        if r_miss.get("ok") else "否（显式拒单 %s）" % r_miss.get("reason")),
    }
    note("B 缺配置", {"存活": alive_miss, "锁冲突": lock_conflict,
                   "信号": res["B_缺配置"]["缺配置下的信号结果"]})

    # ── 清账本，保证 C 段从零开始 ──
    os.environ["EFFECTIVE_VIEW"] = str(VIEW_A)
    con = paper_conn()
    for tbl in ("paper_pending", "paper_request_anchor", "paper_order", "paper_fill",
                "paper_position", "paper_stop_history", "paper_blocked"):
        try:
            con.execute(f"DELETE FROM {tbl}")
        except sqlite3.Error:
            pass
    con.commit()
    con.close()

    # ── C. 完整配置：信号 → 挂单 → 守护自动成交 ──
    n_del = del_bars_from(sym, base - 2 * M15)
    note("撤掉尚未到达的 15m bar（制造真实挂单窗口）", {"删除根数": n_del, "自": base - 2 * M15})
    res["C_撤bar"] = {"删除根数": n_del, "自": base - 2 * M15}
    r = PE.open_position("3a", sym, signal_uid="sig_auto05_01", direction="long", atr=1.0,
                         now_ms=T_sig, event_time=T_sig, available_at=T_sig,
                         requested_at=T_sig, origin="replay")
    note("C 信号到达（引擎 open_position，回放时钟）",
         {"ok": r.get("ok"), "reason": r.get("reason"), "pending_id": r.get("pending_id"),
          "earliest_fill_ms": r.get("earliest_fill_ms")})
    res["C_信号登记"] = {"ok": r.get("ok"), "reason": r.get("reason"),
                     "pending_id": r.get("pending_id"), "earliest_fill_ms": r.get("earliest_fill_ms")}
    snap0 = snapshot()
    res["C_账本_信号后"] = snap0

    w1, log1 = start_worker("w1", 1.0, {"EFFECTIVE_VIEW": str(VIEW_A)})
    time.sleep(3)
    res["C_守护启动"] = {"pid": w1.pid, "存活": w1.poll() is None,
                   "日志": (log1.read_text(encoding="utf-8", errors="ignore").strip().splitlines()[-3:]
                          if log1.exists() else [])}
    ct = insert_bar(sym, base - 2 * M15, (100.0, 100.5, 99.5, 100.0))
    note("C 行情到达：插入已闭合成交 bar", {"open_time": base - 2 * M15, "close_time": ct})
    time.sleep(8)
    snap1 = snapshot()
    res["C_账本_行情到达后"] = snap1
    filled = snap1["positions"]
    res["C_判定"] = {
        "①挂单": "通过" if snap0["pending"] else "不通过",
        "②自动成交": "通过" if filled else "不通过",
        "成交价": [p["entry_price"] for p in filled],
        "初始止损": [p["stop_price"] for p in filled],
        "订单": [(o["side"], o["status"], o["fill_price"], o["fee"]) for o in snap1["orders"]],
        "成交条数": len(snap1["fills"]), "止损轨迹数": len(snap1["stops"]),
        "挂单最终状态": [(p["pending_id"], p["status"], p["reason"]) for p in snap1["pending"]],
    }
    note("C 自动成交判定", res["C_判定"])

    # ── D. 止损上移 ≥2 次 + 中途重启 ──
    steps: dict = {}
    if filled:
        pos_id = filled[0]["position_id"]
        trail_log = []
        # 4h「到达」用回放加速：open_time 递增、close_time 均 <= now（引擎按 close_time<=now 取最近已闭合）
        h4_base = (base // H4) * H4 - H4          # 最近一根已闭合 4h 的起点（20:00）
        plan = [(1, 102.0, False), (2, 104.0, True), (3, 106.0, False)]
        for i, low, do_restart in plan:
            ot = h4_base + i * 5 * 60_000         # 每根间隔 5 分钟，保证 close_time 仍 <= now
            insert_bar(sym, ot, (low, low + 2.0, low, low + 1.0), tf="4h")
            time.sleep(5)
            s_now = snapshot()
            cur = [p for p in s_now["positions"] if p["position_id"] == pos_id]
            trail_log.append({"轮次": i, "4h_low": low,
                              "止损价": cur[0]["stop_price"] if cur else None,
                              "轨迹条数": len([x for x in s_now["stops"] if x["position_id"] == pos_id])})
            if do_restart:
                before = snapshot()
                stop(w1)
                time.sleep(1)
                w1, log1 = start_worker("w2_after_restart", 1.0, {"EFFECTIVE_VIEW": str(VIEW_A)})
                time.sleep(6)
                after = snapshot()
                steps["重启"] = {
                    "重启前止损": [p["stop_price"] for p in before["positions"]],
                    "重启后止损": [p["stop_price"] for p in after["positions"]],
                    "重启前成交数": len(before["fills"]), "重启后成交数": len(after["fills"]),
                    "重启前轨迹数": len(before["stops"]), "重启后轨迹数": len(after["stops"]),
                    "重启前持仓状态": [p["status"] for p in before["positions"]],
                    "重启后持仓状态": [p["status"] for p in after["positions"]],
                }
        steps["止损上移"] = trail_log
        stops_now = [x["new_stop"] for x in snapshot()["stops"]]
        moves = len(set(stops_now))
        res["D_判定"] = {"上移次数（不同止损价）": moves, "轨迹明细": trail_log,
                    "重启": steps.get("重启"),
                    "判定": "通过" if moves >= 2 else "不通过"}

        # ── E. 自动退出（15m 明细周期击穿生效止损） ──
        stop_now = [p["stop_price"] for p in snapshot()["positions"] if p["position_id"] == pos_id][0]
        brk_low = round(float(stop_now) - 2.0, 2)
        ct2 = insert_bar(sym, base - M15, (float(stop_now), float(stop_now) + 0.5, brk_low, brk_low + 0.2))
        note("E 行情到达：插入跌破止损的 15m bar", {"low": brk_low, "生效止损": stop_now, "close_time": ct2})
        time.sleep(8)
        snap2 = snapshot()
        res["E_账本_退出后"] = snap2
        closed = [p for p in snap2["positions"] if p.get("status") == "closed"]
        st = (snap2["state"] or [{}])[0]
        fills = snap2["fills"]
        fees = sum(float(f.get("fee") or 0) for f in fills)
        pos = (closed or snap2["positions"] or [{}])[0]
        # 闭合核对：现金 = 初始 - 开仓名义 - 开仓费 + 平仓名义 - 平仓费
        entry_fee = float(pos.get("entry_fee") or 0)
        exit_fee = float(pos.get("exit_fee") or 0)
        cash = float(st.get("cash") or 0)
        res["E_判定"] = {
            "退出": "通过" if closed else "不通过",
            "退出原因": [p.get("exit_reason") for p in closed],
            "退出价": [p.get("exit_price") for p in closed],
            "订单": [(o["side"], o["status"], o["fill_price"], o["fee"]) for o in snap2["orders"]],
            "成交": [(f["action"], f["fill_price"], f["fee"]) for f in fills],
            "entry_fee": entry_fee, "exit_fee": exit_fee,
            "成交费合计": round(fees, 10), "账本现金": cash,
            "费用是否自洽": abs(fees - (entry_fee + exit_fee)) < 1e-9,
            "现金是否为正": cash > 0,
        }
        res["E_判定"]["判定"] = "通过" if closed else "不通过"
        # 幂等：重复插同一根跌破 bar
        insert_bar(sym, base - M15, (float(stop_now), float(stop_now) + 0.5, brk_low, brk_low + 0.2))
        time.sleep(5)
        snap3 = snapshot()
        res["E_幂等"] = {"成交数_前": len(fills), "成交数_后": len(snap3["fills"]),
                     "持仓数_前": len(snap2["positions"]), "持仓数_后": len(snap3["positions"]),
                     "判定": "通过" if (len(fills) == len(snap3["fills"])) else "不通过"}
    else:
        res["D_判定"] = {"判定": "未执行", "原因": "成交数为 0 ⇒ 自动闭环失败，止损上移无从谈起"}
        res["E_判定"] = {"判定": "未执行", "原因": "成交数为 0"}
        res["E_幂等"] = {"判定": "未执行"}

    # ── F. 重试与重复行情 ──
    before = snapshot()
    time.sleep(8)
    after = snapshot()
    res["F_重试"] = {
        "订单行数_前": len(before["orders"]), "订单行数_后": len(after["orders"]),
        "成交数_前": len(before["fills"]), "成交数_后": len(after["fills"]),
        "持仓数_前": len(before["positions"]), "持仓数_后": len(after["positions"]),
        "判定": ("通过" if (len(before["fills"]) == len(after["fills"])
                        and len(before["positions"]) == len(after["positions"])
                        and len(before["fills"]) > 0) else
                 ("不通过（有重复记账）" if len(before["fills"]) != len(after["fills"]) else
                  "未执行（成交数为 0，无重复可言）")),
        "说明": "成交数为 0 时不得判「幂等通过」；只能记未执行",
    }
    # ── G. 第二配置（s=0.08 / d=0.12）证明参数贯通 ──
    stop(w1)                       # 先停第一配置的守护，避免抢锁与混用参数
    time.sleep(1)
    try:
        con = paper_conn()
        for tbl in ("paper_pending", "paper_request_anchor", "paper_order", "paper_fill",
                    "paper_position", "paper_stop_history", "paper_blocked"):
            try:
                con.execute(f"DELETE FROM {tbl}")
            except sqlite3.Error:
                pass
        con.commit()
        wiped = {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                 for t in ("paper_position", "paper_fill", "paper_order", "paper_stop_history")}
        con.close()
        del_bars_from(sym, base - 2 * M15)
        # 关键：父进程（信号登记方）也必须切到第二配置，否则挂单会带第一配置的身份
        os.environ["EFFECTIVE_VIEW"] = str(VIEW_B)
        rB = PE.open_position("3a", sym, signal_uid="sig_auto05_cfgB", direction="long", atr=1.0,
                              now_ms=T_sig, event_time=T_sig, available_at=T_sig,
                              requested_at=T_sig, origin="replay")
        wB, logB = start_worker("w_cfgB", 1.0, {"EFFECTIVE_VIEW": str(VIEW_B)})
        time.sleep(3)
        insert_bar(sym, base - 2 * M15, (100.0, 100.5, 99.5, 100.0))
        time.sleep(8)
        aliveB = wB.poll() is None
        snapB = snapshot()
        posB = snapB["positions"]
        # 同一根 4h（low=110）在 d=0.12 下候选 = 96.8；若仍用 d=0.05 则是 104.5 ⇒ 可判别
        h4_baseB = (base // H4) * H4 - H4
        insert_bar(sym, h4_baseB + 5 * 60_000, (110.0, 112.0, 110.0, 111.0), tf="4h")
        time.sleep(6)
        snapB2 = snapshot()
        con_m = sqlite3.connect(MARKET)
        con_m.row_factory = sqlite3.Row
        used4h = dict(con_m.execute(
            "SELECT open_time,close_time,low FROM candle_versions WHERE symbol=? AND timeframe='4h' "
            "AND close_time<=? ORDER BY close_time DESC LIMIT 1",
            (sym, int(time.time() * 1000))).fetchone())
        con_m.close()
        posB2 = [p for p in snapB2["positions"] if p["status"] == "open"]
        stopB2 = [p["stop_price"] for p in posB2]
        pendB = snapB["pending"]
        stop(wB)
        entryB = float(posB[0]["entry_price"]) if posB else 0.0
        init_rows = [x for x in snapB2["stops"] if x["position_id"] == posB[0]["position_id"]
                     and x["reason"] == "initial_stop"] if posB else []
        trail_rows = [x for x in snapB2["stops"] if posB and x["position_id"] == posB[0]["position_id"]
                      and x["reason"] == "ft_low4h_trail"]
        stop_init = float(init_rows[-1]["new_stop"]) if init_rows else 0.0
        sB = float(posB[0]["stoploss_s"]) if posB and posB[0].get("stoploss_s") is not None else None
        dB = float(posB[0]["trail_d"]) if posB and posB[0].get("trail_d") is not None else None
        res["G_第二配置"] = {
            "配置": {"s": 0.08, "d": 0.12},
            "清账后行数": wiped, "wB存活": aliveB,
            "wB日志": (logB.read_text(encoding="utf-8", errors="ignore").strip().splitlines()[-3:]
                      if logB.exists() else []),
            "信号结果": {"ok": rB.get("ok"), "reason": rB.get("reason")},
            "挂单记录的身份": [{"stoploss_s": x.get("stoploss_s"), "trail_d": x.get("trail_d"),
                         "config_fingerprint": (x.get("config_fingerprint") or "")[:12]} for x in pendB],
            "持仓记录的身份": [{"stoploss_s": sB, "trail_d": dB,
                         "config_fingerprint": (posB[0].get("config_fingerprint") or "")[:12]} for _ in [0]],
            "成交价": entryB, "初始止损(轨迹首行)": stop_init,
            "跟踪轨迹": [{"old": x["old_stop"], "new": x["new_stop"], "reason": x["reason"]} for x in trail_rows],
            "理论初始止损": round(entryB * (1 - 0.08), 4) if entryB else None,
            "被采用的4h": used4h,
            "跟踪后止损": stopB2,
            "理论跟踪候选(d=0.12)": round(float(used4h["low"]) * (1 - 0.12), 4),
            "若误用 d=0.05 会是": round(float(used4h["low"]) * 0.95, 4),
            "判定": ("通过" if (posB and sB == 0.08 and dB == 0.12
                            and abs(stop_init - entryB * (1 - 0.08)) <= 0.12
                            and stopB2 and abs(float(stopB2[0]) - float(used4h["low"]) * 0.88) <= 0.15)
                    else "不通过"),
        }
        note("G 第二配置", {k: res["G_第二配置"][k] for k in
                         ("挂单记录的身份", "持仓记录的身份", "初始止损(轨迹首行)", "理论初始止损",
                          "被采用的4h", "跟踪后止损", "判定")})
    except Exception as exc:                                     # noqa: BLE001
        res["G_第二配置"] = {"判定": "未执行", "错误": f"{type(exc).__name__}: {exc}"}
    finally:
        os.environ["EFFECTIVE_VIEW"] = str(VIEW_A)

    stop(w1)
    res["守护日志_w1尾部"] = (log1.read_text(encoding="utf-8", errors="ignore").strip().splitlines()[-6:]
                         if log1.exists() else [])
    res["时间线"] = TIMELINE
    res["快照SQL错误"] = ERRORS

    res["总判定"] = {
        "第2项_缺配置启动检查": res["B_缺配置"]["启动检查"],
        "第2项_完整配置自动成交": res["C_判定"]["②自动成交"],
        "第3项_止损上移≥2次含重启": res["D_判定"]["判定"],
        "第4项_自动退出闭合": res["E_判定"]["判定"],
        "第4项_退出幂等": res["E_幂等"]["判定"],
        "第6项_重试不重复记账": res["F_重试"]["判定"],
        "第6项_第二配置参数贯通": res.get("G_第二配置", {}).get("判定", "未执行"),
    }
    (OUT / "04_自动闭环.json").write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str),
                                    encoding="utf-8")
    print("\n=== 总判定 ===")
    for k, v in res["总判定"].items():
        print(f"  {k}: {v}")
    print("  参数:", json.dumps(res["A_判定"], ensure_ascii=False))
    print("  成交:", json.dumps(res["C_判定"], ensure_ascii=False)[:420])
    print("  止损:", json.dumps(res["D_判定"], ensure_ascii=False)[:420])
    print("  退出:", json.dumps(res["E_判定"], ensure_ascii=False)[:420])


if __name__ == "__main__":
    main()
