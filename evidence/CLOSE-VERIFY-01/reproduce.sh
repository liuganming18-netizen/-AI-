#!/usr/bin/env bash
# CLOSE-VERIFY-01 独立核验一键复现脚本
# 运行环境：Python 3.13.12 (/Users/boos/.workbuddy-ai/binaries/python/envs/default/bin/python)
# 作用：独立验证框架版本收口（Part A）与策略增量（Part B）

set -e
PROJECT_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$PROJECT_ROOT"

PY="/Users/boos/.workbuddy-ai/binaries/python/envs/default/bin/python"
OUT_DIR="evidence/CLOSE-VERIFY-01"

echo "=================================================="
echo "CLOSE-VERIFY-01 独立核验开始"
echo "=================================================="

# A. FRAME-CLOSE-01 验证
echo ">>> [A.1] 核实关键文件物理哈希与无变化"
$PY -c "
import hashlib, json, os

with open('$OUT_DIR/pre_test_key_hashes.json') as f:
    key_hashes = json.load(f)

for p, meta in key_hashes.items():
    with open(p, 'rb') as f:
        curr = hashlib.sha256(f.read()).hexdigest()
    assert curr == meta['sha256'], f'Hash mismatch on {p}'
print('  关键 8 文件 SHA-256 校验通过！')
"

echo ">>> [A.2] 运行受影响受控测试套件 (78 项 pytest)"
$PY -m pytest 策略/WyckoffMaster/event_trend_v0_1/tests/test_frame_pit_01.py \
              策略/WyckoffMaster/flow_oi/tests/test_frame_pit_02.py \
              框架/backend/tests/test_frame_fit_02.py -q

echo ">>> [A.3] 运行 FRAME-VERIFY-04 独立验证套件 (25 项综合)"
$PY $OUT_DIR/run_verify04_suite.py > /dev/null
echo "  FRAME-VERIFY-04 25 项全量通过！"

# B. ACCUM-STRAT-01 验证
echo ">>> [B.1] 独立重算 ACCUM-STRAT A/B 统计与配对差异"
$PY -c "
import pandas as pd, numpy as np

df = pd.read_csv('evidence/ACCUM-STRAT-01/out/04_trades_S1_open.csv')
ok = df[df['status'] == 'OK'].copy()
delta_b1 = ok['B1_net'] - ok['A_net']
delta_b2 = ok['B2_net'] - ok['A_net']

print(f'  S1 总交易: {len(ok)} 笔')
print(f'  B1-A 净改善/笔: {delta_b1.mean():+.4%}, 少亏: {delta_b1[delta_b1>1e-9].sum():+.4f}, 错杀: {delta_b1[delta_b1<-1e-9].sum():+.4f}')
print(f'  B2-A 净改善/笔: {delta_b2.mean():+.4%}, 少亏: {delta_b2[delta_b2>1e-9].sum():+.4f}, 错杀: {delta_b2[delta_b2<-1e-9].sum():+.4f}')
"

echo "=================================================="
echo "CLOSE-VERIFY-01 全部独立复现通过！"
echo "=================================================="
