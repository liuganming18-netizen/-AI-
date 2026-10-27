#!/usr/bin/env bash
# ENTRY-AUDIT-04 一键复现
#
# 只读 ENTRY-AUDIT-03 的既有产物，**不重跑任何回测**，全程约 2~3 分钟。
# 全程只写 evidence/ENTRY-AUDIT-04/，不碰原账本、反重力产物、正式策略、撮合、UI、API、采集器、交易闸门。
#
# 用法：
#   cd /Users/boos/Desktop/威科夫因子共振趋势
#   bash evidence/ENTRY-AUDIT-04/reproduce.sh
set -euo pipefail

PY="/Users/boos/.workbuddy-ai/binaries/python/envs/default/bin/python"
ROOT="/Users/boos/Desktop/威科夫因子共振趋势"
cd "$ROOT"

echo "=== [0/5] 环境检查 ==="
"$PY" -c "import pandas, pyarrow; print('pandas', pandas.__version__, '/ pyarrow', pyarrow.__version__)"

echo
echo "=== [1/5] 第一阶段：冻结可交接账本 ==="
"$PY" evidence/ENTRY-AUDIT-04/00_freeze_handoff.py 2>&1 | tee evidence/ENTRY-AUDIT-04/log_01_handoff.txt

echo
echo "=== [2/5] 第二阶段基准：按 FE06 契约独立实现分类 ==="
"$PY" evidence/ENTRY-AUDIT-04/01_independent_classification.py 2>&1 | tee evidence/ENTRY-AUDIT-04/log_02_classification.txt

echo
echo "=== [3/5] 第二阶段：FE07 签收与七项复核（未交付则输出待交付）==="
"$PY" evidence/ENTRY-AUDIT-04/02_fe07_recheck.py 2>&1 | tee evidence/ENTRY-AUDIT-04/log_03_recheck.txt

echo
echo "=== [4/5] 签收单 + 已知差异表 ==="
"$PY" evidence/ENTRY-AUDIT-04/03_diff_and_signoff.py 2>&1 | tee evidence/ENTRY-AUDIT-04/log_05_signoff.txt

echo
echo "=== [5/5] 收尾自检：本轮只新增，不动既有产物 ==="
"$PY" evidence/ENTRY-AUDIT-04/99_verify_unchanged.py 2>&1 | tee evidence/ENTRY-AUDIT-04/log_04_verify.txt

echo
echo "=== 完成 ==="
echo "交接包：evidence/ENTRY-AUDIT-04/handoff/"
echo "供 FE07 读取：evidence/ENTRY-AUDIT-04/handoff/ledger_handoff.parquet"
echo "路径与哈希：evidence/ENTRY-AUDIT-04/handoff/paths_for_fe07.json"
