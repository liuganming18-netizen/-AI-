# -*- coding: utf-8 -*-
"""隔离环境公共助手（供 02~06 号探针复用）。

**只做三件事**：① 把环境变量指到 /tmp/wx_audit03；② 提供与 01_env.py 同构的
`add_bar()`（bar 编号约定见 01_env.py 文档）；③ 读 01 号产物拿 `base`。

不做任何断言、不写任何业务表 —— 断言一律留在各探针里，便于逐条对账。
"""
from __future__ import annotations

import json
import os
import pathlib
import sqlite3
import sys

PROJ = pathlib.Path("/Users/boos/Desktop/威科夫因子共振趋势")
EVID = PROJ / "evidence" / "SYSTEM-CLOSELOOP-AUDIT-03"
OUT = EVID / "out"
BACKEND = PROJ / "框架" / "backend"

ROOT = pathlib.Path("/tmp/wx_audit03")
PAPER = ROOT / "paper"
LIVE_DIR = ROOT / "live"
LIVE_DB = LIVE_DIR / "live_signals.sqlite3"
MARKET = ROOT / "market.sqlite3"
GATE = ROOT / "gate.json"

M15 = 900_000
H1 = 3_600_000

SYM = "BTCUSDT"
SYM2 = "ETHUSDT"
SYM_NODATA = "NODATAUSDT"
QUOTE_VOL = "100000"

P_FLAT = (100.0, 100.5, 99.5, 100.0)
P_BROKEN = (100.0, 100.5, 60.0, 62.0)
P_AFTER = (62.0, 63.0, 61.0, 62.5)


def env_json() -> dict:
    return json.loads((OUT / "01_隔离环境.json").read_text(encoding="utf-8"))


def setup() -> dict:
    """设好环境变量并返回 01 号产物（含 base）。"""
    for k, v in {
        "PAPER_LEDGER_DIR": str(PAPER),
        "PAPER_LEDGER_ALLOW_INIT": "1",
        "LIVE_DIR": str(LIVE_DIR),
        "LIVE_DB": str(LIVE_DB),
        "MARKET_DB": str(MARKET),
        "PAPER_GATE_FILE": str(GATE),
    }.items():
        os.environ[k] = v
    if str(BACKEND) not in sys.path:
        sys.path.insert(0, str(BACKEND))
    return env_json()


def mkt() -> sqlite3.Connection:
    con = sqlite3.connect(MARKET)
    con.row_factory = sqlite3.Row
    return con


def add_bar(symbol: str, open_time: int, ohlc, tf: str = "15m",
            quote_vol: str = QUOTE_VOL, available_at: int | None = None) -> int:
    """插入一根**此刻已闭合**的 bar（versions + heads 双写）。返回 close_time。"""
    span = {"15m": M15, "1h": H1, "4h": 4 * H1}[tf]
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


def seed_history(symbol: str, base: int, n15: int = 16, n1h: int = 8) -> None:
    """预置「在 T=base 时刻已闭合」的历史 bar（k=-n15..-1 / 1h k=-n1h..-1）。"""
    for k in range(-n15, 0):
        add_bar(symbol, base + k * M15, P_FLAT)
    h1b = (base // H1) * H1
    for k in range(-n1h, 0):
        add_bar(symbol, h1b + k * H1, (100.0, 101.0, 99.0, 100.0), tf="1h")


def del_bars(symbol: str) -> int:
    """清掉某标的的全部合成 bar（用于撤掉"种进去反证用的未来 bar"，避免污染后续用例）。"""
    con = sqlite3.connect(MARKET)
    try:
        n = con.execute("DELETE FROM candle_versions WHERE symbol=?", (symbol,)).rowcount
        con.execute("DELETE FROM candle_heads WHERE symbol=?", (symbol,))
        con.commit()
    finally:
        con.close()
    return n


def dump(name: str, obj) -> pathlib.Path:
    p = OUT / name
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    return p


def ms(t: int) -> str:
    import datetime as dt
    return dt.datetime.fromtimestamp(t / 1000).astimezone().isoformat(timespec="seconds")
