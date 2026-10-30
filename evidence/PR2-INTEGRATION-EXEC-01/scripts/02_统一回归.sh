#!/bin/sh
# PR2-INTEGRATION-EXEC-01 · 统一回归（前端 5 项 + 后端 6 套件）
#
# 跑法：
#   sh scripts/02_统一回归.sh <集成工作树根> <输出文件>
#
# 说明：
#   · 前端 `node_modules` 是指向主仓库的符号链接（被 .gitignore 排除），
#     没有它跑不了 tsx / tsc / vite；脚本会先检查。
#   · 后端 `CONTRACT_SPEC_CSV` 只在裸工作树里需要（`数据/` 被 .gitignore 排除）。
#   · 所有测试自带 tempfile 沙盒，不碰生产库、不启服务。
set -u
ROOT="${1:?用法: 02_统一回归.sh <集成工作树根> <输出文件>}"
OUT="${2:?用法: 02_统一回归.sh <集成工作树根> <输出文件>}"
WEB="$ROOT/UI界面/web"
BACKEND="$ROOT/框架/backend"
CONTRACT_SPEC_CSV="${CONTRACT_SPEC_CSV:-/Users/boos/Desktop/威科夫因子共振趋势/数据/研究输入/外部迁移_2026-10-16/本次离线决策_2026-10-12/07_526币保守执行近似规格.csv}"
export CONTRACT_SPEC_CSV

: > "$OUT"
{
  echo "===== 环境 ====="
  echo "集成树     : $ROOT"
  echo "分支/HEAD  : $(git -C "$ROOT" rev-parse --abbrev-ref HEAD) / $(git -C "$ROOT" rev-parse --short HEAD)"
  echo "python     : $(/usr/bin/python3 -V 2>&1)"
  echo "node       : $(node -v 2>&1)"
  echo "合约规格   : $CONTRACT_SPEC_CSV"
  echo "时刻(本机) : $(date '+%Y-%m-%d %H:%M:%S %Z')"
  echo
} >> "$OUT"

if [ ! -e "$WEB/node_modules" ]; then
  echo "[致命] $WEB/node_modules 不存在（符号链接缺失），前端 5 项无法执行" >> "$OUT"
  echo "[致命] node_modules 缺失"
  exit 2
fi

echo "########## 前端 ##########" >> "$OUT"
cd "$WEB" || exit 1

echo "===== 前端-1 纯逻辑（18 条）=====" >> "$OUT"
./node_modules/.bin/tsx tests/display-semantics.pure.test.ts >> "$OUT" 2>&1
echo "退出码: $?" >> "$OUT"; echo >> "$OUT"

echo "===== 前端-2 DOM 渲染（12 条）=====" >> "$OUT"
node tests/display-semantics.dom.test.mjs >> "$OUT" 2>&1
echo "退出码: $?" >> "$OUT"; echo >> "$OUT"

echo "===== 前端-3 TypeScript（tsc --noEmit）=====" >> "$OUT"
./node_modules/.bin/tsc --noEmit >> "$OUT" 2>&1
echo "退出码: $?" >> "$OUT"; echo >> "$OUT"

echo "===== 前端-4 Lint（仓库 lint 脚本 = tsc --noEmit，无 ESLint 依赖）=====" >> "$OUT"
./node_modules/.bin/tsc --noEmit -p tsconfig.json >> "$OUT" 2>&1
echo "退出码: $?" >> "$OUT"; echo >> "$OUT"

echo "===== 前端-5 构建（vite build）=====" >> "$OUT"
VITE_CACHE_DIR=/tmp/pr2-int-vite-cache ./node_modules/.bin/vite build >> "$OUT" 2>&1
echo "退出码: $?" >> "$OUT"; echo >> "$OUT"

echo "########## 后端（88 项）##########" >> "$OUT"
cd "$BACKEND" || exit 1
for m in \
  tests.test_pr2_wo_02_fail_closed \
  tests.test_pr2_recovered_harden \
  tests.test_pr2_paper_entry_harden \
  tests.test_lifecycle_isolation \
  tests.test_live_pool_admission \
  tests.test_paper_live_01
do
  echo "===== 后端 $m =====" >> "$OUT"
  /usr/bin/python3 -m unittest "$m" >> "$OUT" 2>&1
  echo "退出码: $?" >> "$OUT"; echo >> "$OUT"
done

echo "===== 汇总 ====="
grep -E "^===== |^Ran |^OK$|^FAILED|^退出码|通过$" "$OUT" | grep -vE "^退出码: 0$" 
echo "---- 全量明细见 $OUT ----"
