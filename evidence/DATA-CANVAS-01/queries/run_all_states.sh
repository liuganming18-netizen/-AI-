#!/bin/bash
# DATA-CANVAS-01：四态一次跑完（同一版页面 + 同一版脚本 ⇒ 可横向比较）
# 用法：run_all_states.sh
set -u
EV="/Users/boos/Desktop/威科夫因子共振趋势/evidence/DATA-CANVAS-01"
LOG="$EV/out/run_all.log"
: > "$LOG"

# 常驻桩：给隔离库一个"确实存在"的 pid（页面只展示不判活，但留痕要真）
sleep 3600 &
KEEPER=$!
echo "keeper pid=$KEEPER" | tee -a "$LOG"
echo "$KEEPER" > "$EV/out/keeper.pid"

run() {
  local st="$1" port="$2" fault="${3:-}"
  echo "############ $st :$port fault=${fault:-none} $(date -u +%Y-%m-%dT%H:%M:%SZ)" | tee -a "$LOG"
  bash "$EV/queries/run_state.sh" "$st" "$port" "$KEEPER" "$fault" 2>&1 | tee -a "$LOG"
  echo "############ $st exit=$? $(date -u +%Y-%m-%dT%H:%M:%SZ)" | tee -a "$LOG"
  sleep 2
}

run normal    8901
run stopped   8902
run missing   8903
run api_fail  8904 "/api/v1/paper/accounts|500"

kill "$KEEPER" 2>/dev/null
echo "ALL DONE $(date -u +%Y-%m-%dT%H:%M:%SZ)" | tee -a "$LOG"
