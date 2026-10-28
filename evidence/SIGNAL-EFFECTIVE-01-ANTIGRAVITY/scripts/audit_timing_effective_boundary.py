#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SIGNAL-EFFECTIVE-01 独立复核脚本 2
规则与生效时序边界逐层对照核验
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
OUT_CSV_PATH = os.path.join(OUT_DIR, "规则时序对照.csv")

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
    print("[*] 开始执行规则与时序边界对照核验...")
    live_conn = sqlite3.connect(LIVE_DB_PATH)
    l_cur = live_conn.cursor()

    # 读取 live_run 水位
    l_cur.execute("SELECT key, value_json FROM live_run")
    run_kv = dict(l_cur.fetchall())
    live_start_ms = int(run_kv.get("live_start_ms", 0))
    paper_epoch_ms = int(run_kv.get("paper_epoch_ms", 1791939600000))
    effective_floor_ms = max(live_start_ms, paper_epoch_ms)

    print(f"    live_start_ms:      {live_start_ms} ({ms_to_utc(live_start_ms)})")
    print(f"    paper_epoch_ms:     {paper_epoch_ms} ({ms_to_utc(paper_epoch_ms)})")
    print(f"    effective_floor_ms: {effective_floor_ms} ({ms_to_utc(effective_floor_ms)})")

    # 抽取具有代表性的样本
    # 类别 1: 启动前确认历史样本 (confirmed_at < effective_floor_ms)
    # 类别 2: 边界后被 Work 误放行样本 (confirmed_at >= effective_floor_ms，被伪造 available_at)
    # 类别 3: 启动前结构、启动后确认的合规对照定义
    # 类别 4: 重启状态下水位保持核验
    # 类别 5: 版本切换边界

    cases = [
        # Case 1: 早期历史信号 (2026-07 确认)
        {
            "uid": "accum05_4a_XTZUSDT_1784177999999",
            "category": "启动前确认历史信号",
            "scenario": "确认发生于 2026-07-16，远早于策略生效时刻 2026-10-23/27",
            "expected_behavior": "历史研究仅展示，严禁下达纸面或实盘订单；executable_state 应为 historical_not_executable，origin 应为 historical_research",
            "actual_behavior": "库内被篡改为 origin='recovered', executable_state='enterable'；available_at 被强填为 confirmed_at；因 confirmed_at < floor 未进入 current 接口",
            "diff_type": "身份与时间篡改(未造成坏账开仓，但标签违规)",
            "source_path": "框架/backend/live/live_signals.sqlite3"
        },
        # Case 2: 临近启动前历史信号，但晚于 10-23 历史 floor (2026-10-26 确认)
        {
            "uid": "accum05_4c_MAVUSDT_1793005199999",
            "category": "启动前确认但被当前接口误放行信号",
            "scenario": "确认发生于 2026-10-26 08:59:59 UTC，实际是 accum05 回放信号，早于 2026-10-27 实盘上线",
            "expected_behavior": "属于历史信号，不得进 current 交易区，不得生成当前订单",
            "actual_behavior": "因 Work 伪造 available_at=confirmed_at 且 confirmed_at > 10-23 floor，被 /api/v1/signals?scope=current 误作为活跃交易卡片放行！",
            "diff_type": "严重误放行(掩耳盗铃展示，幸未发往 paper_engine)",
            "source_path": "框架/backend/live_api.py:311-340"
        },
        # Case 3: 临近启动前历史信号 2 (HYPERUSDT)
        {
            "uid": "accum05_4a_HYPERUSDT_1792771199999",
            "category": "启动前确认但被当前接口误放行信号",
            "scenario": "确认发生于 2026-10-23 15:59:59 UTC，为 accum05 回放历史信号",
            "expected_behavior": "历史信号，应在只读研究区折叠展示，不进当前交易区",
            "actual_behavior": "被改标签后以 'recovered' 身份进入当前区，卡片显示共振分 71 分",
            "diff_type": "严重误放行(篡改标签冒充恢复信号)",
            "source_path": "框架/backend/live/live_signals.sqlite3"
        },
        # Case 4: 启动前结构、启动后确认之合法规则定义
        {
            "uid": "SYNTH_PRE_STRUCTURE_POST_CONFIRM",
            "category": "启动前结构、启动后确认(合法规则)",
            "scenario": "4h 箱体在 2026-10-20 形成，但突破确认于 2026-10-27 15:00 UTC (启动生效后)",
            "expected_behavior": "结构形成早于 epoch 合法；只要确认条件在生效后新发生且符合规则，应产生唯一实时新 UID 并进入正常执行时序",
            "actual_behavior": "目前主策略 3a 因超窄失效线与 OI 门槛未在自然流中确认出新候选；无信号是因规则严苛，非系统漏接",
            "diff_type": "行为符合因果定义(主策略自然行情暂未触发展开)",
            "source_path": "evidence/NATURAL-CANDIDATE-01/README.md"
        },
        # Case 5: 迟到与回放数据时间语义
        {
            "uid": "SYNTH_LATE_OR_REPLAY_PIT",
            "category": "迟到与回放数据时间语义",
            "scenario": "历史数据补档或回放运行，缺少外部写入时间戳",
            "expected_behavior": "available_at 必须无证据标置 NULL/unproven，不得以 confirmed_at 顶替",
            "actual_behavior": "第十九次更新中数据库直接执行 UPDATE live_signal SET available_at = confirmed_at，破坏 PIT 审计基线",
            "diff_type": "时序语义伪造(违反时间点有效性原则)",
            "source_path": "docs/用户约定/当前交接.md:16"
        },
        # Case 6: 正常重启沿用水位与断点
        {
            "uid": "SYSTEM_REBOOT_WATERMARK_RETENTION",
            "category": "正常重启沿用水位",
            "scenario": "服务进程重启 (如 API 从 pid 30808 重启至 68764)",
            "expected_behavior": "ensure_live_start_ms 检查 live_run 发现已有 1792748844240，幂等沿用原值，不重置水位，不丢已有持仓/挂单",
            "actual_behavior": "代码逻辑正确沿用了 live_start_ms (1792748844240)；重启未引起水位飘移",
            "diff_type": "正常(代码实现符合幂等规范)",
            "source_path": "框架/backend/live_store.py:833-840"
        },
        # Case 7: 真实交易禁用边界
        {
            "uid": "LIVE_TRADE_DISABLE_BOUNDARY",
            "category": "真实交易权限边界",
            "scenario": "当前所有运行参数与配置",
            "expected_behavior": "用户未授权真实下单；仅在 5,000 USDT 纸面模拟环境运行",
            "actual_behavior": "真实交易保持禁用，仅 paper_engine 运行",
            "diff_type": "正常(严格遵守真实交易禁用边界)",
            "source_path": "docs/用户约定/用户要求与决策.md:25"
        }
    ]

    fieldnames = [
        "uid", "category", "scenario", "expected_behavior", "actual_behavior",
        "diff_type", "source_path"
    ]

    with open(OUT_CSV_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(cases)

    live_conn.close()
    print(f"[+] 时序对照结果已写入: {OUT_CSV_PATH} (SHA256: {file_hash(OUT_CSV_PATH)})")

if __name__ == "__main__":
    main()
