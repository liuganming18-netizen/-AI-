# -*- coding: utf-8 -*-
"""GATE-CLOSE-01 · 自检 + 清单。"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import re
from collections import Counter

ROOT = "/Users/boos/Desktop/威科夫因子共振趋势"
D = os.path.join(ROOT, "evidence/GATE-CLOSE-01")
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


def rd(name):
    p = os.path.join(OUT, name)
    if not os.path.exists(p):
        return []
    with open(p, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


# ── 1. 交付物 ──
need = ["00_统一准入表与资格明细.md", "06_回执_2026-10-20.md",
        "out/01_统一阻塞清单.csv", "out/02_测试集资格明细.csv",
        "out/03_已观察证据_批次复核.csv", "out/04_清单不一致整改记录.csv",
        "out/05_样本量依据链.csv"]
for rel in need:
    rec(f"交付物存在 {rel}", os.path.exists(os.path.join(D, rel)))

# ── 2. 行数与结构 ──
blk = rd("01_统一阻塞清单.csv")
seg = rd("02_测试集资格明细.csv")
fix = rd("04_清单不一致整改记录.csv")
rec("统一清单 30 项", len(blk) == 30, len(blk))
rec("资格明细 9 段", len(seg) == 9, len(seg))
rec("整改记录 2 处", len(fix) == 2, len(fix))
rec("批次复核 17 行", len(rd("03_已观察证据_批次复核.csv")) == 17)
rec("样本量依据链 6 行", len(rd("05_样本量依据链.csv")) == 6)

# ── 3. 三类分开 ──
cats = Counter(r["排除原因类别"] for r in seg)
rec("排除原因只有三类", set(cats) <= {"已证明被观察", "数据不合格", "资格无法证明"}, dict(cats))
rec("已观察 5 段 / 数据不合格 4 段 / 仅资格无法证明 0 段",
    (cats.get("已证明被观察"), cats.get("数据不合格"), cats.get("资格无法证明", 0)) == (5, 4, 0),
    dict(cats))
rec("无任何可用段", all("可用" not in r["数据资格"] for r in seg))

# ── 4. 阻止程度分档 ──
vc = Counter(r["是否阻止本次执行"] for r in blk)
rec("阻止程度 7 档且合计 30", len(vc) == 7 and sum(vc.values()) == 30, dict(vc))
rec("硬阻塞 2 项", vc.get("是", 0) == 2, [r["统一编号"] for r in blk if r["是否阻止本次执行"] == "是"])
rec("条件性 3 项", vc.get("条件性", 0) == 3)
rec("已通过 9 项单列", vc.get("否（已通过）", 0) == 9)
rec("未把完善项全列阻塞", vc.get("是", 0) + vc.get("条件性", 0) <= 5,
    f"{vc.get('是', 0)}+{vc.get('条件性', 0)}")

# ── 5. 映射完整性 ──
gate = rd("01_统一阻塞清单.csv")
mapped = {r["原编号（准入表）"] for r in gate if r["原编号（准入表）"] not in ("—",)}
rec("准入表 18 项全部出现", len([m for m in mapped if re.fullmatch(r"[A-E]\d", m)]) == 18,
    sorted(m for m in mapped if re.fullmatch(r"[A-E]\d", m)))
ms = set()
us = set()
for r in gate:
    ms |= set(re.findall(r"M\d", r["原编号（M/L/U 清单）"]))
    us |= set(re.findall(r"U\d", r["原编号（M/L/U 清单）"]))
rec("M 清单 6 项全部出现", ms == {f"M{i}" for i in range(1, 7)}, sorted(ms))
rec("U 清单 8 项全部出现", us == {f"U{i}" for i in range(1, 9)}, sorted(us))

# ── 6. 每项都有证据与理由 ──
rec("每项都有具体证据", all(r["具体证据"].strip() for r in blk))
rec("每项都写了影响环节", all(r["影响哪个验证环节"].strip() for r in blk))
rec("每项都写了理由", all(r["理由"].strip() for r in blk))
rec("每项都写了处置方式", all(r["处置方式"].strip() for r in blk))

# ── 7. 报告口径 ──
rep = open(os.path.join(D, "00_统一准入表与资格明细.md"), encoding="utf-8").read()
vr = json.load(open(os.path.join(LOGS, "00_verify.json"), encoding="utf-8"))
rec("报告写明「本次已检查范围」限制", "本次已检查范围" in rep or "本次已检查的范围" in rep)
rec("报告明确不外推", "不是" in rep and "所有地方都没有可用数据" in rep)
rec("报告标注第 1 次整改", "第 1 次整改" in rep)
rec("报告标注待 Codex 审核", "待 Codex 审核" in rep)
rec("报告说明 18~24 个月不作为决策结论", "不作为决策结论" in rep)
rec("报告未重算收益统计",
    not re.search(r"(t\s*=\s*0\.\d|净额\s*[:：]\s*[-+]?\d)", rep))
rec("实测窗口与核验结果一致",
    vr["sep_all_latest_real_window"]["end"] in rep, vr["sep_all_latest_real_window"]["end"])
rec("标的 528 与核验结果一致", "528" in rep and vr["checked_scope"]["symbols_total"] == 528)

# ── 8. 越界检查 ──
baseline = os.path.getmtime(D)
watched = [
    "evidence/OOS-READY-01/00_报告.md",
    "evidence/OOS-READY-01/out/01_已观察范围_批次.csv",
    "evidence/OOS-READY-01/out/01_已观察范围_币种.csv",
    "evidence/OOS-READY-01/out/02_验证数据资格表.csv",
    "evidence/VALIDATION-READINESS-01/00_报告.md",
    "evidence/NEXT-STAGE-GATE-01/00_报告.md",
    "evidence/NEXT-STAGE-GATE-01/out/01_准入表.csv",
    "evidence/VALIDATION-CONTRACT-01/下一阶段验证实验契约草案.md",
    "策略/WyckoffMaster/event_trend_v0_1/runner.py",
]
dirty = [p for p in watched if os.path.exists(os.path.join(ROOT, p))
         and os.path.getmtime(os.path.join(ROOT, p)) > baseline]
rec("本次执行期间未改动任何原产物", not dirty, dirty)

# ── 9. 清单 ──
files = {}
for dp, _dn, fns in os.walk(D):
    for fn in fns:
        fp = os.path.join(dp, fn)
        if fp in (SELF_LOG, SELF_MANIFEST):
            continue
        files[os.path.relpath(fp, D)] = {"bytes": os.path.getsize(fp), "sha256": sha(fp)}

manifest = {
    "order": "GATE-CLOSE-01",
    "generated_by": "queries/01_selfcheck_and_manifest.py",
    "status": "待 Codex 审核",
    "fix_round": "第 1 次整改",
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

print(json.dumps({"files": len(files), "pass": manifest["pass"], "fail": manifest["fail"]},
                 ensure_ascii=False))
for c in CHECK:
    if c["status"] == "FAIL":
        print("FAIL:", c["item"], "|", c["detail"])
