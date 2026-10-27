#!/usr/bin/env bash
# ENTRY-AUDIT-03 一键复现
#
# 全程只读：不修改正式策略、共享撮合、UI、采集器、生产库、原账本。
# 唯一写入目录：evidence/ENTRY-AUDIT-03/
#
# 预计耗时（本机实测）：冻结约 45 秒；全量重放 176 个 run 约 523 秒；
#           其余步骤各 1 分钟内。总计约 10 分钟。
# 注：02b 不删除已有 run 目录，只覆盖写（批量删除既危险也会被安全策略拦）。
set -euo pipefail

ROOT="/Users/boos/Desktop/威科夫因子共振趋势"
PY="/Users/boos/.workbuddy-ai/binaries/python/envs/default/bin/python"
cd "$ROOT"
OUT="evidence/ENTRY-AUDIT-03"

echo "=== [0/8] 冻结基线：策略代码 / 配置 / 合约规格 / 数据 / 时间窗口（4139 个文件）==="
"$PY" "$OUT/00_freeze_baseline.py" 2>&1 | tee "$OUT/log_00_freeze.txt"

echo "=== [1/8] 全量身份级重放：133 连续 + 43 窗口 = 176 个 run（本机实测 523 秒）==="
rm -f "$OUT/02_progress.jsonl"
WORKERS=6 "$PY" "$OUT/02_replay_full.py" 2>&1 | tee "$OUT/log_02_replay.txt"

echo "=== [2/8] 物化独立 run 目录（不写原账本）==="
"$PY" "$OUT/02b_materialize_runs.py" 2>&1 | tee "$OUT/log_02b_runs.txt"

echo "=== [3/8] 四个源头查明：费用为 0 / 42 条不匹配 / COTI 冲突 / 超时标签 ==="
"$PY" "$OUT/01_source_diff.py" 2>&1 | tee "$OUT/log_01_source.txt"

echo "=== [4/8] 全量身份映射与三态判定 ==="
"$PY" "$OUT/03_identity_map.py" 2>&1 | tee "$OUT/log_03_identity.txt"

echo "=== [5/8] 费用逐笔对账（含原始账本事件探针）==="
"$PY" "$OUT/04_cost_reconcile.py" 2>&1 | tee "$OUT/log_04_cost.txt"

echo "=== [6/8] 新旧样本桥接 ==="
"$PY" "$OUT/05_bridge.py" 2>&1 | tee "$OUT/log_05_bridge.txt"

echo "=== [7/8] 收尾自检：输入零改动 ==="
"$PY" "$OUT/99_verify_unchanged.py" 2>&1 | tee "$OUT/log_99_verify.txt"

echo
echo "全部完成。报告：$OUT/报告.md"
