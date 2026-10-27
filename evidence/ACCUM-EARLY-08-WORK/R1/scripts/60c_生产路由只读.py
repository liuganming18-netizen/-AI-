# -*- coding: utf-8 -*-
"""60c｜在生产库上真调一次路由（只读效果；账本目录已指向副本）。

为什么可以调：`route_pending_orders` 先 `_scan_actionable`，扫不到就直接返回，
不会碰账本。为了让「万一扫到」的后果也不落到生产，`PAPER_LEDGER_DIR` 由
`60_闸门正反例.py` 指到副本目录。
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

PROJECT = Path("/Users/boos/Desktop/威科夫因子共振趋势")
BACKEND = PROJECT / "框架" / "backend"
for p in (str(BACKEND), str(PROJECT / "框架"), str(PROJECT),
          str(Path(__file__).resolve().parent)):
    if p not in sys.path:
        sys.path.insert(0, p)

import probe_market  # noqa: E402

probe_market.install()

import live_store as LS  # noqa: E402
import unified_runtime as UR  # noqa: E402

R: dict = {}


def main() -> int:
    R["自报"] = {
        "LIVE_DB": str(Path(LS.LIVE_DB).resolve()),
        "is_production": Path(LS.LIVE_DB).resolve()
        == (BACKEND / "live" / "live_signals.sqlite3").resolve(),
        "PAPER_LEDGER_DIR": str(Path(LS.LEDGER_DIR).resolve()),
        "pid": os.getpid(),
    }
    router = UR.SignalPersistenceAndRouter(arm="3a")
    now = int(time.time() * 1000)
    R["scan"] = {
        "scan(None)": len(router._scan_actionable(LS.live_conn(), None)),
        "scan(live)": len(router._scan_actionable(LS.live_conn(), "live")),
        "scan(recovered)": len(router._scan_actionable(LS.live_conn(), "recovered")),
        "scan(historical_research)": len(
            router._scan_actionable(LS.live_conn(), "historical_research")),
    }
    for origin in ("live", "recovered", "historical_research", "replay"):
        try:
            R[f"route({origin})"] = router.route_pending_orders(now, origin=origin)
        except Exception as exc:  # noqa: BLE001
            R[f"route({origin})"] = {"error": f"{type(exc).__name__}: {exc}"}
    try:
        R["新开仓闸门"] = {k: v for k, v in (UR.PE.new_entry_gate() or {}).items()
                       if k in ("allow_new_entries", "reason", "set_by", "since_ms")}
    except Exception as exc:  # noqa: BLE001
        R["新开仓闸门"] = {"error": f"{type(exc).__name__}: {exc}"}
    R["行情库写探针"] = probe_market.report()
    print("@@RESULT@@" + json.dumps(R, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
