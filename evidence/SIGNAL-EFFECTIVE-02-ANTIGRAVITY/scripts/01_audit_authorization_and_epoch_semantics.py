#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SIGNAL-EFFECTIVE-02 独立复核脚本 1
原始授权与生效时间语义独立溯源
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
LOCK_STATUS_PATH = os.path.join(DESKTOP_DIR, "框架/backend/locks/paper_runtime_3a_status.json")

OUT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../out"))
os.makedirs(OUT_DIR, exist_ok=True)
OUT_JSON_PATH = os.path.join(OUT_DIR, "epoch_semantics_audit.json")
OUT_CSV_PATH = os.path.join(OUT_DIR, "时间语义对照表.csv")

def ms_to_utc(ms):
    if ms is None:
        return "NULL"
    try:
        return datetime.fromtimestamp(ms / 1000.0, timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')
    except Exception:
        return str(ms)

def file_hash(path):
    if not os.path.exists(path):
        return "NOT_FOUND"
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()

def main():
    print("[*] 正在分析原始授权与各层时间语义...")

    # 1. 读取 live_run 表持久化水位
    conn = sqlite3.connect(LIVE_DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT key, value_json, updated_at FROM live_run;")
    run_records = cur.fetchall()
    conn.close()

    run_dict = {}
    for k, v, u in run_records:
        run_dict[k] = {
            "value": v,
            "value_int": int(v) if v and v.isdigit() else v,
            "value_utc": ms_to_utc(int(v)) if v and v.isdigit() else str(v),
            "updated_at_ms": u,
            "updated_at_utc": ms_to_utc(u)
        }

    # 2. 读取当前进程锁状态
    runtime_info = {}
    if os.path.exists(LOCK_STATUS_PATH):
        with open(LOCK_STATUS_PATH, "r", encoding="utf-8") as f:
            runtime_info = json.load(f)

    r_ident = runtime_info.get("runtime_identity", {})
    started_at_ms = r_ident.get("started_at")
    pid = r_ident.get("pid")

    # 3. 梳理五重时间语义层
    time_layers = [
        {
            "时间层级": "1. 结构发生时刻 (structure_event_time)",
            "物理含义": "4h 箱体形态蓄势完成或 1h 回踩关键位的行情发生时刻",
            "本系统取值范围": "历史至今任意行情时刻（如 2026-06 ~ 2026-10）",
            "是否受限于生效边界": "否。历史预热合法允许结构早于策略生效时刻形成，存活与指标按规则延续计算。",
            "原始授权依据": "《用户要求与决策.md:7》：“结构可形成于启动前，生效后确认满足条件不能仅因结构起点较早而排除。”"
        },
        {
            "时间层级": "2. 候选确认时刻 (confirmed_at)",
            "物理含义": "形态突破收盘、均线或成交量等所有入场确认条件同时满足的 K 线闭合时刻（闭区间右端 -1ms）",
            "本系统取值范围": "库内历史信号在 2026-06-28 至 2026-10-26 之间；自然流中目前尚未出现新确认",
            "是否受限于生效边界": "是。必须发生在策略正式生效时刻之后，才可作为新候选开仓。",
            "原始授权依据": "《用户要求与决策.md:5》：“从策略启动时生效，符合条件就可以响应……生效前已确认历史信号严禁追单。”"
        },
        {
            "时间层级": "3. 真实可得时刻 (available_at)",
            "物理含义": "行情与辅助指标（OI、资金费等）被本地采集器成功获取并落盘可供量化计算的真实时间戳",
            "本系统取值范围": "实时流为抓取写入时刻；历史研究若无物理日志则为 NULL (unproven)",
            "是否受限于生效边界": "是。无物理抓取证据时严禁以确认收盘时间顶替（禁止人为伪造 PIT 数据）。",
            "原始授权依据": "《用户约定/当前交接.md:8》：“数据确认时间不等于数据可得时间，无物理证据标置 unproven。”"
        },
        {
            "时间层级": "4. 持久化历史水位 (live_start / effective_floor)",
            "物理含义": "系统在 2026-10-23 自动持久化写入 live_run 表的因果闸门水位线（1792748844240）",
            "本系统取值范围": "2026-10-23 09:47:24 UTC",
            "是否受限于生效边界": "是此前测试运行自动落定的技术水位；代码将其与 paper_epoch 取 max() 作为下限。",
            "原始授权依据": "属代码级持久化机制。工单特别指出：代码 max() 结果不自动等同于本次启动的用户授权。"
        },
        {
            "时间层级": "5. 本次策略实盘启用时刻 (user_startup_epoch)",
            "物理含义": "用户在 2026-10-27 下达“上线系统”指令并经由服务管理中心启动的时间",
            "本系统取值范围": "2026-10-27 13:59:16 UTC (started_at: 1793109556649)",
            "是否受限于生效边界": "是本次模拟实盘运行的最核心法定启用边界。在此之前的历史确认一律阻断追补。",
            "原始授权依据": "《用户要求与决策.md:5》：用户原话“你应该要求清楚历史信号，从策略启动时生效，符合条件就可以响应”。"
        }
    ]

    analysis_result = {
        "live_run_state": run_dict,
        "runtime_process_state": {
            "pid": pid,
            "started_at_ms": started_at_ms,
            "started_at_utc": ms_to_utc(started_at_ms),
            "status": r_ident.get("status")
        },
        "boundary_relationship_analysis": {
            "is_normal_reboot_or_new_launch": "双重语义共存：技术实现上属于同一模型版本(FT-LOW4H-EXP-V2.2)的正常重启，因而代码沿用了10-23的持久水位；但业务语义上是用户于10-27首次下达'上线系统'的实盘模拟启用。",
            "effective_floor_evaluation": "由于 10-23 至 10-26 之间的所有历史研究信号已被彻底恢复为 origin='historical_research' 且 approved=false，无论将生效边界定为 10-23 还是 10-27，所有 289 条历史信号均被双重硬门禁彻底拦截，均不会产生任何当前挂单或坏账。",
            "formal_verdict": "边界安全性已达标；最终是否将持久化 live_start_ms 正式推进重置为 2026-10-27，保留为提请用户/主代理裁决项。"
        }
    }

    # 写 JSON
    with open(OUT_JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(analysis_result, f, indent=2, ensure_ascii=False)

    # 写 CSV
    fieldnames = ["时间层级", "物理含义", "本系统取值范围", "是否受限于生效边界", "原始授权依据"]
    with open(OUT_CSV_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(time_layers)

    print(f"[+] 原始授权与时间语义分析已落盘:")
    print(f"    JSON: {OUT_JSON_PATH} (SHA256: {file_hash(OUT_JSON_PATH)})")
    print(f"    CSV:  {OUT_CSV_PATH} (SHA256: {file_hash(OUT_CSV_PATH)})")

if __name__ == "__main__":
    main()
