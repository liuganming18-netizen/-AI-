#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SIGNAL-EFFECTIVE-01 独立复核脚本 1
289 条历史 UID 与纸面账本全关联穿透核验
执行方：反重力（只读、无副作用）
"""

import sqlite3
import json
import hashlib
import csv
import os
import sys
from datetime import datetime, timezone

DESKTOP_DIR = "/Users/boos/Desktop/威科夫因子共振趋势"
LIVE_DB_PATH = os.path.join(DESKTOP_DIR, "框架/backend/live/live_signals.sqlite3")
PAPER_DB_PATH = os.path.join(DESKTOP_DIR, "数据/paper/paper_3a.sqlite3")

OUT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../out"))
os.makedirs(OUT_DIR, exist_ok=True)
OUT_CSV_PATH = os.path.join(OUT_DIR, "uid与账本影响复核.csv")
OUT_JSON_PATH = os.path.join(OUT_DIR, "uid与账本影响复核_summary.json")

def file_hash(path):
    if not os.path.exists(path):
        return "NOT_FOUND"
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()

def ms_to_utc(ms):
    if ms is None:
        return "NULL"
    try:
        return datetime.fromtimestamp(ms / 1000.0, timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')
    except Exception:
        return str(ms)

def main():
    print(f"[*] 开始读取生产数据库...")
    print(f"    live_signals db: {LIVE_DB_PATH} (SHA256: {file_hash(LIVE_DB_PATH)})")
    print(f"    paper_3a db:     {PAPER_DB_PATH} (SHA256: {file_hash(PAPER_DB_PATH)})")

    # 1. 查 paper_3a 各表
    paper_conn = sqlite3.connect(PAPER_DB_PATH)
    p_cur = paper_conn.cursor()

    p_cur.execute("SELECT pending_id, symbol, signal_uid, status, reason, event_time, available_at, requested_at FROM paper_pending")
    pendings = p_cur.fetchall()
    pending_map = {r[2]: r for r in pendings if r[2]}

    p_cur.execute("SELECT position_id, symbol, signal_uid, status, entry_price, exit_price, exit_reason, event_time FROM paper_position")
    positions = p_cur.fetchall()
    position_map = {r[2]: r for r in positions if r[2]}

    p_cur.execute("SELECT fill_id, symbol, signal_uid, action, fill_price, fill_ts FROM paper_fill")
    fills = p_cur.fetchall()
    fill_map = {}
    for r in fills:
        if r[2]:
            fill_map.setdefault(r[2], []).append(r)

    p_cur.execute("SELECT order_id, symbol, event_uid, status, reject_reason FROM paper_order")
    orders = p_cur.fetchall()
    order_map = {r[2]: r for r in orders if r[2]}

    p_cur.execute("SELECT id, symbol, event_uid, reason, ts FROM paper_blocked")
    blocked = p_cur.fetchall()
    blocked_map = {r[2]: r for r in blocked if r[2]}

    paper_conn.close()

    # 2. 查 live_signal
    live_conn = sqlite3.connect(LIVE_DB_PATH)
    l_cur = live_conn.cursor()
    l_cur.execute("""
        SELECT signal_uid, source, origin, exchange, market, symbol, timeframe,
               strategy_key, strategy_version, config_hash, direction,
               event_time, confirmed_at, available_at, bar_open_time, bar_close_time,
               executable_state, executable_reason, payload_json, first_seen_at, updated_at
        FROM live_signal
        ORDER BY confirmed_at ASC
    """)
    rows = l_cur.fetchall()
    live_conn.close()

    print(f"[*] live_signal 提取总记录数: {len(rows)}")

    csv_rows = []
    summary = {
        "total_records": len(rows),
        "origin_distribution": {},
        "executable_state_distribution": {},
        "role_distribution": {},
        "approved_distribution": {},
        "time_available_equals_confirmed": 0,
        "ledger_association": {
            "no_order_impact": 0,
            "matched_pending": 0,
            "matched_position": 0,
            "matched_fill": 0,
            "matched_order": 0,
            "matched_blocked": 0,
            "potential_future_execution_risk": 0
        },
        "resonance_stats": {
            "has_resonance_score": 0,
            "avg_resonance_score": 0.0,
            "has_position_multiplier": 0,
            "position_multiplier_dist": {}
        }
    }

    total_score = 0.0

    for r in rows:
        uid = r[0]
        source = r[1]
        origin = r[2]
        exchange = r[3]
        market = r[4]
        symbol = r[5]
        tf = r[6]
        strat_key = r[7]
        strat_ver = r[8]
        cfg_hash = r[9]
        direction = r[10]
        event_time = r[11]
        confirmed_at = r[12]
        available_at = r[13]
        bar_open = r[14]
        bar_close = r[15]
        exec_state = r[16]
        exec_reason = r[17]
        p_json_raw = r[18]
        first_seen = r[19]
        updated_at = r[20]

        payload = {}
        if p_json_raw:
            try:
                payload = json.loads(p_json_raw)
            except Exception:
                pass

        role = payload.get("role", "UNKNOWN")
        approved = payload.get("approved", None)
        arm = payload.get("arm", "UNKNOWN")
        res_score = payload.get("resonance_score", None)
        pos_mult = payload.get("position_multiplier", None)
        res_summary = payload.get("resonance_summary", "")

        # 统计分布
        summary["origin_distribution"][origin] = summary["origin_distribution"].get(origin, 0) + 1
        summary["executable_state_distribution"][exec_state] = summary["executable_state_distribution"].get(exec_state, 0) + 1
        summary["role_distribution"][role] = summary["role_distribution"].get(role, 0) + 1
        summary["approved_distribution"][str(approved)] = summary["approved_distribution"].get(str(approved), 0) + 1
        if confirmed_at == available_at:
            summary["time_available_equals_confirmed"] += 1

        if res_score is not None:
            summary["resonance_stats"]["has_resonance_score"] += 1
            total_score += float(res_score)
        if pos_mult is not None:
            summary["resonance_stats"]["has_position_multiplier"] += 1
            summary["resonance_stats"]["position_multiplier_dist"][str(pos_mult)] = \
                summary["resonance_stats"]["position_multiplier_dist"].get(str(pos_mult), 0) + 1

        # 检查账本匹配
        matched_pend = pending_map.get(uid)
        matched_pos = position_map.get(uid)
        matched_fl = fill_map.get(uid)
        matched_ord = order_map.get(uid)
        matched_blk = blocked_map.get(uid)

        ledger_impact = "无订单影响(未入账本)"
        has_impact = False
        if matched_pend:
            ledger_impact = f"待处理挂单(pending_id={matched_pend[0]}, status={matched_pend[3]})"
            summary["ledger_association"]["matched_pending"] += 1
            has_impact = True
        elif matched_pos:
            ledger_impact = f"持仓记录(position_id={matched_pos[0]}, status={matched_pos[3]})"
            summary["ledger_association"]["matched_position"] += 1
            has_impact = True
        elif matched_fl:
            ledger_impact = f"成交记录(fill_cnt={len(matched_fl)})"
            summary["ledger_association"]["matched_fill"] += 1
            has_impact = True
        elif matched_ord:
            ledger_impact = f"订单记录(order_id={matched_ord[0]}, status={matched_ord[3]})"
            summary["ledger_association"]["matched_order"] += 1
            has_impact = True
        elif matched_blk:
            ledger_impact = f"阻断记录(id={matched_blk[0]}, reason={matched_blk[3]})"
            summary["ledger_association"]["matched_blocked"] += 1
            has_impact = True

        if not has_impact:
            summary["ledger_association"]["no_order_impact"] += 1

        # 潜在未来执行风险判定：
        if exec_state == "enterable" and origin == "recovered":
            summary["ledger_association"]["potential_future_execution_risk"] += 1

        csv_rows.append({
            "signal_uid": uid,
            "symbol": symbol,
            "origin": origin,
            "executable_state": exec_state,
            "role": role,
            "approved": approved,
            "arm": arm,
            "event_time_utc": ms_to_utc(event_time),
            "confirmed_at_utc": ms_to_utc(confirmed_at),
            "available_at_utc": ms_to_utc(available_at),
            "is_available_equal_confirmed": (confirmed_at == available_at),
            "first_seen_at_utc": ms_to_utc(first_seen),
            "resonance_score": res_score,
            "position_multiplier": pos_mult,
            "resonance_summary": res_summary,
            "ledger_impact": ledger_impact,
            "future_execution_risk": "高风险标签(需恢复为historical_research)" if (exec_state == "enterable" and origin == "recovered") else "无风险"
        })

    if summary["resonance_stats"]["has_resonance_score"] > 0:
        summary["resonance_stats"]["avg_resonance_score"] = round(total_score / summary["resonance_stats"]["has_resonance_score"], 2)

    # 写 CSV
    fieldnames = [
        "signal_uid", "symbol", "origin", "executable_state", "role", "approved", "arm",
        "event_time_utc", "confirmed_at_utc", "available_at_utc", "is_available_equal_confirmed",
        "first_seen_at_utc", "resonance_score", "position_multiplier", "resonance_summary",
        "ledger_impact", "future_execution_risk"
    ]
    with open(OUT_CSV_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(csv_rows)

    # 写 JSON
    with open(OUT_JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)

    print(f"[+] 结果已写入:")
    print(f"    CSV:  {OUT_CSV_PATH} (SHA256: {file_hash(OUT_CSV_PATH)})")
    print(f"    JSON: {OUT_JSON_PATH} (SHA256: {file_hash(OUT_JSON_PATH)})")
    print(f"[*] 汇总摘要:")
    print(json.dumps(summary, indent=2, ensure_ascii=False))

if __name__ == "__main__":
    main()
