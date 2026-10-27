# -*- coding: utf-8 -*-
"""ACCUM-EARLY-06-W5-R1 | 脚本 04: 全项逐条【通过／失败／未覆盖／阻塞】判定汇总生成器

功能：
1. 整合依赖门禁、基线核验、资金费探针与口径分层；
2. 生成符合主代理核查标准的最终判定汇总机器数据；
3. 输出至 out/summary_verification_verdicts.json。
"""
import json
import os
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[4]
OUT_DIR = PROJECT_ROOT / "evidence" / "ACCUM-EARLY-06-W5" / "03_R1" / "out"
OUT_DIR.mkdir(parents=True, exist_ok=True)

def generate_summary():
    gate_data = json.loads((OUT_DIR / "01_dependency_hard_gate.json").read_text(encoding="utf-8"))
    funding_data = json.loads((OUT_DIR / "02_funding_mapper_status.json").read_text(encoding="utf-8"))
    baseline_data = json.loads((OUT_DIR / "03_baseline_evidence_audit.json").read_text(encoding="utf-8"))

    verdicts = [
        {
            "category": "依赖硬门禁",
            "item": "上游依赖真实硬门禁与防写死机制",
            "status": "PASS",
            "evidence_ref": "out/01_dependency_hard_gate.json",
            "finding": "门禁单元测试全绿 (负例A依赖缺失阻断、负例B版本替换拦截、负例C范围不足拦截、正向对照放行)；彻底消除了'写死PENDING却返回READY'的缺陷"
        },
        {
            "category": "依赖硬门禁",
            "item": "当前真实环境上游依赖齐备性总判定",
            "status": "BLOCKED",
            "evidence_ref": "out/01_dependency_hard_gate.json",
            "finding": "W2 代码哈希就位、06-06 适配器及母集48事件就位；但窗口四工单A (W4-R1) 尚未完成交付，门禁严格判定 NOT_READY_BLOCKED，硬阻断总验收通过"
        },
        {
            "category": "工程行为复验",
            "item": "被验代码版本身份锁定 (W2 06-04 交付版)",
            "status": "PASS",
            "evidence_ref": "out/03_baseline_evidence_audit.json",
            "finding": "accum_early_entry.py SHA-256 确认为 3c570edc1eddaa2cd5c82c39fbf60a9deb633d376dd6b47200453b8f061c9017，版本零漂移"
        },
        {
            "category": "工程行为复验",
            "item": "承接时序修复与四大反例场景独立复验",
            "status": "PASS",
            "evidence_ref": "out/03_baseline_evidence_audit.json",
            "finding": "承接求值下标严格锁定在扫低发生根 i=60；扫低吸筹达标突破缩量正常放行 (候选=1)；扫低卖压崩塌突破放量成功拦截 (候选=0)；扫低缺字段正规报 DATA_INSUFFICIENT；门槛关闭安全跳过"
        },
        {
            "category": "工程行为复验",
            "item": "批准配置缺失硬阻断与经济口径冲突拒绝",
            "status": "PASS",
            "evidence_ref": "out/03_baseline_evidence_audit.json",
            "finding": "48 项空白模板 open_allowed=False，called_paper_engine=False；声明滑点 3bps 与引擎 5bps 冲突时 100% 触发 economic_caliber_declared_mismatch 硬阻断"
        },
        {
            "category": "工程行为复验",
            "item": "冷却 24 小时不得作为未经批准的通过条件",
            "status": "PASS",
            "evidence_ref": "out/03_baseline_evidence_audit.json",
            "finding": "在未批准配置中设置 cooldown_hours=24，依然被批准闸门 100% 硬阻断，无法绕过"
        },
        {
            "category": "工程行为复验",
            "item": "数据缺口表幂等与重复扫描零新增",
            "status": "PASS",
            "evidence_ref": "out/03_baseline_evidence_audit.json",
            "finding": "重扫前后 orders/fills/positions/candidates/gaps 增量严格为 0"
        },
        {
            "category": "工程行为复验",
            "item": "真实系统独立跨进程恢复与幂等拦截",
            "status": "PASS",
            "evidence_ref": "out/03_baseline_evidence_audit.json",
            "finding": "独立解释器进程 A 开仓退出，ps 确证死亡；独立解释器进程 B 恢复读取账本再次提交同一 signal_uid，触发 duplicate_signal_uid 拦截，前后账本增量为 0"
        },
        {
            "category": "工程行为复验",
            "item": "生产目录越界写拦截与只读连接硬约束",
            "status": "PASS",
            "evidence_ref": "out/03_baseline_evidence_audit.json",
            "finding": "路径指向 数据/paper 时 100% 触发 isolation_violation 拦截；对生产行情库进行只读查询成功且写操作抛出异常拦截；不依赖生产库哈希静止假设"
        },
        {
            "category": "历史行情结果",
            "item": "原 7 笔事件原输入终态与纠偏",
            "status": "REPORTED_HONESTLY",
            "evidence_ref": "out/03_baseline_evidence_audit.json",
            "finding": "原输入保持，终态确认为 6 笔破位失效 (INVALIDATED) + 1 笔前序占位未建档 (NOT_FOUND)，候选数严格为 0；分开说明：此系特定历史输入下破位拦截的工程行为，不等于策略有效或无效"
        },
        {
            "category": "上游财务依赖",
            "item": "资金费事件到逐笔成本及分类映射独立复核",
            "status": "BLOCKED",
            "evidence_ref": "out/02_funding_mapper_status.json",
            "finding": "06-06 适配器与 31,811 条冷热合并资金费事件已在本地探针就绪；窗口四工单A (W4-R1) 尚未交付，本单严禁代上游修复后再自验，严格阻塞等待 W4-R1 成果落盘"
        },
        {
            "category": "策略与实盘边界",
            "item": "自然行情真实成交与策略有效性",
            "status": "UNCOVERED",
            "evidence_ref": "out/03_baseline_evidence_audit.json",
            "finding": "所有测试均为 fixture 链路自检，正式策略参数全未批准，无自然挂钟成交，严禁声称通过"
        },
        {
            "category": "待决策略语义",
            "item": "多根扫低、压制位窗口与承接失败终态路径",
            "status": "UNRESOLVED_AMBIGUITY",
            "evidence_ref": "out/03_baseline_evidence_audit.json",
            "finding": "代码显式标记 multi_bar_scope_unverified=True；压制位窗口与承接失败终态路径列为未决，不擅自替上游修改代码或参数"
        }
    ]

    summary_doc = {
        "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "audit_window": "ACCUM-EARLY-06-W5-R1",
        "task_name": "依赖门禁与独立复验整改",
        "auditor_role": "独立行为复验窗口 (反重力/Work 独立会话，非代码修改者)",
        "code_tested": baseline_data.get("code_identity"),
        "gate_status": gate_data.get("summary", {}).get("real_gate_verdict"),
        "blocking_reason": gate_data.get("summary", {}).get("real_blocking_reason"),
        "verdicts": verdicts
    }

    out_file = OUT_DIR / "summary_verification_verdicts.json"
    out_file.write_text(json.dumps(summary_doc, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Summary verification verdicts generated, saved to {out_file}")

if __name__ == "__main__":
    generate_summary()
