#!/bin/sh
# -*- coding: utf-8 -*-
# ─────────────────────────────────────────────────────────────────────────────
# DATA-AUDIT-01 · 一键复现
#
# 做什么：按顺序跑 scripts/ 下三个只读脚本，重算 out/ 下全部数值产物。
# 不做什么：不改业务代码、不写业务库、不下载、不回测、不覆盖历史产物。
#
# 用法：
#   cd "/Users/boos/Desktop/威科夫因子共振趋势"
#   sh evidence/DATA-AUDIT-01/reproduce.sh
#
# 耗时：约 2–3 分钟（只读 parquet footer + sqlite mode=ro 查询，不扫全表）
# ─────────────────────────────────────────────────────────────────────────────
set -eu

HERE=$(cd "$(dirname "$0")" && pwd -P)
ROOT=$(cd "$HERE/../.." && pwd -P)
SCRIPTS="$HERE/scripts"
OUT="$HERE/out"

echo "════════════════════════════════════════════════════════════════"
echo " DATA-AUDIT-01 复现"
echo " 工程根：$ROOT"
echo " 时刻  ：$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
echo "════════════════════════════════════════════════════════════════"

# ── 解释器：优先工程实际使用的 venv，其次受管 3.13，最后系统 python3 ──────────
PY=""
for cand in \
  "/Users/boos/.workbuddy-ai/binaries/python/envs/default/bin/python" \
  "/Users/boos/.workbuddy-ai/binaries/python/versions/3.13.12/bin/python3" \
  "$(command -v python3 2>/dev/null || true)"
do
  [ -n "$cand" ] || continue
  [ -x "$cand" ] || continue
  if "$cand" -c "import pyarrow" >/dev/null 2>&1; then PY="$cand"; break; fi
done
if [ -z "$PY" ]; then
  echo "✗ 找不到带 pyarrow 的 python。请先装 pyarrow（只读脚本需要读 parquet footer）。" >&2
  exit 1
fi
echo " 解释器：$PY"
"$PY" -c "import sys,pyarrow;print('         ', sys.version.split()[0], 'pyarrow', pyarrow.__version__)"

# ── 前置检查：只读纪律 + 外部路径变量 ────────────────────────────────────────
if [ ! -f "$ROOT/数据/市场数据/db/market_v2.sqlite3" ]; then
  echo "✗ 热库不存在：$ROOT/数据/市场数据/db/market_v2.sqlite3" >&2
  exit 1
fi
if [ ! -d "$ROOT/数据/市场数据/parquet" ]; then
  echo "✗ 冷档目录不存在：$ROOT/数据/市场数据/parquet" >&2
  exit 1
fi
echo " 外部路径变量（核对时全未设置则与工程内路径一致）："
for v in MARKET_DB MARKET_ARCHIVE PAPER_LEDGER_DIR RESEARCH_INPUT_DIR EFFECTIVE_VIEW POOL_SCOPE_JSON; do
  eval "val=\${$v:-}"
  if [ -n "${val:-}" ]; then echo "   $v = $val"; else echo "   $v = （未设置）"; fi
done

mkdir -p "$OUT"

# ── 逐个跑 ──────────────────────────────────────────────────────────────────
run_one() {
  name="$1"; desc="$2"
  echo ""
  echo "── $name · $desc"
  t0=$(date +%s)
  if ! "$PY" "$SCRIPTS/$name"; then
    echo "✗ $name 失败（退出码非 0）" >&2
    exit 1
  fi
  t1=$(date +%s)
  echo "   ✓ 用时 $((t1 - t0))s"
}

run_one "01_资产盘点.py"   "热库 26 表 + 冷档 6 类 + 逐币覆盖 + 币池核对"
run_one "02_可用区间.py"   "526 币逐币可用区间 + 9 种组合统计"
run_one "03_异常与取证.py" "异常抽样 + OI 开关取证 + 受影响回测 + 当前运行路径"

# ── 产物清点 ────────────────────────────────────────────────────────────────
echo ""
echo "── 产物清点：${OUT}"
missing=0
for f in \
  01_热库表与字段.csv 02_热库数据表时间跨度.csv 02b_四时间戳填充.csv \
  03_冷档数据类型.csv 04_冷档目录一致性_汇总.csv 04b_冷档目录一致性_明细.csv \
  05_逐币逐周期覆盖.csv 06_逐币OI覆盖.csv 07_逐币资金费覆盖.csv 08_逐币指标覆盖.csv \
  09_币池核对.json 09_汇总.json \
  10_逐币可用区间.csv 11_组合区间统计.csv 12_区间口径说明.csv \
  13_异常抽样.csv 14_OI开关取证.csv 15_受影响回测.csv 16_当前运行路径.json
do
  if [ -s "$OUT/$f" ]; then
    printf "   ✓ %-34s %8s 字节\n" "$f" "$(wc -c < "$OUT/$f" | tr -d ' ')"
  else
    printf "   ✗ %-34s 缺失或为空\n" "$f"
    missing=$((missing + 1))
  fi
done

echo ""
if [ "$missing" -ne 0 ]; then
  echo "✗ 有 $missing 个产物缺失/为空，复现不完整。" >&2
  exit 1
fi

# ── 关键数字回显（与 README 的"六个必须知道的事实"对齐） ─────────────────────
echo "── 关键数字"
"$PY" - "$OUT" <<'PYEOF'
import json, sys
from pathlib import Path
out = Path(sys.argv[1])

s = json.loads((out / "09_汇总.json").read_text())
c = s["cold"]
print(f"   热库对象 {s['hot_tables']} 个 / {s['hot_db_mb']:,} MB")
print(f"   冷档文件 {c['disk_files']:,} 个；索引登记 {c['catalog']:,} 条")
print(f"   索引未登记 {c['unregistered']:,} 个；索引悬空 {c['dangling']:,} 条")
print(f"   批准币池 {s['pool']['approved_pool']}（scope 版本 {s['pool']['scope_version']}）")

rows = (out / "11_组合区间统计.csv").read_text(encoding="utf-8-sig").strip().splitlines()
hdr = rows[0].split(",")
for line in rows[1:]:
    v = line.split(",")
    if v[0] in ("价格", "OI", "四者齐全"):
        print(f"   {v[0]:<6} 币数 {v[1]}  最短 {v[3]} 天  中位 {v[4]} 天  最长 {v[5]} 天")

g = (out / "15_受影响回测.csv").read_text(encoding="utf-8-sig").strip().splitlines()
print(f"   直接受影响的 FT-LOW4H run：{max(len(g) - 1, 0)} 个")
PYEOF

echo ""
echo "════════════════════════════════════════════════════════════════"
echo " 复现完成。结论与判定见 README.md；口径与公式见 02；"
echo " 使用链路见 03；关闭追溯见 04；受影响结果见 05；接入方案见 06。"
echo " 本脚本全程只读：未改业务代码、未写业务库、未下载、未回测。"
echo "════════════════════════════════════════════════════════════════"
