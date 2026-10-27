#!/bin/sh
# FRAME-PIT-01 一键复现（约 1 分钟）
# 用法：sh evidence/FRAME-PIT-01/reproduce.sh
set -e

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
PY=/Users/boos/.workbuddy-ai/binaries/python/envs/default/bin/python
OUT="$ROOT/evidence/FRAME-PIT-01"
TMP="$OUT/.tmp"

echo "== 0 环境 =="
echo "root = $ROOT"
"$PY" -V
mkdir -p "$OUT/logs" "$OUT/runs" "$TMP"

echo
echo "== 1 全量门禁（预期 129 passed）=="
cd "$ROOT/策略/WyckoffMaster"
"$PY" -m pytest -q -p no:cacheprovider --basetemp="$TMP" | tee "$OUT/logs/pytest_full.log"

echo
echo "== 2 真实 227 条时点回归（预期 old=226 / new=0 / flips=3）=="
cd "$ROOT"
"$PY" "$OUT/scripts/regression_227.py" | tee "$OUT/logs/regression_227.log"

echo
echo "== 3 新旧行为对照（6 标的窗口回放）=="
"$PY" "$OUT/scripts/compare_old_new.py" | tee "$OUT/logs/compare_old_new.log"

echo
echo "== 4 产物自检 =="
"$PY" - <<'PY'
import json, os, hashlib
root = os.path.join(os.environ.get("ROOT") or os.getcwd(), "evidence/FRAME-PIT-01")
root = os.path.abspath(root)
checks = [
    ("logs/pytest_full.log", "129 passed"),
    ("runs/regression_227_summary.json", '"old_rule_future_bucket": 226'),
    ("runs/regression_227_summary.json", '"new_rule_future_bucket": 0'),
    ("runs/regression_227_summary.json", '"gate_2pct_flips": 3'),
    ("runs/compare_old_new_summary.json", '"any_pit_qualified": false'),
]
ok = True
for rel, needle in checks:
    p = os.path.join(root, rel)
    txt = open(p, encoding="utf-8").read() if os.path.exists(p) else ""
    hit = needle in txt
    ok = ok and hit
    print(("PASS " if hit else "FAIL ") + rel + "  <- " + needle)
print("ALL OK" if ok else "SOMETHING FAILED")
PY

echo
echo "== 完成 =="
echo "交付目录：$OUT"
