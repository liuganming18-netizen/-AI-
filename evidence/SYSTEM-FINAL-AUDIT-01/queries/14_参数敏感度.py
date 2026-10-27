# -*- coding: utf-8 -*-
"""14 A/B 关交叉验证：配置视图里哪些字段真的进了定量计算。

为什么需要这一项
----------------
A 关要求「参数身份贯穿挂单/订单/持仓/重启」，B 关要求「从实际成交价、初始止损、
数量独立重算风险」。两关都隐含一个前提：**配置里的风险参数确实驱动了数量**。
13 号实验里我把视图 `initial_equity_USDT` 从 2000 改成 5000 口径各跑一遍，
两套配置开出的数量完全相同（0.009 / 0.006）⇒ 至少有一个字段没生效。

本探针把每个字段单独改一次，看：
  ① `approved_params()` 是否读到该字段（读到 = 配置层通过）；
  ② `equity()` 返回的初始权益是否随之变化（变化 = 引擎用了它）；
  ③ 用引擎同一条调用链（open_position L997-1018 的参数映射）算出的 qty 是否变化。

三者分离才能说清「是没读到」还是「读到了但没用」。

产物：out/14_参数敏感度.json
"""
from __future__ import annotations

import datetime as dt
import json
import os
import pathlib
import shutil
import sqlite3
import sys
import time

BASE = pathlib.Path("/Users/boos/Desktop/威科夫因子共振趋势")
HERE = BASE / "evidence/SYSTEM-FINAL-AUDIT-01"
OUT = HERE / "out"
BACKEND = BASE / "框架/backend"
ROOT = pathlib.Path("/tmp/wx_final05")
M15 = 900_000
SYM = "BTCUSDT"

if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

BASE_VIEW = {
    "schema_version": "research_policy_2.2",
    "spec_version": "F_WX_final01_sens",
    "strategy_version": "FT-LOW4H-EXP-sens",
    "model_version": "FT-LOW4H-EXP-sens",
    "purpose": "audit_sensitivity",
    "production_config": False,
    "live_trading_authorized": False,
    "research_policy_frozen": True,
    "execution": {"detail_timeframe": "15m", "signal_execution_delay_minutes": 15,
                  "fee_each_side": 0.0005, "slippage_each_side": 0.0003,
                  "signal_exit_delay_minutes": 15},
    "stops": {"rule": "FT-LOW4H", "stoploss_s": 0.05, "trail_d": 0.05,
              "uniform": "actual_entry_minus_s_and_4h_low_minus_d",
              "max_holding_hours": 240, "trailing_enabled": True},
    "risk": {"initial_equity_USDT": 5000.0,
             "risk_per_trade_equity_fraction": 0.005,
             "leverage": 1,
             "max_positions": 10,
             "single_name_notional_equity_cap": 0.10,
             "gross_long_notional_equity_cap": 0.80,
             "cash_or_free_margin_reserve_fraction": 0.20,
             "portfolio_initial_stop_risk_cap": 0.05,
             "entry_participation_previous_15m_quote_cap": 0.01,
             "maximum_account_drawdown_fraction": 0.20,
             "short_enabled": False},
}

VARIANTS = [
    ("base", {}),
    ("initial_equity=2000", {"initial_equity_USDT": 2000.0}),
    ("initial_equity=20000", {"initial_equity_USDT": 20000.0}),
    ("risk_per_trade=0.0025", {"risk_per_trade_equity_fraction": 0.0025}),
    ("single_name=0.002", {"single_name_notional_equity_cap": 0.002}),
    ("gross_long=0.002", {"gross_long_notional_equity_cap": 0.002}),
    ("participation=0.0001", {"entry_participation_previous_15m_quote_cap": 0.0001}),
    ("portfolio_cap=0.0001", {"portfolio_initial_stop_risk_cap": 0.0001}),
    ("max_positions=1", {"max_positions": 1}),
    ("cash_reserve=0.99", {"cash_or_free_margin_reserve_fraction": 0.99}),
]


def build_market(market: pathlib.Path) -> None:
    if market.exists():
        market.unlink()
    con = sqlite3.connect(market)
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
    CREATE TABLE funding_history(
      exchange TEXT NOT NULL, market TEXT NOT NULL, symbol TEXT NOT NULL,
      funding_time INTEGER NOT NULL, funding_rate TEXT, mark_price TEXT,
      rate_type TEXT, source TEXT, fetched_at INTEGER,
      PRIMARY KEY(exchange, market, symbol, funding_time));
    """)
    con.execute("CREATE VIEW candles AS SELECT v.* FROM candle_versions v "
                "JOIN candle_heads h USING(exchange, market, symbol, timeframe, open_time, source_id)")
    now = int(time.time() * 1000)
    b = (now // M15) * M15
    for k in range(-8, 0):
        ot = b + k * M15
        con.execute("INSERT INTO candle_versions VALUES('binance','um',?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)",
                    (SYM, "15m", ot, ot + M15 - 1, "100.00", "100.50", "99.50", "100.00",
                     "1000", "100000", 10, "500", "50000", 1, ot + M15 - 1))
        con.execute("INSERT INTO candle_heads VALUES('binance','um',?,?,?,?)", (SYM, "15m", ot, 1))
    con.commit()
    con.close()


def main() -> None:
    if ROOT.exists():
        shutil.rmtree(ROOT)
    ROOT.mkdir(parents=True)

    import live_store as L
    import paper_engine as PE
    from strategies.wx_execution import apply_order_rounding

    res = {"生成时刻": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
           "L_INIT_EQUITY_代码常量": L.INIT_EQUITY,
           "固定输入": {"raw_open": 100.0, "atr": 1.0, "stoploss_s": 0.05,
                     "prev_quote_volume": 100000, "价格步进": 0.1, "数量步进": 0.001,
                     "min_quantity": 0.001, "min_notional": 5.0},
           "用例": []}

    for name, override in VARIANTS:
        d = ROOT / name.replace("=", "_")
        paper = d / "paper"
        live = d / "live"
        for p in (paper, live, d / "locks"):
            p.mkdir(parents=True, exist_ok=True)
        market = d / "market.sqlite3"
        build_market(market)
        view = d / "view.json"
        content = json.loads(json.dumps(BASE_VIEW))
        content["risk"].update(override)
        view.write_text(json.dumps(content, ensure_ascii=False, indent=2), encoding="utf-8")
        (d / "gate.json").write_text(json.dumps({"allow_new_entries": True}), encoding="utf-8")

        for k, v in (("PAPER_LEDGER_DIR", str(paper)), ("PAPER_LEDGER_ALLOW_INIT", "1"),
                     ("PAPER_LOCK_DIR", str(d / "locks")), ("LIVE_DIR", str(live)),
                     ("LIVE_DB", str(live / "live_signals.sqlite3")),
                     ("MARKET_DB", str(market)), ("EFFECTIVE_VIEW", str(view)),
                     ("PAPER_GATE_FILE", str(d / "gate.json"))):
            os.environ[k] = v
        L.ensure_live_schema()
        L.ensure_paper_epoch_ms()

        p = PE.approved_params()
        con = L.paper_conn("3a")
        con_mkt = L.market_conn()
        try:
            eq = PE.equity(con, "3a", con_mkt, p)
        finally:
            con.close()
            con_mkt.close()

        limits = PE.contract_limits(SYM) or {"quantity_step": 0.001, "price_step": 0.1,
                                             "min_quantity": 0.001, "min_notional": 5.0}
        # 完全按 open_position L997-1018 的参数映射（equity 用引擎实际的 eq["equity"]）
        rr = apply_order_rounding(
            raw_open=100.0, atr=1.0, limits=limits,
            risk_per_trade_fraction=float(p["risk_per_trade_equity_fraction"]),
            equity=eq["equity"],
            stop_atr_mult=3.0,
            slippage_each_side=float(p.get("slippage_each_side") or 0.0003),
            gross_open_notional=eq["gross_notional"],
            free_cash=eq["cash"],
            leverage=float(p["leverage"]),
            cash_reserve_fraction=float(p["cash_or_free_margin_reserve_fraction"]),
            single_name_notional_cap=float(p["single_name_notional_equity_cap"]),
            gross_long_notional_cap=float(p["gross_long_notional_equity_cap"]),
            prev_quote_volume=100000,
            participation_cap=float(p["entry_participation_previous_15m_quote_cap"]),
            portfolio_risk_open=eq["open_risk_abs"],
            portfolio_risk_cap=float(p["portfolio_initial_stop_risk_cap"]),
            stoploss_s=p.get("stoploss_s"), strategy_id="FT-LOW4H", direction="long",
        )

        rec = {
            "变体": name, "覆盖字段": override,
            "① approved_params 读到的值": {
                "initial_equity_USDT": p.get("initial_equity_USDT"),
                "risk_per_trade_equity_fraction": p.get("risk_per_trade_equity_fraction"),
                "max_positions": p.get("max_positions"),
                "single_name_notional_equity_cap": p.get("single_name_notional_equity_cap"),
                "gross_long_notional_equity_cap": p.get("gross_long_notional_equity_cap"),
                "portfolio_initial_stop_risk_cap": p.get("portfolio_initial_stop_risk_cap"),
                "entry_participation_previous_15m_quote_cap":
                    p.get("entry_participation_previous_15m_quote_cap"),
                "cash_or_free_margin_reserve_fraction":
                    p.get("cash_or_free_margin_reserve_fraction"),
            },
            "② equity() 返回": {"equity": eq["equity"], "cash": eq["cash"],
                             "initial_equity": eq["initial_equity"]},
            "③ 取整结果": {"entry_price": rr["entry_price"], "stop_price": rr["stop_price"],
                       "stop_dist": rr["stop_dist"], "qty": rr["qty"],
                       "notional": rr["notional"], "planned_risk": rr["planned_risk"],
                       "reject": rr["reject"]},
        }
        res["用例"].append(rec)
        print(f"{name:26s} 视图权益={str(p.get('initial_equity_USDT')):>8}  "
              f"eq()={eq['equity']:>8}  eq初始={eq['initial_equity']:>7}  "
              f"qty={str(rr['qty']):>8}  风险={str(round(rr['planned_risk'], 4) if rr['planned_risk'] else None):>9}  "
              f"reject={rr['reject']}")

    base = res["用例"][0]["③ 取整结果"]
    def diff(name):
        u = next(x for x in res["用例"] if x["变体"] == name)
        r = u["③ 取整结果"]
        return {"qty": r["qty"], "qty变化": r["qty"] != base["qty"],
                "reject": r["reject"],
                "eq初始": u["② equity() 返回"]["initial_equity"],
                "eq初始变化": u["② equity() 返回"]["initial_equity"] != res["用例"][0]["② equity() 返回"]["initial_equity"]}

    res["判定"] = {
        "视图 initial_equity_USDT 是否进入 equity()":
            diff("initial_equity=2000")["eq初始变化"] or diff("initial_equity=20000")["eq初始变化"],
        "initial_equity 是否改变数量": diff("initial_equity=2000")["qty变化"],
        "risk_per_trade 是否改变数量": diff("risk_per_trade=0.0025")["qty变化"],
        "single_name 是否改变数量": diff("single_name=0.002")["qty变化"],
        "gross_long 是否改变数量": diff("gross_long=0.002")["qty变化"],
        "participation 是否改变数量": diff("participation=0.0001")["qty变化"],
        "portfolio_cap 是否改变数量": diff("portfolio_cap=0.0001")["qty变化"],
        "cash_reserve 是否改变数量": diff("cash_reserve=0.99")["qty变化"],
    }
    (OUT / "14_参数敏感度.json").write_text(
        json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n判定:", json.dumps(res["判定"], ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
