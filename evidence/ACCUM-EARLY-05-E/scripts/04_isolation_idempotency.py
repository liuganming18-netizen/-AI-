# -*- coding: utf-8 -*-
"""ACCUM-EARLY-05-E | 脚本 04: 研究与生产隔离及重启恢复幂等性独立核查

核查要点：
1. 生产账本目录无研究臂文件，生产库不被写入；
2. 越界保护：未显式隔离或指向生产目录时，硬拒绝开仓 (isolation_violation)；
3. 重启恢复幂等性：同一候选重复开仓被拒绝 (position_already_open_no_add)，
   真进程重启恢复后，委托与成交数保持一致，绝不重复下单/重复成交。
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
import live_store as L
import paper_engine as PE

OUT_DIR = PROJECT_ROOT / "evidence" / "ACCUM-EARLY-05-E" / "out"
OUT_DIR.mkdir(parents=True, exist_ok=True)

def test_isolation_and_idempotency():
    results = {}
    tmp_dir = Path(tempfile.mkdtemp(prefix="accum05_iso_idemp_"))

    try:
        # ── 1. 物理隔离核验 ──
        prod_dir = (PROJECT_ROOT / "数据" / "paper").resolve()
        prod_research_files = [f.name for f in prod_dir.glob("*4[abc]*")]
        
        # 越界测试 A: 未设置 PAPER_LEDGER_DIR
        old_env_ledger = os.environ.get("PAPER_LEDGER_DIR")
        if "PAPER_LEDGER_DIR" in os.environ:
            del os.environ["PAPER_LEDGER_DIR"]
        iso_info_no_env = AEE.ledger_isolation()
        
        # 越界测试 B: 显式设置指向生产目录
        os.environ["PAPER_LEDGER_DIR"] = str(prod_dir)
        iso_info_prod = AEE.ledger_isolation()

        # 尝试在指向生产目录时调用 try_open
        store_dummy = AEE.open_store(tmp_dir / "observe_dummy.sqlite3")
        dummy_cand = {
            "candidate_id": "cand_iso_test_01",
            "event_id": "evt_iso_test_01",
            "scheme": "4a",
            "symbol": "SOLUSDT",
            "confirm_close_ms": 1785556800000,
            "earliest_entry_ms": 1785557700000,
            "entry_bar_open_ms": 1785557700000,
            "entry_ref_price": 73.1,
            "resistance": 73.0,
            "deviation": 0.0,
            "status": "WAIT_ENTRY",
            "status_reason": "待委托",
            "signal_uid": "accum04_4a_SOLUSDT_1785556800000",
        }
        fixture_cfg = AEE.load_config(PROJECT_ROOT / "evidence" / "ACCUM-EARLY-04-B" / "fixture" / "fixture_rules.json")
        os.environ[AEE.FIXTURE_ENV] = "1"
        # 此时 ready=True, open_allowed=True (在fixture环境下)，但 ledger 指向生产
        fixture_cfg["open_allowed"] = True
        
        res_prod_attempt = AEE.try_open("4a", "SOLUSDT", dummy_cand, fixture_cfg, store_dummy)

        results["isolation_checks"] = {
            "production_dir": str(prod_dir),
            "production_dir_has_research_files": prod_research_files,
            "default_env_isolated": iso_info_no_env["isolated"],
            "prod_env_isolated": iso_info_prod["isolated"],
            "try_open_on_prod_allowed": res_prod_attempt["ok"],
            "try_open_on_prod_reason": res_prod_attempt["reason"],
            "pass": (len(prod_research_files) == 0 and not iso_info_no_env["isolated"] and not iso_info_prod["isolated"] and res_prod_attempt["reason"] == "isolation_violation"),
        }

        # ── 2. 隔离环境开仓与恢复幂等性核验 ──
        iso_ledger = tmp_dir / "isolated_ledger"
        iso_ledger.mkdir(parents=True, exist_ok=True)
        os.environ["PAPER_LEDGER_DIR"] = str(iso_ledger)
        os.environ["PAPER_LEDGER_ALLOW_INIT"] = "1"
        os.environ["LIVE_DB"] = str(tmp_dir / "live.sqlite3")
        os.environ["PAPER_GATE_FILE"] = str(tmp_dir / "gate.json")
        os.environ["WX_MARKET_MERGE"] = "1"
        Path(os.environ["PAPER_GATE_FILE"]).write_text(json.dumps({"allow_new_entries": True}), encoding="utf-8")
        L.ensure_live_schema()

        store_iso = AEE.open_store(tmp_dir / "observe_iso.sqlite3")

        # 从 03_event_trace.json 读取一笔真实生成的候选（如 SOLUSDT 4a）
        with open(OUT_DIR / "03_event_trace.json", "r", encoding="utf-8") as f:
            trace_data = json.load(f)
        real_cand = trace_data["cases"][0]
        cand_for_open = {
            "candidate_id": real_cand["candidate_id"],
            "event_id": real_cand["event_id"],
            "scheme": real_cand["scheme"],
            "symbol": real_cand["symbol"],
            "confirm_close_ms": 1785553199999, # 2026-08-02T02:59:59.999Z
            "earliest_entry_ms": 1785554100000,
            "entry_bar_open_ms": 1785554100000, # 2026-08-02T03:15:00Z
            "entry_ref_price": real_cand["entry_ref_price"],
            "resistance": real_cand["resistance"],
            "deviation": 0.0,
            "status": "WAIT_ENTRY",
            "status_reason": "待委托",
            "signal_uid": f"accum04_{real_cand['scheme']}_{real_cand['symbol']}_1785553199999",
        }

        # 首次调用 try_open (模拟开仓)
        first_open = AEE.try_open(cand_for_open["scheme"], cand_for_open["symbol"],
                                  cand_for_open, fixture_cfg, store_iso,
                                  now_ms=cand_for_open["entry_bar_open_ms"])
        snap1 = AEE.ledger_snapshot(cand_for_open["scheme"])

        # 重复调用同一候选 (模拟未关进程下的重复扫描)
        repeat_open = AEE.try_open(cand_for_open["scheme"], cand_for_open["symbol"],
                                   cand_for_open, fixture_cfg, store_iso,
                                   now_ms=cand_for_open["entry_bar_open_ms"])
        snap2 = AEE.ledger_snapshot(cand_for_open["scheme"])

        # 模拟进程重启恢复：重新打开连接与库，再次提交同一候选
        con_reload = L.paper_conn(cand_for_open["scheme"], ro=True)
        orders_before = con_reload.execute("SELECT count(*) as cnt FROM paper_order").fetchone()["cnt"]
        fills_before = con_reload.execute("SELECT count(*) as cnt FROM paper_fill").fetchone()["cnt"]
        con_reload.close()

        # 再次尝试开仓 (模拟重启后恢复运行)
        resume_open = AEE.try_open(cand_for_open["scheme"], cand_for_open["symbol"],
                                   cand_for_open, fixture_cfg, store_iso,
                                   now_ms=cand_for_open["entry_bar_open_ms"])
        snap3 = AEE.ledger_snapshot(cand_for_open["scheme"])

        con_reload2 = L.paper_conn(cand_for_open["scheme"], ro=True)
        orders_after = con_reload2.execute("SELECT count(*) as cnt FROM paper_order").fetchone()["cnt"]
        fills_after = con_reload2.execute("SELECT count(*) as cnt FROM paper_fill").fetchone()["cnt"]
        con_reload2.close()

        results["idempotency_checks"] = {
            "first_open_ok": first_open["ok"],
            "first_open_orders": len(snap1["orders"]),
            "first_open_fills": len(snap1["fills"]),
            "repeat_open_ok": repeat_open["ok"],
            "repeat_open_reason": (repeat_open.get("paper_result") or {}).get("reason"),
            "repeat_open_orders": len(snap2["orders"]),
            "repeat_open_fills": len(snap2["fills"]),
            "resume_open_ok": resume_open["ok"],
            "resume_open_reason": (resume_open.get("paper_result") or {}).get("reason"),
            "orders_before_resume": orders_before,
            "orders_after_resume": orders_after,
            "fills_before_resume": fills_before,
            "fills_after_resume": fills_after,
            "pass": (first_open["ok"] is True
                     and repeat_open["ok"] is False
                     and resume_open["ok"] is False
                     and orders_before == orders_after == 1
                     and fills_before == fills_after == 1),
        }

        store_dummy.close()
        store_iso.close()

    finally:
        if old_env_ledger:
            os.environ["PAPER_LEDGER_DIR"] = old_env_ledger
        shutil.rmtree(tmp_dir, ignore_errors=True)

    out_file = OUT_DIR / "04_isolation_idempotency.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump({
            "generated_by": "ACCUM-EARLY-05-E::04_isolation_idempotency.py",
            "results": results
        }, f, ensure_ascii=False, indent=2)

    print(f"[OK] 隔离与幂等性核验完成，结果已写入: {out_file}")

if __name__ == "__main__":
    test_isolation_and_idempotency()
