#!/bin/sh
# STEP1-A 复现脚本（只读）
# 用法：cd /Users/boos/Desktop/威科夫因子共振趋势 && sh evidence/STEP1-A/reproduce.sh
#
# 只读 CSV 与 run 目录里的 inputs.json / summary.json；
# 不写任何 run 目录，不运行回测，不改策略/撮合/UI/账本。
set -e

ROOT="/Users/boos/Desktop/威科夫因子共振趋势"
PY="/Users/boos/.workbuddy-ai/binaries/python/envs/default/bin/python"
OUT="$ROOT/evidence/STEP1-A/runs"

cd "$ROOT"
echo "== 工程根 =="
pwd
echo

echo "== 解释器 =="
"$PY" -V
echo

echo "== 1/4 逐 run 盘点（约 20 秒）=="
"$PY" evidence/STEP1-A/scripts/inventory_objects.py
echo

echo "== 2/4 代码代际与当前工作树比对 =="
"$PY" evidence/STEP1-A/scripts/code_generation_diff.py
echo

echo "== 3/4 混装池拆解 =="
"$PY" evidence/STEP1-A/scripts/split_runs_pool.py
echo

echo "== 4/4 关键不变量核对 =="
"$PY" - <<'PY'
import csv, os, json
ROOT = "/Users/boos/Desktop/威科夫因子共振趋势"
rows = list(csv.DictReader(open(os.path.join(ROOT, "evidence/STEP1-A/runs/step1a_runs.csv"), encoding="utf-8")))
with_inp = [r for r in rows if r["has_inputs"] == "True"]
print("run 目录总数            :", len(rows))
print("带 inputs.json 的 run   :", len(with_inp))
print("不同代码代际            :", len({r["code_combined_sha256"] for r in with_inp if r["code_combined_sha256"]}))
print("不同配置指纹            :", len({r["config_sha256"] for r in with_inp if r["config_sha256"]}))
print("不同规则指纹            :", len({r["rules_sha256"] for r in with_inp if r["rules_sha256"]}))
print("formal_pass_allowed=真  :", sum(1 for r in with_inp if r["formal_pass_allowed"] == "True"))
print("初始资金取值集合        :", sorted({r["initial_equity"] for r in with_inp if r["initial_equity"]}))
print("规格 applicability 分布 :", {k: sum(1 for r in with_inp if r["spec_applicability"] == k)
                                for k in sorted({r["spec_applicability"] for r in with_inp})})
print("成交笔数合计            :", sum(int(float(r["trade_count"] or 0)) for r in with_inp))
PY

echo
echo "== 完成：产物在 evidence/STEP1-A/runs/ =="
ls -1 "$OUT"
