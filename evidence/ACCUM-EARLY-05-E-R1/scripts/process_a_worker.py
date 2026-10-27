# -*- coding: utf-8 -*-
"""ACCUM-EARLY-05-E-R1 | 进程 A 独立可执行脚本 (用于真正跨进程恢复测试)

职责：
1. 作为独立子进程启动，记录自身 PID、启动挂钟时间；
2. 正常读取 05 版 fixture 配置，正常通过配置校验 (ACCUM05_FIXTURE_OK=1)；
3. 首次提交候选 (try_open)，产生初始订单、成交与持仓；
4. 记录数据库持久化状态与快照；
5. 正常退出，返回退出码 0。
"""
import json
import os
import sys
import time
from pathlib import Path

start_time_utc = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
start_ts = time.time()
pid = os.getpid()

PROJECT_ROOT = Path(__file__).resolve().parents[3]
BACKEND_DIR = PROJECT_ROOT / "框架" / "backend"
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(BACKEND_DIR))

import accum_early_entry as AEE

def run_process_a():
    if len(sys.argv) < 3:
        print(json.dumps({"error": "Missing arguments", "pid": pid}))
        sys.exit(1)

    ledger_dir = Path(sys.argv[1])
    cand_file = Path(sys.argv[2])
    cand = json.loads(cand_file.read_text(encoding="utf-8"))

    # 设置隔离环境变量
    os.environ["PAPER_LEDGER_DIR"] = str(ledger_dir)
    os.environ["PAPER_LEDGER_ALLOW_INIT"] = "1"
    os.environ["LIVE_DB"] = str(ledger_dir / "live.sqlite3")
    os.environ["PAPER_GATE_FILE"] = str(ledger_dir / "gate.json")
    os.environ["WX_MARKET_MERGE"] = "1"
    os.environ[AEE.FIXTURE_ENV] = "1"
    Path(os.environ["PAPER_GATE_FILE"]).write_text(json.dumps({"allow_new_entries": True}), encoding="utf-8")

    import live_store as L
    L.ensure_live_schema()

    # 正常加载配置，走正常校验链路，绝不手动篡改 open_allowed
    fixture_cfg_path = PROJECT_ROOT / "evidence" / "ACCUM-EARLY-05-B" / "config" / "fixture_rules_05.json"
    os.environ["ACCUM04_CONFIG"] = str(fixture_cfg_path)
    cfg = AEE.load_config(fixture_cfg_path)

    store = AEE.open_store(ledger_dir / "observe.sqlite3")

    # 首次提交候选
    first_open = AEE.try_open(cand["scheme"], cand["symbol"],
                              cand, cfg, store,
                              now_ms=cand.get("entry_bar_open_ms"))

    snap = AEE.ledger_snapshot(cand["scheme"])

    end_time_utc = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    end_ts = time.time()

    out_payload = {
        "process_name": "PROCESS_A",
        "pid": pid,
        "start_time_utc": start_time_utc,
        "end_time_utc": end_time_utc,
        "duration_sec": round(end_ts - start_ts, 4),
        "cfg_open_allowed_natural": cfg.get("open_allowed"),
        "open_scope": cfg.get("open_scope"),
        "open_result": first_open,
        "snapshot": {
            "orders_count": len(snap.get("orders", [])),
            "fills_count": len(snap.get("fills", [])),
            "positions_count": len(snap.get("positions", [])),
            "positions": snap.get("positions", [])
        },
        "exit_code": 0
    }

    # 打印最终输出 JSON供主进程解析
    print("__OUTPUT_START__")
    print(json.dumps(out_payload, ensure_ascii=False))
    print("__OUTPUT_END__")
    sys.exit(0)

if __name__ == "__main__":
    run_process_a()
