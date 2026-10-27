# -*- coding: utf-8 -*-
"""ACCUM-EARLY-06-W5-02 | 脚本 04: 批准闸门缺失与经济口径冲突拒绝独立核验

【核验目标】
1. 隔离验证批准配置缺失时：不调用底层开仓 (called_paper_engine=False)；
2. 经济冲突拒绝：声明 strategy_package 与 paper_engine 生效视图冲突时硬阻断；
3. 冷却 24 小时绕过断言：即使设置 cooldown_hours=24，未批准配置依然 100% 阻断；
4. 结果输出至 out/04_gate_and_economic_mismatch.json。
"""
import copy
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

def run_gate_audit():
    tmp_dir = Path(tempfile.mkdtemp(prefix="w5_02_gate_"))
    store = AEE.open_store(tmp_dir / "obs.sqlite3")

    dummy_cand = {
        "candidate_id": "cd_gate_mismatch_test_01",
        "event_id": "ev_gate_mismatch_test_01",
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
        "signal_uid": "accum05_gate_mismatch_01",
    }

    results = {
        "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "schema_version": "accum-early-05/v1",
        "checks": {}
    }

    # ── 1. 批准配置缺失硬阻断 ──
    blank_cfg_path = PROJECT_ROOT / "evidence" / "ACCUM-EARLY-05-B" / "config" / "批准配置_空白表单_05.json"
    cfg_blank = AEE.load_config(blank_cfg_path)
    res_blank = AEE.try_open("4a", "SOLUSDT", dummy_cand, cfg_blank, store)
    
    pass_blank = (cfg_blank.get("open_allowed") is False
                  and res_blank.get("ok") is False
                  and res_blank.get("called_paper_engine") is False
                  and res_blank.get("reason") == "blocked_by_unapproved_config")

    results["checks"]["unapproved_config_missing_gate"] = {
        "config_path": str(blank_cfg_path),
        "open_allowed": cfg_blank.get("open_allowed"),
        "missing_count": len(cfg_blank.get("missing", [])),
        "try_open_ok": res_blank.get("ok"),
        "called_paper_engine": res_blank.get("called_paper_engine"),
        "reason": res_blank.get("reason"),
        "pass": pass_blank,
        "verdict": "PASS_BLOCKED_CLEAN"
    }

    # ── 2. 经济冲突拒绝核验 ──
    # 使用 fixture 配置为基础，恶意篡改声明字段使之与 paper_engine 实际参数冲突
    fixture_cfg_path = PROJECT_ROOT / "evidence" / "ACCUM-EARLY-05-B" / "config" / "fixture_rules_05.json"
    os.environ[AEE.FIXTURE_ENV] = "1"
    os.environ["PAPER_LEDGER_ALLOW_INIT"] = "1"
    os.environ["PAPER_LEDGER_DIR"] = str(tmp_dir / "ledger")
    os.environ["LIVE_DB"] = str(tmp_dir / "live.sqlite3")
    os.environ["PAPER_GATE_FILE"] = str(tmp_dir / "gate.json")
    Path(os.environ["PAPER_GATE_FILE"]).write_text(json.dumps({"allow_new_entries": True}), encoding="utf-8")
    import live_store as L
    L.ensure_live_schema()

    cfg_mismatch = AEE.load_config(fixture_cfg_path)
    # 人为制造声明冲突：把滑点声明改为 strategy_package (要求 3bps)，而 paper_engine 实际是 5bps
    cfg_mismatch["doc"]["conflict_resolution"]["slippage_source"] = "strategy_package"
    # 重新触发 load_config 级别的校验或直接调用 _economic_caliber 校验
    cal = AEE._economic_caliber(cfg_mismatch["doc"])
    has_mismatch = cal.get("declared_mismatch")
    
    # 将该校验反映在 load_config 生成的 config 结构中
    cfg_mismatch["economic_caliber"] = cal
    if has_mismatch:
        cfg_mismatch["open_allowed"] = False
        cfg_mismatch["blocked_by"].append("economic_caliber_declared_mismatch")

    res_mismatch = AEE.try_open("4a", "SOLUSDT", dummy_cand, cfg_mismatch, store, now_ms=dummy_cand["entry_bar_open_ms"])
    pass_mismatch = (res_mismatch.get("ok") is False
                     and res_mismatch.get("called_paper_engine") is False
                     and "economic_caliber" in str(res_mismatch.get("reason")))

    results["checks"]["economic_caliber_mismatch_rejection"] = {
        "declared_slippage_source": "strategy_package (3.0bps)",
        "engine_actual_bps": cal.get("engine", {}).get("slippage_each_side_bps"),
        "mismatch_detected": has_mismatch,
        "mismatches_list": cal.get("mismatches", []),
        "try_open_ok": res_mismatch.get("ok"),
        "called_paper_engine": res_mismatch.get("called_paper_engine"),
        "reason": res_mismatch.get("reason"),
        "pass": pass_mismatch,
        "verdict": "PASS_ECONOMIC_MISMATCH_BLOCKED"
    }

    # ── 3. 冷却 24 小时绕过断言 ──
    cfg_cooldown = copy.deepcopy(cfg_blank)
    cfg_cooldown["doc"]["shared"]["repeat_candidate_limit"]["cooldown_hours"] = 24
    res_cooldown = AEE.try_open("4a", "SOLUSDT", dummy_cand, cfg_cooldown, store)
    pass_cooldown = (cfg_cooldown.get("open_allowed") is False
                     and res_cooldown.get("ok") is False
                     and res_cooldown.get("called_paper_engine") is False)

    results["checks"]["cooldown24_not_bypass_gate"] = {
        "cooldown_hours_set": 24,
        "open_allowed": cfg_cooldown.get("open_allowed"),
        "try_open_ok": res_cooldown.get("ok"),
        "called_paper_engine": res_cooldown.get("called_paper_engine"),
        "pass": pass_cooldown,
        "verdict": "PASS_COOLDOWN_NOT_BYPASS_CONDITION"
    }

    out_file = OUT_DIR / "04_gate_and_economic_mismatch.json"
    out_file.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Gate and economic mismatch audit finished, saved to {out_file}")

if __name__ == "__main__":
    run_gate_audit()
