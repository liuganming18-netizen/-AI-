# -*- coding: utf-8 -*-
"""ACCUM-EARLY-05-B | 07 经济口径一致性校验与开仓闸门隔离测试

验证目标（用户第 2、3 项要求）：
1. 完整调用链一致性校验：风险、仓位、参与率、滑点等声明与引擎实际不一致时，
   硬阻塞开仓并列明具体差异，绝不静默按另一套值执行；
2. 证明：一致配置可继续执行；
3. 证明：故意不一致配置（滑点/风控/参与率各维度）明确阻塞且不调用 paper_engine；
4. 证明：缺批准配置仍可读数据、记录观察，但绝不能开仓。
"""
import copy
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
BACKEND_DIR = PROJECT_ROOT / "框架" / "backend"
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(BACKEND_DIR))

import accum_early_entry as AEE
import live_store as L

OUT_DIR = PROJECT_ROOT / "evidence" / "ACCUM-EARLY-05-B" / "out"
OUT_DIR.mkdir(parents=True, exist_ok=True)

def run_tests():
    tmp_dir = Path(tempfile.mkdtemp(prefix="accum05b_econ_test_"))
    results = {}

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

        L.ensure_live_schema()
        store = AEE.open_store(iso_db_path)

        fixture_05_path = PROJECT_ROOT / "evidence" / "ACCUM-EARLY-05-B" / "config" / "fixture_rules_05.json"
        base_cfg_doc = json.loads(fixture_05_path.read_text(encoding="utf-8"))

        dummy_cand = {
            "candidate_id": "cand_econ_test_001",
            "event_id": "evt_econ_test_001",
            "scheme": "4a",
            "symbol": "SOLUSDT",
            "confirm_close_ms": 1785553199999,
            "earliest_entry_ms": 1785554100000,
            "entry_bar_open_ms": 1785554100000,
            "entry_ref_price": 73.1,
            "resistance": 73.0,
            "deviation": 0.0,
            "status": "WAIT_ENTRY",
            "status_reason": "待委托",
            "signal_uid": "accum04_4a_SOLUSDT_1785553199999",
        }

        # ── 1. 测试用例 A: 一致配置（全为 effective_view，与引擎参数完全一致） ──
        cfg_path_consistent = tmp_dir / "cfg_consistent.json"
        cfg_path_consistent.write_text(json.dumps(base_cfg_doc, ensure_ascii=False), encoding="utf-8")
        loaded_consistent = AEE.load_config(cfg_path_consistent)

        open_res_consistent = AEE.try_open("4a", "SOLUSDT", dummy_cand, loaded_consistent, store,
                                           now_ms=dummy_cand["entry_bar_open_ms"])

        results["case_A_consistent_config"] = {
            "open_allowed": loaded_consistent["open_allowed"],
            "declared_mismatch": loaded_consistent["economic_caliber"]["declared_mismatch"],
            "blocked_by": loaded_consistent["blocked_by"],
            "try_open_ok": open_res_consistent["ok"],
            "called_paper_engine": open_res_consistent["called_paper_engine"],
            "pass": (loaded_consistent["open_allowed"] is True
                     and loaded_consistent["economic_caliber"]["declared_mismatch"] is None
                     and open_res_consistent["called_paper_engine"] is True),
        }

        # ── 2. 测试用例 B1: 故意滑点不一致 (slippage_source = strategy_package) ──
        doc_slip_mismatch = copy.deepcopy(base_cfg_doc)
        doc_slip_mismatch["conflict_resolution"]["slippage_source"] = "strategy_package"
        cfg_path_slip = tmp_dir / "cfg_slip_mismatch.json"
        cfg_path_slip.write_text(json.dumps(doc_slip_mismatch, ensure_ascii=False), encoding="utf-8")
        loaded_slip = AEE.load_config(cfg_path_slip)

        dummy_cand_slip = copy.deepcopy(dummy_cand)
        dummy_cand_slip["signal_uid"] = "accum04_4a_SOLUSDT_slip_test"
        open_res_slip = AEE.try_open("4a", "SOLUSDT", dummy_cand_slip, loaded_slip, store)

        results["case_B1_slippage_mismatch"] = {
            "open_allowed": loaded_slip["open_allowed"],
            "blocked_by": loaded_slip["blocked_by"],
            "mismatches": loaded_slip["economic_caliber"]["mismatches"],
            "try_open_ok": open_res_slip["ok"],
            "try_open_reason": open_res_slip["reason"],
            "called_paper_engine": open_res_slip["called_paper_engine"],
            "pass": (loaded_slip["open_allowed"] is False
                     and "economic_caliber_declared_mismatch" in loaded_slip["blocked_by"]
                     and open_res_slip["called_paper_engine"] is False
                     and "slippage_source" in loaded_slip["economic_caliber"]["mismatch_details"]),
        }

        # ── 3. 测试用例 B2: 故意风控与仓位不一致 (risk_config = strategy_package) ──
        doc_risk_mismatch = copy.deepcopy(base_cfg_doc)
        doc_risk_mismatch["conflict_resolution"]["risk_config"] = "strategy_package"
        cfg_path_risk = tmp_dir / "cfg_risk_mismatch.json"
        cfg_path_risk.write_text(json.dumps(doc_risk_mismatch, ensure_ascii=False), encoding="utf-8")
        loaded_risk = AEE.load_config(cfg_path_risk)

        dummy_cand_risk = copy.deepcopy(dummy_cand)
        dummy_cand_risk["signal_uid"] = "accum04_4a_SOLUSDT_risk_test"
        open_res_risk = AEE.try_open("4a", "SOLUSDT", dummy_cand_risk, loaded_risk, store)

        results["case_B2_risk_config_mismatch"] = {
            "open_allowed": loaded_risk["open_allowed"],
            "blocked_by": loaded_risk["blocked_by"],
            "mismatches": loaded_risk["economic_caliber"]["mismatches"],
            "try_open_ok": open_res_risk["ok"],
            "try_open_reason": open_res_risk["reason"],
            "called_paper_engine": open_res_risk["called_paper_engine"],
            "pass": (loaded_risk["open_allowed"] is False
                     and "economic_caliber_declared_mismatch" in loaded_risk["blocked_by"]
                     and open_res_risk["called_paper_engine"] is False
                     and "risk_config" in loaded_risk["economic_caliber"]["mismatch_details"]),
        }

        # ── 4. 测试用例 B3: 故意参与率不一致 (participation_cap = strategy_package) ──
        doc_part_mismatch = copy.deepcopy(base_cfg_doc)
        doc_part_mismatch["conflict_resolution"]["participation_cap"] = "strategy_package"
        cfg_path_part = tmp_dir / "cfg_part_mismatch.json"
        cfg_path_part.write_text(json.dumps(doc_part_mismatch, ensure_ascii=False), encoding="utf-8")
        loaded_part = AEE.load_config(cfg_path_part)

        dummy_cand_part = copy.deepcopy(dummy_cand)
        dummy_cand_part["signal_uid"] = "accum04_4a_SOLUSDT_part_test"
        open_res_part = AEE.try_open("4a", "SOLUSDT", dummy_cand_part, loaded_part, store)

        results["case_B3_participation_cap_mismatch"] = {
            "open_allowed": loaded_part["open_allowed"],
            "blocked_by": loaded_part["blocked_by"],
            "mismatches": loaded_part["economic_caliber"]["mismatches"],
            "try_open_ok": open_res_part["ok"],
            "try_open_reason": open_res_part["reason"],
            "called_paper_engine": open_res_part["called_paper_engine"],
            "pass": (loaded_part["open_allowed"] is False
                     and "economic_caliber_declared_mismatch" in loaded_part["blocked_by"]
                     and open_res_part["called_paper_engine"] is False
                     and "participation_cap" in loaded_part["economic_caliber"]["mismatch_details"]),
        }

        # ── 5. 测试用例 C: 缺批准配置（空白表单），仍可读数据与记录观察 ──
        blank_form_path = PROJECT_ROOT / "evidence" / "ACCUM-EARLY-05-B" / "config" / "批准配置_空白表单_05.json"
        loaded_blank = AEE.load_config(blank_form_path)

        dummy_cand_blank = copy.deepcopy(dummy_cand)
        dummy_cand_blank["signal_uid"] = "accum04_4a_SOLUSDT_blank_test"
        open_res_blank = AEE.try_open("4a", "SOLUSDT", dummy_cand_blank, loaded_blank, store)

        feed = AEE.DataFeed()
        t_start = 1785542400000 # 2026-08-01T00:00:00Z
        t_end = 1785715200000   # 2026-08-03T00:00:00Z
        bars_4h = feed.load("4h", "SOLUSDT", t_start - 60*86400000, t_end)
        bars_1h = feed.load("1h", "SOLUSDT", t_start, t_end)

        scan_res_blank = AEE.scan_symbol("SOLUSDT", loaded_blank, feed, store,
                                         start_ms=t_start, end_ms=t_end, max_boxes=1,
                                         open_candidates=False)

        events_blank = store.con.execute(
            "SELECT count(*) as cnt FROM observe_event WHERE strategy_version LIKE '%05b%'"
        ).fetchone()["cnt"]

        results["case_C_missing_config_behavior"] = {
            "open_allowed": loaded_blank["open_allowed"],
            "missing_count": len(loaded_blank["missing"]),
            "blocked_by": loaded_blank["blocked_by"],
            "try_open_ok": open_res_blank["ok"],
            "called_paper_engine": open_res_blank["called_paper_engine"],
            "bars_4h_loaded": len(bars_4h),
            "bars_1h_loaded": len(bars_1h),
            "events_logged": events_blank,
            "candidates_generated": len(scan_res_blank["candidates"]),
            "pass": (loaded_blank["open_allowed"] is False
                     and open_res_blank["called_paper_engine"] is False
                     and len(bars_4h) > 0
                     and len(bars_1h) > 0
                     and len(scan_res_blank["candidates"]) == 0),
        }

        store.close()
        feed.close()

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    out_file = OUT_DIR / "07_经济口径一致性校验.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump({
            "generated_by": "ACCUM-EARLY-05-B::07_经济口径一致性校验.py",
            "results": results
        }, f, ensure_ascii=False, indent=2)

    print(f"[OK] 经济口径一致性校验测试完成，结果已写入: {out_file}")

if __name__ == "__main__":
    run_tests()
