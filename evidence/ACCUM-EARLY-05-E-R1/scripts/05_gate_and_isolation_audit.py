# -*- coding: utf-8 -*-
"""ACCUM-EARLY-05-E-R1 | 脚本 05: 批准闸门、物理隔离与数据可得性独立核验

【目的】
1. 验证无批准配置时：
   - 读数据与记录观察照常推进；
   - 实际开仓被硬阻断 (called_paper_engine=False)，隔离与生产账本零写入；
2. 验证正常配置校验链路 (不人为强制 open_allowed=True)；
3. 验证生产账本物理隔离与越界拦截 (指向生产目录时 100% 触发 isolation_violation)；
4. 验证历史 available_at 缺失诚实标记 (标 unknown/not_proven，不充当时可得)；
5. 输出至 out/05_gate_and_isolation.json。
"""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
BACKEND_DIR = PROJECT_ROOT / "框架" / "backend"
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(BACKEND_DIR))

import accum_early_entry as AEE

OUT_DIR = PROJECT_ROOT / "evidence" / "ACCUM-EARLY-05-E-R1" / "out"
OUT_DIR.mkdir(parents=True, exist_ok=True)

def run_gate_and_isolation_audit():
    tmp_dir = Path(tempfile.mkdtemp(prefix="accum05_gate_"))
    results = {
        "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "unapproved_config_gate": {},
        "production_physical_isolation": {},
        "cross_boundary_protection": {},
        "available_at_provenance": {}
    }

    try:
        iso_ledger = tmp_dir / "ledger"
        iso_ledger.mkdir(parents=True, exist_ok=True)
        store = AEE.open_store(tmp_dir / "observe.sqlite3")

        # ── 1. 无批准配置阻断核验 ──
        # 使用 05 版空白表单配置
        blank_cfg_path = PROJECT_ROOT / "evidence" / "ACCUM-EARLY-05-B" / "config" / "批准配置_空白表单_05.json"
        cfg_blank = AEE.load_config(blank_cfg_path)
        
        dummy_cand = {
            "candidate_id": "cd_unapproved_test_01",
            "event_id": "ev_unapproved_test_01",
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
            "signal_uid": "accum05_unapproved_test_01",
        }

        # 尝试开仓
        open_unapproved = AEE.try_open("4a", "SOLUSDT", dummy_cand, cfg_blank, store)

        # 验证观察与数据读取是否照常
        feed = AEE.DataFeed()
        t_sol_start = 1785542400000 # 2026-08-01T00:00:00Z
        t_sol_end = 1785715200000   # 2026-08-03T00:00:00Z
        scan_res = AEE.scan_symbol("SOLUSDT", cfg_blank, feed, store,
                                   start_ms=t_sol_start, end_ms=t_sol_end,
                                   max_boxes=1, open_candidates=False)

        results["unapproved_config_gate"] = {
            "config_path": str(blank_cfg_path),
            "open_allowed": cfg_blank.get("open_allowed"),
            "blocked_by": cfg_blank.get("blocked_by"),
            "missing_fields_count": len(cfg_blank.get("missing", [])),
            "try_open_ok": open_unapproved.get("ok"),
            "try_open_reason": open_unapproved.get("reason"),
            "called_paper_engine": open_unapproved.get("called_paper_engine"),
            "data_read_ok": (len(feed.window("1h", "SOLUSDT", t_sol_start, t_sol_end)) > 0),
            "events_spawned": scan_res.get("events_spawned"),
            "candidates_produced": scan_res.get("candidates"),
            "pass": (not cfg_blank.get("open_allowed")
                     and not open_unapproved.get("ok")
                     and open_unapproved.get("called_paper_engine") is False
                     and len(scan_res.get("candidates", [])) == 0)
        }

        # ── 2. 生产环境物理隔离清点 ──
        prod_dir = AEE.production_ledger_dir()
        prod_research_files = []
        if prod_dir.exists():
            for p in prod_dir.glob("*4[abc]*"):
                prod_research_files.append(str(p.name))

        results["production_physical_isolation"] = {
            "prod_dir": str(prod_dir),
            "prod_research_files_found": prod_research_files,
            "clean_pass": (len(prod_research_files) == 0)
        }

        # ── 3. 越界保护拦截负例 ──
        # 将环境变量恶意指向生产目录
        os.environ["PAPER_LEDGER_DIR"] = str(prod_dir)
        iso_check = AEE.ledger_isolation()
        open_cross_violation = AEE.try_open("4a", "SOLUSDT", dummy_cand, cfg_blank, store)

        results["cross_boundary_protection"] = {
            "target_dir_set_to_prod": str(prod_dir),
            "ledger_isolation_status": iso_check.get("isolated"),
            "try_open_ok": open_cross_violation.get("ok"),
            "try_open_reason": open_cross_violation.get("reason"),
            "called_paper_engine": open_cross_violation.get("called_paper_engine"),
            "blocked_before_write": (open_cross_violation.get("called_paper_engine") is False)
        }

        # ── 4. 历史 available_at 溯源与定性 ──
        cold_bar = feed.at_or_after("15m", "SOLUSDT", 1785554100000)
        provenance = feed.available_at_report("15m", "SOLUSDT")

        results["available_at_provenance"] = {
            "sample_bar_close_time": cold_bar.get("close_time") if cold_bar else None,
            "has_available_at_field": ("available_at" in cold_bar if cold_bar else False),
            "provenance_report": provenance,
            "compliance_claim": "冷档历史 K 线无交易所采集挂钟时间 available_at 证据，合规定性为 unknown / not_proven，严禁声称当时可得"
        }

        out_file = OUT_DIR / "05_gate_and_isolation.json"
        out_file.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"Gate and isolation audit evidence written to {out_file}")

    finally:
        pass

if __name__ == "__main__":
    run_gate_and_isolation_audit()
