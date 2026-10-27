#!/bin/sh
# FRAME-FIT-01 测试门禁：本单反例测试 + 全量后端回归。
# 只跑测试，不跑回测、不写生产登记库（测试内部用 tempdir 隔离）。
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../.." && pwd)
BACKEND="$ROOT/框架/backend"
LOG="$HERE/日志"
PY=${FRAME_FIT_PY:-/Users/boos/.workbuddy-ai/binaries/python/envs/default/bin/python}

mkdir -p "$LOG"
cd "$BACKEND" || exit 1
export PYTHONDONTWRITEBYTECODE=1

echo "=== 1/2 本单反例测试（test_frame_fit_01）==="
"$PY" -m unittest tests.test_frame_fit_01 -v > "$LOG/01_本单测试.log" 2>&1
RC1=$?
tail -6 "$LOG/01_本单测试.log"
echo "exit=$RC1"

echo "=== 2/2 全量后端回归 ==="
"$PY" -m unittest discover -s tests -p "test_*.py" > "$LOG/02_全量回归.log" 2>&1
RC2=$?
grep -E "^(Ran|OK|FAILED)" "$LOG/02_全量回归.log" | tail -3
echo "exit=$RC2"

if [ "$RC1" -eq 0 ] && [ "$RC2" -eq 0 ]; then
  echo "门禁通过"
  exit 0
fi
echo "门禁未通过（本单=$RC1 全量=$RC2）"
exit 1
