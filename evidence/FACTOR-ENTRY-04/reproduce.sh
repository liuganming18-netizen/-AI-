#!/usr/bin/env bash
# ==============================================================================
# 工单 FACTOR-ENTRY-04 一键独立复现脚本
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

PYTHON_BIN="/Users/boos/.workbuddy-ai/binaries/python/envs/default/bin/python"
if [ ! -x "${PYTHON_BIN}" ]; then
    PYTHON_BIN="python3"
fi

echo "======================================================================"
echo "执行 FACTOR-ENTRY-04 样本对账与分类证据复算..."
echo "工作路径: ${PROJECT_ROOT}"
echo "Python解释器: ${PYTHON_BIN}"
echo "======================================================================"

cd "${PROJECT_ROOT}"

"${PYTHON_BIN}" "${SCRIPT_DIR}/audit_and_reconcile_04.py"

echo "======================================================================"
echo "复算完成！产物已输出到 ${SCRIPT_DIR}/:"
ls -lh "${SCRIPT_DIR}"
echo "======================================================================"
