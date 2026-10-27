# -*- coding: utf-8 -*-
"""12 C 关独立探针：HTTP 下单入口能否被客户端注入回放时钟做「历史回填」。

背景
----
AUDIT-01 的 P0 是「`live_api.py` 接受客户端传入的 requested_at / now_ms ⇒
成交 bar 早于真实挂钟」。当前版 `paper_engine.open_position` L877-880 加了防线：

    real_wall_ms = L.now_ms()
    is_replay = (origin in ("replay", "test") or pending_id is not None)
    attempt_ms = int(now_ms) if (is_replay and now_ms is not None) else real_wall_ms

但 `live_api.paper_order_post` L913 是：

    origin = payload.get("origin") or "signal"        # ← origin 完全来自请求体

若「origin 由客户端声明」这一条成立，则任何调用方只要在请求体里写
`origin="replay"` 并带一个真实存在的 `signal_uid`，就能指定过去时刻开仓。

本探针只用引擎自己的 HTTP 入口（`paper_order_post`），不调任何推进方法、
不写裸 SQL 建仓，账本/信号库/行情库全部隔离在 /tmp/wx_final03。

四个变体（各用独立 arm，互不干扰）：
  V0 对照：origin 缺省（=signal），行情只到过去 ⇒ 期望 pending / 拒绝，不回填
  V1 回填：origin=replay + now_ms/requested_at = 90 分钟前 ⇒ 若成交，则回填成立
  V2 越界：origin=replay + now_ms 早于信号 available_at ⇒ 期望被拒
  V3 无信号：origin=replay 但不给 signal_uid ⇒ 期望被强制 manual、不回填

产物：out/12_C关回填复现.json
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
OUT.mkdir(parents=True, exist_ok=True)
BACKEND = BASE / "框架/backend"

ROOT = pathlib.Path("/tmp/wx_final03")
PAPER = ROOT / "paper"
LIVE_DIR = ROOT / "live"
LIVE_DB = LIVE_DIR / "live_signals.sqlite3"
MARKET = ROOT / "market.sqlite3"
GATE = ROOT / "gate.json"
VIEW = BASE / "evidence/SYSTEM-CLOSELOOP-05/config/exp1_config.json"

M15 = 900_000
SYM = "BTCUSDT"

ENV = {
    "PAPER_LEDGER_DIR": str(PAPER),
    "PAPER_LEDGER_ALLOW_INIT": "1",
    "LIVE_DIR": str(LIVE_DIR),
    "LIVE_DB": str(LIVE_DB),
    "MARKET_DB": str(MARKET),
    "PAPER_GATE_FILE": str(GATE),
    "PAPER_LOCK_DIR": str(ROOT / "locks"),
    "EFFECTIVE_VIEW": str(VIEW),
}


def iso(ms: int) -> str:
    return dt.datetime.fromtimestamp(ms / 1000).astimezone().isoformat(timespec="seconds")


def build_market() -> None:
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
    CREATE TABLE funding_history(
      exchange TEXT NOT NULL, market TEXT NOT NULL, symbol TEXT NOT NULL,
      funding_time INTEGER NOT NULL, funding_rate TEXT, mark_price TEXT,
      rate_type TEXT, source TEXT, fetched_at INTEGER,
      PRIMARY KEY(exchange, market, symbol, funding_time));
    """)
    con.execute("CREATE VIEW candles AS SELECT v.* FROM candle_versions v "
                "JOIN candle_heads h USING(exchange, market, symbol, timeframe, open_time, source_id)")
    con.commit()
    con.close()


def add_bar(open_time: int, ohlc, tf: str = "15m") -> None:
    span = M15 if tf == "15m" else (4 * 3600_000 if tf == "4h" else 3600_000)
    close_time = open_time + span - 1
    o, h, l, c = ohlc
    con = sqlite3.connect(MARKET)
    try:
        con.execute("INSERT OR REPLACE INTO candle_versions VALUES"
                    "('binance','um',?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)",
                    (SYM, tf, open_time, close_time,
                     f"{o:.2f}", f"{h:.2f}", f"{l:.2f}", f"{c:.2f}",
                     "1000", "100000", 10, "500", "50000", 1, close_time))
        con.execute("INSERT OR REPLACE INTO candle_heads VALUES('binance','um',?,?,?,?)",
                    (SYM, tf, open_time, 1))
        con.commit()
    finally:
        con.close()


def main() -> None:
    if ROOT.exists():
        shutil.rmtree(ROOT)
    PAPER.mkdir(parents=True)
    LIVE_DIR.mkdir(parents=True)
    for k, v in ENV.items():
        os.environ[k] = v
    if str(BACKEND) not in sys.path:
        sys.path.insert(0, str(BACKEND))

    now_ms = int(time.time() * 1000)
    cur_b15 = (now_ms // M15) * M15
    t_past = cur_b15 - 6 * M15            # 90 分钟前，对齐 15m 边界
    t_too_early = t_past - 6 * M15        # 再往前 90 分钟，用于「早于信号可用时刻」

    build_market()
    # 15m 行情覆盖 [t_too_early-2h, t_past+15m]，价格恒 100，保证任何时刻都有可成交 bar
    for k in range(-8, 8):
        add_bar(t_past + k * M15, (100.0, 100.5, 99.5, 100.0))
    for k in range(-8, 0):
        add_bar(t_too_early + k * M15, (100.0, 100.5, 99.5, 100.0))

    GATE.write_text(json.dumps({
        "allow_new_entries": True, "reason": "final01_c_gate_probe",
        "gate_reason": "final01_c_gate_probe", "set_by": "SYSTEM-FINAL-AUDIT-01",
        "since_ms": now_ms}, ensure_ascii=False, indent=1), encoding="utf-8")

    import live_store as L
    L.ensure_live_schema()
    epoch = L.ensure_paper_epoch_ms()

    # 造三条真实存在的信号（available_at = t_past），供三个变体各用一条
    con_live = L.live_conn()
    uids = {}
    for arm in ("3a", "3b", "3c"):
        uid = f"sig_final01_{arm}"
        uids[arm] = uid
        con_live.execute(
            "INSERT OR REPLACE INTO live_signal("
            "signal_uid,source,origin,exchange,market,symbol,timeframe,strategy_key,"
            "strategy_version,direction,event_time,confirmed_at,available_at,"
            "bar_open_time,bar_close_time,display_state,executable_state,"
            "first_seen_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (uid, "probe", "live", "binance", "um", SYM, "4h", arm,
             "FT-LOW4H-EXP-V2.2", "long", t_past - M15, t_past, t_past,
             t_past - 4 * 3600_000, t_past - 1, "displayable", "enterable",
             now_ms, now_ms))
    con_live.commit()
    con_live.close()

    import live_api as API

    cases = [
        ("V0", "3a", "对照：origin 缺省", {"origin": None, "now_ms": None, "req": None,
                                          "uid": uids["3a"]}),
        ("V1", "3b", "回填：origin=replay + 90 分钟前", {"origin": "replay", "now_ms": t_past,
                                                     "req": t_past, "uid": uids["3b"]}),
        ("V2", "3c", "越界：origin=replay + 早于信号可用", {"origin": "replay", "now_ms": t_too_early,
                                                     "req": t_too_early, "uid": uids["3c"]}),
        ("V3", "3a", "无信号：origin=replay 但不给 signal_uid", {"origin": "replay",
                                                          "now_ms": t_past, "req": t_past,
                                                          "uid": None}),
    ]

    res = {
        "生成时刻": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "根目录": str(ROOT),
        "环境": ENV,
        "时钟": {"真实现在": now_ms, "真实现在_时刻": iso(now_ms),
               "回填目标": t_past, "回填目标_时刻": iso(t_past),
               "回填提前分钟": (now_ms - t_past) / 60000,
               "paper_epoch_ms": epoch, "paper_epoch_时刻": iso(epoch),
               "回填时刻晚于 epoch": t_past > epoch,
               "回填时刻早于 epoch": t_past < epoch},
        "用例": [],
    }

    for name, arm, desc, c in cases:
        payload = {"arm": arm, "action": "open", "symbol": SYM,
                   "direction": "long", "atr": 1.0}
        if c["uid"]:
            payload["signal_uid"] = c["uid"]
        if c["origin"]:
            payload["origin"] = c["origin"]
        if c["now_ms"] is not None:
            payload["now_ms"] = c["now_ms"]
        if c["req"] is not None:
            payload["requested_at"] = c["req"]

        try:
            body, status = API.paper_order_post(payload)
            err = None
        except Exception as exc:                                   # noqa: BLE001
            body, status, err = None, None, f"{type(exc).__name__}: {exc}"

        rec = {"变体": name, "arm": arm, "说明": desc,
               "请求体": {k: (iso(v) if k in ("now_ms", "requested_at") and v else v)
                        for k, v in payload.items()},
               "HTTP": status, "异常": err}
        if body:
            it = (body.get("items") or [{}])[0]
            rec["返回"] = {
                "ok": it.get("ok"), "reason": it.get("reason"),
                "pending_id": it.get("pending_id"),
                "fill_price": it.get("fill_price") or it.get("entry_price"),
                "fill_ts": it.get("fill_ts"),
                "fill_ts_时刻": iso(it["fill_ts"]) if it.get("fill_ts") else None,
                "requested_at": it.get("requested_at"),
                "earliest_fill_ms": it.get("earliest_fill_ms"),
            }
            rec["quality"] = (body.get("quality") or {}).get("status")
            rec["missing_reason"] = body.get("missing_reason")

        # 账本侧独立复核（只读）
        f = PAPER / f"paper_{arm}.sqlite3"
        led = {"账本": str(f), "存在": f.exists()}
        if f.exists():
            con = sqlite3.connect(f"file:{f}?mode=ro", uri=True)
            con.row_factory = sqlite3.Row
            for tbl, order in (("paper_pending", "pending_id"), ("paper_fill", "fill_id"),
                               ("paper_position", "position_id"), ("paper_order", "order_id")):
                try:
                    rows = [dict(r) for r in con.execute(
                        f"SELECT * FROM {tbl} ORDER BY {order}").fetchall()]
                except sqlite3.Error as exc:
                    rows = [{"_错误": str(exc)}]
                led[tbl] = rows
            con.close()
        rec["账本"] = led

        # 判定：成交时间是否落在过去（回填）
        fills = [r for r in (led.get("paper_fill") or []) if isinstance(r, dict) and r.get("fill_ts")]
        if fills:
            fts = int(fills[0]["fill_ts"])
            rec["判定"] = {
                "已成交": True,
                "fill_ts": fts, "fill_ts_时刻": iso(fts),
                "距真实现在分钟": (now_ms - fts) / 60000,
                "属于历史回填": fts < now_ms - 5 * 60_000,
            }
        else:
            rec["判定"] = {"已成交": False,
                          "挂单数": len(led.get("paper_pending") or []),
                          "持仓数": len(led.get("paper_position") or [])}
        res["用例"].append(rec)
        print(f"[{name}] {desc}  HTTP={status} reason={rec.get('返回', {}).get('reason')} "
              f"成交={rec['判定'].get('已成交')} "
              f"{rec['判定'].get('fill_ts_时刻') or ''}")

    # 总判定
    v1 = next(u for u in res["用例"] if u["变体"] == "V1")
    v0 = next(u for u in res["用例"] if u["变体"] == "V0")
    res["判定"] = {
        "V1_客户端声明replay可回填": bool(v1["判定"].get("属于历史回填")),
        "V0_缺省路径不回填": not v0["判定"].get("属于历史回填"),
        "结论": ("漏洞成立：客户端可在请求体声明 origin=replay 并指定过去时刻成交"
                 if v1["判定"].get("属于历史回填") else
                 "未复现：客户端声明 origin=replay 未能回填"),
    }
    (OUT / "12_C关回填复现.json").write_text(
        json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n总判定:", json.dumps(res["判定"], ensure_ascii=False))


if __name__ == "__main__":
    main()
