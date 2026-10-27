# -*- coding: utf-8 -*-
"""UI-LINK-01 · 给隔离环境铺一条**真实结构**的入场信号（不伪造价格事实）。

做什么：
  1. 在隔离 LIVE_DIR 里建 live_signal 表（`ensure_live_schema`）；
  2. 把 `live_start_ms` 落到「3 小时前」（幂等，只写一次），使有效下限
     `max(live_start, paper_epoch=2026-10-14T01:00Z)` 早于本条信号，信号能进当前区；
  3. 插入一条 `origin=live / executable_state=enterable / payload.role=enterable_arm_entry`
     的信号，标的取真实存在的 BTCUSDT；
  4. ATR 用**真实行情**算：取该标的最新已闭合 4h bar 的 (high-low)，不用手填数字。

不做什么：不写行情库、不写生产账本、不改生产闸门。
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path("/Users/boos/Desktop/威科夫因子共振趋势")
BACKEND = ROOT / "框架" / "backend"
ISO = Path("/tmp/ui-link-01")
sys.path.insert(0, str(BACKEND))

# 必须先固定环境，再 import（模块级常量会在 import 时求值）
os.environ.update({
    "LIVE_DIR": str(ISO / "live"),
    "PAPER_LEDGER_DIR": str(ISO / "paper"),
    "PAPER_LOCK_DIR": str(ISO / "locks"),
    "PAPER_GATE_FILE": str(ISO / "live" / "paper_new_entry_gate.json"),
    "PAPER_LEDGER_ALLOW_INIT": "1",
    "MARKET_DB": str(ROOT / "数据" / "市场数据" / "db" / "market_v2.sqlite3"),
})

import live_store as L  # noqa: E402

SYMBOL = "BTCUSDT"
ARM = "3a"
SIG_UID = "sig_uilink01_seed_btcusdt"     # 避开 sig_ui_test_ / sig_closeloop_ 前缀


def _parse_args() -> None:
    """可选：`--uid <signal_uid> --symbol <标的>`，用于铺第二条信号（同一 uid 会被去重拒绝）。"""
    global SYMBOL, SIG_UID
    argv = sys.argv[1:]
    for i, a in enumerate(argv):
        if a == "--uid" and i + 1 < len(argv):
            SIG_UID = argv[i + 1]
        if a == "--symbol" and i + 1 < len(argv):
            SYMBOL = argv[i + 1]


def real_atr(symbol: str) -> tuple[float, int, int]:
    """取最新已闭合 4h bar 的 (high-low) 作为 ATR 近似；返回 (atr, open_time, close_time)。"""
    con = sqlite3.connect(f"file:{os.environ['MARKET_DB']}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        r = con.execute(
            "SELECT open_time, close_time, high, low FROM candles "
            "WHERE exchange='binance' AND market='um' AND symbol=? AND timeframe='4h' "
            "AND is_closed=1 ORDER BY open_time DESC LIMIT 1", (symbol,)).fetchone()
    finally:
        con.close()
    if not r:
        raise SystemExit(f"行情库里没有 {symbol} 的 4h 已闭合 bar，无法给信号定 ATR")
    return float(r["high"]) - float(r["low"]), int(r["open_time"]), int(r["close_time"])


def main() -> int:
    _parse_args()
    L.ensure_live_schema()
    now = int(time.time() * 1000)

    # 水位线：只在未设置时写（幂等），取 3 小时前，确保不晚于本条信号
    if L.get_live_start_ms() is None:
        L.set_run(L.LIVE_START_KEY, now - 3 * 3600 * 1000)
    floor = L.effective_floor_ms()

    atr, b4_open, b4_close = real_atr(SYMBOL)
    # 信号时点用「刚刚闭合的 1h bar」：真实、且晚于有效下限
    confirmed = (now // 3_600_000) * 3_600_000 - 1          # 上一个整点的右端-1ms
    available = confirmed
    payload = {
        "role": "enterable_arm_entry",
        "arm": ARM,
        "atr": atr,
        "box_high_h": None,
        "box_low_l": None,
        "volume_ratio": None,
        "note": "UI-LINK-01 隔离环境铺底信号；ATR 取真实 4h bar 振幅，非策略真实产出",
    }
    con = L.live_conn(ro=False)
    try:
        con.execute(
            "INSERT INTO live_signal(signal_uid, source, origin, exchange, market, symbol,"
            " timeframe, strategy_key, strategy_version, direction, event_time, confirmed_at,"
            " available_at, bar_open_time, bar_close_time, detection_atr, display_state,"
            " executable_state, executable_reason, payload_json, first_seen_at, updated_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(signal_uid) DO UPDATE SET"
            " confirmed_at=excluded.confirmed_at, available_at=excluded.available_at,"
            " detection_atr=excluded.detection_atr, payload_json=excluded.payload_json,"
            " executable_state=excluded.executable_state, updated_at=excluded.updated_at",
            (SIG_UID, "ui_link_01_seed", "live", "binance", "um", SYMBOL, "1h",
             "wyckoff_v0_1", "v0.1.0", "long",
             confirmed, confirmed, available,
             confirmed - 3_600_000, confirmed, atr,
             "displayable", "enterable", "seed_for_ui_link_01",
             json.dumps(payload, ensure_ascii=False), now, now),
        )
        con.commit()
    finally:
        con.close()

    out = {
        "signal_uid": SIG_UID, "symbol": SYMBOL, "arm": ARM,
        "detection_atr": round(atr, 6),
        "atr_source": f"真实 4h bar 振幅 (open_time={b4_open})",
        "confirmed_at": confirmed, "available_at": available,
        "live_start_ms": L.get_live_start_ms(),
        "paper_epoch_ms": L.get_paper_epoch_ms(),
        "effective_floor_ms": floor,
        "signal_right_ms": max(available, confirmed) + 1,
        "passes_floor": (max(available, confirmed) + 1) >= floor,
        "live_db": str(L.LIVE_DB),
        "paper_dir": str(L.LEDGER_DIR),
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if out["passes_floor"] else 1


if __name__ == "__main__":
    sys.exit(main())
