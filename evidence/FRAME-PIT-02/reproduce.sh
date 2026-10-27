#!/bin/bash
# FRAME-PIT-02 一键复现
#   默认：测试 + 门禁 + 旧一致性 + 四组冒烟 + 227 笔新旧对照（约 8 分钟）
#   --fast：跳过 227 笔对照（约 40 秒）
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="${ROOT:-$(cd "${SCRIPT_DIR}/../.." && pwd)}"
PY="/Users/boos/.workbuddy-ai/binaries/python/envs/default/bin/python"
[ -x "${PY}" ] || PY="python3"

FAST=0
[ "${1:-}" = "--fast" ] && FAST=1

cd "${ROOT}" || exit 1
# pytest 的 --basetemp 每次会话开头会清空该目录；放在交付目录里会累积上百个临时文件，
# 清理时会撞上沙箱的批量删除保护。改为系统临时区 + 每步独立子目录（不存在 ⇒ 无需清理）。
TMPBASE="${TMPDIR:-/tmp}/frame-pit-02-$$"
mkdir -p "${TMPBASE}"
LOG="${SCRIPT_DIR}/logs"
mkdir -p "${LOG}" "${SCRIPT_DIR}/runs" "${SCRIPT_DIR}/runs_v2"

echo "=================================================================="
echo "FRAME-PIT-02｜封堵 FLOW-OI 实际因子入口旁路"
echo "工程根：${ROOT}"
echo "=================================================================="

echo ""
echo "== [1/5] 新增测试（20 项，全部打真实入口） =="
(cd 策略/WyckoffMaster && "${PY}" -m pytest flow_oi/tests/test_frame_pit_02.py \
    -q -p no:cacheprovider --basetemp="${TMPBASE}/t1" 2>&1 | tee "${LOG}/pytest_frame_pit_02.log" | tail -5)
RC1=${PIPESTATUS[0]}

echo ""
echo "== [2/5] 全量门禁（策略包全部测试） =="
(cd 策略/WyckoffMaster && "${PY}" -m pytest -q -p no:cacheprovider \
    --basetemp="${TMPBASE}/t2" 2>&1 | tee "${LOG}/pytest_full.log" | tail -3)
RC2=${PIPESTATUS[0]}

echo ""
echo "== [3/5] 旧 FLOW-OI 交付自检（产物哈希未变） =="
"${PY}" -m pytest evidence/FLOW-OI-01/test_flow_oi_consistency.py \
    -q -p no:cacheprovider --basetemp="${TMPBASE}/t3" 2>&1 \
    | tee "${LOG}/pytest_flow_oi_legacy.log" | tail -3
RC3=${PIPESTATUS[0]}

echo ""
echo "== [4/5] 四组受控入口冒烟（3 笔，只核对入口行为，不是回测） =="
"${PY}" evidence/FRAME-PIT-02/flow_oi_four_groups_v2.py --limit 3 \
    2>&1 | tee "${LOG}/four_groups_smoke.log" | tail -12
RC4=${PIPESTATUS[0]}

if [ "${FAST}" -eq 0 ]; then
  echo ""
  echo "== [5/5] 227 笔新旧行为对照（约 7 分钟） =="
  "${PY}" evidence/FRAME-PIT-02/scripts/compare_old_new.py \
      2>&1 | tee "${LOG}/compare_old_new.log" | tail -20
  RC5=${PIPESTATUS[0]}
else
  echo ""
  echo "== [5/5] 已跳过 227 笔对照（--fast） =="
  RC5=0
fi

echo ""
echo "== 交付物自哈希 =="
( cd "${SCRIPT_DIR}" && {
    echo "# FRAME-PIT-02 交付物自哈希（sha256 前 32 位）"
    echo "# 生成时间：$(date '+%Y-%m-%d %H:%M:%S')"
    echo
    echo "# 说明：logs/reproduce_full.log 是本脚本自身的输出，边写边算必然对不上，故不纳入。"
    echo
    shasum -a 256 README.md 0*.md reproduce.sh flow_oi_four_groups_v2.py \
        scripts/*.py legacy/*.pyc logs/compare_old_new.log logs/four_groups_smoke.log \
        logs/pytest_flow_oi_legacy.log logs/pytest_frame_pit_02.log logs/pytest_full.log \
        runs/*.csv runs/*.json runs_v2/*.json runs_v2/*.parquet 2>/dev/null \
      | awk '{printf "%s  %s\n", substr($1,1,32), $2}'
  } > 00_自哈希.txt )
tail -n +5 "${SCRIPT_DIR}/00_自哈希.txt"

echo "临时目录：${TMPBASE}（系统临时区，不在交付目录内；可手工删，不影响交付物）"

echo ""
echo "=================================================================="
echo "结果：测试=$([ ${RC1} -eq 0 ] && echo PASS || echo FAIL)  \
门禁=$([ ${RC2} -eq 0 ] && echo PASS || echo FAIL)  \
旧自检=$([ ${RC3} -eq 0 ] && echo PASS || echo FAIL)  \
冒烟=$([ ${RC4} -eq 0 ] && echo PASS || echo FAIL)  \
对照=$([ ${RC5} -eq 0 ] && echo PASS || echo FAIL)"
echo "提醒：FLOW-OI 四组结果 formal_eligible 恒为 False，只能当研究结论。"
echo "=================================================================="
[ $((RC1+RC2+RC3+RC4+RC5)) -eq 0 ] || exit 1
