# -*- coding: utf-8 -*-
"""SYSTEM-CLOSELOOP-05｜全自动交易闭环与参数行情收口独立验收脚本。

本脚本严格遵循工单纪律要求：
  - 测试脚本只负责准备隔离配置、输入数据、启动实例和观察结果；
  - 严禁直接调用 execute_cycle、推进持仓、更新止损或写入成交结果；
  - 全流程由常驻守护进程（PaperExecutionDaemon / worker）通过轮询自动完成！
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import pathlib
import shutil
import signal
import sqlite3
import subprocess
import sys
import time
import urllib.request
import urllib.error

BASE = pathlib.Path("/Users/boos/Desktop/威科夫因子共振趋势")
EVID = BASE / "evidence/SYSTEM-CLOSELOOP-05"
DATA_DIR = EVID / "data"
LOG_DIR = EVID / "logs"
CONFIG_EXP1 = EVID / "config/exp1_config.json"
CONFIG_EXP2 = EVID / "config/exp2_config.json"
BACKEND = BASE / "框架/backend"

ROOT = pathlib.Path("/tmp/wx_closeloop05")
PAPER = ROOT / "paper"
LOCKS = ROOT / "locks"
LIVE_DIR = ROOT / "live"
LIVE_DB = LIVE_DIR / "live_signals.sqlite3"
MARKET = ROOT / "market.sqlite3"
GATE = ROOT / "gate.json"
PY = "/Users/boos/.workbuddy-ai/binaries/python/envs/default/bin/python"

M15 = 900_000
H1 = 3_600_000
H4 = 4 * H1
SYM = "BTCUSDT"

ENV_BASE = {
    "PAPER_LEDGER_DIR": str(PAPER),
    "PAPER_LEDGER_ALLOW_INIT": "1",
    "PAPER_LOCK_DIR": str(LOCKS),
    "LIVE_DIR": str(LIVE_DIR),
    "LIVE_DB": str(LIVE_DB),
    "MARKET_DB": str(MARKET),
    "PAPER_GATE_FILE": str(GATE),
    "EFFECTIVE_VIEW": str(CONFIG_EXP1),
    "PYTHONPATH": f"{BACKEND}:{BASE}",
}

OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def t() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def get_file_sha256(p: pathlib.Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def build_market_schema() -> sqlite3.Connection:
    if MARKET.exists():
        MARKET.unlink()
    con = sqlite3.connect(MARKET)
    con.executescript("""
    CREATE TABLE candle_versions(
      exchange TEXT NOT NULL, market TEXT NOT NULL, symbol TEXT NOT NULL,
      timeframe TEXT NOT NULL, open_time INTEGER NOT NULL, close_time INTEGER NOT NULL,
      open TEXT, high TEXT, low TEXT, close TEXT, volume TEXT, quote_volume TEXT,
      trade_count INTEGER, taker_buy_volume TEXT, taker_buy_quote_volume TEXT,
      source_id INTEGER NOT NULL, available_at INTEGER, is_closed INTEGER NOT NULL,
      PRIMARY KEY(exchange, market, symbol, timeframe, open_time, source_id));
    CREATE TABLE candle_heads(
      exchange TEXT NOT NULL, market TEXT NOT NULL, symbol TEXT NOT NULL,
      timeframe TEXT NOT NULL, open_time INTEGER NOT NULL, source_id INTEGER NOT NULL,
      PRIMARY KEY(exchange, market, symbol, timeframe, open_time));
    CREATE TABLE funding_history (
      exchange TEXT NOT NULL, market TEXT NOT NULL, symbol TEXT NOT NULL,
      funding_time INTEGER NOT NULL, funding_rate TEXT, mark_price TEXT,
      rate_type TEXT, source TEXT, fetched_at INTEGER,
      PRIMARY KEY(exchange, market, symbol, funding_time));
    """)
    con.execute("CREATE VIEW candles AS SELECT v.* FROM candle_versions v "
                "JOIN candle_heads h USING(exchange, market, symbol, timeframe, open_time, source_id)")
    con.commit()
    return con


def insert_bar(symbol: str, open_time: int, ohlc, tf: str = "15m", quote_vol: str = "100000") -> int:
    span = {"15m": M15, "1h": H1, "4h": H4}[tf]
    close_time = open_time + span - 1
    o, h, l, c = ohlc
    con = sqlite3.connect(MARKET)
    try:
        con.execute("INSERT OR REPLACE INTO candle_versions VALUES"
                    "('binance','um',?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)",
                    (symbol, tf, open_time, close_time, f"{o:.2f}", f"{h:.2f}", f"{l:.2f}", f"{c:.2f}",
                     "1000", quote_vol, 10, "500", "50000", 1, close_time))
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


def paper_conn(arm: str = "3a"):
    p = PAPER / f"paper_{arm}.sqlite3"
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
                return [{"_error": str(exc)}]
        st = con.execute("SELECT cash, realized_pnl, fees_paid, funding_paid FROM paper_state WHERE account_id=?",
                         (f"paper43_{arm}",)).fetchone()
        return {
            "state": dict(st) if st else {},
            "pending": rows("SELECT pending_id,symbol,status,reason,requested_at,first_request_ms,"
                            "earliest_fill_ms,event_time,stoploss_s,trail_d,model_version,config_fingerprint "
                            "FROM paper_pending ORDER BY pending_id"),
            "orders": rows("SELECT order_id,event_uid,symbol,side,qty,price,status,reject_reason,ts,"
                           "fill_price,notional,model_version FROM paper_order ORDER BY order_id"),
            "positions": rows("SELECT position_id,symbol,qty,entry_price,entry_ts,stop_price,status,"
                              "exit_ts,exit_reason,entry_fee,exit_fee,exit_price,funding_paid,stoploss_s,trail_d,"
                              "model_version,config_fingerprint FROM paper_position ORDER BY position_id"),
            "fills": rows("SELECT fill_id,position_id,order_id,symbol,action,qty,fill_price,fee,fill_ts "
                          "FROM paper_fill ORDER BY fill_id"),
            "stops": rows("SELECT id,position_id,old_stop,new_stop,reason,decision_at,effective_at "
                          "FROM paper_stop_history ORDER BY position_id, effective_at"),
        }
    finally:
        con.close()


def setup_clean_env(effective_view_path: pathlib.Path) -> int:
    if ROOT.exists():
        shutil.rmtree(ROOT)
    PAPER.mkdir(parents=True, exist_ok=True)
    LOCKS.mkdir(parents=True, exist_ok=True)
    LIVE_DIR.mkdir(parents=True, exist_ok=True)

    now_ms = int(time.time() * 1000)
    base = (now_ms // M15) * M15

    con = build_market_schema()
    # 预置已收盘 15m, 1h, 4h bar
    for k in range(-16, 0):
        insert_bar(SYM, base + k * M15, (100.0, 100.5, 99.5, 100.0), tf="15m")
    h1b = (base // H1) * H1
    for k in range(-8, 0):
        insert_bar(SYM, h1b + k * H1, (100.0, 101.0, 99.0, 100.0), tf="1h")
    h4b = (base // H4) * H4
    for k in range(-5, -2):
        insert_bar(SYM, h4b + k * H4, (100.0, 103.0, 98.5, 102.0), tf="4h")
    con.close()

    GATE.write_text(json.dumps({
        "allow_new_entries": True, "reason": "closeloop05_isolated_test",
        "gate_reason": "closeloop05_isolated_test", "set_by": "SYSTEM-CLOSELOOP-05",
        "since_ms": now_ms}, ensure_ascii=False, indent=1), encoding="utf-8")

    env = dict(ENV_BASE)
    env["EFFECTIVE_VIEW"] = str(effective_view_path)
    for k, v in env.items():
        os.environ[k] = v
    if str(BACKEND) not in sys.path:
        sys.path.insert(0, str(BACKEND))

    import live_store as L
    L.ensure_live_schema()
    L.ensure_paper_epoch_ms()
    return base


def run_automated_closeloop_group(tag: str, config_path: pathlib.Path, expected_s: float, expected_d: float) -> dict:
    """自动闭环单组测试：全流程不调推进方法，由真实常驻 worker 推进。"""
    print(f"\n=======================================================")
    print(f"[*] 启动测试组：{tag} (配置：{config_path.name}, s={expected_s}, d={expected_d})")
    print(f"=======================================================")
    base = setup_clean_env(config_path)
    # 将入场成交时间锚定在当前挂钟前 40 分钟：
    # 1. 信号确认时间为 55 分钟前；
    # 2. 挂单登记时未插入成交 bar，返回 pending_future_fill 挂单；
    # 3. 插入成交 bar (open_time = base - 40m)，滞后仅 40 分钟 < 60 分钟 (MAX_FILL_LAG_MS)，由守护进程自动撮合成交；
    # 4. 4h 止损线推进使用已收盘 4h bar；
    # 5. 退出 15m bar 在 20 分钟前开盘、5 分钟前收盘 (close_time <= base 且 open_time > entry_ts)，由守护进程自动平仓！
    t_entry_bar = base - 40 * 60 * 1000
    T_sig = t_entry_bar - 15 * 60 * 1000

    env = dict(ENV_BASE)
    env["EFFECTIVE_VIEW"] = str(config_path)

    import paper_engine as PE
    cfg = PE.approved_params()
    cfg_fp = cfg.get("config_fingerprint")

    # 1. 制造挂单窗口（撤掉成交 bar）
    del_bars_from(SYM, t_entry_bar)

    # 2. 信号到达：仅登记挂单（不推进、不成交）
    reg_res = PE.open_position("3a", SYM, signal_uid=f"sig_{tag}_01", direction="long", atr=1.0,
                               now_ms=T_sig, event_time=T_sig, available_at=T_sig,
                               requested_at=T_sig, origin="replay")
    print(f"[{t()[11:]}] 信号登记挂单结果: {reg_res}")
    assert reg_res.get("reason") == "pending_future_fill", "预期应因缺少成交 bar 登记为未来挂单"
    pending_id = reg_res.get("pending_id")

    # 验证挂单表中持久化了配置快照
    snap_p = snapshot()
    p_row = snap_p["pending"][0]
    print(f"[{t()[11:]}] 挂单持久化校验: s={p_row.get('stoploss_s')} d={p_row.get('trail_d')} fp={p_row.get('config_fingerprint')}")
    assert abs(float(p_row.get("stoploss_s")) - expected_s) < 1e-4, "挂单必须持久化配置中的 stoploss_s"
    assert abs(float(p_row.get("trail_d")) - expected_d) < 1e-4, "挂单必须持久化配置中的 trail_d"

    # 3. 启动常驻执行守护进程（作为独立系统入口运行）
    log_file = LOG_DIR / f"worker_{tag}.log"
    log_fp = open(log_file, "w", encoding="utf-8")
    worker_proc = subprocess.Popen([PY, "-u", str(BACKEND / "workers/paper_execution_worker.py"),
                                   "--arm", "3a", "--interval", "1.0"],
                                  cwd=str(BACKEND), env=env, stdout=log_fp, stderr=subprocess.STDOUT,
                                  start_new_session=True)
    print(f"[{t()[11:]}] 守护进程启动 PID={worker_proc.pid}，日志: {log_file.name}")
    time.sleep(3)

    try:
        # 4. 插入成交 bar，等待守护进程自动撮合成交
        insert_bar(SYM, t_entry_bar, (100.0, 100.5, 99.5, 100.0))
        print(f"[{t()[11:]}] 已插入成交 15m bar (open={t_entry_bar})，等待守护自动撮合...")
        time.sleep(4)

        snap_f = snapshot()
        assert len(snap_f["positions"]) == 1, "守护进程应已自动将挂单撮合成交并生成持仓"
        pos = snap_f["positions"][0]
        pos_id = pos["position_id"]
        fill = snap_f["fills"][0]
        entry_price = float(pos["entry_price"])
        initial_stop = float(pos["stop_price"])
        expected_initial_stop = PE._round_protect_price(entry_price * (1.0 - expected_s), 0.1)
        print(f"[{t()[11:]}] 自动成交成功：Fill={entry_price}, Qty={pos['qty']}, 初始止损={initial_stop} (预期={expected_initial_stop})")
        assert abs(initial_stop - expected_initial_stop) <= 0.01, f"初始止损计算不符：实际={initial_stop}, 预期={expected_initial_stop}"
        assert abs(float(pos["stoploss_s"]) - expected_s) < 1e-4, "持仓记录必须持久化 stoploss_s"
        assert abs(float(pos["trail_d"]) - expected_d) < 1e-4, "持仓记录必须持久化 trail_d"

        # 5. 驱动第 1 次 4h 止损上移（Low=107.0）
        h4b = (base // H4) * H4
        t_4h_1 = h4b - 2 * H4
        low_1 = 107.0
        insert_bar(SYM, t_4h_1, (108.0, 110.0, low_1, 109.0), tf="4h")
        cand_1 = PE._round_protect_price(low_1 * (1.0 - expected_d), 0.1)
        print(f"[{t()[11:]}] 插入第1根已收盘4h bar (open={t_4h_1}, Low={low_1}, 候选止损={cand_1})，等待守护自动上移...")
        time.sleep(4)

        snap_u1 = snapshot()
        pos_u1 = snap_u1["positions"][0]
        cur_stop_1 = float(pos_u1["stop_price"])
        print(f"[{t()[11:]}] 守护上移后止损: {cur_stop_1} (旧止损={initial_stop})")
        assert cur_stop_1 > initial_stop, f"止损应已自动上移：cur={cur_stop_1}, old={initial_stop}"
        assert abs(cur_stop_1 - cand_1) <= 0.01, f"止损上移价不符：实际={cur_stop_1}, 候选={cand_1}"

        # 6. 输入较低候选值的 4h bar（Low=102.0）—— 验证单调性（不得下移、不得重复记账）
        low_lower = 102.0
        t_4h_lower = h4b - 1 * H4
        insert_bar(SYM, t_4h_lower, (103.0, 104.0, low_lower, 103.5), tf="4h")
        cand_lower = PE._round_protect_price(low_lower * (1.0 - expected_d), 0.1)
        print(f"[{t()[11:]}] 插入较低候选 4h bar (open={t_4h_lower}, Low={low_lower}, 候选={cand_lower} < 当前={cur_stop_1})，等待守护轮询...")
        time.sleep(3)

        snap_lower = snapshot()
        pos_lower = snap_lower["positions"][0]
        cur_stop_lower = float(pos_lower["stop_price"])
        stops_lower = [s for s in snap_lower["stops"] if s["reason"] == "ft_low4h_trail"]
        print(f"[{t()[11:]}] 较低候选值测试后止损: {cur_stop_lower}, 轨迹数: {len(stops_lower)}")
        assert cur_stop_lower == cur_stop_1, "较低候选值绝不得导致止损下移"
        assert len(stops_lower) == 1, "较低候选值绝不得重复写入止损轨迹"

        # 7. 驱动第 2 次 4h 止损上移（Low=112.0）
        low_2 = 112.0
        t_4h_2 = h4b - 1 * H4
        insert_bar(SYM, t_4h_2, (113.0, 115.0, low_2, 114.0), tf="4h")
        cand_2 = PE._round_protect_price(low_2 * (1.0 - expected_d), 0.1)
        print(f"[{t()[11:]}] 更新突破 4h bar (open={t_4h_2}, Low={low_2}, 候选止损={cand_2})，等待守护自动第2次上移...")
        time.sleep(4)

        snap_u2 = snapshot()
        pos_u2 = snap_u2["positions"][0]
        cur_stop_2 = float(pos_u2["stop_price"])
        stops_u2 = [s for s in snap_u2["stops"] if s["reason"] == "ft_low4h_trail"]
        print(f"[{t()[11:]}] 第2次上移后止损: {cur_stop_2}, 轨迹总数: {len(stops_u2)}")
        assert cur_stop_2 > cur_stop_1, f"止损应触发第2次上移：cur={cur_stop_2}, prev={cur_stop_1}"
        assert abs(cur_stop_2 - cand_2) <= 0.01, f"第2次上移数值不符：实际={cur_stop_2}, 候选={cand_2}"
        assert len(stops_u2) == 2, f"预期应有2次4h上移记录，实际={len(stops_u2)}"

        # 8. 在持仓尚未退出时【重启守护进程】，核验恢复能力与零重复
        print(f"[{t()[11:]}] 正在优雅停止守护进程 PID={worker_proc.pid} 进行重启测试...")
        worker_proc.terminate()
        try:
            worker_proc.wait(timeout=5)
        except Exception:
            worker_proc.kill()
        time.sleep(1)

        snap_before_restart = snapshot()
        print(f"[{t()[11:]}] 重启前账本快照：持仓数={len(snap_before_restart['positions'])}, 成交数={len(snap_before_restart['fills'])}, 轨迹数={len(snap_before_restart['stops'])}")

        # 重新启动新的守护进程
        log_file_r = LOG_DIR / f"worker_{tag}_restart.log"
        log_fp_r = open(log_file_r, "w", encoding="utf-8")
        worker_proc_2 = subprocess.Popen([PY, "-u", str(BACKEND / "workers/paper_execution_worker.py"),
                                         "--arm", "3a", "--interval", "1.0"],
                                        cwd=str(BACKEND), env=env, stdout=log_fp_r, stderr=subprocess.STDOUT,
                                        start_new_session=True)
        print(f"[{t()[11:]}] 重启后新守护进程启动 PID={worker_proc_2.pid}")
        time.sleep(4)

        try:
            snap_after_restart = snapshot()
            print(f"[{t()[11:]}] 重启后账本快照：持仓数={len(snap_after_restart['positions'])}, 成交数={len(snap_after_restart['fills'])}, 轨迹数={len(snap_after_restart['stops'])}")
            assert len(snap_after_restart["positions"]) == len(snap_before_restart["positions"]), "重启后持仓状态丢失"
            assert len(snap_after_restart["fills"]) == len(snap_before_restart["fills"]), "重启后出现重复成交"
            assert len(snap_after_restart["stops"]) == len(snap_before_restart["stops"]), "重启后出现重复止损轨迹"
            assert snap_after_restart["positions"][0]["status"] == "open", "重启后持仓应继续保持 open"
            assert snap_after_restart["positions"][0]["stop_price"] == cur_stop_2, "重启后止损价格应完全一致"

            # 9. 插入 15m 明细跌破 bar，验证价格触及后自动触发退出与账本逐项闭合
            break_low = cur_stop_2 - 5.0
            t_15m_break = base - 20 * 60 * 1000
            insert_bar(SYM, t_15m_break, (break_low + 2.0, break_low + 3.0, break_low, break_low + 1.0), tf="15m")
            print(f"[{t()[11:]}] 插入跌破 15m bar (open={t_15m_break}, Low={break_low} <= 止损={cur_stop_2})，等待守护自动平仓退出...")
            time.sleep(4)

            snap_exit = snapshot()
            pos_exit = snap_exit["positions"][0]
            print(f"[{t()[11:]}] 平仓后持仓状态: {pos_exit['status']}, 退出原因: {pos_exit['exit_reason']}")
            assert pos_exit["status"] == "closed", "跌破止损后持仓必须自动关闭"
            assert pos_exit["exit_reason"] == "trailing_stop_loss", "退出原因必须为 trailing_stop_loss"

            # 验证订单与成交双向强关联
            orders = snap_exit["orders"]
            fills = snap_exit["fills"]
            exit_orders = [o for o in orders if o["side"] == "sell" and o["status"] == "filled"]
            exit_fills = [f for f in fills if f["action"] == "close"]
            assert len(exit_orders) == 1, "必须生成且仅生成1笔平仓卖出订单"
            assert len(exit_fills) == 1, "必须生成且仅生成1笔平仓成交记录"
            assert exit_fills[0]["order_id"] == exit_orders[0]["order_id"], "平仓成交必须强关联退出订单ID"

            # 10. 账本闭合性残差对账（精确到 1e-6）
            cash = float(snap_exit["state"]["cash"])
            entry_fee = float(pos_exit["entry_fee"])
            exit_fee = float(pos_exit["exit_fee"])
            exit_p = float(pos_exit["exit_price"])
            qty = float(pos_exit["qty"])
            funding = float(pos_exit["funding_paid"] or 0.0)
            expected_cash = 5000.0 + (exit_p - entry_price) * qty - entry_fee - exit_fee - funding
            residual = cash - expected_cash
            print(f"[{t()[11:]}] 账本对账：实际现金={cash}, 理论重算={expected_cash}, 残差={residual}")
            assert abs(residual) < 1e-6, f"账本闭合残差必须为0，实际={residual}"

        finally:
            worker_proc_2.terminate()
            try:
                worker_proc_2.wait(timeout=5)
            except Exception:
                worker_proc_2.kill()

    finally:
        worker_proc.terminate()
        try:
            worker_proc.wait(timeout=5)
        except Exception:
            worker_proc.kill()

    return {
        "tag": tag,
        "config": str(config_path.name),
        "params": {"stoploss_s": expected_s, "trail_d": expected_d},
        "initial_entry_price": entry_price,
        "initial_stop": initial_stop,
        "stops_progression": [
            {"event": "initial_stop", "stop": initial_stop},
            {"event": "trail_update_1", "bar_low": low_1, "candidate": cand_1, "effective_stop": cur_stop_1},
            {"event": "trail_lower_candidate_ignored", "bar_low": low_lower, "candidate": cand_lower, "effective_stop": cur_stop_lower},
            {"event": "trail_update_2", "bar_low": low_2, "candidate": cand_2, "effective_stop": cur_stop_2},
        ],
        "restart_consistency": {
            "positions_before": len(snap_before_restart["positions"]),
            "positions_after": len(snap_after_restart["positions"]),
            "fills_before": len(snap_before_restart["fills"]),
            "fills_after": len(snap_after_restart["fills"]),
            "stops_before": len(snap_before_restart["stops"]),
            "stops_after": len(snap_after_restart["stops"]),
            "match": True
        },
        "exit": {
            "status": pos_exit["status"],
            "reason": pos_exit["exit_reason"],
            "exit_price": exit_p,
            "entry_fee": entry_fee,
            "exit_fee": exit_fee,
            "cash_residual": residual,
            "closed": True
        }
    }


def run_guard_defenses() -> dict:
    """运行可信行情防线与外部覆盖拒绝专项测试（复用 03_guard.py 标准并验证全部 8 项）。"""
    print(f"\n=======================================================")
    print(f"[*] 运行可信行情防线与外部覆盖拦截测试 (AUDIT-04 8 项矩阵)")
    print(f"=======================================================")
    env_clean = dict(os.environ)
    env_clean.pop("EFFECTIVE_VIEW", None)
    out_script = subprocess.run([PY, "evidence/SYSTEM-CLOSELOOP-AUDIT-04/queries/03_guard.py"],
                                cwd=str(BASE), env=env_clean, capture_output=True, text=True, timeout=60)
    print(out_script.stdout)
    out_json_path = BASE / "evidence/SYSTEM-CLOSELOOP-AUDIT-04/out/03_行情防线.json"
    assert out_json_path.exists(), "防线测试产物必须生成"
    guard_data = json.loads(out_json_path.read_text(encoding="utf-8"))
    stats = guard_data.get("统计", {})
    assert stats.get("通过") == 8 and stats.get("不通过") == 0, f"8项防线测试必须全部通过，当前={stats}"
    return guard_data


def verify_production_db_unpolluted(hashes_before: dict[str, str]) -> dict:
    """验证生产环境 4 臂数据库哈希值绝对一致（0 变化）。"""
    results = {}
    for arm in ("3a", "3b", "3c", "3d"):
        p = BASE / f"数据/paper/paper_{arm}.sqlite3"
        h = hashlib.sha256(p.read_bytes()).hexdigest()
        orig = hashes_before[arm]
        match = (h == orig)
        results[arm] = {
            "path": str(p),
            "sha256_before": orig,
            "sha256_after": h,
            "match": match
        }
        assert match, f"生产数据库被篡改污染：{p} (前={orig}, 后={h})"
    return results


def main():
    print(f"=== SYSTEM-CLOSELOOP-05 自动闭环验收开始 [{t()}] ===")
    
    # 记录生产数据库初始哈希
    prod_hashes = {}
    for arm in ("3a", "3b", "3c", "3d"):
        p = BASE / f"数据/paper/paper_{arm}.sqlite3"
        prod_hashes[arm] = hashlib.sha256(p.read_bytes()).hexdigest()
        print(f"  [基线] 生产账本 paper_{arm}.sqlite3: {prod_hashes[arm][:16]}...")

    # 1. 运行第一组配置全自动测试 (s=0.05, d=0.05)
    res_g1 = run_automated_closeloop_group("GROUP1", CONFIG_EXP1, expected_s=0.05, expected_d=0.05)

    # 2. 运行第二组配置全自动测试 (s=0.03, d=0.08)
    res_g2 = run_automated_closeloop_group("GROUP2", CONFIG_EXP2, expected_s=0.03, expected_d=0.08)

    # 3. 对比两组结果，证明参数完全贯通且无硬编码
    print(f"\n=======================================================")
    print(f"[*] 两组隔离配置结果对比（证明参数真实生效无硬编码）")
    print(f"=======================================================")
    g1_stop0 = res_g1["initial_stop"]
    g2_stop0 = res_g2["initial_stop"]
    g1_stop2 = res_g1["stops_progression"][3]["effective_stop"]
    g2_stop2 = res_g2["stops_progression"][3]["effective_stop"]
    print(f"  Group 1 (s=0.05, d=0.05) -> 初始止损: {g1_stop0}, 两次上移后止损: {g1_stop2}")
    print(f"  Group 2 (s=0.03, d=0.08) -> 初始止损: {g2_stop0}, 两次上移后止损: {g2_stop2}")
    assert g1_stop0 != g2_stop0, f"不同 s 参数应产生不同初始止损 (G1={g1_stop0}, G2={g2_stop0})"
    assert g1_stop2 != g2_stop2, f"不同 d 参数应产生不同跟踪止损 (G1={g1_stop2}, G2={g2_stop2})"

    # 4. 运行防线测试（假 K 线/未来时间/外部覆盖拦截）
    guard_report = run_guard_defenses()

    # 5. 校验生产数据库未受任何修改
    prod_check = verify_production_db_unpolluted(prod_hashes)
    print(f"[*] 生产数据库零修改核验: 全部 4 臂 SHA-256 100% 保持一致！")

    # 6. 数据结果汇总落盘
    summary = {
        "suite": "SYSTEM-CLOSELOOP-05",
        "finished_at": t(),
        "group1": res_g1,
        "group2": res_g2,
        "two_groups_distinct": {
            "group1_initial_stop": g1_stop0,
            "group2_initial_stop": g2_stop0,
            "group1_final_stop": g1_stop2,
            "group2_final_stop": g2_stop2,
            "proved_no_hardcoding": True
        },
        "guard_defense_summary": guard_report.get("统计"),
        "production_safety_check": prod_check,
        "conclusion": "ALL_PASSED_READY_TO_FREEZE"
    }
    
    (DATA_DIR / "01_automated_test_results.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (DATA_DIR / "04_two_groups_params_comparison.json").write_text(
        json.dumps({
            "group1": res_g1,
            "group2": res_g2,
            "diff_proved": True
        }, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\n[+] 全流程测试已全部成功完成，数据已落盘至 {DATA_DIR}！")


if __name__ == "__main__":
    main()
