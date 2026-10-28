# -*- coding: utf-8 -*-
"""SIGNAL-NOW-01-WORK｜步骤10：重载后 API 字段核对（只读）。

核对两件事：
  ① `scope=current`（当前交易信号区）为 0 条、289 条研究行全部被移出，且 `by_reason` 各项之和
     允许 ≥ `excluded_n`（一条行可被多条原因命中，不冒充「唯一原因」）。
  ② `scope=all&origin=historical_research` 仍是 289 条，身份字段逐条保持
     `historical_research` / `historical_not_executable` / `research_only` / `approved=false` /
     `available_at=null`（**不伪填**）。

产物：out/10_API字段核对.json
"""
from __future__ import annotations

import json
import subprocess
import time
from collections import Counter
from pathlib import Path

ROOT = Path("/Users/boos/Desktop/威科夫因子共振趋势/evidence/SIGNAL-NOW-01-WORK")
OUT = ROOT / "out"
API = "http://127.0.0.1:8787"


def utc():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def g(url, t=30):
    cp = subprocess.run(["curl", "-s", "-m", str(t), "-w", "\n__H__%{http_code}", url],
                        capture_output=True, text=True, timeout=t + 10)
    b, c = cp.stdout, ""
    if "__H__" in b:
        b, c = b.rsplit("__H__", 1)
    try:
        return c.strip(), json.loads(b)
    except Exception:
        return c.strip(), None


def main() -> int:
    R = {"ticket": "SIGNAL-NOW-01-WORK", "step": "10 重载后 API 字段核对（只读）",
         "at_utc": utc(), "api": API}

    c, j = g(f"{API}/api/v1/signals?scope=current&limit=500")
    j = j or {}
    q = j.get("quality") or {}
    eb = q.get("excluded_breakdown") or {}
    it = j.get("items") or []
    R["current_scope"] = {
        "http": c, "total": j.get("total"), "items_n": len(it),
        "excluded_n": q.get("excluded_n"), "by_reason": eb.get("by_reason"),
        "by_reason_sum": sum((eb.get("by_reason") or {}).values()) if eb.get("by_reason") else None,
        "effective_floor_ms": q.get("effective_floor_ms"),
        "live_start_ms": q.get("live_start_ms"),
        "paper_epoch_ms": q.get("paper_epoch_ms"),
        "origins_in_current": dict(Counter(x.get("origin") for x in it)),
        "strategy_version": j.get("strategy_version"), "rule_version": j.get("rule_version"),
        "param_hash": j.get("param_hash"),
    }

    c2, j2 = g(f"{API}/api/v1/signals?scope=all&origin=historical_research&limit=500")
    j2 = j2 or {}
    it2 = j2.get("items") or []
    R["research_scope"] = {
        "http": c2, "total": j2.get("total"), "has_more": j2.get("has_more"),
        "items_n": len(it2),
        "origin": dict(Counter(x.get("origin") for x in it2)),
        "executable_state": dict(Counter(x.get("executable_state") for x in it2)),
        "role": dict(Counter(x.get("role") for x in it2)),
        "actionable": dict(Counter(x.get("actionable") for x in it2)),
        "research_approved": dict(Counter((x.get("research") or {}).get("approved")
                                          for x in it2)),
        "available_at_null_n": sum(1 for x in it2 if x.get("available_at") is None),
        "arm": dict(Counter(x.get("arm") for x in it2)),
        "position_multiplier": dict(Counter(str(x.get("position_multiplier")) for x in it2)),
        "actionable_reason_unique_n": len({x.get("actionable_reason") for x in it2}),
        "actionable_reason_sample": (it2[0].get("actionable_reason") if it2 else None),
        "uid_sample": [x.get("signal_id") for x in it2[:3]],
        # 字段名事实（本单实测，供后续单直接引用，避免再次用错名）
        "field_name_facts": {
            "可执行态字段名": "executable_state（不是 execution_state）",
            "批准字段名": "research.approved（顶层没有 approved）",
            "研究段字段": list((it2[0].get("research") or {}).keys()) if it2 else [],
        },
    }

    # 断言
    A = []
    def chk(name, ok, detail):
        A.append({"assert": name, "ok": bool(ok), "detail": detail})

    chk("current 区 0 条", R["current_scope"]["total"] == 0,
        f"total={R['current_scope']['total']}")
    chk("current 区无研究行污染", not R["current_scope"]["origins_in_current"],
        str(R["current_scope"]["origins_in_current"]))
    chk("excluded_n = 289", R["current_scope"]["excluded_n"] == 289,
        f"excluded_n={R['current_scope']['excluded_n']}")
    chk("by_reason 各项之和 ≥ excluded_n（多因命中，不冒充唯一原因）",
        (R["current_scope"]["by_reason_sum"] or 0) >= (R["current_scope"]["excluded_n"] or 0),
        f"sum={R['current_scope']['by_reason_sum']} excluded_n={R['current_scope']['excluded_n']}")
    chk("effective_floor_ms = live_start_ms = 1792748844240",
        R["current_scope"]["effective_floor_ms"] == 1792748844240
        and R["current_scope"]["live_start_ms"] == 1792748844240,
        f"floor={R['current_scope']['effective_floor_ms']} live={R['current_scope']['live_start_ms']}")
    chk("研究行 289 条且 has_more=false", R["research_scope"]["total"] == 289
        and R["research_scope"]["has_more"] is False,
        f"total={R['research_scope']['total']} has_more={R['research_scope']['has_more']}")
    chk("研究行 origin 全为 historical_research",
        R["research_scope"]["origin"] == {"historical_research": 289},
        str(R["research_scope"]["origin"]))
    chk("研究行可执行态全为 historical_not_executable",
        R["research_scope"]["executable_state"] == {"historical_not_executable": 289},
        str(R["research_scope"]["executable_state"]))
    chk("研究行 role 全为 research_only",
        R["research_scope"]["role"] == {"research_only": 289},
        str(R["research_scope"]["role"]))
    chk("研究行 actionable 全为 false",
        R["research_scope"]["actionable"] == {False: 289},
        str(R["research_scope"]["actionable"]))
    chk("研究行 approved 全为 false",
        R["research_scope"]["research_approved"] == {False: 289},
        str(R["research_scope"]["research_approved"]))
    chk("研究行 available_at 全为 null（不伪填）",
        R["research_scope"]["available_at_null_n"] == 289,
        f"null={R['research_scope']['available_at_null_n']}/289")

    R["assertions"] = A
    R["passed"] = sum(1 for a in A if a["ok"])
    R["total_assertions"] = len(A)

    OUT.mkdir(parents=True, exist_ok=True)
    p = OUT / "10_API字段核对.json"
    p.write_text(json.dumps(R, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    for a in A:
        print(("  [PASS] " if a["ok"] else "  [FAIL] ") + a["assert"] + "  " + a["detail"])
    print(f"\n合计 {R['passed']}/{R['total_assertions']}")
    print(f"产物：{p}")
    return 0 if R["passed"] == R["total_assertions"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
