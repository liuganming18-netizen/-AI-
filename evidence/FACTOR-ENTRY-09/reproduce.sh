#!/bin/bash
set -euo pipefail

echo "================================================================="
echo "FACTOR-ENTRY-09 一键复现与交付一致性全量校验"
echo "================================================================="

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "${WORKSPACE}"

PYTHON_BIN="/Users/boos/.workbuddy-ai/binaries/python/envs/default/bin/python"
if [ ! -x "${PYTHON_BIN}" ]; then
    PYTHON_BIN="python3"
fi

echo "[1/2] 运行 FACTOR-ENTRY-09 自动化构建脚本..."
"${PYTHON_BIN}" evidence/FACTOR-ENTRY-09/generate_factor_entry_09.py

echo "[2/2] 执行 pytest 一致性测试套件..."
"${PYTHON_BIN}" -m pytest -v evidence/FACTOR-ENTRY-09/test_manifest_consistency.py

echo "================================================================="
echo "FACTOR-ENTRY-09 全量验证通过，版本已完全冻结！"
echo "================================================================="
