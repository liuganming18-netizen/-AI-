# -*- coding: utf-8 -*-
"""01 隔离环境构建（AUDIT-04）—— 独立账本 / 信号库 / 闸门 / 合成行情库。

与 AUDIT-03 的 01_env.py 同构，差异只有三处：
  ① 根目录换成 /tmp/wx_audit04（不与上一轮互相污染）；
  ② 增加 4h 行情表预置（本轮要测「4h 跟踪上移」与「15m 明细退出」两条不同周期）；
  ③ 不预置「尚未闭合」的 bar —— 未来 bar 一律由各探针在需要时插入，
     以保证「行情到达 → 引擎推进」的时序是真实的。

bar 编号约定：b_k.open_time = base + k*15m；在 T 时刻已闭合 ⇔ base+(k+1)*15m <= T。
产物：out/01_隔离环境.json
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
EVID = BASE / "evidence/SYSTEM-CLOSELOOP-AUDIT-04"
OUT = EVID / "out"
OUT.mkdir(parents=True, exist_ok=True)
BACKEND = BASE / "框架/backend"

ROOT = pathlib.Path("/tmp/wx_audit04")
PAPER = ROOT / "paper"
LIVE_DIR = ROOT / "live"
LIVE_DB = LIVE_DIR / "live_signals.sqlite3"
MARKET = ROOT / "market.sqlite3"
GATE = ROOT / "gate.json"

M15 = 900_000
H1 = 3_600_000
H4 = 4 * H1

SYM = "BTCUSDT"
SYM2 = "ETHUSDT"
SYM_NODATA = "NODATAUSDT"
QUOTE_VOL = "100000"

P_FLAT = (100.0, 100.5, 99.5, 100.0)
P_HIGH4H = (100.0, 103.0, 97.0, 102.0)   # 4h：low=97 ⇒ 跟踪候选 = 97*(1-d)
P_BREAK = (100.0, 100.5, 60.0, 62.0)     # 15m：low=60，远低于任何止损


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


def add_bar(symbol: str, open_time: int, ohlc, tf: str = "15m",
            quote_vol: str = QUOTE_VOL, available_at: int | None = None) -> int:
    span = {"15m": M15, "1h": H1, "4h": H4}[tf]
    close_time = open_time + span - 1
    o, h, l, c = ohlc
    con = sqlite3.connect(MARKET)
    try:
        con.execute("INSERT OR REPLACE INTO candle_versions VALUES"
                    "('binance','um',?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)",
                    (symbol, tf, open_time, close_time,
                     f"{o:.2f}", f"{h:.2f}", f"{l:.2f}", f"{c:.2f}",
                     "1000", quote_vol, 10, "500", "50000", 1,
                     close_time if available_at is None else available_at))
        con.execute("INSERT OR REPLACE INTO candle_heads VALUES('binance','um',?,?,?,?)",
                    (symbol, tf, open_time, 1))
        con.commit()
    finally:
        con.close()
    return close_time


def main() -> None:
    if ROOT.exists():
        shutil.rmtree(ROOT)
    PAPER.mkdir(parents=True)
    LIVE_DIR.mkdir(parents=True)

    now_ms = int(time.time() * 1000)
    base = (now_ms // M15) * M15          # 当前 15m 区间起点（尚未收盘）

    con = build_market_schema()
    seeded = {}
    for sym in (SYM, SYM2):
        for k in range(-16, 0):           # 15m：T=base 时全部已闭合
            add_bar(sym, base + k * M15, P_FLAT)
        h1b = (base // H1) * H1
        for k in range(-8, 0):
            add_bar(sym, h1b + k * H1, (100.0, 101.0, 99.0, 100.0), tf="1h")
        h4b = (base // H4) * H4
        for k in range(-4, 0):            # 4h：全部已闭合
            add_bar(sym, h4b + k * H4, P_HIGH4H, tf="4h")
        seeded[sym] = {
            "15m": con.execute("SELECT COUNT(*) FROM candles WHERE symbol=? AND timeframe='15m'", (sym,)).fetchone()[0],
            "1h": con.execute("SELECT COUNT(*) FROM candles WHERE symbol=? AND timeframe='1h'", (sym,)).fetchone()[0],
            "4h": con.execute("SELECT COUNT(*) FROM candles WHERE symbol=? AND timeframe='4h'", (sym,)).fetchone()[0],
        }
    con.close()

    GATE.write_text(json.dumps({
        "allow_new_entries": True, "reason": "audit04_isolated_test",
        "gate_reason": "audit04_isolated_test", "set_by": "SYSTEM-CLOSELOOP-AUDIT-04",
        "since_ms": now_ms}, ensure_ascii=False, indent=1), encoding="utf-8")

    env = {
        "PAPER_LEDGER_DIR": str(PAPER),
        "PAPER_LEDGER_ALLOW_INIT": "1",
        "LIVE_DIR": str(LIVE_DIR),
        "LIVE_DB": str(LIVE_DB),
        "MARKET_DB": str(MARKET),
        "PAPER_GATE_FILE": str(GATE),
    }
    for k, v in env.items():
        os.environ[k] = v
    if str(BACKEND) not in sys.path:
        sys.path.insert(0, str(BACKEND))

    import live_store as L
    L.ensure_live_schema()
    epoch = L.ensure_paper_epoch_ms()

    res = {
        "生成时刻": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "根目录": str(ROOT),
        "环境变量": env,
        "bar编号约定": "b_k.open_time = base + k*15m；在 T 已闭合 ⇔ base+(k+1)*15m <= T",
        "合成行情": {"主标的": SYM, "副标的": SYM2, "无行情标的": SYM_NODATA,
                 "base": base,
                 "base_时刻": dt.datetime.fromtimestamp(base / 1000).astimezone().isoformat(timespec="seconds"),
                 "预置": seeded, "上一根15m成交额": QUOTE_VOL},
        "闸门": {"路径": str(GATE), "内容": json.loads(GATE.read_text(encoding="utf-8"))},
        "paper_epoch_ms": epoch,
        "paper_epoch_时刻": dt.datetime.fromtimestamp(epoch / 1000).astimezone().isoformat(timespec="seconds"),
        "账本目录": str(L.LEDGER_DIR),
        "信号库": str(L.LIVE_DB),
        "行情库": str(L.MARKET_DB),
        "初始权益": L.INIT_EQUITY,
        "臂": list(L.ARMS),
    }
    (OUT / "01_隔离环境.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"根目录 {ROOT}")
    print(f"base={base} ({res['合成行情']['base_时刻']})")
    for sym, n in seeded.items():
        print(f"  {sym} 15m={n['15m']} 1h={n['1h']} 4h={n['4h']}")
    print(f"epoch={epoch} ({res['paper_epoch_时刻']})  账本={L.LEDGER_DIR}")


if __name__ == "__main__":
    main()
