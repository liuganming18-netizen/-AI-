#!/usr/bin/env bash
# ==============================================================================
# FACTOR-ENTRY-01 一键离线复现脚本
# 用途：全流程复现第一阶段数据契约审计、第二阶段微观特征提取、第三阶段消融评估
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "${PROJECT_ROOT}"

PY="/Users/boos/.workbuddy-ai/binaries/python/envs/default/bin/python"
if [ ! -f "${PY}" ]; then
    PY="python3"
fi

echo "=============================================================================="
echo "FACTOR-ENTRY-01 离线验证执行开始: $(date '+%Y-%m-%d %H:%M:%S')"
echo "Python 环境: ${PY}"
echo "工程根目录: ${PROJECT_ROOT}"
echo "=============================================================================="

echo ">>> [Stage 1] 运行数据契约与覆盖率审计..."
"${PY}" evidence/FACTOR-ENTRY-01/stage1_feature_contract_and_coverage.py

echo ">>> [Stage 2] 运行候选池特征提取与 As-of 对齐管线..."
"${PY}" evidence/FACTOR-ENTRY-01/enrich_unified_candidate_dataset.py

echo ">>> [Stage 3] 运行消融对比与统计评估..."
"${PY}" evidence/FACTOR-ENTRY-01/stage3_ablation_evaluation.py

echo "=============================================================================="
echo "FACTOR-ENTRY-01 离线验证全部完成: $(date '+%Y-%m-%d %H:%M:%S')"
echo "产物目录: evidence/FACTOR-ENTRY-01/"
echo "=============================================================================="
