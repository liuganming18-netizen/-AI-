# -*- coding: utf-8 -*-
"""01 隔离环境构建 —— 独立账本 / 独立信号库 / 独立闸门 / 合成行情库。

设计要点：
  · 所有落盘路径都在 /tmp/wx_audit03 下，生产 `数据/paper` 与 `live` 库一个字节都不碰；
  · 行情库是**合成**的，但**表结构与生产同构**（`candle_versions` + `candle_heads` + `candles` 视图）；
  · 标的名用**真实规格币种** BTCUSDT / ETHUSDT —— 因为 `paper_engine.contract_limits()`
    只认冻结规格 CSV（526 币）里的名字，编造标的名会直接卡在 `missing_contract_spec`，
    那样测出来的就不是「引擎能不能成交」而是「规格查不到」。价格是合成的（100 附近），
    仅用于隔离环境，绝不写回生产行情库。
  · **只预置「在基准时刻已经闭合」的 bar**（k <= -1）。未来 bar 一律由 `02_causal.py`
    在回放时钟推进到该 bar 已闭合之后才插入 —— 否则 `bar_at_or_after()` 会读到未来 bar，
    被 `bar_after_attempt_clock_no_lookahead` 拦下，测出来的就不是正常成交路径。

bar 编号约定（`02_causal.py` 沿用同一约定）：
  b_k: open_time = base + k*15m, close_time = base + k*15m + 15m - 1
  在时刻 T 已闭合  ⇔  base + (k+1)*15m <= T

产物：out/01_隔离环境.json
"""
from __future__ import annotations

import datetime as dt
import json
import os
import pathlib
import sqlite3
import time

ROOT = pathlib.Path("/tmp/wx_audit03")
PAPER = ROOT / "paper"
LIVE_DIR = ROOT / "live"
LIVE_DB = LIVE_DIR / "live_signals.sqlite3"
MARKET = ROOT / "market.sqlite3"
GATE = ROOT / "gate.json"

M15 = 900_000
H1 = 3_600_000

SYM = "BTCUSDT"           # 主标的（有完整合成行情 + 破损 bar）
SYM2 = "ETHUSDT"          # 副标的（反例矩阵用，无破损 bar）
SYM_NODATA = "NODATAUSDT"  # 完全无行情（数据失效反例）

# 合成价格（隔离环境专用）
P_FLAT = (100.0, 100.5, 99.5, 100.0)      # o,h,l,c 平价 bar
P_BROKEN = (100.0, 100.5, 60.0, 62.0)     # 跌破止损的破损 bar（low=60）
P_AFTER = (62.0, 63.0, 61.0, 62.5)        # 破损 bar 之后

QUOTE_VOL = "100000"      # 上一根 15m 成交额（参与率上限 = 1% ⇒ 1000 USDT）


def build_schema() -> sqlite3.Connection:
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
      exchange     TEXT NOT NULL,
      market       TEXT NOT NULL,
      symbol       TEXT NOT NULL,
      funding_time INTEGER NOT NULL,
      funding_rate TEXT,
      mark_price   TEXT,
      rate_type    TEXT,
      source       TEXT,
      fetched_at   INTEGER,
      PRIMARY KEY(exchange, market, symbol, funding_time)
    );
    """)
    con.execute("CREATE VIEW candles AS SELECT v.* FROM candle_versions v "
                "JOIN candle_heads h USING(exchange, market, symbol, timeframe, open_time, source_id)")
    con.commit()
    return con


def add_bar(con: sqlite3.Connection, symbol: str, tf: str, open_time: int,
            ohlc, *, quote_vol: str = QUOTE_VOL, source_id: int = 1) -> int:
    """插入一根**已闭合** bar（versions + heads 双写，视图才查得到）。幂等。"""
    span = M15 if tf == "15m" else H1
    close_time = open_time + span - 1
    o, h, l, c = ohlc
    con.execute("INSERT OR REPLACE INTO candle_versions VALUES"
                "('binance','um',?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)",
                (symbol, tf, open_time, close_time,
                 f"{o:.2f}", f"{h:.2f}", f"{l:.2f}", f"{c:.2f}",
                 "1000", quote_vol, 10, "500", "50000", source_id, close_time))
    con.execute("INSERT OR REPLACE INTO candle_heads VALUES('binance','um',?,?,?,?)",
                (symbol, tf, open_time, source_id))
    con.commit()
    return close_time


def main() -> None:
    if ROOT.exists():
        import shutil
        shutil.rmtree(ROOT)
    PAPER.mkdir(parents=True)
    LIVE_DIR.mkdir(parents=True)

    now_ms = int(time.time() * 1000)
    base = ((now_ms + M15 - 1) // M15) * M15     # 基准 = 当前时刻向上取整的 15m 边界

    con = build_schema()
    seeded = {}
    for sym in (SYM, SYM2):
        # 15m：k = -16..-1（在 T=base 时刻全部已闭合）
        for k in range(-16, 0):
            add_bar(con, sym, "15m", base + k * M15, P_FLAT)
        # 1h：k = -8..-1（在 T=base 时刻全部已闭合）
        h1_base = (base // H1) * H1
        for k in range(-8, 0):
            add_bar(con, sym, "1h", h1_base + k * H1, (100.0, 101.0, 99.0, 100.0))
        seeded[sym] = {
            "15m已闭合根数": con.execute(
                "SELECT COUNT(*) FROM candles WHERE symbol=? AND timeframe='15m'", (sym,)).fetchone()[0],
            "1h已闭合根数": con.execute(
                "SELECT COUNT(*) FROM candles WHERE symbol=? AND timeframe='1h'", (sym,)).fetchone()[0],
        }
    n_nodata = con.execute("SELECT COUNT(*) FROM candles WHERE symbol=?", (SYM_NODATA,)).fetchone()[0]
    con.close()

    GATE.write_text(json.dumps({
        "allow_new_entries": True, "reason": "audit03_isolated_test",
        "gate_reason": "audit03_isolated_test", "set_by": "SYSTEM-CLOSELOOP-AUDIT-03",
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

    import sys
    sys.path.insert(0, "/Users/boos/Desktop/威科夫因子共振趋势/框架/backend")
    import live_store as L
    L.ensure_live_schema()
    epoch = L.ensure_paper_epoch_ms()

    res = {
        "生成时刻": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "根目录": str(ROOT),
        "环境变量": env,
        "bar编号约定": "b_k.open_time = base + k*15m；在 T 时刻已闭合 ⇔ base+(k+1)*15m <= T",
        "合成行情": {
            "主标的": SYM, "副标的": SYM2, "无行情标的": SYM_NODATA,
            "bar锚点base": base,
            "base_时刻": dt.datetime.fromtimestamp(base / 1000).astimezone().isoformat(timespec="seconds"),
            "预置": seeded,
            "无行情标的行数": n_nodata,
            "上一根15m成交额": QUOTE_VOL,
            "待回放插入的bar": {
                "b0": {"open_time": base, "open": 100.0, "在T>=base+15m 才闭合": True},
                "b1": {"open_time": base + M15, "open": 100.0,
                       "在T>=base+30m 才闭合": True, "用途": "场景1 成交 bar"},
                "b2": {"open_time": base + 2 * M15, "low": 60.0,
                       "在T>=base+45m 才闭合": True, "用途": "跌破止损的破损 bar"},
                "b3": {"open_time": base + 3 * M15, "open": 62.0,
                       "在T>=base+60m 才闭合": True, "用途": "破损 bar 之后"},
            },
        },
        "闸门": {"路径": str(GATE), "内容": json.loads(GATE.read_text(encoding="utf-8"))},
        "paper_epoch_ms": epoch,
        "paper_epoch_时刻": dt.datetime.fromtimestamp(epoch / 1000).astimezone().isoformat(timespec="seconds"),
        "账本目录": str(L.LEDGER_DIR),
        "信号库": str(L.LIVE_DB),
        "行情库": str(L.MARKET_DB),
        "初始权益": L.INIT_EQUITY,
        "臂": list(L.ARMS),
    }
    out = pathlib.Path("/Users/boos/Desktop/威科夫因子共振趋势/evidence/SYSTEM-CLOSELOOP-AUDIT-03/out")
    (out / "01_隔离环境.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"根目录 {ROOT}")
    print(f"base = {base} ({res['合成行情']['base_时刻']})")
    print(f"  {SYM} 15m {seeded[SYM]['15m已闭合根数']} 根 / 1h {seeded[SYM]['1h已闭合根数']} 根")
    print(f"  {SYM2} 15m {seeded[SYM2]['15m已闭合根数']} 根 / 1h {seeded[SYM2]['1h已闭合根数']} 根")
    print(f"  {SYM_NODATA} {n_nodata} 根（应为 0）")
    print(f"paper_epoch = {epoch} ({res['paper_epoch_时刻']})")
    print(f"闸门 {GATE} → allow_new_entries=True")
    print(f"账本 {L.LEDGER_DIR} / 信号库 {L.LIVE_DB} / 行情库 {L.MARKET_DB}")


if __name__ == "__main__":
    main()
