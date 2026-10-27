# -*- coding: utf-8 -*-
"""FT-BASELINE-05 固化版本指纹清单：03_build_manifest.py"""
from __future__ import annotations

import hashlib
import json
import os

BASE_DIR = "/Users/boos/Desktop/威科夫因子共振趋势"
TARGET_DIR = os.path.join(BASE_DIR, "evidence/FT-BASELINE-05")

FILES_TO_HASH = [
    "evidence/FT-BASELINE-05/user_data/strategies/WyckoffEventTrendBaseline.py",
    "evidence/FT-BASELINE-05/user_data/config.json",
    "evidence/FT-BASELINE-05/user_data/run_validation_status.json",
    "evidence/FT-BASELINE-05/code/metrics_entrypoint.py",
    "evidence/FT-BASELINE-05/code/02_build_audit_tables.py",
    "evidence/FT-BASELINE-05/out/01_P0候选绑定与止损核对表.csv",
    "evidence/FT-BASELINE-05/out/02_资金费逐事件核对表.csv",
    "evidence/FT-BASELINE-05/out/03_资金费按笔汇总对账表.csv",
    "evidence/FT-BASELINE-05/out/04_freqtrade_trades.csv",
    "evidence/FT-BASELINE-05/out/exp3_metrics_rejection_proof.txt",
    "evidence/FT-BASELINE-05/logs/freqtrade_backtest_5symbols.log",
    "evidence/FT-BASELINE-05/logs/recursive_analysis.log",
    "evidence/FT-BASELINE-05/logs/lookahead_analysis.log",
    "evidence/FT-BASELINE-05/experiments/exp1_normal/run.log",
    "evidence/FT-BASELINE-05/experiments/exp1_normal/user_data/run_validation_status.json",
    "evidence/FT-BASELINE-05/experiments/exp2_pre_tamper/run.log",
    "evidence/FT-BASELINE-05/experiments/exp2_pre_tamper/user_data/strategies/WyckoffPreTamperStrategy.py",
    "evidence/FT-BASELINE-05/experiments/exp2_pre_tamper/user_data/run_validation_status.json",
    "evidence/FT-BASELINE-05/experiments/exp3_post_tamper/run.log",
    "evidence/FT-BASELINE-05/experiments/exp3_post_tamper/user_data/strategies/WyckoffPostTamperStrategy.py",
    "evidence/FT-BASELINE-05/experiments/exp3_post_tamper/user_data/run_validation_status.json",
    "evidence/FT-BASELINE-05/00_收尾报告.md"
]

def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()

def main():
    manifest = {
        "work_order": "FT-BASELINE-05",
        "title": "候选严格绑定与异常运行失效验证",
        "engine": "Freqtrade 2026.9",
        "python_version": "Python 3.11.16",
        "input_dataset_version": "WX-FT-IN-1.0 (evidence/FT-DATA-01)",
        "symbols": ["AR/USDT:USDT", "BANANA/USDT:USDT", "BAT/USDT:USDT", "CTSI/USDT:USDT", "PUMPBTC/USDT:USDT"],
        "timerange": "20250801-20251231",
        "review_status": "待Luna定向取证与审核判定",
        "closure_items": {
            "reproduction_findings": "证实 custom_stoploss 抛出 RuntimeError 被 strategy_safe_wrapper(..., supress_error=True) 吞掉；证实候选ID不匹配时旧代码存在时间窗替换漏洞",
            "minimal_fix": "彻底移除时间窗兜底；实现候选严格唯一绑定；开仓前拒单；持仓后绑定异常持久化明确无效状态 run_validation_status.json；指标入口硬性拦截拒绝采纳",
            "experiment_1_normal": "正常候选绑定，退出码0，5笔全成，与04逐笔核心字段0差异完全一致",
            "experiment_2_pre_tamper": "开仓前破坏候选ID，confirm_trade_entry 坚决拒单，无成交持仓，原因清晰记录",
            "experiment_3_post_tamper": "开仓后破坏候选绑定，真实引擎触发异常，持久化明确无效状态，指标入口坚决拒绝采纳",
            "funding_fee_explanation_corrected": "更正04说明：资金费差额100%来自数量口径微小差异(数量比与资金费比完全相等)，与Mark价格采样无关",
            "future_data_bias_check": "recursive-analysis 完全通过；lookahead-analysis 样本不足 10 笔，严格保留为【未完成】"
        },
        "files": {}
    }

    for rel_path in FILES_TO_HASH:
        full_path = os.path.join(BASE_DIR, rel_path)
        if os.path.exists(full_path):
            manifest["files"][rel_path] = {
                "size_bytes": os.path.getsize(full_path),
                "sha256": sha256_file(full_path)
            }
        else:
            manifest["files"][rel_path] = {"status": "pending_or_generated_next"}

    out_p = os.path.join(TARGET_DIR, "out/versions.json")
    with open(out_p, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=4, ensure_ascii=False)
        
    print(f"固化清单已生成: {out_p}")

if __name__ == "__main__":
    main()
