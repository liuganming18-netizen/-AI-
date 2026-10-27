#!/usr/bin/env bash
# ENTRY-AUDIT-06 · 一键复现（只读源数据，不重跑回测，只在 evidence/ENTRY-AUDIT-06/ 写入）
set -euo pipefail
cd "$(dirname "$0")/../.."

PY=/Users/boos/.workbuddy-ai/binaries/python/envs/default/bin/python
D=evidence/ENTRY-AUDIT-06

echo "== 1/4 逐文件哈希、循环引用、版本映射、数据不变、字段契约、文档层、来源绑定 =="
$PY $D/00_verify_sync.py | tee $D/log_01_sync.txt

echo "== 2/4 FE09 到货复核（未交付则只报待交付） =="
$PY $D/02_fe09_recheck.py | tee $D/log_02_fe09.txt

echo "== 3/4 生成统一验收表、最终状态、一致性自检 =="
$PY $D/01_emit_acceptance.py | tee $D/log_03_acceptance.txt

echo "== 4/4 本目录自哈希 =="
$PY $D/03_self_hashes.py | tee $D/log_04_self_hashes.txt

echo "完成。看 $D/00_acceptance_table.csv、08_final_status.json、报告.md"
