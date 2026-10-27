#!/bin/sh
# FRAME-FIT-01 一键复现（约 40 秒）。
#
# 只读源数据；只写本目录。不跑回测、不写生产登记库、不改任何生产文件。
# 重跑会先清空本目录的 registry/（保证幂等：已观察样本不会被重复累加）。
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../.." && pwd)
PY=${FRAME_FIT_PY:-/Users/boos/.workbuddy-ai/binaries/python/envs/default/bin/python}

export PYTHONDONTWRITEBYTECODE=1
cd "$ROOT" || exit 1

echo "=== 1/6 清点现有机制 ==="
"$PY" evidence/FRAME-FIT-01/00_scan.py || exit 1

echo "=== 2/6 登记 227 条已观察样本（重建 registry）==="
rm -rf evidence/FRAME-FIT-01/registry
"$PY" evidence/FRAME-FIT-01/03_register_observed.py || exit 1

echo "=== 3/6 推隔离长度与候选划分 ==="
"$PY" evidence/FRAME-FIT-01/04_split_basis.py || exit 1

echo "=== 4/6 实测正式实验阻断原因 ==="
"$PY" evidence/FRAME-FIT-01/08_probe_blockers.py || exit 1

echo "=== 5/6 测试门禁（本单 + 全量回归）==="
sh evidence/FRAME-FIT-01/05_run_tests.sh || exit 1

echo "=== 6/6 自哈希 ==="
"$PY" evidence/FRAME-FIT-01/10_self_hashes.py || exit 1

echo "全部完成"
