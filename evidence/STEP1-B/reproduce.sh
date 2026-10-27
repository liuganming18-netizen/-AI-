#!/bin/sh
# STEP1-B 一键复现（只读清点，不跑回测、不写上游、不联网）
#
# 用法：bash evidence/STEP1-B/reproduce.sh
# 退出码：0 = 三步全过；非 0 = 有失败
set -e

HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../.." && pwd)
PY=/Users/boos/.workbuddy-ai/binaries/python/envs/default/bin/python

if [ ! -x "$PY" ]; then
  echo "⚠ 解释器不可用：$PY" >&2
  exit 1
fi

export PYTHONDONTWRITEBYTECODE=1
cd "$ROOT"

echo "== STEP1-B / 1 只读扫描 =="
"$PY" evidence/STEP1-B/00_scan.py

echo "== STEP1-B / 2 生成验收矩阵与缺口清单 =="
"$PY" evidence/STEP1-B/01_emit_matrix.py

echo "== STEP1-B / 3 本目录自哈希 =="
"$PY" evidence/STEP1-B/02_self_hashes.py

echo "== STEP1-B 完成 =="
echo "主交付：evidence/STEP1-B/00_验收矩阵.csv"
echo "入口：  evidence/STEP1-B/README.md"
