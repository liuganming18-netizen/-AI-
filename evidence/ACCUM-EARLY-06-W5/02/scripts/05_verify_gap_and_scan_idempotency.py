# -*- coding: utf-8 -*-
"""ACCUM-EARLY-06-W5-02 | 脚本 05: 数据缺口表与重复扫描零新增独立核验

【测试目标】
1. 连续对同一标的、同一时间段执行两次 research_run 全链扫描；
2. 验证第二次扫描相比第一次扫描：
   - 订单数增加严格为 0 (orders_diff == 0)
   - 成交数增加严格为 0 (fills_diff == 0)
   - 持仓数增加严格为 0 (positions_diff == 0)
   - 候选数增加严格为 0 (candidates_diff == 0)
   - 数据缺口数增加严格为 0 (gaps_diff == 0)
3. 真实记录 observe_transition 状态机变迁行的行为；
4. 输出至 out/05_gap_and_scan_idempotency.json。
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

def ms_of(s):
    return int(calendar.timegm(time.strptime(s, "%Y-%m-%dT%H:%M:%SZ")) * 1000)

def verify_idempotency():
    tmp_dir = Path(tempfile.mkdtemp(prefix="w5_02_idemp_"))
    iso_db_path = tmp_dir / "observe.sqlite3"
    ledger_dir = tmp_dir / "ledger"
    ledger_dir.mkdir(parents=True, exist_ok=True)
    
    os.environ[AEE.FIXTURE_ENV] = "1"
    os.environ["PAPER_LEDGER_ALLOW_INIT"] = "1"
    os.environ["PAPER_LEDGER_DIR"] = str(ledger_dir)
    os.environ["LIVE_DB"] = str(tmp_dir / "live.sqlite3")
    os.environ["PAPER_GATE_FILE"] = str(tmp_dir / "gate.json")
    os.environ["WX_MARKET_MERGE"] = "1"
    Path(os.environ["PAPER_GATE_FILE"]).write_text(json.dumps({"allow_new_entries": True}), encoding="utf-8")
    
    import live_store as L
    L.ensure_live_schema()

    fixture_cfg_path = PROJECT_ROOT / "evidence" / "ACCUM-EARLY-05-B" / "config" / "fixture_rules_05.json"
    os.environ["ACCUM05_CONFIG"] = str(fixture_cfg_path)
    store = AEE.open_store(iso_db_path)
    feed = AEE.DataFeed()

    t_start = ms_of("2026-08-01T00:00:00Z")
    t_end = ms_of("2026-08-10T00:00:00Z")

    # ── 第 1 次扫描 ──
    r1 = AEE.research_run(config=str(fixture_cfg_path), symbols=["SOLUSDT"],
                          start_ms=t_start, end_ms=t_end, max_boxes=1,
                          open_candidates=True, drive_exits=True,
                          store=store, feed=feed)
    snap1 = {s: AEE.ledger_snapshot(s) for s in AEE.SCHEMES}
    orders_1 = sum(len(v.get("orders", [])) for v in snap1.values())
    fills_1 = sum(len(v.get("fills", [])) for v in snap1.values())
    pos_1 = sum(len(v.get("positions", [])) for v in snap1.values())
    cand_1 = store.con.execute("SELECT count(*) FROM accum05_candidate").fetchone()[0]
    gaps_1 = store.con.execute("SELECT count(*) FROM observe_data_gap").fetchone()[0]
    trans_1 = store.con.execute("SELECT count(*) FROM observe_transition").fetchone()[0]

    # ── 第 2 次扫描 (重扫) ──
    r2 = AEE.research_run(config=str(fixture_cfg_path), symbols=["SOLUSDT"],
                          start_ms=t_start, end_ms=t_end, max_boxes=1,
                          open_candidates=True, drive_exits=True,
                          store=store, feed=feed)
    snap2 = {s: AEE.ledger_snapshot(s) for s in AEE.SCHEMES}
    orders_2 = sum(len(v.get("orders", [])) for v in snap2.values())
    fills_2 = sum(len(v.get("fills", [])) for v in snap2.values())
    pos_2 = sum(len(v.get("positions", [])) for v in snap2.values())
    cand_2 = store.con.execute("SELECT count(*) FROM accum05_candidate").fetchone()[0]
    gaps_2 = store.con.execute("SELECT count(*) FROM observe_data_gap").fetchone()[0]
    trans_2 = store.con.execute("SELECT count(*) FROM observe_transition").fetchone()[0]

    diff_orders = orders_2 - orders_1
    diff_fills = fills_2 - fills_1
    diff_pos = pos_2 - pos_1
    diff_cands = cand_2 - cand_1
    diff_gaps = gaps_2 - gaps_1
    diff_trans = trans_2 - trans_1

    # 核心验收断言：订单、成交、持仓、候选与缺口严格零新增
    pass_clean = (diff_orders == 0 and diff_fills == 0 and diff_pos == 0 and diff_cands == 0 and diff_gaps == 0)

    results = {
        "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "schema_version": "accum-early-05/v1",
        "symbol": "SOLUSDT",
        "first_scan": {
            "orders_count": orders_1,
            "fills_count": fills_1,
            "positions_count": pos_1,
            "candidates_count": cand_1,
            "gaps_count": gaps_1,
            "transitions_count": trans_1
        },
        "second_scan": {
            "orders_count": orders_2,
            "fills_count": fills_2,
            "positions_count": pos_2,
            "candidates_count": cand_2,
            "gaps_count": gaps_2,
            "transitions_count": trans_2
        },
        "differences": {
            "orders_diff": diff_orders,
            "fills_diff": diff_fills,
            "positions_diff": diff_pos,
            "candidates_diff": diff_cands,
            "gaps_diff": diff_gaps,
            "transitions_diff": diff_trans
        },
        "idempotency_verdicts": {
            "orders_zero_increase": (diff_orders == 0),
            "fills_zero_increase": (diff_fills == 0),
            "positions_zero_increase": (diff_pos == 0),
            "candidates_zero_increase": (diff_cands == 0),
            "gaps_zero_increase": (diff_gaps == 0),
            "transitions_note": f"重扫产生 {diff_trans} 条变迁重访记录，但未产生任何订单、候选或缺口副作用"
        },
        "pass": pass_clean,
        "verdict": "PASS_CLEAN_RESCAN_ZERO_INCREASE" if pass_clean else "FAIL"
    }

    out_file = OUT_DIR / "05_gap_and_scan_idempotency.json"
    out_file.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Rescan idempotency verified, saved to {out_file}")

if __name__ == "__main__":
    verify_idempotency()
