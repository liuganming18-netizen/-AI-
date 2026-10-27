# -*- coding: utf-8 -*-
"""NEXT-STAGE-GATE-01 · 自检 + 清单。"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import re

ROOT = "/Users/boos/Desktop/威科夫因子共振趋势"
D = os.path.join(ROOT, "evidence/NEXT-STAGE-GATE-01")
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
need = ["00_报告.md", "05_回执_2026-10-20.md",
        "out/01_准入表.csv", "out/02_必须先解决清单.csv",
        "out/03_可限制保留清单.csv", "out/04_待用户决定.csv",
        "out/05_三项任务汇总.csv"]
for rel in need:
    rec(f"交付物存在 {rel}", os.path.exists(os.path.join(D, rel)))

# ── 2. 行数 ──
rec("准入表 18 项", len(rows("01_准入表.csv")) == 18, len(rows("01_准入表.csv")))
rec("必须先解决 6 项", len(rows("02_必须先解决清单.csv")) == 6, len(rows("02_必须先解决清单.csv")))
rec("可限制保留 6 项", len(rows("03_可限制保留清单.csv")) == 6, len(rows("03_可限制保留清单.csv")))
rec("待用户决定 8 项", len(rows("04_待用户决定.csv")) == 8, len(rows("04_待用户决定.csv")))
rec("三项任务汇总 3 行", len(rows("05_三项任务汇总.csv")) == 3, len(rows("05_三项任务汇总.csv")))

# ── 3. 判定取值与计数 ──
g = rows("01_准入表.csv")
verdicts = [r["判定"] for r in g]
rec("判定只取三值", set(verdicts) <= {"通过", "部分通过", "不通过"}, sorted(set(verdicts)))
n_pass = verdicts.count("通过")
n_part = verdicts.count("部分通过")
n_fail = verdicts.count("不通过")
rec("通过 9 / 部分 3 / 不通过 6", (n_pass, n_part, n_fail) == (9, 3, 6), (n_pass, n_part, n_fail))
rec("存在不通过项 ⇒ 不得判为可执行", n_fail > 0)

# ── 4. 阻断项与清单一致 ──
blocking = sorted(r["编号"] for r in g if r["处置"].startswith("必须先解决"))
rec("阻断项 = A1/A2/B4/C2/E2/E3",
    blocking == ["A1", "A2", "B4", "C2", "E2", "E3"], blocking)
must = rows("02_必须先解决清单.csv")
rec("必须先解决清单每项都有闭环标准",
    all(r["闭环标准（可验收）"].strip() for r in must))

# ── 5. 无占位/空单元格 ──
for name in ("01_准入表.csv", "02_必须先解决清单.csv", "03_可限制保留清单.csv",
             "04_待用户决定.csv", "05_三项任务汇总.csv"):
    txt = open(os.path.join(OUT, name), encoding="utf-8-sig").read()
    rec(f"{name} 无空单元格", ",," not in txt.replace("\n", ""))

# ── 6. 报告与数据自洽 ──
rep = open(os.path.join(D, "00_报告.md"), encoding="utf-8").read()
gate_json = json.load(open(os.path.join(LOGS, "00_gate.json"), encoding="utf-8"))
rec("报告写明「当前不能进入执行阶段」", "当前不能进入执行阶段" in rep)
rec("报告给出三档判定（执行/准备/方案）",
    all(k in rep for k in ("执行准入", "准备准入", "方案准入")))
rec("报告列出「不做的事」并排除下载/扩池/重跑",
    "明确不做的事" in rep and "扩大币池" in rep and "重跑全池" in rep)
rec("报告含本方三项任务状态表",
    all(k in rep for k in ("OOS-READY-01", "VALIDATION-READINESS-01", "NEXT-STAGE-GATE-01")))
rec("报告声明契约未更新且未修改",
    "未更新" in rep and "未修改对方文件" in rep)
rec("报告未把执行阶段说成已具备",
    not re.search(r"(可以进入执行阶段|执行准入[^\n]{0,6}允许[^\n]{0,4}✅)", rep))

# ── 7. 契约未被改动 ──
contract_md = os.path.join(ROOT, "evidence/VALIDATION-CONTRACT-01/下一阶段验证实验契约草案.md")
cur = sha(contract_md)
rec("契约哈希与 gate 记录一致", cur == gate_json["contract"]["sha256"], cur[:16])
rec("契约仍为待审批", gate_json["contract"]["status"] == "待用户审批",
    gate_json["contract"]["status"])

# ── 8. 越界检查 ──
baseline = os.path.getmtime(D)
watched = [
    "evidence/OOS-READY-01/00_报告.md",
    "evidence/OOS-READY-01/out/02_验证数据资格表.csv",
    "evidence/VALIDATION-READINESS-01/00_报告.md",
    "evidence/VALIDATION-READINESS-01/out/03_前置缺口清单.csv",
    "evidence/VALIDATION-CONTRACT-01/下一阶段验证实验契约草案.md",
    "evidence/VALIDATION-CONTRACT-01/out/versions.json",
    "策略/WyckoffMaster/event_trend_v0_1/runner.py",
    "策略/WyckoffMaster/trading_core/fill.py",
]
dirty = [p for p in watched if os.path.exists(os.path.join(ROOT, p))
         and os.path.getmtime(os.path.join(ROOT, p)) > baseline]
rec("本次执行期间未改动前两项产物与对方契约", not dirty, dirty)

# ── 9. 清单 ──
files = {}
for dirpath, _dn, fnames in os.walk(D):
    for fn in fnames:
        fp = os.path.join(dirpath, fn)
        if fp in (SELF_LOG, SELF_MANIFEST):
            continue
        files[os.path.relpath(fp, D)] = {"bytes": os.path.getsize(fp), "sha256": sha(fp)}

manifest = {
    "order": "NEXT-STAGE-GATE-01",
    "generated_by": "queries/01_selfcheck_and_manifest.py",
    "verdict": gate_json["verdict"],
    "counts": gate_json["counts"],
    "blocking_items": gate_json["blocking_items"],
    "file_count": len(files),
    "files": dict(sorted(files.items())),
    "watched_snapshot": {p: sha(os.path.join(ROOT, p)) for p in watched
                         if os.path.exists(os.path.join(ROOT, p))},
    "checks": CHECK,
    "pass": sum(1 for c in CHECK if c["status"] == "PASS"),
    "fail": sum(1 for c in CHECK if c["status"] == "FAIL"),
}
with open(SELF_MANIFEST, "w", encoding="utf-8") as f:
    json.dump(manifest, f, ensure_ascii=False, indent=2)

print(json.dumps({"files": len(files), "pass": manifest["pass"], "fail": manifest["fail"],
                  "verdict": manifest["verdict"]}, ensure_ascii=False))
for c in CHECK:
    if c["status"] == "FAIL":
        print("FAIL:", c["item"], "|", c["detail"])
