#!/usr/bin/env bash
# =============================================================================
# 工单 FACTOR-ENTRY-08｜一键复现脚本
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE="$(cd "${SCRIPT_DIR}/../.." && pwd)"

PYTHON_BIN="/Users/boos/.workbuddy-ai/binaries/python/envs/default/bin/python"
if [ ! -f "${PYTHON_BIN}" ]; then
  PYTHON_BIN="python3"
fi

echo "================================================================="
echo "执行 FACTOR-ENTRY-08 复现流程..."
echo "工作区根目录: ${WORKSPACE}"
echo "Python 解释器: ${PYTHON_BIN}"
echo "================================================================="

cd "${WORKSPACE}"

# 1. 执行来源逐字段全量等价性验证
echo "[1/3] 运行来源全量等价性核验..."
"${PYTHON_BIN}" evidence/FACTOR-ENTRY-08/tools/verify_source_equivalence.py

# 2. 运行主生成脚本（显式读取 EA04 冻结交接包，重算分类并生成全部产物）
echo "[2/3] 运行 FE08 主生成脚本..."
"${PYTHON_BIN}" evidence/FACTOR-ENTRY-08/generate_factor_entry_08.py

# 3. 运行一致性与防呆测试套件
echo "[3/3] 运行自动化测试套件..."
cd "${SCRIPT_DIR}"
"${PYTHON_BIN}" -m pytest test_classification_and_cost_consistency.py -v | tee test_results.log

echo "================================================================="
echo "FACTOR-ENTRY-08 全部复现完成！产物已就绪。"
echo "================================================================="
