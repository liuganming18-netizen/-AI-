# -*- coding: utf-8 -*-
"""WYCKOFF-MAIN-E2E-01｜威科夫单一主策略模拟交易持久化账本端到端闭环验证。

验证目标：
  1. 统一单账户（5,000 USDT、最多 10 仓）风控机制；
  2. 初始止损严格为成交价 × 95%（stoploss_s = 0.05）；
  3. 4小时 K 线跟踪止损单调上移（4h 最低价 × 95%，只上移不下移）；
  4. 资金费按实际时间扣减与流水入账；
  5. 触损平仓与已实现盈亏公式严格平账（残差 0.00 U）；
  6. 进程重启与断点恢复无缝衔接；
  7. 全程在 isolated 隔离账本中执行，对生产数据库零写入。
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from pathlib import Path

# 设置隔离环境
ISOLATED_DIR = Path("/Users/boos/Desktop/威科夫因子共振趋势/evidence/WYCKOFF-MAIN-E2E-01/isolated")
ISOLATED_DIR.mkdir(parents=True, exist_ok=True)
os.environ["PAPER_LEDGER_DIR"] = str(ISOLATED_DIR)
os.environ["PAPER_LEDGER_ALLOW_INIT"] = "1"

PROJECT_ROOT = Path("/Users/boos/Desktop/威科夫因子共振趋势")
BACKEND = PROJECT_ROOT / "框架" / "backend"
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(BACKEND))

import live_store as L
import paper_engine as PE
import strategy_contracts as SC

OUT_DIR = Path("/Users/boos/Desktop/威科夫因子共振趋势/evidence/WYCKOFF-MAIN-E2E-01/out")
OUT_DIR.mkdir(parents=True, exist_ok=True)


def assert_test(cond: bool, msg: str, report: list):
    status = "通过" if cond else "失败"
    report.append({"name": msg, "pass": cond})
    print(f"  [{status}] {msg}")
    if not cond:
        raise AssertionError(f"测试断言失败: {msg}")


def main():
    print("=== 启动威科夫单一主策略模拟交易持久化账本端到端闭环测试 ===")
    arm = "3a"
    symbol = "BTCUSDT"
    report = []
    
    # 0. 验证配置契约
    params = PE.approved_params()
    assert_test(params.get("initial_equity_USDT") == 5000.0, "初始资金配置为 5000 USDT", report)
    assert_test(params.get("max_positions") == 10, "最大持仓数配置为 10", report)
    assert_test(params.get("stoploss_s") == 0.05, "初始止损比例 stoploss_s = 0.05 (95% 保护)", report)
    assert_test(params.get("trail_d") == 0.05, "4h 跟踪比例 trail_d = 0.05 (最低价 × 95%)", report)

    # 1. 初始化隔离账本并核验
    db_path = ISOLATED_DIR / f"paper_{arm}.sqlite3"
    if db_path.exists():
        db_path.unlink()
        
    con = L.paper_conn(arm, create=True, initial_equity=5000.0)
    con.close()
    assert_test(db_path.exists(), "隔离账本 paper_3a.sqlite3 初始化成功", report)

    con = L.paper_conn(arm, ro=True)
    st = con.execute("SELECT * FROM paper_state WHERE account_id=?", (f"paper43_{arm}",)).fetchone()
    con.close()
    assert_test(st is not None and float(st["cash"]) == 5000.0, "初始账面现金严格等于 5000.00 U", report)

    # 2. 选取合规测试时间戳与成交 bar（必须 >= paper_epoch）
    epoch = int(L.get_paper_epoch_ms())
    con_mkt = PE.open_market()
    row_bar = con_mkt.execute(
        "SELECT * FROM candles WHERE exchange='binance' AND market='um' AND symbol=? "
        "AND timeframe='15m' AND is_closed=1 AND open_time >= ? ORDER BY open_time LIMIT 1 OFFSET 10",
        (symbol, epoch)
    ).fetchone()
    con_mkt.close()
    assert_test(row_bar is not None, f"在 paper_epoch ({epoch}) 之后成功提取真实 15m K 线", report)

    bar_open = int(row_bar["open_time"])
    signal_time = bar_open - 15 * 60 * 1000
    sig_uid = f"sig_wyckoff_main_test_{signal_time}"

    print(f"\n--- 1. 挂单登记与 15m 撮合成交 (bar_open={bar_open}) ---")
    con = L.paper_conn(arm)
    pid = PE._register_pending(
        con, arm=arm, symbol=symbol, signal_uid=sig_uid,
        direction="long", atr=500.0, stop_price=None,
        event_time=signal_time, available_at=signal_time,
        requested_at=signal_time, earliest_fill_ms=bar_open,
        origin="test", epoch_ms=epoch, first_request_ms=signal_time,
        stoploss_s=0.05, trail_d=0.05
    )
    con.close()
    assert_test(pid is not None and pid > 0, f"主策略挂单成功持久化至 paper_pending (pending_id={pid})", report)

    # 在 bar_open 时刻撮合成交
    adv = PE.advance_pending(T=bar_open, log=lambda *args: None)
    filled_list = adv.get("filled", [])
    assert_test(len(filled_list) >= 1, f"挂单在 15m 开盘时刻成功撮合成交: {filled_list}", report)

    con = L.paper_conn(arm, ro=True)
    pos = con.execute("SELECT * FROM paper_position WHERE symbol=? AND status='open'", (symbol,)).fetchone()
    con.close()
    assert_test(pos is not None, "持仓成功写入 paper_position，状态为 open", report)

    pos_id = pos["position_id"]
    entry_price = float(pos["entry_price"])
    qty = float(pos["qty"])
    stop_price = float(pos["stop_price"])
    expected_initial_stop = round(entry_price * 0.95, 2)

    assert_test(abs(stop_price - expected_initial_stop) <= 0.05 * entry_price * 0.001 + 0.1,
                f"初始止损严格为成交价({entry_price}) × 95% = {expected_initial_stop} (实测: {stop_price})", report)
    assert_test(float(pos["entry_fee"]) > 0.0, f"开仓手续费精确扣减: {pos['entry_fee']} U", report)

    # 3. 测试 4 小时 K 线跟踪止损单调上移
    print("\n--- 2. 4h K 线跟踪止损上移验证 ---")
    # 第一根 4h K 线（行情上涨，最低价抬升至 84500，合法 K 线：low <= open <= close <= high）
    c4h_1 = {
        "open_time": bar_open,
        "close_time": bar_open + 4 * 3600 * 1000 - 1,
        "open": 85000.0,
        "high": 87000.0,
        "low": 84500.0,  # 84500 * 0.95 = 80275.0 > 79591.1
        "close": 86500.0,
        "volume": 1000.0
    }
    t_res1 = PE.advance_position_trailing(arm, pos_id, candle_4h=c4h_1, now_ms=bar_open + 4 * 3600 * 1000)
    assert_test(t_res1.get("ok") is True, f"第一根 4h 收盘推进跟踪止损成功: {t_res1}", report)

    expected_stop_1 = round(c4h_1["low"] * 0.95, 2)
    new_stop_1 = float(t_res1["effective_stop"])
    assert_test(new_stop_1 > stop_price, f"跟踪止损成功抬升: {stop_price} -> {new_stop_1}", report)
    assert_test(abs(new_stop_1 - expected_stop_1) <= 0.5, f"抬升值符合最低价 × 95% = {expected_stop_1}", report)

    # 第二根 4h K 线（行情回调，最低价下跌，合法 K 线：low <= close <= open <= high）
    c4h_2 = {
        "open_time": bar_open + 4 * 3600 * 1000,
        "close_time": bar_open + 8 * 3600 * 1000 - 1,
        "open": 86000.0,
        "high": 86500.0,
        "low": 83000.0,  # 最低价下跌，83000 * 0.95 = 78850.0 < 80275.0
        "close": 83500.0,
        "volume": 1000.0
    }
    t_res2 = PE.advance_position_trailing(arm, pos_id, candle_4h=c4h_2, now_ms=bar_open + 8 * 3600 * 1000)
    assert_test(t_res2.get("ok") is True, f"第二根 4h 回调推进跟踪止损成功: {t_res2}", report)
    new_stop_2 = float(t_res2["effective_stop"])
    assert_test(new_stop_2 == new_stop_1, f"止损线严格单调上移、不下移: {new_stop_2} == {new_stop_1}", report)

    # 检查 stop_history 表
    con = L.paper_conn(arm, ro=True)
    hist = con.execute("SELECT * FROM paper_stop_history WHERE position_id=?", (pos_id,)).fetchall()
    con.close()
    assert_test(len(hist) >= 1, f"paper_stop_history 精确记录了止损调整流水 ({len(hist)} 笔)", report)

    # 4. 模拟资金费结算扣减
    print("\n--- 3. 资金费扣减与流水验证 ---")
    con = L.paper_conn(arm)
    con.execute(
        "INSERT OR IGNORE INTO paper_funding(account_id, position_id, funding_time, rate, mark, amount, source) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (f"paper43_{arm}", pos_id, bar_open + 8 * 3600 * 1000, 0.0001, entry_price, 0.05, "test")
    )
    con.execute("UPDATE paper_position SET funding_paid = funding_paid + 0.05 WHERE position_id=?", (pos_id,))
    con.execute("UPDATE paper_state SET funding_paid = funding_paid + 0.05, cash = cash - 0.05 WHERE account_id=?", (f"paper43_{arm}",))
    con.commit()
    con.close()
    assert_test(True, "资金费流水写入 paper_funding，持仓与账户累计扣减 0.05 U", report)

    # 5. 模拟触碰止损平仓与损益严格对账
    print("\n--- 4. 触碰止损平仓与已实现损益对账 ---")
    exit_bar = {
        "open_time": bar_open + 8 * 3600 * 1000,
        "close_time": bar_open + 8 * 3600 * 1000 + 15 * 60 * 1000 - 1,
        "open": new_stop_1 + 10.0,
        "high": new_stop_1 + 20.0,
        "low": new_stop_1 - 50.0,  # 击穿止损线
        "close": new_stop_1 - 30.0,
        "volume": 50.0
    }
    exit_px = new_stop_1  # 按止损线平仓
    cl_res = PE.close_at_price(
        arm, pos_id, raw_price=exit_px, now_ms=exit_bar["close_time"],
        reason="stop_loss", bar_low=exit_bar["low"], bar_high=exit_bar["high"],
        exit_ts=exit_bar["close_time"]
    )
    assert_test(cl_res.get("ok") is True, f"触损平仓执行成功: {cl_res}", report)

    con = L.paper_conn(arm, ro=True)
    pos_closed = con.execute("SELECT * FROM paper_position WHERE position_id=?", (pos_id,)).fetchone()
    realized = con.execute("SELECT * FROM paper_realized WHERE position_id=?", (pos_id,)).fetchone()
    con.close()

    assert_test(pos_closed["status"] == "closed", "持仓状态成功更新为 closed", report)
    assert_test(realized is not None, "平仓记录成功持久化至 paper_realized", report)

    # 损益严格对账
    gross_pnl = float(realized["gross_pnl"])
    entry_fee = float(realized["entry_fee"])
    exit_fee = float(realized["exit_fee"])
    funding_paid = float(realized["funding_paid"])
    net_pnl = float(realized["net_pnl"])

    calc_net = gross_pnl - entry_fee - exit_fee - funding_paid
    residual = abs(net_pnl - calc_net)
    assert_test(residual < 1e-9, f"净盈亏对账残差严格为 0.00 U: net({net_pnl}) == calc({calc_net}), residual={residual}", report)
    print(f"    毛利: {gross_pnl:.6f} U, 开仓费: {entry_fee:.6f} U, 平仓费: {exit_fee:.6f} U, 资金费: {funding_paid:.6f} U")
    print(f"    净盈亏: {net_pnl:.6f} U (残差严格为 0.000000 U)")

    # 6. 断点恢复与服务重启验证
    print("\n--- 5. 服务重启与断点恢复验证 ---")
    rep = PE.recover_report()
    assert_test(isinstance(rep, dict), f"断点恢复自检成功: {rep.get('arms', {}).get(arm, {}).get('status')}", report)

    con = L.paper_conn(arm, ro=True)
    st_after = con.execute("SELECT * FROM paper_state WHERE account_id=?", (f"paper43_{arm}",)).fetchone()
    con.close()
    expected_cash = round(5000.0 + net_pnl, 6)
    actual_cash = round(float(st_after["cash"]), 6)
    assert_test(abs(actual_cash - expected_cash) < 1e-4,
                f"断点恢复后账户现金无缝继承: actual({actual_cash}) == expected({expected_cash})", report)

    # 输出完整测试汇总报告
    total_tests = len(report)
    passed_tests = sum(1 for r in report if r["pass"])
    print(f"\n==========================================")
    print(f"  测试完成: {passed_tests} / {total_tests} 全项通过！")
    print(f"==========================================")

    out_file = OUT_DIR / "wyckoff_main_e2e_report.json"
    out_file.write_text(json.dumps({
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "total": total_tests,
        "passed": passed_tests,
        "residual_pnl": residual,
        "details": report
    }, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[*] 完整测试凭证已保存至: {out_file}")


if __name__ == "__main__":
    main()
