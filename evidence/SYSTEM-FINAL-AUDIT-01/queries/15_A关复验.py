# -*- coding: utf-8 -*-
"""15 A 关在当前版（10:13~10:17 后端）上的重验。

A 关三条：
  A1 必需配置缺失时拒绝新单，无静默兜底
  A2 参数身份贯穿挂单/订单/持仓/重启（本探针验前三层 + 13 号验重启）
  A3 客户端不能覆盖冻结参数

上一轮（01:40 版）我验过 A1/A2 通过、A3 未单独测。后端在 10:13~10:17 又改过
（live_store 40058B / paper_engine 138932B / live_api 107086B），故按纪律只复验 A 关。

产物：out/15_A关复验.json
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
ROOT = pathlib.Path("/tmp/wx_final06")
M15 = 900_000
SYM = "BTCUSDT"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

GOOD = {
    "schema_version": "research_policy_2.2",
    "spec_version": "F_WX_final01_gateA",
    "strategy_version": "FT-LOW4H-EXP-gateA",
    "model_version": "FT-LOW4H-EXP-gateA",
    "purpose": "audit_gateA",
    "production_config": False,
    "live_trading_authorized": False,
    "research_policy_frozen": True,
    "execution": {"detail_timeframe": "15m", "signal_execution_delay_minutes": 15,
                  "fee_each_side": 0.0005, "slippage_each_side": 0.0003,
                  "signal_exit_delay_minutes": 15},
    "stops": {"rule": "FT-LOW4H", "stoploss_s": 0.05, "trail_d": 0.05,
              "uniform": "actual_entry_minus_s_and_4h_low_minus_d",
              "max_holding_hours": 240, "trailing_enabled": True},
    "risk": {"initial_equity_USDT": 5000.0, "risk_per_trade_equity_fraction": 0.005,
             "leverage": 1, "max_positions": 10,
             "single_name_notional_equity_cap": 0.10,
             "gross_long_notional_equity_cap": 0.80,
             "cash_or_free_margin_reserve_fraction": 0.20,
             "portfolio_initial_stop_risk_cap": 0.05,
             "entry_participation_previous_15m_quote_cap": 0.01,
             "maximum_account_drawdown_fraction": 0.20, "short_enabled": False},
}


def market_schema(market: pathlib.Path) -> None:
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
    paper = ROOT / "paper"
    live = ROOT / "live"
    for p in (paper, live, ROOT / "locks"):
        p.mkdir(parents=True, exist_ok=True)
    market = ROOT / "market.sqlite3"
    market_schema(market)
    gate = ROOT / "gate.json"
    gate.write_text(json.dumps({"allow_new_entries": True, "reason": "gateA_probe",
                                "gate_reason": "gateA_probe", "set_by": "FINAL-01",
                                "since_ms": int(time.time() * 1000)}), encoding="utf-8")
    for k, v in (("PAPER_LEDGER_DIR", str(paper)), ("PAPER_LEDGER_ALLOW_INIT", "1"),
                 ("PAPER_LOCK_DIR", str(ROOT / "locks")), ("LIVE_DIR", str(live)),
                 ("LIVE_DB", str(live / "live_signals.sqlite3")),
                 ("MARKET_DB", str(market)), ("PAPER_GATE_FILE", str(gate))):
        os.environ[k] = v

    import live_store as L
    import paper_engine as PE
    L.ensure_live_schema()
    epoch = L.ensure_paper_epoch_ms()

    res = {"生成时刻": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
           "根目录": str(ROOT), "paper_epoch_时刻":
           dt.datetime.fromtimestamp(epoch / 1000).astimezone().isoformat(timespec="seconds"),
           "场景": [], "判定": {}}

    # ── A1：四种坏配置，各自必须被拒 ──────────────────────────────
    bad = [
        ("S1 视图文件不存在", "missing_file", None),
        ("S2 缺 stops 段", "no_stops", None),
        ("S3 stoploss_s = 0", "zero_s", None),
        ("S4 trail_d = 0", "zero_d", None),
        ("S5 好配置（对照，应登记挂单）", "good", None),
    ]
    for name, kind, _ in bad:
        view = ROOT / f"view_{kind}.json"
        if kind == "missing_file":
            # 必须指向一个**确实不存在**的路径；unset 会回落到工程内默认视图，那不是「缺配置」
            view = ROOT / "view_absent.json"
            if view.exists():
                view.unlink()
            os.environ["EFFECTIVE_VIEW"] = str(view)
            vpath = str(view)
        else:
            content = json.loads(json.dumps(GOOD))
            if kind == "no_stops":
                content.pop("stops", None)
            elif kind == "zero_s":
                content["stops"]["stoploss_s"] = 0
            elif kind == "zero_d":
                content["stops"]["trail_d"] = 0
            view.write_text(json.dumps(content, ensure_ascii=False, indent=2), encoding="utf-8")
            os.environ["EFFECTIVE_VIEW"] = str(view)
            vpath = str(view)
        try:
            p = PE.approved_params()
        except Exception as exc:                                   # noqa: BLE001
            res["场景"].append({"场景": name, "approved_params": f"异常 {exc}",
                              "结论": "拒绝(抛错)"})
            continue
        uid = f"sig_gateA_{kind}"
        try:
            r = PE.open_position("3a", SYM, signal_uid=uid, direction="long", atr=1.0,
                                 now_ms=L.now_ms(), event_time=L.now_ms(),
                                 available_at=L.now_ms(), requested_at=L.now_ms(),
                                 origin="replay")
            r = {"ok": r.get("ok"), "reason": r.get("reason"),
                 "pending_id": r.get("pending_id")}
        except Exception as exc:                                   # noqa: BLE001
            r = {"异常": f"{type(exc).__name__}: {exc}"}
        rec = {"场景": name, "视图": vpath,
               "approved_params": {"_config_valid": p.get("_config_valid"),
                                   "_config_error": p.get("_config_error"),
                                   "source": p.get("source") or p.get("_source"),
                                   "stoploss_s": p.get("stoploss_s"),
                                   "trail_d": p.get("trail_d"),
                                   "max_holding_hours": p.get("max_holding_hours"),
                                   "initial_equity_USDT": p.get("initial_equity_USDT")},
               "open_position": r}
        if p.get("_config_valid") is False:
            rec["结论"] = f"拒绝(配置无效: {p.get('_config_error')})"
        elif r.get("pending_id"):
            rec["结论"] = "登记挂单"
        else:
            rec["结论"] = f"拒绝({r.get('reason')})"
        res["场景"].append(rec)
        print(f"{name:26s} valid={str(p.get('_config_valid')):5s} "
              f"err={str(p.get('_config_error'))[:44]:46s} → {rec['结论']}")

    # ── A2：四层身份列 ────────────────────────────────────────────
    cols = ("stoploss_s", "trail_d", "config_fingerprint", "config_source",
            "initial_stop_price", "planned_risk")
    ident = {}
    f = paper / "paper_3a.sqlite3"
    if f.exists():
        con = sqlite3.connect(f"file:{f}?mode=ro", uri=True)
        for t in ("paper_pending", "paper_order", "paper_fill", "paper_position"):
            try:
                have = {r[1] for r in con.execute(f"PRAGMA table_info({t})")}
            except sqlite3.Error:
                have = set()
            ident[t] = {c: (c in have) for c in cols}
        con.close()
    res["四层身份列"] = ident
    print("\n四层身份列:")
    for t, v in ident.items():
        print(f"  {t:16s}", {k: ("有" if x else "无") for k, x in v.items()})

    # ── A3：客户端覆盖冻结参数必须被拒 ────────────────────────────
    os.environ["EFFECTIVE_VIEW"] = str(ROOT / "view_good.json")
    import live_api as API
    a3 = []
    for label, arm, payload in (
        ("C1 覆盖 stoploss_s=0.20", "3b", {"arm": "3b", "action": "open", "symbol": SYM,
                                          "stoploss_s": 0.20, "origin": "signal"}),
        ("C2 覆盖 trail_d=0.50", "3c", {"arm": "3c", "action": "open", "symbol": SYM,
                                       "trail_d": 0.50, "origin": "signal"}),
        ("C3 覆盖 stop_price=1.0", "3d", {"arm": "3d", "action": "open", "symbol": SYM,
                                         "stop_price": 1.0, "origin": "signal"}),
    ):
        try:
            body, status = API.paper_order_post(payload)
            it = (body.get("items") or [{}])[0]
            rec = {"用例": label, "HTTP": status, "reason": it.get("reason"),
                   "missing_reason": body.get("missing_reason"),
                   "quality": (body.get("quality") or {}).get("status"),
                   "pending_id": it.get("pending_id")}
        except Exception as exc:                                   # noqa: BLE001
            rec = {"用例": label, "异常": f"{type(exc).__name__}: {exc}"}
        # 关键：客户端传的值到底有没有落库（拒绝 / 忽略 / 真的覆盖）
        led = paper / f"paper_{arm}.sqlite3"
        landed = {"账本存在": led.exists()}
        if led.exists():
            con = sqlite3.connect(f"file:{led}?mode=ro", uri=True)
            con.row_factory = sqlite3.Row
            for t in ("paper_pending", "paper_position"):
                try:
                    rows = [dict(r) for r in con.execute(f"SELECT * FROM {t}").fetchall()]
                except sqlite3.Error:
                    rows = []
                if rows:
                    landed[t] = {k: rows[0].get(k) for k in
                                 ("stoploss_s", "trail_d", "stop_price",
                                  "initial_stop_price", "config_fingerprint")}
            con.close()
        rec["落库实际值"] = landed
        a3.append(rec)
        print(f"{label:26s} HTTP={rec.get('HTTP')} reason={rec.get('reason')} "
              f"note={rec.get('quality')} 落库={json.dumps(landed, ensure_ascii=False)[:150]}")
    res["A3_客户端覆盖"] = a3

    # ── C3 深挖：客户端指定 stop_price=1.0 之后，成交落库的止损与风险 ──
    # 说明：这一步用 advance_pending 把挂单推到成交，目的是**复现缺陷**，
    # 不是拿它当「自动闭环通过」的证据（自动闭环证据见 13 号真进程重启）。
    deep = {"说明": "C3 挂单推进到成交后的实际止损与风险"}
    led = paper / "paper_3d.sqlite3"
    if led.exists():
        con = sqlite3.connect(f"file:{led}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        row = con.execute("SELECT pending_id, requested_at, earliest_fill_ms, "
                          "stop_price, initial_stop_price, stoploss_s FROM paper_pending").fetchone()
        con.close()
        if row and row["earliest_fill_ms"]:
            ef = int(row["earliest_fill_ms"])
            con = sqlite3.connect(market)
            con.execute("INSERT OR REPLACE INTO candle_versions VALUES"
                        "('binance','um',?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)",
                        (SYM, "15m", ef, ef + M15 - 1, "100.00", "100.50", "99.50", "100.00",
                         "1000", "100000", 10, "500", "50000", 1, ef + M15 - 1))
            con.execute("INSERT OR REPLACE INTO candle_heads VALUES('binance','um',?,?,?,?)",
                        (SYM, "15m", ef, 1))
            con.commit()
            con.close()
            adv = PE.advance_pending(T=ef, log=lambda *a: None)
            deep["advance"] = {"filled": len(adv.get("filled") or []),
                               "rejected": adv.get("rejected"),
                               "still_pending": adv.get("still_pending")}
            con = sqlite3.connect(f"file:{led}?mode=ro", uri=True)
            con.row_factory = sqlite3.Row
            pos = con.execute("SELECT position_id, entry_price, stop_price, initial_stop_price, "
                              "qty, stoploss_s, planned_risk FROM paper_position").fetchone()
            con.close()
            if pos:
                pd = dict(pos)
                dist = abs(float(pd["entry_price"]) - float(pd["initial_stop_price"]))
                deep["成交后持仓"] = pd
                deep["实际止损距离"] = dist
                deep["实际风险_止损距离x数量"] = dist * float(pd["qty"])
                deep["风险预算_权益x0.005"] = 5000.0 * 0.005
                deep["实际风险/预算"] = (dist * float(pd["qty"])) / (5000.0 * 0.005)
                print(f"\n[C3深挖] 成交 entry={pd['entry_price']} stop={pd['initial_stop_price']} "
                      f"qty={pd['qty']} ⇒ 实际风险={dist * float(pd['qty']):.2f} "
                      f"= 预算的 {(dist * float(pd['qty'])) / 25.0:.1f}x")
    res["C3深挖"] = deep

    s1 = next(x for x in res["场景"] if x["场景"].startswith("S1"))
    s2 = next(x for x in res["场景"] if x["场景"].startswith("S2"))
    s3 = next(x for x in res["场景"] if x["场景"].startswith("S3"))
    s4 = next(x for x in res["场景"] if x["场景"].startswith("S4"))
    s5 = next(x for x in res["场景"] if x["场景"].startswith("S5"))
    res["判定"] = {
        "A1_缺视图被拒": "拒绝" in s1["结论"],
        "A1_缺stops被拒": "拒绝" in s2["结论"],
        "A1_s为0被拒": "拒绝" in s3["结论"],
        "A1_d为0被拒": "拒绝" in s4["结论"],
        "A1_好配置能登记": "登记挂单" in s5["结论"],
        "A2_四层有s_d_指纹": all(ident.get(t, {}).get(c) for t in ident
                              for c in ("stoploss_s", "trail_d", "config_fingerprint")),
        "A3_s覆盖被拒": "rejected" == str(a3[0].get("quality")),
        "A3_trail_d未被客户端覆盖":
            (a3[1].get("落库实际值", {}).get("paper_pending", {}).get("trail_d") == 0.05),
        "A3_stop_price未被客户端覆盖":
            (a3[2].get("落库实际值", {}).get("paper_pending", {}).get("initial_stop_price")
             != 1.0),
    }
    (OUT / "15_A关复验.json").write_text(
        json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n判定:", json.dumps(res["判定"], ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
