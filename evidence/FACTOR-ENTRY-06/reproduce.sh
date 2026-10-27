#!/usr/bin/env bash
# ==============================================================================
# 工单 FACTOR-ENTRY-06 一键独立复现与测试脚本
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

PYTHON_BIN="/Users/boos/.workbuddy-ai/binaries/python/envs/default/bin/python"
if [ ! -x "${PYTHON_BIN}" ]; then
    PYTHON_BIN="python3"
fi

echo "======================================================================"
echo "执行 FACTOR-ENTRY-06 分类契约与研究产物一致性修复..."
echo "工作路径: ${PROJECT_ROOT}"
echo "Python解释器: ${PYTHON_BIN}"
echo "======================================================================"

cd "${PROJECT_ROOT}"

# 1. 运行核心重算与同源生成脚本
echo "[1/3] 运行 audit_and_reconstruct_06.py 生成四表一文..."
"${PYTHON_BIN}" "${SCRIPT_DIR}/audit_and_reconstruct_06.py"

# 2. 运行自动化一致性与契约约束测试套件
echo "[2/3] 运行 test_classification_consistency.py 自动化测试..."
"${PYTHON_BIN}" "${SCRIPT_DIR}/test_classification_consistency.py" 2>&1 | tee "${SCRIPT_DIR}/test_results.log"

# 3. 重新生成并刷新哈希清单
echo "[3/3] 刷新 hashes.json 文件指纹..."
"${PYTHON_BIN}" -c '
import hashlib, json
from pathlib import Path

root = Path("'${PROJECT_ROOT}'")
fe06_dir = root / "evidence" / "FACTOR-ENTRY-06"

def get_file_info(p):
    b = p.read_bytes()
    return {
        "size_bytes": len(b),
        "sha256": hashlib.sha256(b).hexdigest()
    }

manifest = {"inputs": {}, "outputs": {}}

inputs = [
    root / "evidence/ENTRY-AUDIT-02/报告.md",
    root / "evidence/FACTOR-ENTRY-05/reconstructed_candidate_features.parquet",
    root / "evidence/FACTOR-ENTRY-05/discrepancy_summary.json",
    root / "evidence/FACTOR-ENTRY-04/candidate_evaluations_audit.parquet",
    root / "evidence/FACTOR-ENTRY-03/candidate_features_enriched.parquet",
    root / "evidence/FACTOR-ENTRY-01/paired_trades_and_rejects.csv"
]
for p in inputs:
    if p.exists():
        manifest["inputs"][str(p.relative_to(root))] = get_file_info(p)

outputs = [
    fe06_dir / "classification_contract.md",
    fe06_dir / "audit_and_reconstruct_06.py",
    fe06_dir / "reconstructed_candidate_features.csv",
    fe06_dir / "reconstructed_candidate_features.parquet",
    fe06_dir / "discrepancy_old_vs_new.csv",
    fe06_dir / "discrepancy_summary.json",
    fe06_dir / "test_classification_consistency.py",
    fe06_dir / "test_results.log",
    fe06_dir / "reproduce.sh",
    fe06_dir / "报告.md"
]
for p in outputs:
    if p.exists():
        manifest["outputs"][str(p.relative_to(root))] = get_file_info(p)

with open(fe06_dir / "hashes.json", "w", encoding="utf-8") as f:
    json.dump(manifest, f, indent=2, ensure_ascii=False)
print("hashes.json 刷新完成！")
'

echo "======================================================================"
echo "复算与验证成功！所有产物已输出到 ${SCRIPT_DIR}/:"
ls -lh "${SCRIPT_DIR}"
echo "======================================================================"
