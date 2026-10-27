# -*- coding: utf-8 -*-
"""GATE-CLOSE-02 · 自检 + 清单（含输入与交付指纹）。"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import re

ROOT = "/Users/boos/Desktop/威科夫因子共振趋势"
D = os.path.join(ROOT, "evidence/GATE-CLOSE-02")
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
need = ["00_修正报告.md", "07_回执_2026-10-20.md",
        "out/01_官方分类与范围清单.csv", "out/02_纳入清单.csv", "out/03_排除清单.csv",
        "out/04_资格明细.csv", "out/05_统一准入表.csv", "out/06_必须解决清单.csv",
        "out/07_历史规格准入判定.csv"]
for rel in need:
    rec(f"交付物存在 {rel}", os.path.exists(os.path.join(D, rel)))

# ── 2. 行数 ──
rec("官方分类清单 528 行", len(rd("01_官方分类与范围清单.csv")) == 528,
    len(rd("01_官方分类与范围清单.csv")))
rec("纳入清单 526 行", len(rd("02_纳入清单.csv")) == 526, len(rd("02_纳入清单.csv")))
rec("排除清单 2 行", len(rd("03_排除清单.csv")) == 2, len(rd("03_排除清单.csv")))
rec("资格明细 9 段", len(rd("04_资格明细.csv")) == 9, len(rd("04_资格明细.csv")))
rec("统一准入表 22 项", len(rd("05_统一准入表.csv")) == 22, len(rd("05_统一准入表.csv")))
rec("必须解决清单 5 项", len(rd("06_必须解决清单.csv")) == 5, len(rd("06_必须解决清单.csv")))
rec("历史规格判定 5 行", len(rd("07_历史规格准入判定.csv")) == 5)

# ── 3. 范围自洽 ──
inc = rd("02_纳入清单.csv")
exc = rd("03_排除清单.csv")
rec("纳入 + 排除 = 528", len(inc) + len(exc) == 528, len(inc) + len(exc))
rec("排除项 = ALLUSDT / BTCDOMUSDT",
    sorted(r["symbol"] for r in exc) == ["ALLUSDT", "BTCDOMUSDT"],
    sorted(r["symbol"] for r in exc))
rec("排除项全部 underlyingType=INDEX", all(r["underlyingType"] == "INDEX" for r in exc))
rec("纳入项全部 underlyingType=COIN", all(r["underlyingType"] == "COIN" for r in inc))
rec("纳入项全部 contractType=PERPETUAL", all(r["contractType"] == "PERPETUAL" for r in inc))
rec("无「不在快照」的标的",
    all(r["underlyingType"] != "(不在快照)" for r in rd("01_官方分类与范围清单.csv")))

# ── 4. 资格三类分开 ──
seg = rd("04_资格明细.csv")
from collections import Counter
cc = Counter(r["排除原因类别"] for r in seg)
rec("已观察 5 / 数据不合格 4 / 仅资格无法证明 0",
    (cc.get("已证明被观察"), cc.get("数据不合格"), cc.get("资格无法证明", 0)) == (5, 4, 0),
    dict(cc))
rec("S6a 时长 90.0 小时（不写成 4 天）",
    next(r for r in seg if r["段"] == "S6a")["时长（小时）"] == "90.0",
    next(r for r in seg if r["段"] == "S6a")["时长（小时）"])
rec("S6b 时长 18.0 小时（不写成 1 天）",
    next(r for r in seg if r["段"] == "S6b")["时长（小时）"] == "18.0",
    next(r for r in seg if r["段"] == "S6b")["时长（小时）"])
rec("S6a 止于 2026-10-17 18:00:00",
    next(r for r in seg if r["段"] == "S6a")["止（UTC）"] == "2026-10-17 18:00:00")
rec("S2 的 OI 覆盖 = 1 币（BTC）",
    next(r for r in seg if r["段"] == "S2")["OI 可用标的数（526 内）"] == "1")

# ── 5. 准入表与修复任务 ──
g = rd("05_统一准入表.csv")
gm = {r["统一编号"]: r for r in g}
rec("DQ-04 判定为「未满足」（已升级）", gm["DQ-04"]["判定"].startswith("未满足"),
    gm["DQ-04"]["判定"])
rec("DQ-04 不再标「可限制保留」", "可限制" not in gm["DQ-04"]["判定"])
rec("DQ-01/DQ-02 共用 FIX-01",
    gm["DQ-01"]["修复任务"] == gm["DQ-02"]["修复任务"] == "FIX-01")
rec("EX-02/EX-03 共用 FIX-05",
    gm["EX-02"]["修复任务"] == gm["EX-03"]["修复任务"] == "FIX-05")
rec("每项都有证据", all(r["证据"].strip() for r in g))
rec("每项都有影响环节", all(r["影响的验证环节"].strip() for r in g))
fx = rd("06_必须解决清单.csv")
rec("修复任务编号 FIX-01~FIX-05",
    sorted(r["修复编号"] for r in fx) == [f"FIX-0{i}" for i in range(1, 6)])

# ── 6. 历史规格判定 ──
sp = rd("07_历史规格准入判定.csv")
spt = " ".join(" ".join(r.values()) for r in sp)
rec("历史规格判定含「撤回」", "撤回" in spt)
rec("历史规格正式验证 = 未满足", "未满足（不得放行）" in spt)
rec("明确无可放行子集", "未满足**（无可放行子集）" in spt or "无可放行子集" in spt)
rec("写明「公告没变更≠没变更」", "不等于" in spt or "不能把" in spt)

# ── 7. 报告口径 ──
rep = open(os.path.join(D, "00_修正报告.md"), encoding="utf-8").read()
vr = json.load(open(os.path.join(LOGS, "00_verify.json"), encoding="utf-8"))
rec("报告含版本标识 WX-GATE-v2.0", "WX-GATE-v2.0" in rep)
rec("报告标注待 Codex 审核", "待 Codex 审核" in rep)
rec("报告标注循环第 2 轮", "第 2 轮" in rep)
rec("报告声明不假定 528 减 2", "不是\"528 减 2\"" in rep or "不能假定" in rep)
rec("报告写明已观察末端 2026-10-17 18:00:00", "2026-10-17 18:00:00" in rep)
rec("报告写明数据末端 15m 2026-10-18 12:00:00", "2026-10-18 12:00:00" in rep)
rec("S6a 按小时表达（90.0 h）且否定「4 个自然日」",
    "90.0 h" in rep and "不是 4 个自然日" in rep)
rec("S6b 按小时表达（18.0 h）且否定「1 天」",
    "18.0 h" in rep and "不是 1 天" in rep)
rec("报告写明原报告保留未改", "原报告保留" in rep)
rec("报告未宣布正式准入通过", "未通过" in rep and "不宣布正式准入通过" in rep)
rec("报告未重算收益", not re.search(r"(t\s*=\s*0\.\d|\+\d+\.\d+\s*U)", rep))

# ── 8. 输入指纹与实算一致 ──
ins = vr["inputs"]
ok_in = True
for k, v in ins.items():
    p = os.path.join(ROOT, v["path"])
    if not os.path.exists(p) or sha(p) != v["sha256"]:
        ok_in = False
rec("输入指纹与实算一致", ok_in, list(ins))

# ── 9. 越界检查 ──
baseline = os.path.getmtime(D)
watched = [
    "evidence/GATE-CLOSE-01/00_统一准入表与资格明细.md",
    "evidence/GATE-CLOSE-01/out/01_统一阻塞清单.csv",
    "evidence/GATE-CLOSE-01/out/02_测试集资格明细.csv",
    "evidence/OOS-READY-01/00_报告.md",
    "evidence/OOS-READY-01/out/02_验证数据资格表.csv",
    "evidence/NEXT-STAGE-GATE-01/00_报告.md",
    "evidence/VALIDATION-READINESS-01/00_报告.md",
    "evidence/VALIDATION-CONTRACT-01/下一阶段验证实验契约草案.md",
    "evidence/PRECISION-04/当前响应_exchangeInfo.json",
    "策略/WyckoffMaster/event_trend_v0_1/runner.py",
]
dirty = [p for p in watched if os.path.exists(os.path.join(ROOT, p))
         and os.path.getmtime(os.path.join(ROOT, p)) > baseline]
rec("本次执行期间未改动原产物与对方文件", not dirty, dirty)

# ── 10. 清单（含输入指纹） ──
files = {}
for dp, _dn, fns in os.walk(D):
    for fn in fns:
        fp = os.path.join(dp, fn)
        if fp in (SELF_LOG, SELF_MANIFEST):
            continue
        files[os.path.relpath(fp, D)] = {"bytes": os.path.getsize(fp), "sha256": sha(fp)}

manifest = {
    "version": vr["version"],
    "order": "GATE-CLOSE-02",
    "status": "待Codex审核",
    "fix_round": "第 2 轮",
    "inputs": ins,
    "scope": vr["scope"],
    "time_axis": vr["time_axis"],
    "segments": vr["segments"],
    "historical_spec": vr["historical_spec"],
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

print(json.dumps({"version": manifest["version"], "files": len(files),
                  "pass": manifest["pass"], "fail": manifest["fail"]}, ensure_ascii=False))
for c in CHECK:
    if c["status"] == "FAIL":
        print("FAIL:", c["item"], "|", c["detail"])
