#!/bin/sh
# PR2-INTEGRATION-EXEC-01 · 跨树逐文件哈希比对（证明集成结果 = 各来源原样，未被改动）
# 跑法：sh scripts/04_跨树哈希比对.sh <输出文件>
# 只读：仅对来源树执行 shasum / git rev-parse，不写、不 checkout、不 clean。
set -u
OUT="${1:?用法: 04_跨树哈希比对.sh <输出文件>}"
INT=/private/tmp/pr2-integration-exec-01
WO01=/private/tmp/pr2-wo-01-work
WO02=/private/tmp/pr2-wo-02-work
REC=/private/tmp/pr2-recovered-harden-01
PE=/private/tmp/pr2-paper-entry-harden

chk() {
  a=$(shasum -a 256 "$1" 2>/dev/null | cut -c1-16)
  b=$(shasum -a 256 "$2" 2>/dev/null | cut -c1-16)
  if [ -z "$a" ] || [ -z "$b" ]; then r="缺文件"
  elif [ "$a" = "$b" ]; then r="一致"
  else r="不一致"; fi
  printf "  %-34s %-18s %-18s %s\n" "$3" "$a" "$b" "$r"
}

{
  echo "PR2-INTEGRATION-EXEC-01 · 跨树逐文件哈希比对"
  echo "时刻：$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
  echo
  echo "########## 一、四个来源树状态（只读） ##########"
  for t in "$WO01" "$WO02" "$REC" "$PE"; do
    echo "-- $t"
    echo "   分支: $(git -C "$t" rev-parse --abbrev-ref HEAD 2>/dev/null)"
    echo "   HEAD: $(git -C "$t" rev-parse HEAD 2>/dev/null)"
    echo "   未提交:"
    git -C "$t" -c core.quotepath=false status --porcelain 2>/dev/null | sed 's/^/     /'
  done
  echo
  echo "########## 二、逐文件哈希（sha256 前 16 位） ##########"
  printf "  %-34s %-18s %-18s %s\n" "文件" "集成树" "来源树" "判定"
  chk "$INT/UI界面/web/src/api/adapters.ts"                 "$WO01/UI界面/web/src/api/adapters.ts"                 "adapters.ts"
  chk "$INT/UI界面/web/src/components/ObservePanel.tsx"     "$WO01/UI界面/web/src/components/ObservePanel.tsx"     "ObservePanel.tsx"
  for f in README-PR2-WO-01.md display-semantics.dom.harness.tsx display-semantics.dom.test.mjs \
           display-semantics.pure.test.ts pr2-before-after-probe.mjs pr2-trigger-probe.ts; do
    chk "$INT/UI界面/web/tests/$f" "$WO01/UI界面/web/tests/$f" "tests/$f"
  done
  chk "$INT/框架/backend/unified_runtime.py"                       "$REC/框架/backend/unified_runtime.py"                       "unified_runtime.py"
  chk "$INT/框架/backend/tests/test_pr2_recovered_harden.py"       "$REC/框架/backend/tests/test_pr2_recovered_harden.py"       "test_pr2_recovered_harden.py"
  chk "$INT/框架/backend/live_api.py"                              "$PE/框架/backend/live_api.py"                               "live_api.py"
  chk "$INT/框架/backend/tests/test_pr2_paper_entry_harden.py"     "$PE/框架/backend/tests/test_pr2_paper_entry_harden.py"      "test_pr2_paper_entry_harden.py"
  chk "$INT/框架/backend/tests/test_paper_live_01.py"              "$WO02/框架/backend/tests/test_paper_live_01.py"             "test_paper_live_01.py"
  chk "$INT/框架/backend/tests/test_pr2_wo_02_fail_closed.py"      "$WO02/框架/backend/tests/test_pr2_wo_02_fail_closed.py"     "test_pr2_wo_02_fail_closed.py"
  chk "$INT/框架/backend/tests/pr2_wo_02_probe.py"                 "$WO02/框架/backend/tests/pr2_wo_02_probe.py"                "pr2_wo_02_probe.py"
  chk "$INT/evidence/PR2-RECOVERED-HARDEN-01/README.md"            "$REC/evidence/PR2-RECOVERED-HARDEN-01/README.md"            "RECOVERED README"
  echo
  echo "########## 三、结论 ##########"
  echo "  全部「一致」= 集成结果与各来源树逐字节相同，集成过程未对来源内容做任何改写。"
} > "$OUT" 2>&1
echo "已写出：$OUT"
