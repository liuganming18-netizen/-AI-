#!/bin/bash
# FRAME-FIT-02 一键复现
#   默认：新增测试 + 后端全量 + 基线 + 前后对照探针 + 快照一致性 + 自哈希（约 2 分钟）
#   --fast：只跑新增 32 项 + 探针 + 快照一致性（约 25 秒）
#   退出码：0 全绿；1 测试或探针失败；2 快照与工作区不一致（交付后被改过）
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="${ROOT:-$(cd "${SCRIPT_DIR}/../.." && pwd)}"
PY="/Users/boos/.workbuddy-ai/binaries/python/envs/default/bin/python"
[ -x "${PY}" ] || PY="python3"

FAST=0
[ "${1:-}" = "--fast" ] && FAST=1

cd "${ROOT}" || exit 1
# pytest 每次会话开头会清空 --basetemp；共用一个目录会撞上沙箱批量删除保护。
# 每步给一个不存在的独立目录（系统临时区，不进交付目录）。
TMPBASE="${TMPDIR:-/tmp}/frame-fit-02-$$"
mkdir -p "${TMPBASE}"
# ⚠ 任务队列默认落在生产库 `数据/任务队列/jobs.sqlite3`；跑测试时指到临时区，
#   否则 `create_run` 会往生产队列投递「回测」任务（工单边界：不写生产数据）。
export JOBS_DB="${TMPBASE}/jobs.sqlite3"
export INPROCESS_JOBS=1
# 留出段守卫读 `data/experiments/holdout.json`。复现要求确定性 ——
# 不能让「本机当前有没有配留出段」决定结果，故指向一个不存在的临时文件（只读行为，不改任何文件）。
export FRAME_HOLDOUT_FILE="${TMPBASE}/holdout.json"
LOG="${SCRIPT_DIR}/logs"
mkdir -p "${LOG}" "${SCRIPT_DIR}/runs"

echo "=================================================================="
echo "FRAME-FIT-02｜修复首次正式实验启动阻断"
echo "工程根：${ROOT}"
echo "=================================================================="

echo ""
echo "== [1/6] 新增测试（32 项，全部打真实入口） =="
( cd "${ROOT}/框架/backend" && "${PY}" -m pytest tests/test_frame_fit_02.py \
    -q -p no:cacheprovider --basetemp="${TMPBASE}/t1" 2>&1 \
    | tee "${LOG}/pytest_frame_fit_02.log" | tail -3 )
RC1=${PIPESTATUS[0]}

if [ "${FAST}" -eq 0 ]; then
  echo ""
  echo "== [2/6] 后端全量测试 =="
  ( cd "${ROOT}/框架/backend" && "${PY}" -m pytest tests \
      -q -p no:cacheprovider --basetemp="${TMPBASE}/t2" 2>&1 \
      | tee "${LOG}/pytest_backend_full.log" | tail -3 )
  RC2=${PIPESTATUS[0]}

  echo ""
  echo "== [3/6] 基线（排除本单新增，用于证明只增不减） =="
  ( cd "${ROOT}/框架/backend" && "${PY}" -m pytest tests \
      -q -p no:cacheprovider --basetemp="${TMPBASE}/t3" \
      --ignore=tests/test_frame_fit_02.py 2>&1 \
      | tee "${LOG}/pytest_backend_baseline.log" | tail -3 )
  RC3=${PIPESTATUS[0]}
else
  echo ""
  echo "== [2/6] 已跳过全量测试（--fast） =="
  echo "== [3/6] 已跳过基线测试（--fast） =="
  RC2=0
  RC3=0
fi

echo ""
echo "== [4/6] 修复前后对照探针（首个正式实验，零历史记录） =="
"${PY}" "${SCRIPT_DIR}/scripts/probe_launch_before_after.py" \
    2>&1 | tee "${LOG}/probe_launch_before_after.log" | tail -10
RC4=${PIPESTATUS[0]}

echo ""
echo "== [5/6] 快照一致性 + 生产登记库只读拍照 =="
# 快照是独立复验的比对基准。工作区文件若被并发进程继续改动，这里会报出差异 ——
# 那是「文件变了」，不是「本单错了」；以 snapshot/ 为准，不以行号为准。
RC5=0
for pair in \
    "experiment_registry.py:${ROOT}/框架/backend/experiment_registry.py" \
    "backtest_runner.py:${ROOT}/框架/backend/backtest_runner.py" \
    "backtest_api.py:${ROOT}/框架/backend/backtest_api.py" \
    "test_frame_fit_02.py:${ROOT}/框架/backend/tests/test_frame_fit_02.py" ; do
  snap="${SCRIPT_DIR}/snapshot/${pair%%:*}"
  live="${pair#*:}"
  if [ ! -f "${snap}" ] || [ ! -f "${live}" ]; then
    echo "  [SKIP] ${pair%%:*} 缺失"; continue
  fi
  a=$(shasum -a 256 "${snap}" | cut -c1-16)
  b=$(shasum -a 256 "${live}" | cut -c1-16)
  if [ "${a}" = "${b}" ]; then
    echo "  [OK]   ${pair%%:*}  ${a}"
  else
    echo "  [DIFF] ${pair%%:*}  快照=${a} 工作区=${b}  ← 交付后被改过"
    RC5=1
  fi
done
# 生产登记库只读拍照：本脚本不写生产，只导出当前状态供复验核对。
"${PY}" "${SCRIPT_DIR}/scripts/dump_production_registry_state.py" >/dev/null 2>&1 \
  && echo "  [OK]   生产登记库状态已导出（logs/production_registry_state.txt）" \
  || echo "  [WARN] 生产登记库导出失败（不影响测试结论）"

echo ""
echo "== [6/6] 交付物自哈希 =="
( cd "${SCRIPT_DIR}" && {
    echo "# FRAME-FIT-02 交付物自哈希（sha256 前 32 位）"
    echo "# 生成时间：$(date '+%Y-%m-%d %H:%M:%S')"
    echo "# 说明：logs/reproduce_full.log 是本脚本自身的输出，边写边算必然对不上，故不纳入。"
    echo "# 说明：logs/production_registry_state.txt 首行含导出时间戳，每次重跑该行哈希会变，属正常。"
    echo "#      其余条目应逐字节稳定；不稳定即说明源文件被改过。"
    echo
    shasum -a 256 README.md 0*.md reproduce.sh scripts/*.py snapshot/* \
        logs/pytest_frame_fit_02.log logs/pytest_backend_full.log \
        logs/pytest_backend_baseline.log logs/probe_launch_before_after.log \
        logs/production_registry_state.txt \
        runs/*.json 2>/dev/null \
      | awk '{printf "%s  %s\n", substr($1,1,32), $2}'
  } > 00_自哈希.txt )
tail -n +5 "${SCRIPT_DIR}/00_自哈希.txt"

echo "临时目录：${TMPBASE}（系统临时区，不在交付目录内）"

echo ""
echo "=================================================================="
echo "结果：新增测试=$([ ${RC1} -eq 0 ] && echo PASS || echo FAIL)  \
全量=$([ ${RC2} -eq 0 ] && echo PASS || echo FAIL)  \
基线=$([ ${RC3} -eq 0 ] && echo PASS || echo FAIL)  \
探针=$([ ${RC4} -eq 0 ] && echo PASS || echo FAIL)  \
快照=$([ ${RC5} -eq 0 ] && echo PASS || echo 'DIFF(见上)')"
echo "提醒：启动通过 ≠ 可交付；四组/正式结论仍未通过，等反重力独立复验。"
echo "=================================================================="
[ $((RC1+RC2+RC3+RC4)) -eq 0 ] || exit 1
[ ${RC5} -eq 0 ] || exit 2
