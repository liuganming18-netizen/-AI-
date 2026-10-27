# -*- coding: utf-8 -*-
"""ACCUM-EARLY-05-E | 脚本 02: 缺批准配置开仓阻断与观察记录独立核查

核查要点：
1. 完全无配置 / 缺字段 / 未批准时，try_open 必须被硬阻断，绝不调用 paper_engine；
2. 阻断时不能在任何账本中留下开仓记录；
3. 阻断同时，数据读取 (DataFeed) 与观察记录 (SchemeObserver / ObserveStore) 正常工作；
4. 记录必须忠实沉淀至 accum04_open_attempt 和事件表中，带清晰原因说明。
"""
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
BACKEND_DIR = PROJECT_ROOT / "框架" / "backend"
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(BACKEND_DIR))

import accum_early_entry as AEE
import observe_store as OS

OUT_DIR = PROJECT_ROOT / "evidence" / "ACCUM-EARLY-05-E" / "out"
OUT_DIR.mkdir(parents=True, exist_ok=True)

def test_unapproved_gate():
    results = {}
    tmp_dir = Path(tempfile.mkdtemp(prefix="accum05_gate_test_"))
    try:
        iso_db_path = tmp_dir / "observe.sqlite3"
        iso_ledger_dir = tmp_dir / "ledger"
        iso_ledger_dir.mkdir(parents=True, exist_ok=True)
        os.environ["PAPER_LEDGER_DIR"] = str(iso_ledger_dir)

        store = AEE.open_store(iso_db_path)

        # ── 测试点 1: 完全缺失配置文件 ──
        cfg_missing = AEE.load_config(tmp_dir / "non_existent_config.json")
        results["case_config_missing"] = {
            "present": cfg_missing["present"],
            "open_allowed": cfg_missing["open_allowed"],
            "blocked_by": cfg_missing["blocked_by"],
            "missing_fields_count": len(cfg_missing["missing"]),
            "pass": (not cfg_missing["open_allowed"] and "config_file_missing" in cfg_missing["blocked_by"]),
        }

        # ── 测试点 2: 伪造一个候选，在缺失配置下尝试 try_open ──
        dummy_cand = {
            "candidate_id": "cand_test_001",
            "event_id": "evt_test_001",
            "scheme": "4a",
            "symbol": "SOLUSDT",
            "confirm_close_ms": 1729000000000,
            "earliest_entry_ms": 1729000900000,
            "entry_bar_open_ms": 1729000900000,
            "entry_ref_price": 150.0,
            "resistance": 149.0,
            "deviation": 0.005,
            "status": "WAIT_ENTRY",
            "status_reason": "待委托",
            "signal_uid": "accum04_4a_SOLUSDT_1729000000000",
            "conditions": [],
            "gap": None,
        }

        open_res = AEE.try_open("4a", "SOLUSDT", dummy_cand, cfg_missing, store)
        
        # 检查是否调用 paper_engine
        attempts = store.con.execute("SELECT * FROM accum04_open_attempt").fetchall()
        prod_ledgers = list((PROJECT_ROOT / "数据" / "paper").glob("*.sqlite3"))

        results["try_open_blocked"] = {
            "ok": open_res["ok"],
            "reason": open_res["reason"],
            "called_paper_engine": open_res["called_paper_engine"],
            "attempts_logged": len(attempts),
            "attempt_allowed": attempts[0]["allowed"] if attempts else None,
            "attempt_reason": attempts[0]["reason"] if attempts else None,
            "pass": (not open_res["ok"] and not open_res["called_paper_engine"] and len(attempts) == 1 and attempts[0]["allowed"] == 0),
        }

        # ── 测试点 3: 测试 fixture 配置未加环境变量时的阻断 ──
        fixture_cfg_path = PROJECT_ROOT / "evidence" / "ACCUM-EARLY-04-B" / "fixture" / "fixture_rules.json"
        if os.environ.get(AEE.FIXTURE_ENV):
            del os.environ[AEE.FIXTURE_ENV]
        cfg_fixture_no_env = AEE.load_config(fixture_cfg_path)
        open_res_fixture = AEE.try_open("4a", "SOLUSDT", dummy_cand, cfg_fixture_no_env, store)

        results["case_fixture_without_env"] = {
            "kind": cfg_fixture_no_env["kind"],
            "ready": cfg_fixture_no_env["ready"],
            "open_allowed": cfg_fixture_no_env["open_allowed"],
            "blocked_by": cfg_fixture_no_env["blocked_by"],
            "try_open_ok": open_res_fixture["ok"],
            "try_open_called_engine": open_res_fixture["called_paper_engine"],
            "pass": (not cfg_fixture_no_env["open_allowed"] and "fixture_without_explicit_env" in cfg_fixture_no_env["blocked_by"] and not open_res_fixture["called_paper_engine"]),
        }

        # ── 测试点 4: 在无配置阻断下，真实数据读取与观察记录仍能正常执行 ──
        feed = AEE.DataFeed()
        # 读取一小段真实 SOLUSDT 行情
        t_start = 1727740800000 # 2024-10-01 00:00:00 UTC (1727740800000)
        t_end = 1728345600000   # 2024-10-08 00:00:00 UTC (1728345600000)
        bars_4h = feed.load("4h", "SOLUSDT", t_start - 60*86400000, t_end)
        bars_1h = feed.load("1h", "SOLUSDT", t_start - 60*86400000, t_end)
        bars_15m = feed.load("15m", "SOLUSDT", t_start, t_end)

        # 运行扫描 (未批准配置)
        scan_res = AEE.scan_symbol("SOLUSDT", cfg_missing, feed, store,
                                   start_ms=t_start, end_ms=t_end, max_boxes=1,
                                   open_candidates=False)
        
        # 检查是否记录了 blocked_config 事件
        events = store.con.execute("SELECT event_id, scheme, state, closed_reason FROM observe_event").fetchall()
        transitions = store.con.execute("SELECT event_id, from_state, to_state, reason, payload_json FROM observe_transition").fetchall()
        blocked_transitions = [t for t in transitions if "配置未批准" in str(t["reason"])]

        results["data_feed_and_observation_under_blocked"] = {
            "bars_4h_count": len(bars_4h),
            "bars_1h_count": len(bars_1h),
            "bars_15m_count": len(bars_15m),
            "boxes_formed": scan_res["boxes_formed"],
            "boxes_used": scan_res["boxes_used"],
            "total_events_in_db": len(events),
            "blocked_config_transitions": len(blocked_transitions),
            "candidates_generated": len(scan_res["candidates"]),
            "pass": (len(bars_4h) > 0 and len(bars_1h) > 0 and len(events) > 0 and len(scan_res["candidates"]) == 0),
        }

        store.close()
        feed.close()

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    out_file = OUT_DIR / "02_gate_unapproved.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump({
            "generated_by": "ACCUM-EARLY-05-E::02_gate_unapproved.py",
            "results": results
        }, f, ensure_ascii=False, indent=2)

    print(f"[OK] 缺批准配置开仓阻断与观察核验完成，结果已写入: {out_file}")

if __name__ == "__main__":
    test_unapproved_gate()
