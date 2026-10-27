#!/bin/bash
set -euo pipefail

echo "================================================================="
echo "FLOW-OI-01 主动成交、OI与币安大户指标联合回测 一键复现与全量验证"
echo "================================================================="

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "${WORKSPACE}"

PYTHON_BIN="/Users/boos/.workbuddy-ai/binaries/python/envs/default/bin/python"
if [ ! -x "${PYTHON_BIN}" ]; then
    PYTHON_BIN="python3"
fi

echo "[1/2] 运行全流程特征提取与四组对照回测生成脚本..."
"${PYTHON_BIN}" evidence/FLOW-OI-01/generate_flow_oi_01.py

echo "[2/2] 执行 pytest 一致性与契约自动化测试套件..."
"${PYTHON_BIN}" -m pytest -v evidence/FLOW-OI-01/test_flow_oi_consistency.py

echo "================================================================="
echo "FLOW-OI-01 全部 7 项验证 100% 通过！研究交付已完整冻结。"
echo "================================================================="
