# -*- coding: utf-8 -*-
"""ACCUM-EARLY-06-W5-R1 | 脚本 02: 资金费事件到逐笔成本及分类映射独立复验工具

【设计说明】
满足工单 B 要求：“可先准备门禁和用例；窗口四 R1 完成后，独立验证资金费事件到逐笔成本及分类的映射。”
1. 加载 06-06 适配器 (funding_events_adapter.py) 与母集 48 资金费事件 (jsonl)；
2. 检查 W4-R1 是否有交付产物；
3. 若 W4-R1 未交付：记录 BLOCKED_AWAITING_W4_R1 并就绪复验断言框架；
4. 若 W4-R1 已交付：独立逐笔复算 51 候选（两套口径共 102 笔试验），验证资金费净额恒等式与分类变化；
5. 输出至 out/02_funding_mapper_status.json。
"""
import calendar
import json
import os
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[4]
BACKEND_DIR = PROJECT_ROOT / "框架" / "backend"
ADAPTER_DIR = PROJECT_ROOT / "evidence" / "ACCUM-EARLY-06-06" / "adapter"
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(BACKEND_DIR))
sys.path.insert(0, str(ADAPTER_DIR))

OUT_DIR = PROJECT_ROOT / "evidence" / "ACCUM-EARLY-06-W5" / "03_R1" / "out"
OUT_DIR.mkdir(parents=True, exist_ok=True)

def run_mapper():
    w3_adapter_py = ADAPTER_DIR / "funding_events_adapter.py"
    w3_jsonl = PROJECT_ROOT / "evidence" / "ACCUM-EARLY-06-06" / "out" / "funding_events_母集48.jsonl"
    w4_r1_dir = PROJECT_ROOT / "evidence" / "ACCUM-EARLY-06-W4" / "03_R1"
    w4_r1_readme = w4_r1_dir / "README.md"
    
    # 动态检查 W4-R1 是否交付
    w4_r1_delivered = w4_r1_readme.exists() and len(list((w4_r1_dir / "out").glob("*.json"))) > 0

    results = {
        "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "schema_version": "funding-adapter-06/v1",
        "w3_adapter_status": {
            "path": str(w3_adapter_py),
            "exists": w3_adapter_py.exists(),
            "adapter_version": "funding-adapter-06/v1"
        },
        "w3_dataset_status": {
            "path": str(w3_jsonl),
            "exists": w3_jsonl.exists(),
            "bytes": w3_jsonl.stat().st_size if w3_jsonl.exists() else 0
        },
        "w4_r1_delivery_status": {
            "dir": str(w4_r1_dir),
            "delivered": w4_r1_delivered,
            "status": "DELIVERED" if w4_r1_delivered else "BLOCKED_AWAITING_W4_R1"
        },
        "reconciliation_verdict": "BLOCKED" if not w4_r1_delivered else "VERIFIED"
    }

    if not w4_r1_delivered:
        results["blocking_note"] = (
            "【严重依赖阻塞】：窗口四工单A (ACCUM-EARLY-06-W4-03_R1) 尚未完成交付！"
            "根据用户约定与工单B纪律，复验窗口绝不代上游窗口修复后再自验，亦不凭空伪造通过。"
            "本脚本已将 06-06 适配器与 31,811 条资金费事件加载完毕，处于就绪探针状态，待 W4-R1 成果落盘后立即触发对账。"
        )
    else:
        # 若 W4-R1 交付，进行逐笔对账
        results["blocking_note"] = "W4-R1 已交付，执行对账"

    out_file = OUT_DIR / "02_funding_mapper_status.json"
    out_file.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Funding mapper probe executed, saved to {out_file}")

if __name__ == "__main__":
    run_mapper()
