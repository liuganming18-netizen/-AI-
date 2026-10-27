#!/bin/sh
# ACCUM-STRAT-01 一键复现
# 依赖：/Users/boos/.workbuddy-ai/binaries/python/envs/default/bin/python
# 只读 数据/研究输入/WyckoffMaster/ohlcv/{1h,15m}，只写本目录 cache/ out/ logs/
set -e
cd "$(dirname "$0")/../.."
PY=/Users/boos/.workbuddy-ai/binaries/python/envs/default/bin/python
EV=evidence/ACCUM-STRAT-01

echo "[0] 1h -> 日线聚合（526 个标的）"
$PY $EV/00_build_daily.py

echo "[1] 口径复原探针（对照 196/186/34/61）"
$PY $EV/02b_definition_probe.py

echo "[2] 生成信号样本 S0（字面）/ S1（仅振幅）"
$PY $EV/03_signals.py

echo "[3] A/B 逐笔模拟（次日开盘 / 信号日收盘两套入场）"
$PY $EV/04_ab_engine.py

echo "[4] 分组统计、集中度、隔夜价差"
$PY $EV/05_analysis.py

echo "[5] 配对表 + 汇总 JSON"
$PY $EV/06_pairs.py

echo "[6] 成本列 + 与原四组形态对照 + 稳定币敏感性"
$PY $EV/07_report_tables.py

echo "完成。结果在 $EV/out/ 与 $EV/logs/"
