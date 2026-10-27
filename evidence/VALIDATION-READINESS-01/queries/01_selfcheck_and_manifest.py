# -*- coding: utf-8 -*-
"""VALIDATION-READINESS-01 · 自检 + 清单。

自检只验「交付物自身是否自相矛盾、是否越界」，不代替独立验收。
清单对交付目录内所有文件算 sha256 落盘（排除本脚本自己的日志）。
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import re

ROOT = "/Users/boos/Desktop/威科夫因子共振趋势"
D = os.path.join(ROOT, "evidence/VALIDATION-READINESS-01")
OUT = os.path.join(D, "out")
LOGS = os.path.join(D, "logs")
SELF_LOG = os.path.join(LOGS, "01_selfcheck_and_manifest.log")
SELF_MANIFEST = os.path.join(D, "manifest.json")

CHECK = []


def rec(item, ok, detail=""):
    CHECK.append({"item": item, "status": "PASS" if ok else "FAIL", "detail": str(detail)[:300]})


def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def rows(name):
    p = os.path.join(OUT, name)
    if not os.path.exists(p):
        return []
    with open(p, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


# ── 1. 交付物存在 ──
need = ["00_报告.md", "out/01_入口路径表.csv", "out/02_能力核对表.csv",
        "out/03_前置缺口清单.csv", "out/04_最小运行命令草案.md", "05_回执_2026-10-20.md"]
for rel in need:
    rec(f"交付物存在 {rel}", os.path.exists(os.path.join(D, rel)))

# ── 2. 行号/符号全部命中，无占位 ──
for name in ("01_入口路径表.csv", "02_能力核对表.csv", "03_前置缺口清单.csv"):
    txt = open(os.path.join(OUT, name), encoding="utf-8-sig").read()
    rec(f"{name} 无「未找到」占位", "未找到" not in txt)
    rec(f"{name} 无空单元格", ",," not in txt.replace("\n", ""))

# ── 3. 行数 ──
rec("入口路径表 15 环", len(rows("01_入口路径表.csv")) == 15, len(rows("01_入口路径表.csv")))
rec("能力核对表 8 项", len(rows("02_能力核对表.csv")) == 8, len(rows("02_能力核对表.csv")))
rec("缺口清单 11 项", len(rows("03_前置缺口清单.csv")) == 11, len(rows("03_前置缺口清单.csv")))

# ── 4. 缺口分类只取两值，且计数与报告一致 ──
g = rows("03_前置缺口清单.csv")
cls = [r["分类"] for r in g]
rec("缺口分类仅两种取值", set(cls) <= {"阻止结果可信", "仅影响方便使用"}, sorted(set(cls)))
n_block = sum(1 for c in cls if c == "阻止结果可信")
n_easy = sum(1 for c in cls if c == "仅影响方便使用")
rec("阻止可信 5 项 / 仅影响方便 6 项", (n_block, n_easy) == (5, 6), (n_block, n_easy))

# ── 5. 报告与数据自洽 ──
rep = open(os.path.join(D, "00_报告.md"), encoding="utf-8").read()
vr = json.load(open(os.path.join(LOGS, "00_verify.json"), encoding="utf-8"))
rec("报告引用的冻结哈希 = 实算哈希", vr["frozen_rules_sha256"] in rep, vr["frozen_rules_sha256"][:16])
rec("报告写明 17 个产物文件", "17" in rep)
rec("报告写明代码指纹 20 个文件", "20 个文件" in rep or "20 项" in rep)
rec("报告标注 V6/V7/V8 引用 OOS-READY-01 不重测", rep.count("OOS-READY-01") >= 3)
rec("报告未声称测试/验收已通过",
    ("测试通过" not in rep) and ("验收通过" not in rep)
    and ("自检通过不等于独立验收" in rep))
# 「正式通过」只允许出现在否定语境（不得/不能/产不出/无法）
bad = []
for m in re.finditer(r"正式通过", rep):
    ctx = rep[max(0, m.start() - 12):m.start()]
    if not re.search(r"(不得|不能|无法|产不出|不算|非)", ctx):
        bad.append(ctx)
rec("「正式通过」只出现在否定语境", not bad, bad)

# ── 6. 命令草案确实只写不跑 ──
cmd = open(os.path.join(OUT, "04_最小运行命令草案.md"), encoding="utf-8").read()
rec("命令草案声明未执行", "未执行" in cmd)
rec("命令草案含输入/输出/写入范围三要素",
    all(k in cmd for k in ("输入", "输出", "预计写入范围")))

# ── 7. 越界检查：关键生产文件 mtime 早于本工单目录创建时间 ──
baseline = os.path.getmtime(D)
prod = [
    "策略/WyckoffMaster/event_trend_v0_1/config.py",
    "策略/WyckoffMaster/event_trend_v0_1/runner.py",
    "策略/WyckoffMaster/event_trend_v0_1/engine_adapter.py",
    "策略/WyckoffMaster/trading_core/engine.py",
    "策略/WyckoffMaster/trading_core/fill.py",
    "策略/WyckoffMaster/trading_core/funding.py",
    "evidence/BREAKEVEN-02/code/be_core.py",
    "evidence/BREAKEVEN-02/code/02_run_fixed.py",
    "run_batch_2026_sep_oct_50.py",
]
dirty = [p for p in prod if os.path.exists(os.path.join(ROOT, p))
         and os.path.getmtime(os.path.join(ROOT, p)) > baseline]
rec("本次执行期间未改动生产/对方文件", not dirty, dirty)
rec("生产文件哈希已记录（可与下次比对）",
    all(sha(os.path.join(ROOT, p)) for p in prod if os.path.exists(os.path.join(ROOT, p))))

# ── 8. 写清单 ──
files = {}
for dirpath, _dirnames, fnames in os.walk(D):
    for fn in fnames:
        fp = os.path.join(dirpath, fn)
        if fp in (SELF_LOG, SELF_MANIFEST):
            continue
        rel = os.path.relpath(fp, D)
        files[rel] = {"bytes": os.path.getsize(fp), "sha256": sha(fp)}

manifest = {
    "order": "VALIDATION-READINESS-01",
    "generated_by": "queries/01_selfcheck_and_manifest.py",
    "commit_mode": "write_in_place",
    "file_count": len(files),
    "files": dict(sorted(files.items())),
    "prod_snapshot": {p: sha(os.path.join(ROOT, p)) for p in prod
                      if os.path.exists(os.path.join(ROOT, p))},
    "checks": CHECK,
    "pass": sum(1 for c in CHECK if c["status"] == "PASS"),
    "fail": sum(1 for c in CHECK if c["status"] == "FAIL"),
}
with open(os.path.join(D, "manifest.json"), "w", encoding="utf-8") as f:
    json.dump(manifest, f, ensure_ascii=False, indent=2)

print(json.dumps({"files": len(files), "pass": manifest["pass"], "fail": manifest["fail"]},
                 ensure_ascii=False))
for c in CHECK:
    if c["status"] == "FAIL":
        print("FAIL:", c["item"], "|", c["detail"])
