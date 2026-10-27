#!/usr/bin/env bash
# ==============================================================================
# FACTOR-ENTRY-02 一键离线复现脚本
# 用途：全流程复现第一阶段样本与时点审计、第二与第三阶段双口径重算与有限消融
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
echo "FACTOR-ENTRY-02 离线复核执行开始: $(date '+%Y-%m-%d %H:%M:%S')"
echo "Python 环境: ${PY}"
echo "工程根目录: ${PROJECT_ROOT}"
echo "=============================================================================="

echo ">>> [Stage 1] 运行样本映射与特征时点审计..."
"${PY}" evidence/FACTOR-ENTRY-02/stage1_audit.py

echo ">>> [Stage 2 & 3] 运行双口径重算、微观归因与有限消融..."
"${PY}" evidence/FACTOR-ENTRY-02/stage2_stage3_recalculation_and_ablation.py

echo "=============================================================================="
echo "FACTOR-ENTRY-02 离线复核全部完成: $(date '+%Y-%m-%d %H:%M:%S')"
echo "产物目录: evidence/FACTOR-ENTRY-02/"
echo "=============================================================================="
