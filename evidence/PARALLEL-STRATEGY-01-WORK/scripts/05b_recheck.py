#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""05b · bootstrap 完成后的复查（补 05 里 `universe_526` 那条 FAIL）。

为什么需要单独一步：`05_controlled_reload.py` 在拉起后只等到
`runtime_identity.status == running` 就立刻读状态，此时 `cycle_count=0`、
`universe=0`、`watermark=None` —— **bootstrap 还没跑完**。
于是 `universe_526` 必然 FAIL。这不是「加载失败」，是**取样太早**。

本脚本只读，不重启任何进程；等 `cycle_count` 增长且 `universe` 稳定后
重新评估那一条，并把结果**追加**写进 `out/05_reload.json` 的
`after_bootstrap` 字段（保留原 `checks` 不动，避免覆盖「取样太早」这个事实）。

用法：05b_recheck.py [--wait 600]
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

PROJ = Path("/Users/boos/Desktop/威科夫因子共振趋势")
OUT = PROJ / "evidence" / "PARALLEL-STRATEGY-01-WORK" / "out"
BACKEND = PROJ / "框架" / "backend"
STATUS = BACKEND / "locks" / "paper_runtime_3a_status.json"


def utc(ts=None):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ",
                         time.gmtime(time.time() if ts is None else float(ts)))


def rt_status() -> dict:
    d = json.loads(STATUS.read_text(encoding="utf-8"))
    ri = d.get("runtime_identity") or {}
    se = d.get("signal_engine") or {}
    od = d.get("order_processing") or {}
    ed = d.get("execution_daemon") or {}
    return {"file_mtime_utc": utc(STATUS.stat().st_mtime),
            "pid": ri.get("pid"), "status": ri.get("status"),
            "overall_health": ri.get("overall_health"),
            "started_at_utc": (utc(ri["started_at"] / 1000) if ri.get("started_at") else None),
            "cycle_count": ri.get("cycle_count"),
            "universe": se.get("universe"),
            "watermark_1h_utc": (utc(se["watermark_1h"] / 1000)
                                 if se.get("watermark_1h") else None),
            "watermark_4h_utc": (utc(se["watermark_4h"] / 1000)
                                 if se.get("watermark_4h") else None),
            "pending_in_db": od.get("current_pending_in_db"),
            "allow_new_entries": (od.get("new_entry_gate") or {}).get("allow_new_entries"),
            "open_positions": ed.get("open_positions"),
            "pending_matched_total": ed.get("pending_matched_total"),
            "trailing_updates_total": ed.get("trailing_updates_total"),
            "exits_triggered_total": ed.get("exits_triggered_total")}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--wait", type=int, default=600)
    a = ap.parse_args()
    deadline = time.time() + a.wait
    seen = []
    st = rt_status()
    seen.append(st)
    print(f"[起始] cycle={st['cycle_count']} universe={st['universe']} "
          f"wm1h={st['watermark_1h_utc']} pid={st['pid']}")
    while time.time() < deadline:
        if (st.get("cycle_count") or 0) >= 3 and (st.get("universe") or 0) > 0 \
                and st.get("watermark_1h_utc"):
            break
        time.sleep(10)
        st = rt_status()
        seen.append(st)
        print(f"[等] cycle={st['cycle_count']} universe={st['universe']} "
              f"wm1h={st['watermark_1h_utc']} wm4h={st['watermark_4h_utc']}")

    p = OUT / "05_reload.json"
    R = json.loads(p.read_text(encoding="utf-8"))
    checks = {
        "universe_526": st.get("universe") == 526,
        "cycle_count_grew_from_zero": (st.get("cycle_count") or 0) >= 3,
        "watermark_present": bool(st.get("watermark_1h_utc")),
        "runtime_status_running": st.get("status") == "running",
        "runtime_health_healthy": st.get("overall_health") in ("healthy", "ok"),
        "allow_new_entries_still_true": st.get("allow_new_entries") is True,
    }
    R["after_bootstrap"] = {"at_utc": utc(), "status": st,
                            "checks": checks, "all_pass": all(checks.values()),
                            "samples": len(seen),
                            "note": ("05 的 `universe_526` 是在 bootstrap 完成前取的样 ⇒ "
                                     "必然为 0。本条为 bootstrap 完成后的复查，"
                                     "不覆盖 05 原始 checks。")}
    p.write_text(json.dumps(R, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"written {p}")
    for k, v in checks.items():
        print(f"  [{'PASS' if v else 'FAIL'}] {k}")
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
