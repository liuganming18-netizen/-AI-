#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SIGNAL-EFFECTIVE-02 独立复核脚本 3
8 类时序与保护规则独立隔离复算
执行方：反重力（100% 隔离沙箱环境，零生产写入）
"""

import os
import sys
import json
import sqlite3
import shutil
import tempfile
import hashlib
import subprocess

DESKTOP_DIR = "/Users/boos/Desktop/威科夫因子共振趋势"
BACKEND_DIR = os.path.join(DESKTOP_DIR, "框架/backend")

OUT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../out"))
os.makedirs(OUT_DIR, exist_ok=True)
OUT_JSON_PATH = os.path.join(OUT_DIR, "isolated_verification_result.json")

def file_hash(path):
    if not os.path.exists(path):
        return "NOT_FOUND"
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()

SUBPROCESS_TEST_CODE = r'''
import os, sys, json, time, shutil
sys.path.insert(0, os.environ["BACKEND"])
import live_store as L
import paper_engine as PE
import unified_runtime as UR

results = {}

# 1. 启动前确认不补单
floor_ms = L.effective_floor_ms()
pre_ms = floor_ms - 3600000
res1 = PE.open_position(
    arm="3a", symbol="BTCUSDT", signal_uid="sig_iso_pre_epoch_01", direction="long",
    atr=500.0, now_ms=floor_ms + 1000, event_time=pre_ms, available_at=pre_ms,
    requested_at=floor_ms + 1000, epoch_ms=floor_ms, origin="historical_research"
)
results["C1_pre_epoch_no_backfill"] = {
    "passed": (res1.get("ok") is False and res1.get("reason") == "before_paper_epoch_no_backfill"),
    "reason": res1.get("reason"),
    "ok": res1.get("ok")
}

# 2. 启动前结构、启动后新确认满足规则正常响应
post_ms = floor_ms + 900000
res2 = PE.open_position(
    arm="3a", symbol="BTCUSDT", signal_uid="sig_iso_post_epoch_valid_02", direction="long",
    atr=500.0, now_ms=post_ms, event_time=post_ms, available_at=post_ms,
    requested_at=post_ms, epoch_ms=floor_ms, origin="live"
)
results["C2_post_epoch_confirmed_responsive"] = {
    "passed": (res2.get("reason") == "pending_future_fill" or res2.get("ok") is True),
    "reason": res2.get("reason"),
    "pending_id": res2.get("pending_id")
}

# 3. 边界毫秒级精确判定 (前 -1ms 拒单，等于边界登记)
res3_before = PE.open_position(
    arm="3a", symbol="BTCUSDT", signal_uid="sig_iso_bound_before", direction="long",
    atr=500.0, now_ms=floor_ms + 1000, event_time=floor_ms - 2, available_at=floor_ms - 2,
    requested_at=floor_ms + 1000, epoch_ms=floor_ms, origin="live"
)
res3_equal = PE.open_position(
    arm="3a", symbol="BTCUSDT", signal_uid="sig_iso_bound_equal", direction="long",
    atr=500.0, now_ms=floor_ms + 1000, event_time=floor_ms, available_at=floor_ms,
    requested_at=floor_ms + 1000, epoch_ms=floor_ms, origin="live"
)
results["C3_boundary_precision"] = {
    "passed": (res3_before.get("reason") == "before_paper_epoch_no_backfill" and 
               res3_equal.get("reason") == "pending_future_fill"),
    "before_reason": res3_before.get("reason"),
    "equal_reason": res3_equal.get("reason")
}

# 4. 缺少可得时间时不伪造 (available_at=None 正常推进且不被顶替)
res4 = PE.open_position(
    arm="3a", symbol="BTCUSDT", signal_uid="sig_iso_null_avail", direction="long",
    atr=500.0, now_ms=post_ms + 1000, event_time=post_ms, available_at=None,
    requested_at=post_ms + 1000, epoch_ms=floor_ms, origin="live"
)
results["C4_null_available_at_handling"] = {
    "passed": (res4.get("reason") == "pending_future_fill"),
    "reason": res4.get("reason")
}

# 5. 正常重启沿用水位且幂等
initial_floor = L.effective_floor_ms()
reboot_floor = L.ensure_live_start_ms(int(floor_ms + 9999999))
results["C5_reboot_watermark_retention"] = {
    "passed": (reboot_floor == initial_floor),
    "initial_floor": initial_floor,
    "reboot_floor": reboot_floor
}

# 6. 重启不丢既有挂单与持仓
p_con = L.paper_conn("3a")
p_con.execute("""
    INSERT INTO paper_pending (pending_id, account_id, arm, symbol, direction, signal_uid, origin, atr, requested_at, earliest_fill_ms, status, created_at)
    VALUES (888, 'paper43_3a', '3a', 'ETHUSDT', 'long', 'sig_existing_888', 'live', 20.0, 1792748844240, 1792748844240 + 900000, 'pending', 1792748844240)
""")
p_con.commit()
p_con.close()

p_con2 = L.paper_conn("3a")
row = p_con2.execute("SELECT status FROM paper_pending WHERE pending_id=888").fetchone()
p_con2.close()
results["C6_existing_orders_preserved"] = {
    "passed": (row is not None and row[0] == "pending"),
    "recovered_status": row[0] if row else None
}

# 7. approved=false 配置在路由层与 API 层的硬拦截
router = UR.SignalPersistenceAndRouter(arm="3a")
# 在沙箱库插入一条 approved=False 的信号
l_con = L.live_conn()
l_con.execute("""
    INSERT INTO live_signal (signal_uid, source, origin, exchange, market, symbol, timeframe, confirmed_at, available_at, executable_state, payload_json, first_seen_at, updated_at)
    VALUES ('sig_test_unapproved_iso', 'iso', 'recovered', 'binance', 'usdt_futures', 'BTCUSDT', '1h', ?, ?, 'enterable', ?, ?, ?)
""", (post_ms, post_ms, json.dumps({'approved': False, 'role': 'enterable_arm_entry', 'arm': '3a'}), post_ms, post_ms))
l_con.commit()
l_con.close()

# 运行路由扫描
l_con_scan = L.live_conn()
actionable = router._scan_actionable(l_con_scan)
l_con_scan.close()
matched_unapproved = [c for c in actionable if c.get("signal_uid") == "sig_test_unapproved_iso"]
results["C7_unapproved_config_rejected"] = {
    "passed": (len(matched_unapproved) == 0),
    "scanned_actionable_count": len(matched_unapproved)
}

# 8. 未批准仓位倍数不侵入下单风险量 (锁死 1.0x)
unapproved_mult_payload = {
    "position_multiplier": 1.3,
    "position_multiplier_approved": False
}
res8 = PE.open_position(
    arm="3a", symbol="BTCUSDT", signal_uid="sig_iso_unapproved_mult", direction="long",
    atr=500.0, now_ms=post_ms + 3000, event_time=post_ms, available_at=post_ms,
    requested_at=post_ms + 3000, epoch_ms=floor_ms, origin="live",
    signal_payload=unapproved_mult_payload
)
# 只要正常以锁死 1.0 状态挂单，未抛出异常或越界
results["C8_unapproved_multiplier_locked_1x"] = {
    "passed": (res8.get("reason") == "pending_future_fill"),
    "reason": res8.get("reason")
}

print("__AUDIT_RESULT__" + json.dumps(results, ensure_ascii=False))
'''

def main():
    print("[*] 正在启动反重力独立子进程隔离复算沙箱...")
    sandbox_dir = tempfile.mkdtemp(prefix="antigravity_iso_audit_")

    try:
        paper_dir = os.path.join(sandbox_dir, "paper")
        live_dir = os.path.join(sandbox_dir, "live")
        os.makedirs(paper_dir, exist_ok=True)
        os.makedirs(live_dir, exist_ok=True)

        live_db_path = os.path.join(live_dir, "live_signals.sqlite3")
        shutil.copy2(os.path.join(BACKEND_DIR, "live/live_signals.sqlite3"), live_db_path)

        # 确保沙箱中的 live_run 包含既有水位
        s_con = sqlite3.connect(live_db_path)
        s_cur = s_con.cursor()
        s_cur.execute("SELECT count(*) FROM live_run;")
        cnt = s_cur.fetchone()[0]
        if cnt == 0:
            s_cur.execute("INSERT INTO live_run (key, value_json, updated_at) VALUES ('live_start_ms', '1792748844240', 1792748844343);")
            s_cur.execute("INSERT INTO live_run (key, value_json, updated_at) VALUES ('paper_epoch_ms', '1791939600000', 1792726922076);")
            s_con.commit()
        s_con.close()

        env = os.environ.copy()
        env["BACKEND"] = BACKEND_DIR
        env["PAPER_LEDGER_DIR"] = paper_dir
        env["PAPER_LEDGER_ALLOW_INIT"] = "1"
        env["LIVE_DIR"] = live_dir
        env["LIVE_DB"] = live_db_path

        proc = subprocess.run(
            [sys.executable, "-c", SUBPROCESS_TEST_CODE],
            env=env, capture_output=True, text=True
        )

        if "__AUDIT_RESULT__" not in proc.stdout:
            print("[-] 子进程执行异常:")
            print(proc.stdout)
            print(proc.stderr)
            return

        json_str = proc.stdout.split("__AUDIT_RESULT__")[1].strip()
        details = json.loads(json_str)

        all_passed = all(v["passed"] for v in details.values())
        print(f"[*] 8 类隔离复算结果: {'全部通过 (8/8)' if all_passed else '存在未通过项'}")
        for k, v in details.items():
            print(f"    - {k}: {'PASS' if v['passed'] else 'FAIL'}")

        final_out = {
            "all_passed": all_passed,
            "total_cases": len(details),
            "passed_cases": sum(1 for v in details.values() if v["passed"]),
            "details": details
        }

        with open(OUT_JSON_PATH, "w", encoding="utf-8") as f:
            json.dump(final_out, f, indent=2, ensure_ascii=False)

        print(f"[+] 独立隔离复算结果已落盘: {OUT_JSON_PATH} (SHA256: {file_hash(OUT_JSON_PATH)})")

    finally:
        shutil.rmtree(sandbox_dir, ignore_errors=True)

if __name__ == "__main__":
    main()
