# -*- coding: utf-8 -*-
"""降级验证：纸面账本目录存在、但库是空壳（缺 paper_order 表）时，
当前信号区**不得整体失败**，更不得把错误谎报成「信号库未初始化」。

复现的原始缺陷（修复前实测）：
  账本目录存在 ⇒ ATTACH 成功 ⇒ SQL 引用 `sig_3a.paper_order` ⇒ no such table
  ⇒ 整条 SELECT 抛 OperationalError ⇒ 被 except 当成「live_signals 未初始化」
  ⇒ 页面信号区空白，信号明明已落库却看不到。
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path("/Users/boos/Desktop/威科夫因子共振趋势")
BACKEND = ROOT / "框架" / "backend"
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(ROOT))

TMP = Path(tempfile.mkdtemp(prefix="rt_deg_"))
LIVE_DIR = TMP / "live"
LEDGER = TMP / "paper"
LIVE_DIR.mkdir(parents=True, exist_ok=True)
LEDGER.mkdir(parents=True, exist_ok=True)
# 空壳账本：文件存在、但没有任何表
for arm in ("3a", "3b", "3c", "3d"):
    sqlite3.connect(str(LEDGER / f"paper_{arm}.sqlite3")).close()

os.environ.update({
    "LIVE_DIR": str(LIVE_DIR),
    "LIVE_DB": str(LIVE_DIR / "live_signals.sqlite3"),
    "PAPER_LEDGER_DIR": str(LEDGER),
    "PAPER_GATE_FILE": str(TMP / "gate.json"),
    "MARKET_DB": str(TMP / "market.sqlite3"),
})

import live_store as L  # noqa: E402

L.ensure_live_schema()
NOW = int(time.time() * 1000)
T0 = NOW - 3 * 3600 * 1000
L.set_run("live_start_ms", T0)
L.set_run("paper_epoch_ms", T0)

con = L.live_conn()
con.execute(
    "INSERT INTO live_signal(signal_uid,source,origin,exchange,market,symbol,timeframe,"
    "strategy_key,direction,event_time,confirmed_at,available_at,display_state,"
    "executable_state,payload_json,first_seen_at,updated_at) "
    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
    ("cand_deg_1", "wyckoff_v0_1", "live", "binance", "um", "BTCUSDT", "1h",
     "wyckoff_v0_1", "long", NOW - 60_000, NOW - 60_000, NOW - 60_000,
     "displayable", "enterable",
     json.dumps({"role": "enterable_arm_entry", "arm": "3a"}), NOW, NOW))
con.commit()
con.close()

import live_api  # noqa: E402

res = live_api.live_signals({})
out = {
    "paper_ledger_shell_only": True,
    "api_total": res.get("total"),
    "api_items": len(res.get("items") or []),
    "missing_reason": res.get("missing_reason"),
    "quality_status": (res.get("quality") or {}).get("status"),
    "item_role": ((res.get("items") or [{}])[0]).get("role"),
    "verdict": "PASS" if (res.get("total") == 1 and not res.get("missing_reason")) else "FAIL",
}
print(json.dumps(out, ensure_ascii=False, indent=2))
shutil.rmtree(TMP, ignore_errors=True)
