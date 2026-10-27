#!/bin/sh
# DATA-AUDIT-01-R1 · 一键复算
#
# 做什么：把本单 11 个取证脚本从头跑一遍，产物落到 out_repro/（**不覆盖**已交付的 out/），
#         再跑「复算对撞」把 交付证据 ↔ 复算产物 ↔ 正文数字 三方对一遍。
#
# 为什么产物要落 out_repro/：交付的 out/ 是带统计时点的冻结证据。
# 采集器一直在写热库，重跑一次行数就变；如果直接覆盖 out/，
# 正文里引用的数字（绑定 2026-10-26T03:0x）就会和产物对不上。
# 所以复算单独落目录，对撞脚本负责判定「哪些必须一致、哪些允许增长」。
#
# 只读承诺：sqlite 一律 mode=ro + query_only，duckdb 只读 parquet，
#           不写业务目录、不改业务代码、不切策略开关、不启动任何下载、不停采集。
#
# 用法：
#   sh evidence/DATA-AUDIT-01-R1/reproduce.sh
# 预计耗时：10~20 分钟（大头是 1.28 亿行 metrics 全量聚合 + 526 币逐个调读接口）

set -eu

ROOT="/Users/boos/Desktop/威科夫因子共振趋势"
PY="/Users/boos/.workbuddy-ai/binaries/python/envs/default/bin/python"
BASE="$ROOT/evidence/DATA-AUDIT-01-R1"
REPRO="$BASE/out_repro"

cd "$ROOT"

[ -x "$PY" ] || { echo "找不到托管 python：$PY"; exit 2; }
[ -d "$BASE" ] || { echo "找不到工单目录：$BASE"; exit 2; }

echo "=================================================================="
echo "DATA-AUDIT-01-R1 复算开始"
echo "  项目根      : $ROOT"
echo "  交付证据    : $BASE/out        （只读，不改）"
echo "  复算产物    : $REPRO           （本次新建/覆盖）"
echo "  开始时刻    : $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "=================================================================="

# ---------------------------------------------------------------- 0. 只读前置断言
echo
echo "[0/3] 只读前置断言"
mkdir -p "$REPRO"

# 0.1 业务代码与冻结规则：跑前跑后必须逐字节一致
CODE_LIST="$REPRO/_code_sha_before.txt"
for f in 框架/backend/unified_runtime.py 框架/backend/market_repository.py \
         策略/WyckoffMaster/event_trend_v0_1/config.py \
         策略/WyckoffMaster/event_trend_v0_1/state_machine.py \
         数据/contracts/旧标的池/scope_crypto.json; do
  if [ -f "$f" ]; then
    printf '%s  %s\n' "$(shasum -a 256 "$f" | cut -d' ' -f1)" "$f"
  fi
done > "$CODE_LIST"
echo "  业务代码/币池 指纹已记录：$(wc -l < "$CODE_LIST" | tr -d ' ') 个文件"

# 0.2 旧工单目录（原报告）必须一字不动
OLD_LIST="$REPRO/_old_evidence_sha_before.txt"
if [ -d "$ROOT/evidence/DATA-AUDIT-01" ]; then
  find "$ROOT/evidence/DATA-AUDIT-01" -type f -print0 \
    | xargs -0 shasum -a 256 | sort > "$OLD_LIST"
  echo "  原报告目录指纹已记录：$(wc -l < "$OLD_LIST" | tr -d ' ') 个文件"
else
  : > "$OLD_LIST"
  echo "  原报告目录不存在，跳过"
fi

# ---------------------------------------------------------------- 1. 跑脚本
echo
echo "[1/3] 逐个跑取证脚本（产物 → out_repro/）"
echo "  说明：92 号（生成 queries/_pool_526.sql）不在这里跑——"
echo "        它是静态名单生成器，重跑只会把交付的 queries/ 原地重写一遍，没有复算价值。"
for s in 10_资产与覆盖 20_索引一致性 30_资金费冷读 40_关闭时间三类证据 \
         50_抽样与全量 60_代码锚点 70_聪明钱基线 80_可得时间 90_事实清单 \
         91_事实清单_md; do
  printf '  -> %-24s ' "$s"
  if WX_OUT_DIR="$REPRO" WX_T0_DIR="$REPRO/forward_t0" \
     "$PY" "$BASE/scripts/$s.py" > "$REPRO/_log_$s.txt" 2>&1; then
    echo "OK"
  else
    echo "失败（日志 $REPRO/_log_$s.txt）"
    tail -20 "$REPRO/_log_$s.txt"
    exit 1
  fi
done

# ---------------------------------------------------------------- 2. 复算对撞
echo
echo "[2/3] 复算对撞（交付 ↔ 复算 ↔ 正文）"
set +e
"$PY" "$BASE/scripts/93_复算对撞.py"
CROSS=$?
set -e

# ---------------------------------------------------------------- 3. 只读后置断言
echo
echo "[3/3] 只读后置断言"
CODE_AFTER="$REPRO/_code_sha_after.txt"
for f in 框架/backend/unified_runtime.py 框架/backend/market_repository.py \
         策略/WyckoffMaster/event_trend_v0_1/config.py \
         策略/WyckoffMaster/event_trend_v0_1/state_machine.py \
         数据/contracts/旧标的池/scope_crypto.json; do
  if [ -f "$f" ]; then
    printf '%s  %s\n' "$(shasum -a 256 "$f" | cut -d' ' -f1)" "$f"
  fi
done > "$CODE_AFTER"

if diff -q "$CODE_LIST" "$CODE_AFTER" > /dev/null; then
  echo "  OK  业务代码/币池逐字节未变"
else
  echo "  !!  业务代码/币池被改动："
  diff "$CODE_LIST" "$CODE_AFTER" || true
  CROSS=1
fi

if [ -s "$OLD_LIST" ]; then
  find "$ROOT/evidence/DATA-AUDIT-01" -type f -print0 \
    | xargs -0 shasum -a 256 | sort > "$REPRO/_old_evidence_sha_after.txt"
  if diff -q "$OLD_LIST" "$REPRO/_old_evidence_sha_after.txt" > /dev/null; then
    echo "  OK  原报告目录 evidence/DATA-AUDIT-01/ 一字未动"
  else
    echo "  !!  原报告目录被改动（违反「保留原报告」）："
    diff "$OLD_LIST" "$REPRO/_old_evidence_sha_after.txt" || true
    CROSS=1
  fi
fi

# 交付的 out/ 是否被复算污染（不该被写）
if [ -d "$BASE/out" ]; then
  echo "  交付 out/ 最新修改时刻：$(ls -lt "$BASE/out" | sed -n '2p' | awk '{print $6, $7, $8}')"
  echo "  （若晚于本次开始时刻，说明有脚本误写了交付目录，要查）"
fi

echo
echo "=================================================================="
if [ "$CROSS" -eq 0 ]; then
  echo "复算结束：对撞通过"
else
  echo "复算结束：对撞**未通过**（退出码 $CROSS），明细见 $REPRO/93_crosscheck.json"
fi
echo "  结束时刻 : $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "  复算产物 : $REPRO"
echo "=================================================================="
exit "$CROSS"
