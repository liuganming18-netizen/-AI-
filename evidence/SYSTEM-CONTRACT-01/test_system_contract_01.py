# -*- coding: utf-8 -*-
"""SYSTEM-CONTRACT-01 统一策略与参数契约验收套件

验证内容：
1. 三条实际执行路径枚举与边界核对（Freqtrade、UI新建回测、模拟盘）
2. FT-LOW4H 配置身份建模、自检与指纹计算
3. 缺配置、坏配置、错误指纹、参数覆盖均不得开仓（新单硬拦截）
4. 两组有效配置分别完成加载、挂单持久化、重启恢复与隔离性
5. 订单全链路参数追溯性（订单表包含 stoploss_s, trail_d, config_fingerprint, config_source）
6. 维护既有持仓管理：新单因配置缺失被封锁时，已有持仓跟踪止损与退出不受影响
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = PROJECT_ROOT / "框架" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import live_store as L
import paper_engine as PE
import paper_loop as PL
import strategy_contracts as SC

TEST_DIR = Path("/tmp/wx_contract_test")
TEST_CONF_DIR = TEST_DIR / "configs"
TEST_PAPER_DIR = TEST_DIR / "paper"
TEST_LOCK_DIR = TEST_DIR / "locks"
TEST_DATA_DIR = TEST_DIR / "data"

ARM = "3a"
ACC = f"paper43_{ARM}"

# 模板配置视图
VIEW_TEMPLATE = {
    "schema_version": "research_policy_2.2",
    "spec_version": "F_WX_v2.2_test_contract",
    "strategy_version": "FT-LOW4H-EXP-V2.2",
    "model_version": "FT-LOW4H-EXP-V2.2",
    "purpose": "contract_audit_testing",
    "production_config": False,
    "live_trading_authorized": False,
    "research_policy_frozen": True,
    "execution": {
        "detail_timeframe": "15m",
        "signal_execution_delay_minutes": 15,
        "fee_each_side": 0.0005,
        "slippage_each_side": 0.0005,
        "signal_exit_delay_minutes": 15
    },
    "stops": {
        "rule": "FT-LOW4H",
        "stoploss_s": 0.05,
        "trail_d": 0.05,
        "uniform": "actual_entry_minus_s_and_4h_low_minus_d",
        "max_holding_hours": 240,
        "trailing_enabled": True
    },
    "risk": {
        "initial_equity_USDT": 5000,
        "risk_per_trade_equity_fraction": 0.005,
        "leverage": 1,
        "max_positions": 10,
        "single_name_notional_equity_cap": 0.10,
        "gross_long_notional_equity_cap": 0.80,
        "cash_or_free_margin_reserve_fraction": 0.20,
        "portfolio_initial_stop_risk_cap": 0.05,
        "entry_participation_previous_15m_quote_cap": 0.01,
        "maximum_account_drawdown_fraction": 0.20,
        "short_enabled": False
    }
}


def write_test_view(path: Path, s: float, d: float, label: str) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    d_view = json.loads(json.dumps(VIEW_TEMPLATE))
    d_view["stops"]["stoploss_s"] = float(s)
    d_view["stops"]["trail_d"] = float(d)
    d_view["spec_version"] = f"F_WX_v2.2_test_contract_{label}"
    content = json.dumps(d_view, indent=2, ensure_ascii=False)
    path.write_text(content, encoding="utf-8")
    return SC.compute_config_fingerprint(content.encode("utf-8"))[:12]


def setup_test_env():
    if TEST_DIR.exists():
        shutil.rmtree(TEST_DIR)
    TEST_DIR.mkdir(parents=True, exist_ok=True)
    TEST_CONF_DIR.mkdir(parents=True, exist_ok=True)
    TEST_PAPER_DIR.mkdir(parents=True, exist_ok=True)
    TEST_LOCK_DIR.mkdir(parents=True, exist_ok=True)
    TEST_DATA_DIR.mkdir(parents=True, exist_ok=True)

    os.environ["PAPER_LEDGER_DIR"] = str(TEST_PAPER_DIR)
    os.environ["PAPER_LOCK_DIR"] = str(TEST_LOCK_DIR)
    os.environ["PAPER_LEDGER_ALLOW_INIT"] = "1"
    os.environ["MARKET_DB_DIR"] = str(TEST_DATA_DIR)

    # 设置测试隔离新开仓闸门（允许测试执行开仓与成交）
    test_gate_path = TEST_DIR / "paper_test_gate.json"
    test_gate_path.write_text(json.dumps({
        "allow_new_entries": True,
        "gate_reason": "test_contract_allowed",
        "set_by": "test_suite"
    }), encoding="utf-8")
    os.environ["PAPER_GATE_FILE"] = str(test_gate_path)

    # 建立隔离市场行情库
    mkt_path = TEST_DATA_DIR / "market.sqlite3"
    os.environ["MARKET_DB"] = str(mkt_path)
    con_m = sqlite3.connect(mkt_path)
    con_m.execute("""
    CREATE TABLE IF NOT EXISTS candles(
        exchange TEXT NOT NULL DEFAULT 'binance',
        market TEXT NOT NULL DEFAULT 'um',
        symbol TEXT NOT NULL,
        timeframe TEXT NOT NULL,
        open_time INTEGER NOT NULL,
        close_time INTEGER NOT NULL,
        open REAL NOT NULL,
        high REAL NOT NULL,
        low REAL NOT NULL,
        close REAL NOT NULL,
        volume REAL NOT NULL DEFAULT 1000.0,
        quote_volume REAL NOT NULL DEFAULT 100000.0,
        is_closed INTEGER NOT NULL DEFAULT 1,
        PRIMARY KEY(exchange, market, symbol, timeframe, open_time)
    )
    """)
    con_m.execute("""
    CREATE TABLE IF NOT EXISTS candle_versions(
        symbol TEXT, timeframe TEXT, open_time INTEGER, close_time INTEGER,
        open REAL, high REAL, low REAL, close REAL, volume REAL, quote_volume REAL,
        available_at INTEGER, PRIMARY KEY(symbol, timeframe, open_time)
    )
    """)
    con_m.execute("""
    CREATE TABLE IF NOT EXISTS funding_history(
        exchange TEXT DEFAULT 'binance',
        market TEXT DEFAULT 'um',
        symbol TEXT NOT NULL,
        rate_type TEXT NOT NULL DEFAULT 'settled',
        funding_time INTEGER NOT NULL,
        funding_rate REAL NOT NULL,
        mark_price REAL NOT NULL,
        PRIMARY KEY(exchange, market, symbol, rate_type, funding_time)
    )
    """)
    con_m.commit()
    con_m.close()


def insert_candle(sym: str, tf: str, op_ms: int, cl_ms: int, op: float, hi: float, lo: float, cl: float, vol: float = 1000.0, qv: float = 100000.0):
    mkt_path = TEST_DATA_DIR / "market.sqlite3"
    con_m = sqlite3.connect(mkt_path)
    con_m.execute(
        "INSERT OR REPLACE INTO candles(exchange,market,symbol,timeframe,open_time,close_time,open,high,low,close,volume,quote_volume,is_closed) "
        "VALUES('binance','um',?,?,?,?,?,?,?,?,?,?,1)",
        (sym, tf, op_ms, cl_ms, op, hi, lo, cl, vol, qv)
    )
    con_m.execute(
        "INSERT OR REPLACE INTO candle_versions(symbol,timeframe,open_time,close_time,open,high,low,close,volume,quote_volume,available_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (sym, tf, op_ms, cl_ms, op, hi, lo, cl, vol, qv, cl_ms)
    )
    con_m.commit()
    con_m.close()


def main():
    setup_test_env()
    results = {}
    print("=" * 70)
    print("开始执行工单1 SYSTEM-CONTRACT-01 统一策略与参数契约综合验收")
    print("=" * 70)

    # ── 1. 三条实际执行路径核对 ────────────────────────────────────────────────
    paths = SC.get_execution_paths()
    expected_keys = {"FT_144_RESEARCH", "UI_LAB_BACKTEST", "PAPER_TRADING"}
    assert set(paths.keys()) == expected_keys, f"执行路径集合不符: {set(paths.keys())}"
    for pk, p_info in paths.items():
        assert p_info["unifiable"] is False, f"路径 {pk} 必须标明不可统一"
        assert p_info["entrypoint"], f"路径 {pk} 缺入口"
        assert p_info["executor"], f"路径 {pk} 缺执行器"
        assert p_info["parameter_source"], f"路径 {pk} 缺参数来源"
        assert p_info["fee_model"], f"路径 {pk} 缺费用口径"
    results["1_三条执行路径"] = {
        "ok": True,
        "paths": paths,
        "summary": "144组Freqtrade研究、UI新建回测、模拟盘各自具备独立入口、策略、执行器、参数来源、数据源及费用口径，明确标识不可统一。"
    }
    print("【通过】1. 三条执行路径元数据与边界隔离核对通过")

    # ── 2. FT-LOW4H 配置身份对象测试 ──────────────────────────────────────────
    conf_a_path = TEST_CONF_DIR / "view_a.json"
    fp_a = write_test_view(conf_a_path, 0.05, 0.05, "A")
    ident_a, p_a, err_a = SC.load_ft_low4h_config(conf_a_path)
    assert ident_a is not None and err_a is None, f"配置A加载失败: {err_a}"
    assert ident_a.stoploss_s == 0.05 and ident_a.trail_d == 0.05
    assert ident_a.config_fingerprint == fp_a
    assert ident_a.risk_per_trade_equity_fraction == 0.005
    assert ident_a.fee_each_side == 0.0005
    assert ident_a.detail_timeframe == "15m"
    assert ident_a.tracking_timeframe == "4h"

    # 检验越界配置自检拦截
    bad_ident = SC.FtLow4hConfigIdentity(
        strategy_id="FT-LOW4H", strategy_version="FT-LOW4H-EXP-V2.2", rule="FT-LOW4H",
        stoploss_s=0.99, trail_d=0.05, risk_per_trade_equity_fraction=0.005,
        portfolio_initial_stop_risk_cap=0.05, fee_each_side=0.0005, slippage_each_side=0.0005,
        detail_timeframe="15m", tracking_timeframe="4h", max_holding_hours=240,
        config_fingerprint="abcdef123456", config_path="test", spec_version="v2.2"
    )
    val_errs = bad_ident.validate()
    assert len(val_errs) > 0 and "stoploss_s 越界" in val_errs[0], f"越界未拦截: {val_errs}"
    results["2_配置身份建模"] = {
        "ok": True,
        "identity_sample": ident_a.to_dict(),
        "validation_test": "越界数值成功拦截"
    }
    print("【通过】2. FT-LOW4H 配置身份建模、指纹计算与合规校验通过")

    # ── 3. 缺配置、坏配置、错误指纹、参数覆盖均不得开仓 ────────────────────────
    # 场景 3.1：缺配置
    missing_path = TEST_CONF_DIR / "non_existent_view.json"
    os.environ["EFFECTIVE_VIEW"] = str(missing_path)
    p_missing = PE.approved_params()
    assert p_missing.get("_config_valid") is False, "缺配置未标记 _config_valid=False"
    assert p_missing.get("stoploss_s") is None, "缺配置不应有 stoploss_s 默认兜底"

    res_open_missing = PE.open_position(ARM, "BTCUSDT", signal_uid="sig_test_miss", origin="test", now_ms=1700000000000)
    assert res_open_missing.get("ok") is False, "缺配置竟然开仓成功！"
    assert "rejected_by_config_contract" in res_open_missing.get("reason", "") or "missing_configuration" in res_open_missing.get("reason", "")

    # 验证缺配置登记挂单同样被拒
    con_p = L.paper_conn(ARM, ro=False)
    try:
        PE._register_pending(
            con_p, arm=ARM, symbol="BTCUSDT", signal_uid="sig_pend_miss", direction="long",
            atr=100.0, stop_price=None, event_time=1700000000000, available_at=1700000000000,
            requested_at=1700000000000, earliest_fill_ms=1700000900000, origin="test"
        )
        assert False, "缺配置登记挂单应当抛出异常阻止，却通过了！"
    except ValueError as exc:
        assert "rejected_by_config_contract" in str(exc)
    finally:
        con_p.close()

    # 场景 3.2：坏配置（缺少必需参数 stoploss_s）
    bad_conf_path = TEST_CONF_DIR / "corrupted_view.json"
    bad_data = json.loads(json.dumps(VIEW_TEMPLATE))
    del bad_data["stops"]["stoploss_s"]
    bad_conf_path.write_text(json.dumps(bad_data), encoding="utf-8")
    os.environ["EFFECTIVE_VIEW"] = str(bad_conf_path)
    res_open_bad = PE.open_position(ARM, "BTCUSDT", signal_uid="sig_test_bad", origin="test", now_ms=1700000000000)
    assert res_open_bad.get("ok") is False, "坏配置竟然开仓成功！"
    assert "missing_required_parameters" in str(res_open_bad.get("reason"))

    # 场景 3.3：错误指纹拦截
    os.environ["EFFECTIVE_VIEW"] = str(conf_a_path) # 恢复为有效配置A
    res_wrong_fp = PE.open_position(
        ARM, "BTCUSDT", signal_uid="sig_wrong_fp", origin="test",
        now_ms=1700000000000, config_fingerprint="deadbeef9999"
    )
    assert res_wrong_fp.get("ok") is False, "错误指纹开仓竟然放行！"
    assert "config_fingerprint_mismatch" in str(res_wrong_fp.get("reason"))

    # 场景 3.4：请求体参数覆盖拦截
    res_override = PE.open_position(
        ARM, "BTCUSDT", signal_uid="sig_override", origin="test",
        now_ms=1700000000000, stoploss_s=0.20 # 试图把 0.05 覆盖成 0.20
    )
    assert res_override.get("ok") is False, "请求体覆盖配置竟然放行！"
    assert "external_param_override_rejected" in str(res_override.get("reason"))

    # 场景 3.5：守护进程在缺配置时启动自检报错
    os.environ["EFFECTIVE_VIEW"] = str(missing_path)
    daemon_arm = PL.PaperExecutionDaemon(arm=ARM)
    try:
        daemon_arm.run_forever()
        assert False, "缺配置守护进程应当在启动自检时抛出 RuntimeError，却成功运行了！"
    except RuntimeError as exc:
        assert "startup_config_check_failed" in str(exc)
        st = PL.get_daemon_status(ARM)
        assert st["status"] == "failed_config_check", f"守护状态落盘应为 failed_config_check，实测 {st['status']}"

    # 验证测试库没有残留任何非预期持仓或挂单
    con_check = L.paper_conn(ARM, ro=True)
    n_pos = con_check.execute("SELECT COUNT(*) FROM paper_position WHERE account_id=?", (ACC,)).fetchone()[0]
    n_pend = con_check.execute("SELECT COUNT(*) FROM paper_pending WHERE account_id=?", (ACC,)).fetchone()[0]
    con_check.close()
    assert n_pos == 0 and n_pend == 0, f"坏配置测试后不应有开仓或挂单，实际 pos={n_pos}, pend={n_pend}"

    results["3_异常防御硬闸门"] = {
        "ok": True,
        "missing_config_rejected": res_open_missing.get("reason"),
        "bad_config_rejected": res_open_bad.get("reason"),
        "wrong_fingerprint_rejected": res_wrong_fp.get("reason"),
        "param_override_rejected": res_override.get("reason"),
        "daemon_startup_check": "RuntimeError 阻断且状态记录 failed_config_check",
        "ledger_records": {"positions": n_pos, "pending": n_pend}
    }
    print("【通过】3. 缺配置、坏配置、错误指纹、参数覆盖与守护自检全部严格阻断")

    # ── 4. 两组有效配置分别完成加载、挂单持久化与重启恢复 ──────────────────────
    # 准备市场 K 线数据（时间戳须晚于 PAPER_EPOCH_MS 1_791_939_600_000）
    T0 = 1792000000000
    T_prev = T0 - 15 * 60 * 1000
    T_entry = T0 + 15 * 60 * 1000  # +15m
    insert_candle("BTCUSDT", "15m", T_prev, T0, 99.0, 100.0, 98.5, 99.8)
    insert_candle("BTCUSDT", "15m", T0, T_entry, 100.0, 101.0, 99.5, 100.5)
    insert_candle("BTCUSDT", "15m", T_entry, T_entry + 15 * 60 * 1000, 100.0, 102.0, 99.0, 101.0)
    insert_candle("ETHUSDT", "15m", T_prev, T0, 1990.0, 2000.0, 1980.0, 1995.0)
    insert_candle("ETHUSDT", "15m", T0, T_entry, 2000.0, 2010.0, 1990.0, 2005.0)
    insert_candle("ETHUSDT", "15m", T_entry, T_entry + 15 * 60 * 1000, 2000.0, 2020.0, 1980.0, 2010.0)

    # 4.1 测试配置 A (s=0.05, d=0.05)
    os.environ["EFFECTIVE_VIEW"] = str(conf_a_path)
    con_a = L.paper_conn(ARM, ro=False)
    pend_id_a = PE._register_pending(
        con_a, arm=ARM, symbol="BTCUSDT", signal_uid="sig_A_001", direction="long",
        atr=5.0, stop_price=None, event_time=T0, available_at=T0, requested_at=T0,
        earliest_fill_ms=T_entry, origin="test"
    )
    con_a.close()

    # 模拟服务重启：关闭所有连接并重新读取挂单
    time.sleep(0.05)
    con_a_verify = L.paper_conn(ARM, ro=True)
    pend_row_a = dict(con_a_verify.execute("SELECT * FROM paper_pending WHERE pending_id=?", (pend_id_a,)).fetchone())
    con_a_verify.close()
    assert pend_row_a["stoploss_s"] == 0.05, f"挂单A s 值不符: {pend_row_a['stoploss_s']}"
    assert pend_row_a["trail_d"] == 0.05, f"挂单A d 值不符: {pend_row_a['trail_d']}"
    assert pend_row_a["config_fingerprint"] == fp_a, f"挂单A 指纹不符: {pend_row_a['config_fingerprint']}"

    # 推进挂单 A 成交
    adv_a = PE.advance_pending(T=T_entry + 1000, log=lambda *args: None)
    assert len(adv_a["filled"]) == 1, f"挂单A未能成交: {adv_a}"
    fill_a = adv_a["filled"][0]
    pos_id_a = fill_a["position_id"]
    order_id_a = fill_a["order_id"]
    # 理论初始止损 = 成交价 × (1 - 0.05) 并按 price_step 向下取整
    expected_stop_a = PE._round_protect_price(fill_a["entry_price"] * (1.0 - 0.05), 0.1)
    assert abs(fill_a["stop_price"] - expected_stop_a) <= 1e-4, f"配置A初始止损计算不符: 实测={fill_a['stop_price']}, 期望={expected_stop_a}"

    # 4.2 测试配置 B (s=0.08, d=0.12)
    conf_b_path = TEST_CONF_DIR / "view_b.json"
    fp_b = write_test_view(conf_b_path, 0.08, 0.12, "B")
    os.environ["EFFECTIVE_VIEW"] = str(conf_b_path)

    con_b = L.paper_conn(ARM, ro=False)
    pend_id_b = PE._register_pending(
        con_b, arm=ARM, symbol="ETHUSDT", signal_uid="sig_B_001", direction="long",
        atr=20.0, stop_price=None, event_time=T0, available_at=T0, requested_at=T0,
        earliest_fill_ms=T_entry, origin="test"
    )
    con_b.close()

    # 模拟服务重启并恢复
    time.sleep(0.05)
    con_b_verify = L.paper_conn(ARM, ro=True)
    pend_row_b = dict(con_b_verify.execute("SELECT * FROM paper_pending WHERE pending_id=?", (pend_id_b,)).fetchone())
    con_b_verify.close()
    assert pend_row_b["stoploss_s"] == 0.08, f"挂单B s 值不符: {pend_row_b['stoploss_s']}"
    assert pend_row_b["trail_d"] == 0.12, f"挂单B d 值不符: {pend_row_b['trail_d']}"
    assert pend_row_b["config_fingerprint"] == fp_b, f"挂单B 指纹不符: {pend_row_b['config_fingerprint']}"

    # 推进挂单 B 成交
    adv_b = PE.advance_pending(T=T_entry + 1000, log=lambda *args: None)
    assert len(adv_b["filled"]) == 1, f"挂单B未能成交: {adv_b}"
    fill_b = adv_b["filled"][0]
    pos_id_b = fill_b["position_id"]
    order_id_b = fill_b["order_id"]
    expected_stop_b = PE._round_protect_price(fill_b["entry_price"] * (1.0 - 0.08), 0.01)
    assert abs(fill_b["stop_price"] - expected_stop_b) <= 1e-4, f"配置B初始止损计算不符: 实测={fill_b['stop_price']}, 期望={expected_stop_b}"

    # 4.3 配置变更隔离测试：环境切回配置 A，但测试持仓 B 是否依然按照持久化的 d=0.12 执行跟踪
    os.environ["EFFECTIVE_VIEW"] = str(conf_a_path) # 当前环境为 A (d=0.05)
    T_4h_close = T0 + 4 * 3600 * 1000
    c4h_b = {
        "symbol": "ETHUSDT", "open_time": T0, "close_time": T_4h_close,
        "open": 2000.0, "high": 2100.0, "low": 1950.0, "close": 2080.0,
        "available_at": T_4h_close
    }
    adv_trail_b = PE.advance_position_trailing(ARM, pos_id_b, candle_4h=c4h_b, now_ms=T_4h_close + 1000)
    assert adv_trail_b.get("ok"), f"持仓B推进失败: {adv_trail_b}"
    # 持仓 B 绑定的 d=0.12，4h low=1950.0 ⇒ 候选止损 = 1950.0 * (1 - 0.12) = 1716.0
    # 若误用当前环境 A 的 d=0.05，候选止损会是 1950.0 * (1 - 0.05) = 1852.5
    cand_stop_b = adv_trail_b["candidate_stop"]
    expected_cand_b = round(1950.0 * (1.0 - 0.12), 2)
    assert abs(cand_stop_b - expected_cand_b) <= 0.05, f"持仓B未能按固化的 d=0.12 推进: 实测={cand_stop_b}, 期望={expected_cand_b}"

    results["4_双配置持久化与重启隔离"] = {
        "ok": True,
        "config_A": {"s": 0.05, "d": 0.05, "fp": fp_a, "init_stop": fill_a["stop_price"], "expected_stop": expected_stop_a},
        "config_B": {"s": 0.08, "d": 0.12, "fp": fp_b, "init_stop": fill_b["stop_price"], "expected_stop": expected_stop_b},
        "isolation_test": f"环境切为A后持仓B依然沿用持久化的 d=0.12 推进（候选止损={cand_stop_b}）"
    }
    print("【通过】4. 两组有效配置分别完成挂单持久化、重启恢复与配置隔离")

    # ── 5. 参数来源从订单到配置文件及版本追溯性 ────────────────────────────────
    con_ord = L.paper_conn(ARM, ro=True)
    orders = [dict(r) for r in con_ord.execute("SELECT * FROM paper_order WHERE account_id=? AND status='filled'", (ACC,)).fetchall()]
    con_ord.close()
    assert len(orders) >= 2, f"至少应有2笔订单，实有 {len(orders)}"

    trace_records = []
    for ord_row in orders:
        assert ord_row["stoploss_s"] is not None, f"订单 {ord_row['order_id']} 缺 stoploss_s 列"
        assert ord_row["trail_d"] is not None, f"订单 {ord_row['order_id']} 缺 trail_d 列"
        assert ord_row["config_fingerprint"], f"订单 {ord_row['order_id']} 缺 config_fingerprint 列"
        assert ord_row["model_version"], f"订单 {ord_row['order_id']} 缺 model_version 列"
        trace_records.append({
            "order_id": ord_row["order_id"],
            "symbol": ord_row["symbol"],
            "stoploss_s": ord_row["stoploss_s"],
            "trail_d": ord_row["trail_d"],
            "config_fingerprint": ord_row["config_fingerprint"],
            "config_source": ord_row.get("config_source"),
            "model_version": ord_row["model_version"]
        })
    results["5_订单参数全链路追溯"] = {
        "ok": True,
        "traced_orders": trace_records,
        "conclusion": "订单直接包含 stoploss_s, trail_d, config_fingerprint, model_version，可追溯至配置文件与规则版本。"
    }
    print("【通过】5. 订单全链路参数与配置来源追溯核验通过")

    # ── 6. 维护既有持仓管理：新单禁止不影响已有持仓退出 ───────────────────────
    # 破坏全局配置：设置为不存在的视图
    os.environ["EFFECTIVE_VIEW"] = str(missing_path)

    # 尝试开新单：验证新单被彻底封锁
    res_new_block = PE.open_position(ARM, "SOLUSDT", signal_uid="sig_sol_block", origin="test", now_ms=T_4h_close + 2000)
    assert res_new_block.get("ok") is False, "新单在配置损坏时未被封锁！"

    # 对已有持仓 A (BTCUSDT) 执行退出推进：制造一根击穿止损的 K 线
    # 入场价 ~100.0，初始止损 95.0
    c4h_break = {
        "symbol": "BTCUSDT", "open_time": T0, "close_time": T_4h_close,
        "open": 98.0, "high": 99.0, "low": 94.0, # 击穿 95.0
        "close": 94.5, "available_at": T_4h_close
    }
    adv_exit = PE.advance_position_trailing(ARM, pos_id_a, candle_4h=c4h_break, now_ms=T_4h_close + 2000)
    assert adv_exit.get("ok"), f"已有持仓退出推进失败: {adv_exit}"
    assert adv_exit.get("exit_triggered"), "击穿止损未能触发平仓！"
    exit_res = adv_exit.get("exit_result")
    assert exit_res and exit_res.get("ok"), f"平仓执行失败: {exit_res}"

    # 检查持仓已关闭，且平仓订单带有配置身份
    con_pos_chk = L.paper_conn(ARM, ro=True)
    pos_a_after = dict(con_pos_chk.execute("SELECT * FROM paper_position WHERE position_id=?", (pos_id_a,)).fetchone())
    exit_order = dict(con_pos_chk.execute("SELECT * FROM paper_order WHERE order_id=?", (exit_res["order_id"],)).fetchone())
    con_pos_chk.close()
    assert pos_a_after["status"] == "closed", f"持仓A状态应为 closed，实际: {pos_a_after['status']}"
    assert exit_order["side"] == "sell"
    assert exit_order["stoploss_s"] == 0.05
    assert exit_order["trail_d"] == 0.05
    assert exit_order["config_fingerprint"] == fp_a

    results["6_既有持仓退出保护"] = {
        "ok": True,
        "new_order_blocked_when_config_lost": res_new_block.get("reason"),
        "held_position_exit_completed": True,
        "exit_price": exit_res["exit_price"],
        "exit_order_id": exit_res["order_id"],
        "exit_order_stoploss_s": exit_order["stoploss_s"],
        "exit_order_trail_d": exit_order["trail_d"],
        "exit_order_fingerprint": exit_order["config_fingerprint"],
        "conclusion": "在配置缺失、新单完全封锁的情况下，已有持仓依然依赖自身绑定的配置身份成功完成跟踪与退出，平仓订单完整记录配置身份。"
    }
    print("【通过】6. 既有持仓退出保护核验通过（新单禁止不影响已有持仓平仓退出）")

    # ── 汇总落盘交付物 ────────────────────────────────────────────────────────
    evidence_dir = PROJECT_ROOT / "evidence" / "SYSTEM-CONTRACT-01"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    out_json = evidence_dir / "audit_contract_verification.json"
    out_json.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n验收结果成功保存至: {out_json}")
    print("=" * 70)
    print("工单1 SYSTEM-CONTRACT-01 全部 6 项验收点 100% 达成！")
    print("=" * 70)


if __name__ == "__main__":
    main()
