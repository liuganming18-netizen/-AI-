#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""04 · 既有测试零回归对照：改动后不得出现「改动前通过、改动后失败」。

做法：同一批 unittest，先用**当前**（已改）`paper_engine.py` 跑一遍，再用
`evidence/PARALLEL-STRATEGY-01-ANTIGRAVITY-A/snapshot/paper_engine.py`（sha
`a99f8af00d4d82ca…`，已核等于本单 `00_freeze_before` 记录的改动前哈希）跑一遍，
逐条比对「测试方法 → 通过/失败 + 失败断言原文」。

**安全**：替换文件用 try/finally 包裹，结束必恢复并校验 sha；替换期间不启动任何
生产进程，且只跑离线 unittest（不触网、不写生产账本）。

输出 `out/04_regression_parity.json`。
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
import time
from pathlib import Path

PROJ = Path("/Users/boos/Desktop/威科夫因子共振趋势")
EVID = PROJ / "evidence" / "PARALLEL-STRATEGY-01-WORK"
OUT = EVID / "out"
BACKEND = PROJ / "框架" / "backend"
TARGET = BACKEND / "paper_engine.py"
BASELINE = PROJ / "evidence" / "PARALLEL-STRATEGY-01-ANTIGRAVITY-A" / "snapshot" / "paper_engine.py"
PY = "/Users/boos/.workbuddy-ai/binaries/python/envs/default/bin/python"

SUITES = [
    "tests.test_paper_live_01",
    "tests.test_data_path_01",
    "tests.test_research_ledger",
    "tests.test_lifecycle_isolation",
]

LINE_RE = re.compile(r"^(test_\w+) \(([\w\.]+)\)")


def sha(p: Path) -> str:
    h = hashlib.sha256()
    h.update(p.read_bytes())
    return h.hexdigest()


def run_suite(name: str) -> dict:
    p = subprocess.run([PY, "-m", "unittest", name, "-v"],
                       cwd=str(BACKEND), capture_output=True, text=True, timeout=900)
    text = (p.stdout or "") + "\n" + (p.stderr or "")
    cases = {}
    cur = None
    for ln in text.splitlines():
        s = ln.strip()
        m = LINE_RE.match(s)
        if m:
            # 第一行只有测试名；结果（ok/FAIL/ERROR）可能在同一行，也可能在紧随的
            # docstring 行末尾（unittest 对带 docstring 的用例就是这么打印的）。
            cur = m.group(1)
            cases.setdefault(cur, {"status": "unknown", "detail": ""})
            tail = s[m.end():]
            if "... ok" in tail:
                cases[cur]["status"] = "pass"
            elif "... FAIL" in tail or "... ERROR" in tail:
                cases[cur]["status"] = "fail"
            continue
        if cur is None:
            continue
        if "... ok" in s and cases[cur]["status"] == "unknown":
            cases[cur]["status"] = "pass"
        elif ("... FAIL" in s or "... ERROR" in s) and cases[cur]["status"] == "unknown":
            cases[cur]["status"] = "fail"
        elif cases[cur]["status"] == "fail" and not cases[cur]["detail"]:
            if s.startswith("AssertionError") or s.startswith("Error"):
                cases[cur]["detail"] = s[:400]
    m = re.search(r"^Ran (\d+) tests", text, re.M)
    return {"suite": name, "rc": p.returncode,
            "ran": int(m.group(1)) if m else None,
            "cases": cases,
            "tail": text.strip().splitlines()[-3:]}


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    cur_sha = sha(TARGET)
    base_sha = sha(BASELINE)
    rec = {"generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "python": PY, "suites": SUITES,
           "current_sha256": cur_sha, "baseline_sha256": base_sha,
           "baseline_is_antigravity_snapshot": str(BASELINE)}

    # ── 阶段 A：当前（已改）版本 ────────────────────────────────────────────────
    rec["after_change"] = [run_suite(s) for s in SUITES]

    # ── 阶段 B：改动前版本（临时替换，finally 必恢复）───────────────────────────
    backup = TARGET.read_bytes()
    try:
        TARGET.write_bytes(BASELINE.read_bytes())
        if sha(TARGET) != base_sha:
            raise RuntimeError("baseline 替换后哈希不符")
        rec["before_change"] = [run_suite(s) for s in SUITES]
    finally:
        TARGET.write_bytes(backup)
        rec["restored_sha256"] = sha(TARGET)
        rec["restore_ok"] = (rec["restored_sha256"] == cur_sha)

    # ── 逐条比对 ──────────────────────────────────────────────────────────────
    def flat(stage):
        out = {}
        for s in rec[stage]:
            for k, v in s["cases"].items():
                out[f"{s['suite'].split('.')[-1]}::{k}"] = v
        return out

    before = flat("before_change")
    after = flat("after_change")
    regressions, fixes, same_fail, same_pass = [], [], [], []
    for k in sorted(set(before) | set(after)):
        b = before.get(k, {}).get("status")
        a = after.get(k, {}).get("status")
        if b == "pass" and a != "pass":
            regressions.append({"case": k, "before": b, "after": a,
                                "after_detail": after.get(k, {}).get("detail")})
        elif b != "pass" and a == "pass":
            fixes.append({"case": k, "before": b, "after": a,
                          "before_detail": before.get(k, {}).get("detail")})
        elif b == a == "fail":
            same_fail.append({"case": k,
                              "before_detail": before.get(k, {}).get("detail"),
                              "after_detail": after.get(k, {}).get("detail")})
        elif b == a == "pass":
            same_pass.append(k)
    rec["summary"] = {
        "same_pass_n": len(same_pass),
        "same_fail_n": len(same_fail),
        "regressions_n": len(regressions),
        "fixed_n": len(fixes),
        "regressions": regressions,
        "fixed": fixes,
        "same_fail": same_fail,
        "zero_regression": len(regressions) == 0,
        "restore_ok": rec["restore_ok"],
    }
    p = OUT / "04_regression_parity.json"
    p.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
    s = rec["summary"]
    print(f"written {p}")
    print(f"  同通过 {s['same_pass_n']} / 同失败 {s['same_fail_n']} / "
          f"回归 {s['regressions_n']} / 修好 {s['fixed_n']}")
    print(f"  零回归 = {s['zero_regression']}；文件已恢复 = {s['restore_ok']} "
          f"(sha {rec['restored_sha256'][:20]})")
    for r in s["regressions"]:
        print(f"  [回归] {r['case']}: {r['before']} -> {r['after']}  {r['after_detail']}")
    for r in s["same_fail"]:
        print(f"  [同失败] {r['case']}: {r['after_detail']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
