#!/usr/bin/env bash
# ENTRY-AUDIT-01 一键复现（只读审计，不写任何生产/研究目录）
# 用法：bash evidence/ENTRY-AUDIT-01/reproduce.sh
set -euo pipefail

ROOT="/Users/boos/Desktop/威科夫因子共振趋势"
PY="/Users/boos/.workbuddy-ai/binaries/python/envs/default/bin/python"
cd "$ROOT"

echo "=== 0/4 冻结输入指纹 ==="
"$PY" evidence/ENTRY-AUDIT-01/00_freeze_manifest.py | tee evidence/ENTRY-AUDIT-01/log_00_freeze.txt

echo "=== 1/4 独立计数与 227→205 映射 ==="
"$PY" evidence/ENTRY-AUDIT-01/01_counts_and_mapping.py | tee evidence/ENTRY-AUDIT-01/log_01_counts.txt

echo "=== 2/4 用原始 K 线独立复算分类 ==="
"$PY" evidence/ENTRY-AUDIT-01/02_recompute_classification.py | tee evidence/ENTRY-AUDIT-01/log_02_recompute.txt

echo "=== 3/4 8 笔案例 + 事实与解释分离 ==="
"$PY" evidence/ENTRY-AUDIT-01/03_cases.py | tee evidence/ENTRY-AUDIT-01/log_03_cases.txt

echo "=== 4/4 复核输入未被改动 ==="
"$PY" - <<'PYEOF'
import json, hashlib
from pathlib import Path
ROOT = Path("/Users/boos/Desktop/威科夫因子共振趋势")
m = json.load(open(ROOT / "evidence/ENTRY-AUDIT-01/freeze_manifest.json"))
def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()
bad = [f["path"] for f in m["audited_side_files"]
       if not (ROOT / f["path"]).exists() or sha(ROOT / f["path"]) != f["sha256"]]
print("变动文件:", bad if bad else "无（输入与冻结时一致）")
PYEOF

echo "完成。产物在 evidence/ENTRY-AUDIT-01/"
