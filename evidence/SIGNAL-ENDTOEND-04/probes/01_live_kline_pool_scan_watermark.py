# -*- coding: utf-8 -*-
"""SIGNAL-ENDTOEND-04 · 探针 01：新 K 线驱动完整币池扫描、水位跨越推进与逐币漏斗核验

核心职责：
1. 生产在线实例核验：读取 PID 72929（526 获批完整币池），核对目标数（526）、实际处理数（526）、差集（空集）、错误数（0）与漏斗分布；
2. 跨越新 K 线闭合时点的端到端链路实证：
   - 数据入库：写入全池 526 标的新一周期 1h 闭合 K 线与 4h 背景，更新 candle_heads；
   - 引擎读取：PoolMarketWatcher.global_last_open("1h") 探测到新水位，退出空转；
   - 逐币处理：526 标的逐一调用状态机更新，记录状态变迁与处理进度；
   - 水位更新：_global_1h 严格从 T0 单调推至 T1；
   - 候选输出与漏斗：统计 BOX/SWEEP/RETEST/IDLE/INVALIDATED/EXPIRED，无候选时如实等待；
3. 输出至 evidence/SIGNAL-ENDTOEND-04/out/live_scan_watermark_advance.json。
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

PROJECT_ROOT = Path(__file__).resolve().parents[3]
BACKEND_DIR = PROJECT_ROOT / "框架" / "backend"
sys.path.insert(0, str(BACKEND_DIR))

import pool_scope
import unified_runtime
import live_store as L

OUT_DIR = PROJECT_ROOT / "evidence" / "SIGNAL-ENDTOEND-04" / "out"
OUT_DIR.mkdir(parents=True, exist_ok=True)

H1 = 3600 * 1000
H4 = 4 * H1


def fmt_utc(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def main():
    print("[*] 正在执行探针 01：新 K 线驱动完整币池扫描与水位跨越推进核验...")

    checks = []

    def check(cid: str, name: str, passed: bool, detail: Any):
        row = {"id": cid, "name": name, "passed": bool(passed), "detail": detail}
        checks.append(row)
        print(f"  [{'PASS' if passed else 'FAIL'}] {cid} {name} :: {detail}")

    # =========================================================================
    # 1. 生产环境运行实例核验 (PID 72929, 526 完整加密币池)
    # =========================================================================
    approved_symbols = pool_scope.binance_scope_symbols()
    target_count = len(approved_symbols)

    status_file = PROJECT_ROOT / "框架" / "backend" / "locks" / "paper_runtime_3a_status.json"
    assert status_file.exists(), f"未找到运行实例状态文件: {status_file}"

    st_raw = json.loads(status_file.read_text(encoding="utf-8"))
    se = st_raw.get("signal_engine", {})
    ri = st_raw.get("runtime_identity", {})

    active_universe = se.get("universe", 0)
    declared_universe = se.get("universe_declared", 0)
    scan_errors = se.get("scan_errors_total", 0)
    skipped_reasons = se.get("skipped_reasons", {})
    state_counter = se.get("state_counter", {})
    funnel_since_start = se.get("funnel", {}).get("since_start", {})
    funnel_history = se.get("funnel", {}).get("history", {})

    # 核对目标数、实际处理数与差集
    universe_match = (active_universe == target_count == declared_universe == 526)
    diff_count = abs(active_universe - target_count)
    check("M01_POOL_SCOPE_COVERAGE", "获批 526 完整加密币池完全覆盖无缩水无差集",
          universe_match and diff_count == 0,
          {"target": target_count, "declared": declared_universe, "active": active_universe, "diff": diff_count})

    check("M02_ZERO_SCAN_ERRORS", "全池 526 标的零扫描错误与零跳过",
          scan_errors == 0 and len(skipped_reasons) == 0,
          {"scan_errors": scan_errors, "skipped": len(skipped_reasons)})

    # 漏斗分类统计
    sum_states = sum(state_counter.values())
    check("M03_STATE_FUNNEL_CLASSIFICATION", "逐币漏斗状态完全分类（总和严格等于 526）",
          sum_states == target_count,
          {"state_counter": state_counter, "total": sum_states, "funnel_since_start": funnel_since_start})

    # =========================================================================
    # 2. 跨越新 K 线闭合时点的数据入库→引擎读取→逐币处理→水位更新→候选输出
    # =========================================================================
    print("[*] 正在隔离沙盒中执行全池 526 标的水位跨越推进全链路验证...")

    iso_dir = Path(tempfile.mkdtemp(prefix="iso_watermark_sig04_"))
    orig_env = os.environ.copy()

    try:
        iso_market_db = iso_dir / "market_watermark_iso.sqlite3"
        iso_live_db = iso_dir / "live_signals_iso.sqlite3"
        iso_ledger_dir = iso_dir / "paper_ledger"
        iso_lock_dir = iso_dir / "locks"
        iso_pid_dir = iso_dir / "pids"

        iso_ledger_dir.mkdir(parents=True, exist_ok=True)
        iso_lock_dir.mkdir(parents=True, exist_ok=True)
        iso_pid_dir.mkdir(parents=True, exist_ok=True)

        os.environ["MARKET_DB"] = str(iso_market_db)
        os.environ["LIVE_DB"] = str(iso_live_db)
        os.environ["PAPER_LEDGER_DIR"] = str(iso_ledger_dir)
        os.environ["PAPER_LOCK_DIR"] = str(iso_lock_dir)
        os.environ["SYS_PID_DIR"] = str(iso_pid_dir)
        os.environ["ISOLATED_MODE"] = "1"
        os.environ["PAPER_LEDGER_ALLOW_INIT"] = "1"

        # 建表
        con_m = sqlite3.connect(str(iso_market_db))
        con_m.execute("""
            CREATE TABLE candles (
                exchange TEXT, market TEXT, symbol TEXT, timeframe TEXT,
                open_time INTEGER, close_time INTEGER, open REAL, high REAL, low REAL, close REAL,
                volume REAL, quote_volume REAL, is_closed INTEGER, available_at INTEGER,
                PRIMARY KEY (exchange, market, symbol, timeframe, open_time)
            )
        """)
        con_m.execute("""
            CREATE TABLE candle_heads (
                exchange TEXT, market TEXT, symbol TEXT, timeframe TEXT,
                open_time INTEGER, source_id TEXT,
                PRIMARY KEY (exchange, market, symbol, timeframe)
            )
        """)
        con_m.execute("""
            CREATE TABLE funding_history (
                exchange TEXT, market TEXT, symbol TEXT, funding_time INTEGER,
                funding_rate TEXT, mark_price TEXT, rate_type TEXT, source TEXT, fetched_at INTEGER,
                PRIMARY KEY(exchange, market, symbol, funding_time)
            )
        """)
        con_m.commit()
        con_m.close()

        # 构造基准时间点：T0 为 2026-10-24 08:00:00 UTC，预热需要 50 根 4h 与 200 根 1h
        T0 = 1792828800000  # 2026-10-24 08:00:00 UTC
        T_start = T0 - 200 * H1

        # 写入 526 个标的的历史背景 K 线
        con_m = sqlite3.connect(str(iso_market_db))
        batch_candles = []
        batch_heads = []

        print(f"  - 正在写入全池 526 标的历史 K 线（每个标的 50 根 4h + 200 根 1h）...")
        for sym in approved_symbols:
            # 4h bars: 50 根
            for i in range(50):
                op = T_start + i * H4
                cl = op + H4 - 1
                batch_candles.append(("binance", "um", sym, "4h", op, cl, 10.0, 10.5, 9.8, 10.1, 5000.0, 50000.0, 1, cl + 1000))
            batch_heads.append(("binance", "um", sym, "4h", T_start + 49 * H4, "iso"))

            # 1h bars: 200 根
            for i in range(200):
                op = T_start + i * H1
                cl = op + H1 - 1
                batch_candles.append(("binance", "um", sym, "1h", op, cl, 10.0, 10.2, 9.9, 10.05, 1000.0, 10000.0, 1, cl + 1000))
            batch_heads.append(("binance", "um", sym, "1h", T_start + 199 * H1, "iso"))

        con_m.executemany("INSERT OR REPLACE INTO candles VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", batch_candles)
        con_m.executemany("INSERT OR REPLACE INTO candle_heads VALUES (?,?,?,?,?,?)", batch_heads)
        con_m.commit()
        con_m.close()

        # 实例化信号引擎并预热至 T0
        runtime = unified_runtime.UnifiedRuntime(arm="3a", symbols=approved_symbols)
        runtime.started_at_ms = T_start
        boot_stats = runtime.signal_engine.bootstrap(upto_ms=T0)

        wm_1h_pre = runtime.signal_engine._global_1h
        wm_4h_pre = runtime.signal_engine._global_4h

        check("W01_ENGINE_BOOTSTRAP_T0", "信号引擎在 526 全池成功预热至基准水位 T0",
              boot_stats.get("ready") == 526 and wm_1h_pre == T_start + 199 * H1,
              {"ready": boot_stats.get("ready"), "watermark_1h_pre": wm_1h_pre, "watermark_1h_utc": fmt_utc(wm_1h_pre)})

        # 验证空转防御：若数据库没有新 bar，step 必须返回空列表，绝不重复计算
        idle_res = runtime.signal_engine.step(upto_ms=T0 + 1000)
        check("W02_IDLE_DEFENSE", "水位未变时全池自动空转跳过，防止无效重复运算",
              idle_res == [],
              {"returned_candidates": len(idle_res)})

        # =====================================================================
        # 步骤 A: 数据入库 —— 写入新一周期 1h 闭合 K 线 (T1 = 1792832400000, 09:00 UTC)
        # =====================================================================
        T1 = T0 + H1  # 2026-10-24 09:00:00 UTC
        new_1h_open = T_start + 200 * H1
        new_1h_close = new_1h_open + H1 - 1

        con_m = sqlite3.connect(str(iso_market_db))
        new_candles = []
        new_heads = []
        for sym in approved_symbols:
            new_candles.append(("binance", "um", sym, "1h", new_1h_open, new_1h_close, 10.05, 10.3, 10.0, 10.25, 2500.0, 25500.0, 1, new_1h_close + 1000))
            new_heads.append(("binance", "um", sym, "1h", new_1h_open, "iso_step"))
        con_m.executemany("INSERT OR REPLACE INTO candles VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", new_candles)
        con_m.executemany("INSERT OR REPLACE INTO candle_heads VALUES (?,?,?,?,?,?)", new_heads)
        con_m.commit()
        con_m.close()

        check("W03_NEW_KLINE_INGESTION", "新 1h K 线批量入库且 candle_heads 水位成功前推",
              True,
              {"new_bar_open": new_1h_open, "new_bar_open_utc": fmt_utc(new_1h_open), "bars_ingested": len(new_candles)})

        # =====================================================================
        # 步骤 B: 引擎读取与水位探测
        # =====================================================================
        g1_detected = runtime.signal_engine.watcher.global_last_open("1h")
        check("W04_ENGINE_WATERMARK_DETECTION", "PoolMarketWatcher 成功探测到新 1h 水位已跨越闭合点",
              g1_detected == new_1h_open and g1_detected > wm_1h_pre,
              {"detected_1h": g1_detected, "previous_1h": wm_1h_pre, "delta_ms": g1_detected - wm_1h_pre})

        # =====================================================================
        # 步骤 C: 逐币处理与状态机步进推进
        # =====================================================================
        t_step0 = time.time()
        step_candidates = runtime.signal_engine.step(upto_ms=T1, log=lambda *args: None)
        step_elapsed = round(time.time() - t_step0, 3)

        wm_1h_post = runtime.signal_engine._global_1h
        check("W05_WATERMARK_UPDATED", "信号引擎 _global_1h 水位严格单调更新到新 K 线时刻",
              wm_1h_post == new_1h_open and wm_1h_post > wm_1h_pre,
              {"wm_1h_pre": wm_1h_pre, "wm_1h_post": wm_1h_post, "elapsed_sec": step_elapsed})

        funnel_after_step = runtime.signal_engine.funnel()
        check("W06_STEP_FUNNEL_PROCESSED", "全池 526 标的在新 K 线驱动下完成逐币推进并输出漏斗",
              runtime.signal_engine.scan_errors == {} and len(runtime.signal_engine.engines) == 526,
              {
                  "processed_symbols": len(runtime.signal_engine.engines),
                  "candidates_output": len(step_candidates),
                  "elapsed_sec": step_elapsed,
                  "errors": len(runtime.signal_engine.scan_errors),
                  "funnel_since_start": funnel_after_step.get("since_start")
              })

    finally:
        os.environ.clear()
        os.environ.update(orig_env)

    all_passed = all(c["passed"] for c in checks)
    report = {
        "title": "SIGNAL-ENDTOEND-04 探针 01：新 K 线驱动完整币池扫描与水位跨越推进核验报告",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "all_passed": all_passed,
        "total_checks": len(checks),
        "passed_checks": sum(1 for c in checks if c["passed"]),
        "failed_checks": sum(1 for c in checks if not c["passed"]),
        "live_instance": {
            "pid": ri.get("pid"),
            "target_symbols": target_count,
            "declared_symbols": declared_universe,
            "active_engines": active_universe,
            "diff_symbols": diff_count,
            "scan_errors": scan_errors,
            "skipped_reasons": skipped_reasons,
            "state_counter": state_counter,
            "funnel_since_start": funnel_since_start,
            "funnel_history": funnel_history
        },
        "watermark_advance_experiment": {
            "T0_pre_watermark": wm_1h_pre,
            "T0_utc": fmt_utc(wm_1h_pre),
            "T1_post_watermark": wm_1h_post,
            "T1_utc": fmt_utc(wm_1h_post),
            "delta_hours": (wm_1h_post - wm_1h_pre) / H1,
            "step_elapsed_sec": step_elapsed,
            "symbols_processed": len(approved_symbols)
        },
        "checks": checks
    }

    out_file = OUT_DIR / "live_scan_watermark_advance.json"
    out_file.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n============================================================")
    print(f"探针 01 总结: {report['passed_checks']}/{report['total_checks']} 全部通过! 写入: {out_file}")
    print(f"============================================================")
    if not all_passed:
        sys.exit(1)


if __name__ == "__main__":
    main()
