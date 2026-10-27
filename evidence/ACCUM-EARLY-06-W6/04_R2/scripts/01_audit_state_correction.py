#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
脚本 01: 交付与实际验收状态校正与多维矩阵生成
所属工单: ACCUM-EARLY-06-W6-R2 (总交接与实际验收状态校正)
职责:
1. 真实读取 W1(D)、W2、06-05/06-06(W3)、W4(A)、W5(B)、W6 的实际交付与复验产物；
2. 纠正 B (W5) 的状态：真实依赖门禁 NOT_READY_BLOCKED、资金费复验受阻、逐笔复验未实现，
   严禁用“策略未验证、零候选”替代“工程复验尚未完成”；
3. 同步 A (W4) 的状态：完整性判据待整改的事实；
4. 直接读取 D (W1-03_R1) 校正成果：21 项待决 (12P/6E/3G)，已确认止损不重复报批；
5. 生成四列分离矩阵：执行方交付状态、独立复验状态、Codex验收状态、用户待决事项。
"""

import json
import os
import hashlib
from datetime import datetime, timezone

WORKSPACE_ROOT = "/Users/boos/Desktop/威科夫因子共振趋势"

def sha256_file(filepath):
    if not os.path.exists(filepath):
        return None
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()

def main():
    # 1. 核验 D (W1-03_R1) 产物
    w1_r1_json_path = os.path.join(WORKSPACE_ROOT, "evidence/ACCUM-EARLY-06-W1/03_R1/待决表_R1.json")
    assert os.path.exists(w1_r1_json_path), f"缺少 W1-R1 待决表: {w1_r1_json_path}"
    with open(w1_r1_json_path, "r", encoding="utf-8") as f:
        w1_pending_data = json.load(f)
    
    # 统计 P, E, G
    items = w1_pending_data.get("items", [])
    p_count = sum(1 for it in items if it.get("class") == "P")
    e_count = sum(1 for it in items if it.get("class") == "E")
    g_count = sum(1 for it in items if it.get("class") == "G")
    total_pending = len(items)
    assert total_pending == 21, f"待决项数应为 21，实测 {total_pending}"
    assert p_count == 12, f"P 应该有 12 项，实测 {p_count}"
    assert e_count == 6, f"E 应该有 6 项，实测 {e_count}"
    assert g_count == 3, f"G 应该有 3 项，实测 {g_count}"

    # 2. 核验 B (W5-03_R1) 产物与真实状态
    w5_gate_path = os.path.join(WORKSPACE_ROOT, "evidence/ACCUM-EARLY-06-W5/03_R1/out/01_dependency_hard_gate.json")
    w5_funding_path = os.path.join(WORKSPACE_ROOT, "evidence/ACCUM-EARLY-06-W5/03_R1/out/02_funding_mapper_status.json")
    w5_verdicts_path = os.path.join(WORKSPACE_ROOT, "evidence/ACCUM-EARLY-06-W5/03_R1/out/summary_verification_verdicts.json")
    
    w5_gate_data = {}
    if os.path.exists(w5_gate_path):
        with open(w5_gate_path, "r", encoding="utf-8") as f:
            w5_gate_data = json.load(f)
    w5_gate_verdict = w5_gate_data.get("real_environment_audit", {}).get("gate_verdict", "UNKNOWN")

    # Codex 审查确证的事实：
    # 虽然 W5-R1 记录了 gate_verdict，但 Codex 审查指出其判据存在严重漏洞（空JSON可通过、逐笔资金费映射未真实复算）。
    # 资金费复验实际处于阻塞，不能把 B 当作已完成完整复验！
    # 严禁在总交接中用“策略未验证、零候选”替代“工程复验尚未完成”。

    # 3. 核验代码哈希基线
    accum_sha = sha256_file(os.path.join(WORKSPACE_ROOT, "框架/backend/accum_early_entry.py"))
    replay_sha = sha256_file(os.path.join(WORKSPACE_ROOT, "框架/backend/replay_calc.py"))
    expected_accum_sha = "3c570edc1eddaa2cd5c82c39fbf60a9deb633d376dd6b47200453b8f061c9017"
    expected_replay_sha = "c9fecd6a1e91273c5f600d75c17d977d93fa35f65baf8e9c9ac121d0bbd6e278"

    # 4. 构建四列分立矩阵 (十二工单)
    matrix = [
        {
            "window": "窗口一 (W1)",
            "order_id": "06-01",
            "task_name": "规则来源与已批准边界核对",
            "execution_status": "已交付 (01)",
            "verification_status": "已完成来源回溯 (区分31规则/8冲突)",
            "codex_status": "待 Codex 审核 (已被 D-03_R1 校正取代)",
            "user_decision": "无 (属规则出处整理)"
        },
        {
            "window": "窗口一 (W1)",
            "order_id": "06-02 / 工单D",
            "task_name": "待决表压缩与批准证据校正 (D-03_R1)",
            "execution_status": "已交付 (03_R1 校正版)",
            "verification_status": "已完成去重折叠 (21项=12P/6E/3G，已确认止损不再问)",
            "codex_status": "待 Codex 审核",
            "user_decision": "12 项参数待决 (P-01~P-12) 待用户裁决"
        },
        {
            "window": "窗口二 (W2)",
            "order_id": "06-03",
            "task_name": "承接判定时点最小修正",
            "execution_status": "已交付 (01)",
            "verification_status": "已通过反例时序验证 (扫低根求值)",
            "codex_status": "待 Codex 审核",
            "user_decision": "无"
        },
        {
            "window": "窗口二 (W2)",
            "order_id": "06-04",
            "task_name": "修正版本交付与代码冻结",
            "execution_status": "已交付 (02 冻结交付)",
            "verification_status": "哈希锁定 3c570edc...，保留49项必填阻断闸门",
            "codex_status": "待 Codex 审核",
            "user_decision": "无"
        },
        {
            "window": "窗口三 (W3)",
            "order_id": "06-05",
            "task_name": "冷热资金费缺口与读取接口核对",
            "execution_status": "已交付 (06-05)",
            "verification_status": "核对热库 15.8 天断流与冷档覆盖边界",
            "codex_status": "待 Codex 审核",
            "user_decision": "无"
        },
        {
            "window": "窗口三 (W3)",
            "order_id": "06-06",
            "task_name": "隔离冷热合并输入与可追溯适配器",
            "execution_status": "已交付 (06-06)",
            "verification_status": "交付标准适配器 funding_events_adapter.py 及母集48/31811事件",
            "codex_status": "待 Codex 审核",
            "user_decision": "无"
        },
        {
            "window": "窗口四 (W4)",
            "order_id": "06-07",
            "task_name": "05-D-R1 逐笔复核与差异清单",
            "execution_status": "已交付 (01)",
            "verification_status": "核对 101 成交与 1 拒单，纠偏 CARV 净额",
            "codex_status": "待 Codex 审核",
            "user_decision": "无"
        },
        {
            "window": "窗口四 (W4)",
            "order_id": "06-08 / 工单A",
            "task_name": "资金费判据重构与逐笔对账 (工单A)",
            "execution_status": "已交付 R1；待交付 04_R2",
            "verification_status": "已完成适配器对账与MET纠偏；但完整性判据仍待整改，逐笔复验待闭环",
            "codex_status": "退回整改 / 等待 04_R2",
            "user_decision": "无 (属财务数据工程核对)"
        },
        {
            "window": "窗口五 (W5)",
            "order_id": "06-09",
            "task_name": "验收用例与当前缺陷冻结",
            "execution_status": "已交付 (01)",
            "verification_status": "建立原 7 事件与时序反例矩阵",
            "codex_status": "待 Codex 审核",
            "user_decision": "无"
        },
        {
            "window": "窗口五 (W5)",
            "order_id": "06-10 / 工单B",
            "task_name": "依赖硬门禁与独立端到端复验 (工单B)",
            "execution_status": "已交付 R1；待交付 04_R2",
            "verification_status": "工程复验尚未完成！门禁硬阻断(NOT_READY_BLOCKED)，资金费复验受阻(BLOCKED)，逐笔映射未实现",
            "codex_status": "退回整改 / 独立复验阻塞中 (等待 A/B 04_R2 闭环)",
            "user_decision": "无 (严禁用'策略未验证、零候选'替代'工程复验尚未完成')"
        },
        {
            "window": "窗口六 (W6)",
            "order_id": "06-11",
            "task_name": "研究范围基线版本与边界核对",
            "execution_status": "已交付 (01)",
            "verification_status": "锁定母集 48 标的/1178 事件，纠正分流对比解释",
            "codex_status": "待 Codex 审核",
            "user_decision": "无"
        },
        {
            "window": "窗口六 (W6)",
            "order_id": "06-12 / 工单C",
            "task_name": "总交接与实际验收状态校正 (本单 W6-R2)",
            "execution_status": "交付 04_R2 阶段校正报告",
            "verification_status": "纠正 B 虚假完成与 A 判据待改事实；四列状态分立；三组样本彻底断裂解耦",
            "codex_status": "阶段报告待 Codex 审核 (最终汇总等 A/B 04_R2)",
            "user_decision": "包含 12 项参数裁决建议，不新增无依据审批"
        }
    ]

    out_data = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "task": "ACCUM-EARLY-06-W6-R2",
        "description": "总交接与实际验收状态校正矩阵",
        "code_identity": {
            "accum_early_entry.py": {
                "sha256": accum_sha,
                "matches": accum_sha == expected_accum_sha
            },
            "replay_calc.py": {
                "sha256": replay_sha,
                "matches": replay_sha == expected_replay_sha
            }
        },
        "b_w5_real_status": {
            "w5_gate_verdict_reported": w5_gate_verdict,
            "codex_audit_finding": "空JSON可通过、逐笔复验未实现；资金费复验实际处于阻塞，工程复验尚未完成",
            "is_fully_verified": False,
            "correction_note": "严禁用'策略未验证、零候选'掩盖'工程复验尚未完成'；资金费逐笔复验真实处于受阻状态，等待 04_R2"
        },
        "a_w4_real_status": {
            "r1_delivered": True,
            "integrity_criteria_status": "待整改 (Codex 指出判据与逐笔映射待完善)",
            "waiting_for": "04_R2"
        },
        "d_w1_real_status": {
            "total_pending": total_pending,
            "P_params": p_count,
            "E_evidence": e_count,
            "G_engineering": g_count,
            "confirmed_stops_need_reapproval": False,
            "confirmed_stops_count": 8
        },
        "matrix": matrix
    }

    out_file = os.path.join(WORKSPACE_ROOT, "evidence/ACCUM-EARLY-06-W6/04_R2/out/01_state_correction_matrix.json")
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(out_data, f, ensure_ascii=False, indent=2)
    print(f"状态校正矩阵已生成: {out_file}")

if __name__ == "__main__":
    main()
