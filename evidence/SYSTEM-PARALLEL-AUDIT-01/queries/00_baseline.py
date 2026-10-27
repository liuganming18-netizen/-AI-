# -*- coding: utf-8 -*-
"""00 风险定量基线冻结（SYSTEM-PARALLEL-AUDIT-01）。

目的
----
11:49 我已在 FINAL-01 里确认「风险定量分叉已消除」（固定价 + 固定 s 只改 ATR ⇒
止损与数量都不变）。后续 A 任务（本金与风险参数真实生效）会改 `live_store` /
`paper_engine` / `wx_execution`，**B 关已通过的结论只对当前 SHA 有效**。

本脚本把「已通过的定量行为」冻成一个可机械复跑的基准：
  · 纯函数 `apply_order_rounding` 在 4 档 s × 4 档 ATR × 2 档 equity 下的输出；
  · 关键常量（`INIT_EQUITY`、`FALLBACK` 里的 `initial_equity_USDT`）；
  · 冻结视图（exp1/exp2）下 `approved_params()` 的返回值；
  · 相关文件的 SHA-256。

配套 `01_regress.py` 在代码变动后重跑同一组输入，逐字段比对 ⇒ 只作废真正受影响的项。

产物：out/00_基线.json
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import pathlib
import shutil
import sqlite3
import sys
import time

BASE = pathlib.Path("/Users/boos/Desktop/威科夫因子共振趋势")
HERE = BASE / "evidence/SYSTEM-PARALLEL-AUDIT-01"
OUT = HERE / "out"
OUT.mkdir(parents=True, exist_ok=True)
BACKEND = BASE / "框架/backend"
ROOT = pathlib.Path("/tmp/wx_parallel")

M15 = 900_000
SYM = "BTCUSDT"

FILES = [
    "框架/backend/live_store.py",
    "框架/backend/paper_engine.py",
    "框架/backend/strategies/wx_execution.py",
    "框架/backend/strategy_contracts.py",
    "框架/backend/unified_runtime.py",
    "框架/backend/live_api.py",
    "框架/backend/paper_loop.py",
    "框架/backend/api/routes.py",
]

# 冻结规则（FT-VALIDATION-02 认定，**已授权，不再作为用户待选项**）
FROZEN = {
    "atr_warmup_bars": 200,
    "initial_stop_buffer_atr": 0.1,
    "risk_budget_per_trade": 0.005,
    "initial_equity": 2000,
    "max_active_positions": 3,
    "max_total_risk_budget": 0.015,
    "max_notional_per_position": 0.30,
    "max_total_notional": 0.90,
    "trailing_trigger_r": 2.0,
    "trailing_lookback_4h_bars": 10,
    "max_holding_days": 10,
    "taker_fee_bps": 5.0,
    "slippage_bps": 3.0,
    "max_participation_of_prev_15m_quote": 0.001,
}

S_GRID = [0.03, 0.05, 0.08, 0.20]
ATR_GRID = [0.5, 1.0, 2.0, 5.0]
EQ_GRID = [2000.0, 5000.0]

LIMITS = {"quantity_step": 0.001, "price_step": 0.1, "min_quantity": 0.001,
          "min_notional": 5.0, "max_quantity": 1000.0}


def sha_of(rel: str) -> dict:
    p = BASE / rel
    b = p.read_bytes()
    return {"sha256": hashlib.sha256(b).hexdigest(), "bytes": len(b),
            "mtime": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(p.stat().st_mtime))}


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
    (ROOT / "paper").mkdir(parents=True)
    (ROOT / "live").mkdir(parents=True)
    (ROOT / "locks").mkdir(parents=True)
    market = ROOT / "market.sqlite3"
    build_market(market)
    (ROOT / "gate.json").write_text(json.dumps({"allow_new_entries": True}), encoding="utf-8")
    for k, v in (("PAPER_LEDGER_DIR", str(ROOT / "paper")), ("PAPER_LEDGER_ALLOW_INIT", "1"),
                 ("PAPER_LOCK_DIR", str(ROOT / "locks")), ("LIVE_DIR", str(ROOT / "live")),
                 ("LIVE_DB", str(ROOT / "live" / "live_signals.sqlite3")),
                 ("MARKET_DB", str(market)), ("PAPER_GATE_FILE", str(ROOT / "gate.json"))):
        os.environ[k] = v
    if str(BACKEND) not in sys.path:
        sys.path.insert(0, str(BACKEND))

    import live_store as L
    import paper_engine as PE
    from strategies.wx_execution import apply_order_rounding
    L.ensure_live_schema()
    L.ensure_paper_epoch_ms()

    res = {
        "生成时刻": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "用途": "风险定量基线：A 任务改动后用它做受影响回归，避免全量重审",
        "冻结规则_已授权": FROZEN,
        "冻结规则来源": "FT-VALIDATION-02 认定；本轮按既定值执行，不再列为用户待选项",
        "文件SHA": {f: sha_of(f) for f in FILES},
        "关键常量": {
            "L.INIT_EQUITY": L.INIT_EQUITY,
            "FALLBACK.initial_equity_USDT": PE.FALLBACK.get("initial_equity_USDT"),
            "FALLBACK.risk_per_trade_equity_fraction":
                PE.FALLBACK.get("risk_per_trade_equity_fraction"),
            "FALLBACK.max_positions": PE.FALLBACK.get("max_positions"),
            "冻结_initial_equity": FROZEN["initial_equity"],
            "常量与冻结一致": abs(float(L.INIT_EQUITY) - FROZEN["initial_equity"]) < 1e-9,
        },
        "定量矩阵": [],
        "判据": {
            "B关已通过的定义": "固定 raw_open 与 stoploss_s，只改 ATR ⇒ stop_price/stop_dist/qty/planned_risk 全部不变",
            "回归失败判据": "同一组输入下任一字段与基线不同",
            "A任务通过判据": "equity() 随视图 initial_equity_USDT 变化，且 2000 时数量按 10 USDT 预算算",
        },
    }

    # 定量矩阵：s × atr × equity（固定 raw_open=100）
    for s in S_GRID:
        for atr in ATR_GRID:
            for eq in EQ_GRID:
                r = apply_order_rounding(
                    raw_open=100.0, atr=atr, limits=LIMITS,
                    risk_per_trade_fraction=FROZEN["risk_budget_per_trade"],
                    equity=eq, stop_atr_mult=3.0,
                    slippage_each_side=FROZEN["slippage_bps"] / 10000.0,
                    stoploss_s=s, strategy_id="FT-LOW4H", direction="long")
                budget = eq * FROZEN["risk_budget_per_trade"]
                res["定量矩阵"].append({
                    "s": s, "atr": atr, "equity": eq,
                    "entry": r["entry_price"], "stop": r["stop_price"],
                    "stop_dist": r["stop_dist"], "qty": r["qty"],
                    "planned_risk": r["planned_risk"], "budget": budget,
                    "风险比预算": (r["planned_risk"] / budget) if r["planned_risk"] else None,
                    "reject": r["reject"]})

    # 分叉判据：同一 s 下，四档 ATR 的 (stop, qty) 必须唯一
    fork = {}
    for s in S_GRID:
        for eq in EQ_GRID:
            rows = [x for x in res["定量矩阵"] if x["s"] == s and x["equity"] == eq]
            fork[f"s={s},eq={eq}"] = {
                "止损唯一": len({x["stop"] for x in rows}) == 1,
                "数量唯一": len({x["qty"] for x in rows}) == 1,
                "止损": sorted({x["stop"] for x in rows}),
                "数量": sorted({x["qty"] for x in rows}),
            }
    res["分叉判据"] = fork

    # 冻结视图下 approved_params()
    for tag, rel in (("exp1", "evidence/SYSTEM-CLOSELOOP-05/config/exp1_config.json"),
                     ("exp2", "evidence/SYSTEM-CLOSELOOP-05/config/exp2_config.json")):
        p = BASE / rel
        if not p.exists():
            res.setdefault("冻结视图", {})[tag] = {"存在": False}
            continue
        os.environ["EFFECTIVE_VIEW"] = str(p)
        ap = PE.approved_params()
        res.setdefault("冻结视图", {})[tag] = {
            "路径": rel, "_config_valid": ap.get("_config_valid"),
            "stoploss_s": ap.get("stoploss_s"), "trail_d": ap.get("trail_d"),
            "initial_equity_USDT": ap.get("initial_equity_USDT"),
            "risk_per_trade_equity_fraction": ap.get("risk_per_trade_equity_fraction"),
            "max_positions": ap.get("max_positions"),
            "single_name_notional_equity_cap": ap.get("single_name_notional_equity_cap"),
            "gross_long_notional_equity_cap": ap.get("gross_long_notional_equity_cap"),
            "portfolio_initial_stop_risk_cap": ap.get("portfolio_initial_stop_risk_cap"),
            "entry_participation_previous_15m_quote_cap":
                ap.get("entry_participation_previous_15m_quote_cap"),
            "slippage_each_side": ap.get("slippage_each_side"),
            "config_fingerprint": ap.get("config_fingerprint"),
        }

    (OUT / "00_基线.json").write_text(json.dumps(res, ensure_ascii=False, indent=1),
                                    encoding="utf-8")

    print(f"文件 SHA 已采 {len(res['文件SHA'])} 个")
    print(f"关键常量：INIT_EQUITY={res['关键常量']['L.INIT_EQUITY']}  "
          f"冻结={res['关键常量']['冻结_initial_equity']}  "
          f"一致={res['关键常量']['常量与冻结一致']}")
    print(f"定量矩阵 {len(res['定量矩阵'])} 组")
    print("\n分叉判据（每组四档 ATR 应完全一致）：")
    for k, v in fork.items():
        print(f"  {k:22s} 止损唯一={v['止损唯一']} 数量唯一={v['数量唯一']} "
              f"止损={v['止损']} 数量={v['数量']}")
    print("\n冻结视图:")
    for k, v in res.get("冻结视图", {}).items():
        print(f"  {k}: valid={v.get('_config_valid')} s={v.get('stoploss_s')} "
              f"d={v.get('trail_d')} eq={v.get('initial_equity_USDT')}")
    print("\n→ out/00_基线.json")


if __name__ == "__main__":
    main()
