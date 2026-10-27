#!/bin/sh
# FRAME-CLOSE-01 一键复现
#
# 作用：把本工单的全部核清动作重跑一遍。
# 边界：只读代码，不改任何策略/框架文件；不跑回测；不覆盖 VERIFY-04 旧证据。
#
# 注意：本脚本会重写 evidence/FRAME-CLOSE-01/logs/ 下的日志，
#       以及 versions.csv / versions.md / versions.json（版本清单）。
#       不会修改 evidence/FRAME-VERIFY-04/ 下的任何原有文件。
#
# 用法： sh evidence/FRAME-CLOSE-01/reproduce.sh
set -e

PY=/Users/boos/.workbuddy-ai/binaries/python/envs/default/bin/python
ROOT=/Users/boos/Desktop/威科夫因子共振趋势
OUT=$ROOT/evidence/FRAME-CLOSE-01
cd "$ROOT"

echo "=============================================================="
echo "FRAME-CLOSE-01 复现开始  $(date '+%Y-%m-%d %H:%M:%S')"
echo "=============================================================="

echo
echo "[1/6] 关键文件哈希（期望 79f60d11adc1aa63…）"
shasum -a 256 "策略/WyckoffMaster/trading_core/pit_guard.py" | tee "$OUT/logs/pit_guard_sha256.txt"

echo
echo "[2/6] 反查报告登记的错误值 cfadbf（期望：哈希命中「无」）"
echo "      注：不用 grep -r，本工程 78G 会卡死；改用限定文本面的哈希反查。"
$PY evidence/FRAME-CLOSE-01/repro/scan_hash_prefix.py cfadbf --show-text \
  | tee "$OUT/logs/errata_hash_search.txt"

echo
echo "[3/6] PIT-01 专项门禁"
( cd 策略/WyckoffMaster && $PY -m pytest event_trend_v0_1/tests/test_frame_pit_01.py \
    -q -p no:cacheprovider --basetemp=/tmp/fc01_r1 ) 2>&1 | tee "$OUT/logs/pytest_frame_pit_01.log" | tail -2

echo
echo "[4/6] PIT-02 专项门禁"
( cd 策略/WyckoffMaster && $PY -m pytest flow_oi/tests/test_frame_pit_02.py \
    -q -p no:cacheprovider --basetemp=/tmp/fc01_r2 ) 2>&1 | tee "$OUT/logs/pytest_frame_pit_02.log" | tail -2

echo
echo "[5/6] 策略包全量门禁（期望 149 passed）"
( cd 策略/WyckoffMaster && $PY -m pytest -q -p no:cacheprovider \
    --basetemp=/tmp/fc01_r3 ) 2>&1 | tee "$OUT/logs/pytest_full_strategy.log" | tail -2

echo
echo "[6/6] VERIFY-04 套件复跑（期望 25 PASS，落盘到 rerun/，不动旧证据）"
$PY evidence/FRAME-CLOSE-01/repro/rerun_verify04_suite.py 2>&1 \
  | tee "$OUT/logs/rerun_verify04.log" | tail -3

echo
echo "--- 版本清单采集 ---"
$PY evidence/FRAME-CLOSE-01/repro/collect_versions.py

echo
echo "--- 旧证据完整性（期望 8/8）---"
( cd evidence/FRAME-VERIFY-04 && $PY - <<'PY'
import hashlib, json
h = json.load(open('hashes.json'))
ok = bad = 0
for name, expect in h.items():
    cur = hashlib.sha256(open(name, 'rb').read()).hexdigest()
    if cur == expect:
        ok += 1
    else:
        print('不一致:', name); bad += 1
print(f'VERIFY-04 旧证据完整性: 一致 {ok} / 异常 {bad}（共 {len(h)} 个）')
PY
)

echo
echo "=============================================================="
echo "复现结束。请对照 05_测试日志.md 中的期望值逐项核对。"
echo "=============================================================="
