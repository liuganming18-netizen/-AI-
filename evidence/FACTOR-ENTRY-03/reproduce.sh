#!/usr/bin/env bash
# ==============================================================================
# FACTOR-ENTRY-03 一键离线复现脚本
# 用途：全流程复现入场质量微观路径归因、缺失偏差分析与指标相关性检验
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
echo "FACTOR-ENTRY-03 离线归因执行开始: $(date '+%Y-%m-%d %H:%M:%S')"
echo "Python 环境: ${PY}"
echo "工程根目录: ${PROJECT_ROOT}"
echo "=============================================================================="

echo ">>> 运行入场质量归因与指标有效性分析管线..."
"${PY}" evidence/FACTOR-ENTRY-03/attribution_analysis.py

echo "=============================================================================="
echo "FACTOR-ENTRY-03 离线归因全部完成: $(date '+%Y-%m-%d %H:%M:%S')"
echo "产物目录: evidence/FACTOR-ENTRY-03/"
echo "=============================================================================="
