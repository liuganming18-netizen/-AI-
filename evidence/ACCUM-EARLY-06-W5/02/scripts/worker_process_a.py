# -*- coding: utf-8 -*-
"""ACCUM-EARLY-06-W5-02 | 进程 A 工作脚本 (独立系统进程)"""
import json
import os
import sys
import time
from pathlib import Path

pid = os.getpid()
start_ts = time.time()
start_time_utc = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

PROJECT_ROOT = Path(__file__).resolve().parents[4]
BACKEND_DIR = PROJECT_ROOT / "框架" / "backend"
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(BACKEND_DIR))

import accum_early_entry as AEE

def main():
    if len(sys.argv) < 3:
        print(json.dumps({"error": "Missing args"}))
        sys.exit(1)

    ledger_dir = Path(sys.argv[1])
    cand_file = Path(sys.argv[2])
    cand = json.loads(cand_file.read_text(encoding="utf-8"))

    os.environ["PAPER_LEDGER_DIR"] = str(ledger_dir)
    os.environ["PAPER_LEDGER_ALLOW_INIT"] = "1"
    os.environ["LIVE_DB"] = str(ledger_dir / "live.sqlite3")
    os.environ["PAPER_GATE_FILE"] = str(ledger_dir / "gate.json")
    os.environ["WX_MARKET_MERGE"] = "1"
    os.environ[AEE.FIXTURE_ENV] = "1"
    Path(os.environ["PAPER_GATE_FILE"]).write_text(json.dumps({"allow_new_entries": True}), encoding="utf-8")

    import live_store as L
    L.ensure_live_schema()

    fixture_cfg_path = PROJECT_ROOT / "evidence" / "ACCUM-EARLY-05-B" / "config" / "fixture_rules_05.json"
    os.environ["ACCUM04_CONFIG"] = str(fixture_cfg_path)
    cfg = AEE.load_config(fixture_cfg_path)
    store = AEE.open_store(ledger_dir / "observe.sqlite3")

    first_open = AEE.try_open(cand["scheme"], cand["symbol"], cand, cfg, store, now_ms=cand["entry_bar_open_ms"])
    snap = AEE.ledger_snapshot(cand["scheme"])

    end_ts = time.time()
    end_time_utc = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    out_data = {
        "process_name": "PROCESS_A",
        "pid": pid,
        "start_time_utc": start_time_utc,
        "end_time_utc": end_time_utc,
        "duration_sec": round(end_ts - start_ts, 4),
        "open_result": first_open,
        "snapshot": {
            "orders_count": len(snap.get("orders", [])),
            "fills_count": len(snap.get("fills", [])),
            "positions_count": len(snap.get("positions", [])),
            "positions": snap.get("positions", [])
        },
        "exit_code": 0
    }

    print("__JSON_START__")
    print(json.dumps(out_data, ensure_ascii=False))
    print("__JSON_END__")
    sys.exit(0)

if __name__ == "__main__":
    main()
