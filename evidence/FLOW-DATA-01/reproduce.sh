#!/bin/sh
# FLOW-DATA-01 复现脚本（只读；不写生产库、不改采集器、不跑回测）
# 用法：
#   sh evidence/FLOW-DATA-01/reproduce.sh          # 抽查三步（约 3 分钟）
#   sh evidence/FLOW-DATA-01/reproduce.sh full     # 追加全池盘点（约 8 分钟）
set -e

ROOT="/Users/boos/Desktop/威科夫因子共振趋势"
PY="/Users/boos/.workbuddy-ai/binaries/python/envs/default/bin/python"
cd "$ROOT"

echo "== 1/3 官方 zip 原件 → 独立复算 → 与 Parquet 对撞 =="
"$PY" evidence/FLOW-DATA-01/scripts/spotcheck_recompute.py

echo "== 2/3 跨源单位核对 / OI 价格效应 / 字段可用时间窗 =="
"$PY" evidence/FLOW-DATA-01/scripts/cross_checks.py

echo "== 3/3 PIT 时间审计 + 与反重力特征逐值比对 =="
"$PY" evidence/FLOW-DATA-01/scripts/pit_and_feature_diff.py

if [ "$1" = "full" ]; then
  echo "== 4/4 全池字段覆盖盘点（慢） =="
  "$PY" evidence/FLOW-DATA-01/scripts/inventory.py all
fi

echo "完成。产出在 evidence/FLOW-DATA-01/runs/"
