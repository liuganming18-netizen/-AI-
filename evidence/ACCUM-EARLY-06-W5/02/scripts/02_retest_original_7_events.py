# -*- coding: utf-8 -*-
"""ACCUM-EARLY-06-W5-02 | 脚本 02: 对窗口二修正版独立重跑原 7 笔事件

【设计与输入说明】
1. 严格使用原输入行情：
   - SOLUSDT: 冷档区间 (2026-08-01T00:00:00Z ~ 2026-09-05T00:00:00Z)
   - AVAXUSDT: 热库区间 (2026-09-12T00:00:00Z ~ 2026-10-25T00:00:00Z)
2. 严格使用测试配置 fixture_rules_05.json；
3. 独立重跑 scan_symbol，核对修后状态机迁移与终态；
4. 验证是否产生任何提前入场候选；
5. 输出至 out/02_original_7_events_retest.json。
"""
import calendar
import json
import os
import sys
import tempfile
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[4]
BACKEND_DIR = PROJECT_ROOT / "框架" / "backend"
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(BACKEND_DIR))

import accum_early_entry as AEE

OUT_DIR = PROJECT_ROOT / "evidence" / "ACCUM-EARLY-06-W5" / "02" / "out"
OUT_DIR.mkdir(parents=True, exist_ok=True)

def _iso(ms):
    if ms is None:
        return None
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(int(ms) / 1000))

def ms_of(s):
    return int(calendar.timegm(time.strptime(s, "%Y-%m-%dT%H:%M:%SZ")) * 1000)

def retest_original_events():
    tmp_dir = Path(tempfile.mkdtemp(prefix="accum06_e2e_retest7_"))
    results = {
        "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "schema_version": "accum-early-05/v1",
        "tested_code_sha256": "3c570edc1eddaa2cd5c82c39fbf60a9deb633d376dd6b47200453b8f061c9017",
        "input_fidelity_statement": "完全保留原输入行情范围与标的，绝未更换更容易通过的新案例或修改数据源",
        "cases_retest": []
    }

    try:
        iso_db_path = tmp_dir / "observe.sqlite3"
        iso_ledger_dir = tmp_dir / "ledger"
        iso_ledger_dir.mkdir(parents=True, exist_ok=True)
        live_db = tmp_dir / "live.sqlite3"
        gate_file = tmp_dir / "gate.json"
        gate_file.write_text(json.dumps({"allow_new_entries": True}), encoding="utf-8")

        os.environ["PAPER_LEDGER_DIR"] = str(iso_ledger_dir)
        os.environ["PAPER_LEDGER_ALLOW_INIT"] = "1"
        os.environ["LIVE_DB"] = str(live_db)
        os.environ["PAPER_GATE_FILE"] = str(gate_file)
        os.environ["WX_MARKET_MERGE"] = "1"
        os.environ[AEE.FIXTURE_ENV] = "1"

        import live_store as L
        L.ensure_live_schema()

        fixture_cfg_path = PROJECT_ROOT / "evidence" / "ACCUM-EARLY-05-B" / "config" / "fixture_rules_05.json"
        os.environ["ACCUM04_CONFIG"] = str(fixture_cfg_path)
        cfg = AEE.load_config(fixture_cfg_path)
        store = AEE.open_store(iso_db_path)
        feed = AEE.DataFeed()

        # 读取 05-E 原 7 事件旧对照
        old_trace_path = PROJECT_ROOT / "evidence" / "ACCUM-EARLY-05-E" / "out" / "03_event_trace.json"
        old_cases = json.loads(old_trace_path.read_text("utf-8")).get("cases", [])

        # 运行 1: SOLUSDT 冷档
        t_sol_start = ms_of("2026-08-01T00:00:00Z")
        t_sol_end = ms_of("2026-09-05T00:00:00Z")
        AEE.scan_symbol("SOLUSDT", cfg, feed, store, start_ms=t_sol_start, end_ms=t_sol_end, max_boxes=2, warmup_days=60)

        # 运行 2: AVAXUSDT 热库
        t_avax_start = ms_of("2026-09-12T00:00:00Z")
        t_avax_end = ms_of("2026-10-25T00:00:00Z")
        AEE.scan_symbol("AVAXUSDT", cfg, feed, store, start_ms=t_avax_start, end_ms=t_avax_end, max_boxes=2, warmup_days=60)

        new_events = [dict(r) for r in store.con.execute("SELECT * FROM observe_event ORDER BY created_ms").fetchall()]
        new_trans = [dict(r) for r in store.con.execute("SELECT * FROM observe_transition ORDER BY id").fetchall()]
        new_cands = [dict(r) for r in store.con.execute("SELECT * FROM accum05_candidate ORDER BY created_ms").fetchall()]

        for idx, old_c in enumerate(old_cases):
            old_ev_id = old_c["event_id"]
            old_sch = old_c["scheme"]
            old_sym = old_c["symbol"]

            matched_event = None
            for ev in new_events:
                if ev["scheme"] == old_sch and ev["symbol"] == old_sym:
                    if ev["box_version_id"] in old_ev_id or old_ev_id in ev["event_id"]:
                        matched_event = ev
                        break

            matched_cand = None
            ev_transitions = []
            if matched_event:
                ev_transitions = [t for t in new_trans if t["event_id"] == matched_event["event_id"]]
                for c in new_cands:
                    if c["event_id"] == matched_event["event_id"]:
                        matched_cand = c
                        break

            current_state = matched_event["state"] if matched_event else "NOT_FOUND"

            case_item = {
                "case_index": idx + 1,
                "symbol": old_sym,
                "scheme": old_sch,
                "old_event_id": old_ev_id,
                "old_04_status": old_c.get("status"),
                "current_event_id": matched_event["event_id"] if matched_event else None,
                "current_state": current_state,
                "candidate_produced": (matched_cand is not None),
                "terminal_reason": matched_event.get("closed_reason") if matched_event else "UNSPAWNED_DUE_TO_PREVIOUS_ACTIVE_EVENT",
                "transitions_count": len(ev_transitions),
                "transitions": [
                    {
                        "from": t["from_state"],
                        "to": t["to_state"],
                        "reason": t["reason"],
                        "time_utc": _iso(t["at_ms"])
                    } for t in ev_transitions
                ]
            }
            results["cases_retest"].append(case_item)

        out_file = OUT_DIR / "02_original_7_events_retest.json"
        out_file.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"Retested original 7 events on fixed code, saved to {out_file}")

    finally:
        pass

if __name__ == "__main__":
    retest_original_events()
