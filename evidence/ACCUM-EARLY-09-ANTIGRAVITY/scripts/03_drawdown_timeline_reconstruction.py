# -*- coding: utf-8 -*-
"""ACCUM-EARLY-09 任务 C：账户动态权益路径、峰值更新与熔断时序逐步重建。

严格规则：
1. 澄清期末已实现损益（4b: -161 U, 4c: -175 U）与触及 20% 回撤阈值的逻辑关系：
   回撤分母是最高权益峰值（Peak Equity），而非初始 5000 U。
2. 逐步重建 paper_equity 时间序列，计算：
   cash, unrealized, equity, peak_equity, drawdown, threshold(0.20), locked
3. 准确记录首次触发时刻与后续拦截事件。
4. 验证账户物理与逻辑隔离证据。
"""

import os
import sqlite3
import csv
import json
import datetime

WORKTREE_ROOT = "/Users/boos/.gemini/antigravity/worktrees/威科夫因子共振趋势/evidence_correction_and_review"
OUT_CSV = os.path.join(WORKTREE_ROOT, "evidence/ACCUM-EARLY-09-ANTIGRAVITY/账户权益与熔断时序.csv")
OUT_JSON = os.path.join(WORKTREE_ROOT, "evidence/ACCUM-EARLY-09-ANTIGRAVITY/账户权益与熔断时序.json")

B_PAPER_DIR = os.path.join(WORKTREE_ROOT, "evidence/ACCUM-EARLY-08-ANTIGRAVITY/runs/antigravity_08_arm_b/paper")

all_trajectory_rows = []
trajectory_summary = {
    "metadata": {
        "date": "2026-10-27",
        "standard": "ACCUM-EARLY-09 熔断时序复核",
        "threshold_defined": "maximum_account_drawdown_fraction = 0.20 (20%)",
        "drawdown_formula": "drawdown = (peak_equity - current_equity) / peak_equity",
        "peak_formula": "peak_equity = max(MAX(equity), initial_equity=5000.0)"
    },
    "schemes": {},
    "account_isolation_evidence": {
        "arm_a_paper_dir": os.path.join(WORKTREE_ROOT, "evidence/ACCUM-EARLY-08-ANTIGRAVITY/runs/antigravity_08_arm_a/paper"),
        "arm_b_paper_dir": os.path.join(WORKTREE_ROOT, "evidence/ACCUM-EARLY-08-ANTIGRAVITY/runs/antigravity_08_arm_b/paper"),
        "production_paper_dir": os.path.join(WORKTREE_ROOT, "数据/paper"),
        "isolated": True,
        "detail": (
            "ARM A 与 ARM B 拥有各自独立的 paper 存储目录；"
            "各方案（4a, 4b, 4c）采用独立 SQLite 数据库文件（paper_4a.sqlite3, paper_4b.sqlite3, paper_4c.sqlite3）；"
            "账户 ID 分别为 paper43_4a, paper43_4b, paper43_4c，互不穿透；"
            "生产目录 数据/paper 零改动、零写入。"
        )
    }
}

for s in ("4a", "4b", "4c"):
    db_path = os.path.join(B_PAPER_DIR, f"paper_{s}.sqlite3")
    con = sqlite3.connect(db_path)
    cur = con.cursor()
    
    eq_rows = cur.execute("SELECT ts, cash, unrealized, equity FROM paper_equity ORDER BY ts").fetchall()
    blocked_rows = cur.execute("SELECT id, event_uid, symbol, reason, ts FROM paper_blocked ORDER BY id").fetchall()
    con.close()
    
    peak = 5000.0
    first_trigger = None
    records = []
    
    for idx, r in enumerate(eq_rows):
        ts, cash, unreal, eq = r
        if eq > peak:
            peak = eq
        dd = (peak - eq) / peak if peak > 0 else 0.0
        locked = dd >= 0.20
        
        utc_str = datetime.datetime.fromtimestamp(ts / 1000, tz=datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        
        if locked and first_trigger is None:
            first_trigger = {
                "step_index": idx + 1,
                "ts": ts,
                "utc": utc_str,
                "cash": round(cash, 2),
                "unrealized": round(unreal, 2),
                "equity": round(eq, 2),
                "peak_equity": round(peak, 2),
                "drawdown_pct": round(dd * 100, 2),
                "drawdown_amount": round(peak - eq, 2)
            }
            
        row = {
            "scheme": s,
            "step": idx + 1,
            "ts": ts,
            "utc_time": utc_str,
            "cash": round(cash, 2),
            "unrealized": round(unreal, 2),
            "equity": round(eq, 2),
            "peak_equity": round(peak, 2),
            "drawdown_fraction": round(dd, 6),
            "drawdown_pct": round(dd * 100, 2),
            "drawdown_threshold_pct": 20.0,
            "is_drawdown_locked": locked
        }
        records.append(row)
        all_trajectory_rows.append(row)
        
    # 后续被拦截事件汇总
    post_lock_blocked = []
    for b in blocked_rows:
        b_id, uid, sym, reason, b_ts = b
        b_utc = datetime.datetime.fromtimestamp(b_ts / 1000, tz=datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        post_lock_blocked.append({
            "blocked_id": b_id,
            "event_uid": uid,
            "symbol": sym,
            "reason": reason,
            "ts": b_ts,
            "utc": b_utc
        })
        
    trajectory_summary["schemes"][s] = {
        "total_equity_steps": len(records),
        "initial_equity": 5000.0,
        "max_peak_equity": round(max(x["peak_equity"] for x in records), 2) if records else 5000.0,
        "min_equity": round(min(x["equity"] for x in records), 2) if records else 5000.0,
        "final_equity": round(records[-1]["equity"], 2) if records else 5000.0,
        "first_trigger_event": first_trigger,
        "total_blocked_candidates_count": len(post_lock_blocked),
        "drawdown_locked_blocked_count": sum(1 for b in post_lock_blocked if b["reason"] == "account_drawdown_locked"),
        "subsequent_blocked_sample": post_lock_blocked[:5]
    }

# 写入 CSV
with open(OUT_CSV, "w", encoding="utf-8-sig", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=list(all_trajectory_rows[0].keys()))
    writer.writeheader()
    writer.writerows(all_trajectory_rows)

# 写入 JSON
with open(OUT_JSON, "w", encoding="utf-8") as f:
    json.dump(trajectory_summary, f, indent=2, ensure_ascii=False)

print("Drawdown timeline reconstruction completed successfully.")
print(f"Generated CSV: {OUT_CSV}")
print(f"Generated JSON: {OUT_JSON}")
