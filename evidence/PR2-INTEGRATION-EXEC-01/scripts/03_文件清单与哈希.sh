#!/bin/sh
# PR2-INTEGRATION-EXEC-01 · 文件级集成清单与哈希
# 跑法：sh scripts/03_文件清单与哈希.sh <集成工作树根> <输出文件>
# 说明：全程 -c core.quotepath=false，否则中文路径会被转义成八进制、后续读文件必失败。
set -u
ROOT="${1:?用法: 03_文件清单与哈希.sh <集成工作树根> <输出文件>}"
OUT="${2:?用法: 03_文件清单与哈希.sh <集成工作树根> <输出文件>}"
cd "$ROOT" || exit 1
G="git -c core.quotepath=false"

{
  echo "PR2-INTEGRATION-EXEC-01 · 文件级集成清单与哈希"
  echo "生成时刻：$(date -u '+%Y-%m-%dT%H:%M:%SZ')（本机 $(date '+%H:%M:%S %Z')）"
  echo "集成树：$ROOT"
  echo "分支/HEAD：$(git rev-parse --abbrev-ref HEAD) / $(git rev-parse HEAD)"
  echo
  echo "########## 一、已跟踪文件（相对基线 9c8929d5 有改动） ##########"
  $G diff --numstat | while IFS="	" read -r add del path; do
    printf "  %-52s +%-5s -%-5s blob:%s\n" "$path" "$add" "$del" "$(git hash-object "$path" | cut -c1-16)"
  done
  echo
  echo "（末列 = git hash-object 前 16 位，即 git blob 哈希，用于跨树逐位比对；与 sha256 不同）"
  echo
  echo "########## 二、新增未跟踪文件（node_modules 是符号链接，不计入） ##########"
  $G status --porcelain | sed -n 's/^?? //p' | while read -r p; do
    case "$p" in
      */node_modules) continue ;;
    esac
    if [ -d "$p" ]; then
      find "$p" -type f | sort | while read -r f; do
        printf "  %-66s sha256:%s\n" "$f" "$(shasum -a 256 "$f" | cut -c1-16)"
      done
    else
      printf "  %-66s sha256:%s\n" "$p" "$(shasum -a 256 "$p" | cut -c1-16)"
    fi
  done
  echo
  echo "########## 三、四个生产文件 sha256（完整 64 位） ##########"
  shasum -a 256 "UI界面/web/src/api/adapters.ts" \
                "UI界面/web/src/components/ObservePanel.tsx" \
                "框架/backend/live_api.py" \
                "框架/backend/unified_runtime.py"
  echo
  echo "########## 四、禁改项核对（下面两段之间应无任何行） ##########"
  echo "-- BEGIN git status --porcelain -- <禁改路径> --"
  $G status --porcelain -- 框架/backend/paper_engine.py 框架/backend/live_store.py \
      框架/backend/live_signal_engine.py 框架/backend/research_signal_import.py \
      框架/backend/strategy_contracts.py 策略/ project_state.json 数据/ 框架/backend/locks
  echo "-- END（无行 = 全部未改）--"
} > "$OUT" 2>&1
echo "已写出：$OUT"
