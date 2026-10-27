#!/usr/bin/env bash
# ENTRY-AUDIT-05 一键复现：FE07 / FE08 最终交接一致性复核
#
# 只读源数据与反重力产物，**不重跑任何回测**，全程约 1~2 分钟。
# 全程只写 evidence/ENTRY-AUDIT-05/，不碰原账本、反重力产物、正式策略、撮合、
# UI、API、采集器或交易闸门。
#
# 用法：
#   cd /Users/boos/Desktop/威科夫因子共振趋势
#   bash evidence/ENTRY-AUDIT-05/reproduce.sh
#
# 前置：evidence/FACTOR-ENTRY-07/ 与 evidence/FACTOR-ENTRY-08/ 已落盘。
#       若 FE08 未到，第 2 步会输出「第二阶段待交付」，其余步骤照常。
set -euo pipefail

PY="/Users/boos/.workbuddy-ai/binaries/python/envs/default/bin/python"
ROOT="/Users/boos/Desktop/威科夫因子共振趋势"
D="$ROOT/evidence/ENTRY-AUDIT-05"
cd "$ROOT"

echo "=== [0/6] 环境检查 ==="
"$PY" -c "import pandas, pyarrow, numpy; print('pandas', pandas.__version__, '/ pyarrow', pyarrow.__version__, '/ numpy', numpy.__version__)"

echo
echo "=== [1/6] 第一阶段：FE07 七项独立复核 ==="
"$PY" "$D/00_verify_fe07.py" 2>&1 | tee "$D/log_01_fe07.txt"

echo
echo "=== [2/6] 第二阶段：接收 FE08，核对来源与差异 ==="
if [ -d "$ROOT/evidence/FACTOR-ENTRY-08" ]; then
  "$PY" "$D/01_verify_fe08.py" 2>&1 | tee "$D/log_02_fe08.txt"
else
  echo "evidence/FACTOR-ENTRY-08/ 不存在 ⇒ 第二阶段待交付（本单不得报整体完成）" | tee "$D/log_02_fe08.txt"
fi

echo
echo "=== [3/6] 导出明细表与差异表 ==="
"$PY" "$D/07_emit_tables.py" 2>&1 | tee "$D/log_03_tables.txt"

echo
echo "=== [4/6] 收尾自检：本轮只读，只在本目录新增 ==="
"$PY" "$D/99_verify_unchanged.py" 2>&1 | tee "$D/log_04_verify.txt"

echo
echo "=== [5/6] 本目录自哈希（放最后，避免把日志的中间态记进去）==="
"$PY" "$D/09_self_hashes.py" 2>&1 | tee "$D/log_05_selfhash.txt"

echo
echo "=== [6/6] 汇总 ==="
"$PY" - "$D" <<'PYEOF'
import json, sys
from pathlib import Path
d = Path(sys.argv[1])
a = json.loads((d / "00_verify_fe07.json").read_text(encoding="utf-8"))
print("第一阶段 FE07 七项：")
for r in a["acceptance_table"]:
    print(f"  {r['序号']}. {r['检查项']:<28} {r['判定']}")
print(f"  严格全通过={a['all_pass_strict']} / 含注记可用={a['all_pass_or_note']}")
p = d / "01_fe08_recheck.json"
if p.exists():
    b = json.loads(p.read_text(encoding="utf-8"))
    print("\n第二阶段 FE08 六项：")
    for k, v in b["acceptance"].items():
        print(f"  {k:<24} {v}")
    print(f"  复用决定：{b['reuse_decision']}")
v = json.loads((d / "99_verify_result.json").read_text(encoding="utf-8"))
print(f"\n零改动自检：{json.dumps(v['hard_verdict'], ensure_ascii=False)}")
PYEOF

echo
echo "=== 完成 ==="
echo "逐项验收表：evidence/ENTRY-AUDIT-05/00_acceptance_table.csv"
echo "身份映射表：evidence/ENTRY-AUDIT-05/01_identity_map.csv"
echo "差异总表：  evidence/ENTRY-AUDIT-05/08_diff_table.csv"
echo "验收报告：  evidence/ENTRY-AUDIT-05/报告.md"
