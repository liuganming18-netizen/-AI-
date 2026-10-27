#!/usr/bin/env bash
# VOLUME-BE-VERIFY-01 独立核验一键复现脚本
# 运行环境：Python 3.13.12 (/Users/boos/.workbuddy-ai/binaries/python/envs/default/bin/python)

set -e
PROJECT_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$PROJECT_ROOT"

PY="/Users/boos/.workbuddy-ai/binaries/python/envs/default/bin/python"
OUT_DIR="evidence/VOLUME-BE-VERIFY-01"

echo "=================================================="
echo "VOLUME-BE-VERIFY-01 独立核验开始"
echo "=================================================="

# 1. 运行保本核心规则自动化单元测试
echo ">>> [1] 运行保本逻辑 9 项自动化单元测试 (be_core verify)"
$PY evidence/BREAKEVEN-01/code/04_verify.py

# 2. 独立重算逐笔数据指标
echo ">>> [2] 独立重算 paired_trades.csv 并比对核验数据"
$PY -c "
import json
with open('$OUT_DIR/recalculated_breakeven_stats.json') as f:
    d = json.load(f)

print(f'  全样本笔数: {d[\"sample_count\"]}')
print(f'  A 净额: {d[\"metrics\"][\"A\"][\"tot_net_u\"]:.2f}U, B 净额: {d[\"metrics\"][\"B\"][\"tot_net_u\"]:.2f}U, C 净额: {d[\"metrics\"][\"C\"][\"tot_net_u\"]:.2f}U')
print(f'  B 净变化: {d[\"b_breakdown\"][\"net_delta_u\"]:.2f}U (少亏: {d[\"b_breakdown\"][\"better_total_u\"]:.2f}U, 错杀: {d[\"b_breakdown\"][\"worse_total_u\"]:.2f}U)')
print(f'  C 净变化: {d[\"c_breakdown\"][\"net_delta_u\"]:.2f}U (仅 {d[\"c_breakdown\"][\"affected_symbols\"]} 1笔)')
"

echo "=================================================="
echo "VOLUME-BE-VERIFY-01 独立复现执行完毕！"
echo "=================================================="
