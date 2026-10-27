#!/usr/bin/env bash
# ENTRY-AUDIT-02 一键复现
# 全程只读：不修改任何上游产物、策略、撮合、界面、接口或账本。
# 唯一写入目录：evidence/ENTRY-AUDIT-02/
set -euo pipefail

ROOT="/Users/boos/Desktop/威科夫因子共振趋势"
PY="/Users/boos/.workbuddy-ai/binaries/python/envs/default/bin/python"
cd "$ROOT"
OUT="evidence/ENTRY-AUDIT-02"

echo "=== [0/7] 冻结输入指纹（2673 个文件，含行情历史切片指纹）==="
"$PY" "$OUT/00_freeze_manifest.py" | tee "$OUT/log_00_freeze.txt"

echo "=== [1/7] 身份映射与计数闭合 ==="
"$PY" "$OUT/01_identity_and_counts.py" | tee "$OUT/log_01_counts.txt"

echo "=== [2/7] 22 条未成交：重放 21 个标的取引擎原始拒单日志（约 3 分钟）==="
rm -f "$OUT/02_progress.jsonl"
"$PY" "$OUT/02_unfilled_replay.py" | tee "$OUT/log_02_replay.txt"

echo "=== [3/7] ATR 独立复算（205 笔，约 2 分钟）==="
"$PY" "$OUT/03_atr_independent.py" | tee "$OUT/log_03_atr.txt"

echo "=== [4/7] FACTOR-ENTRY-05 内部一致性 ==="
"$PY" "$OUT/04_fe05_consistency.py" | tee "$OUT/log_04_fe05.txt"

echo "=== [5/7] 交叉校验（候选时间窗歧义 / 取整入场价）==="
"$PY" "$OUT/05_cross_checks.py" | tee "$OUT/log_05_cross.txt"

echo "=== [6/7] 收尾自检：输入零改动 ==="
"$PY" "$OUT/99_verify_unchanged.py" | tee "$OUT/log_99_verify.txt"

echo
echo "全部完成。报告：$OUT/报告.md"
