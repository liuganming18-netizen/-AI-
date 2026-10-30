#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PR2-WO-02 修改前后对照探针（只打印、不断言，不进交付链路）

同一份夹具，分别在**修复后工作树**与**基线工作树**跑一次，把关键结论打出来对比。
只使用两个版本都存在的公开入口（`persist_candidates` / `route_pending_orders` /
直接 SQL），因此在基线树里也能跑。

跑法：cd 框架/backend && /usr/bin/python3 tests/pr2_wo_02_probe.py
"""
import json
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parent
for p in (str(BACKEND), str(BACKEND.parents[1])):
    if p not in sys.path:
        sys.path.insert(0, p)
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import live_api as API
import live_store as L
import wyckoff_factor_resonance as WFR
import unified_runtime as UR
import test_pr2_wo_02_fail_closed as T


class _Case(T.FailClosedBase):
    def runTest(self):
        pass


def new_case():
    c = _Case()
    c.setUp()
    return c


def line(tag, value):
    print(f"  {tag:<28} {value}")


def case_resonance_exception():
    """A：共振函数抛异常。"""
    c = new_case()
    r = UR.SignalPersistenceAndRouter(arm=T.ARM)
    orig = WFR.evaluate_resonance
    WFR.evaluate_resonance = lambda **kw: (_ for _ in ()).throw(RuntimeError("down"))
    try:
        r.persist_candidates([T.make_candidate(candidate_id="probe_exc")],
                             origin="live", now_ms=T.T0 + 1000)
    finally:
        WFR.evaluate_resonance = orig
    row = c.live_row("probe_exc")
    p = json.loads(row["payload_json"])
    res = r.route_pending_orders(now_ms=T.T0 + 60_000, origin="live")
    line("executable_state", row["executable_state"])
    line("payload.resonance_score", p.get("resonance_score"))
    line("payload.position_multiplier", p.get("position_multiplier"))
    line("payload.approved", p.get("approved", "(无此键 ⇒ COALESCE 取 1)"))
    line("挂单数", c.pending_count("probe_exc"))
    line("路由 blocked_reason", res.get("blocked_reason"))
    c.tearDown()


def case_test_uid():
    """C：测试 UID 进入扫描范围。"""
    c = new_case()
    c.insert_signal_row("sig_ui_test_probe", origin="live")
    r = UR.SignalPersistenceAndRouter(arm=T.ARM)
    res = r.route_pending_orders(now_ms=T.T0 + 60_000, origin="live")
    line("registered", res.get("registered"))
    line("挂单数", c.pending_count("sig_ui_test_probe"))
    c.tearDown()


def case_historical_no_origin():
    """C：历史来源 + 调用方没传 origin（旧实现 = 全库扫描）。"""
    c = new_case()
    c.insert_signal_row("probe_hist", origin="historical_research")
    r = UR.SignalPersistenceAndRouter(arm=T.ARM)
    res = r.route_pending_orders(now_ms=T.T0 + 60_000)      # 故意不传 origin
    line("registered", res.get("registered"))
    line("blocked_reason", res.get("blocked_reason"))
    line("挂单数", c.pending_count("probe_hist"))
    c.tearDown()


def case_watermark_unavailable():
    """D：水位不可用。"""
    c = new_case()
    c.insert_signal_row("probe_wm", origin="live")
    r = UR.SignalPersistenceAndRouter(arm=T.ARM)
    orig = L.effective_floor_ms
    L.effective_floor_ms = lambda: (_ for _ in ()).throw(RuntimeError("db_unreadable"))
    try:
        res = r.route_pending_orders(now_ms=T.T0 + 60_000, origin="live")
        api_res = API.live_signals({"scope": ["current"]})
    except Exception as exc:                                  # noqa: BLE001
        line("路由/接口结果", f"抛异常 {type(exc).__name__}: {exc}")
        c.tearDown()
        return
    finally:
        L.effective_floor_ms = orig
    line("路由 blocked_reason", res.get("blocked_reason"))
    line("路由 scanned", res.get("scanned"))
    line("接口 quality.status", (api_res.get("quality") or {}).get("status"))
    line("接口 quality.reason", (api_res.get("quality") or {}).get("reason"))
    c.tearDown()


def case_illegal_direction():
    """第 10 条：方向缺失。"""
    c = new_case()
    c.insert_signal_row("probe_nodir", origin="live", direction=None)
    r = UR.SignalPersistenceAndRouter(arm=T.ARM)
    res = r.route_pending_orders(now_ms=T.T0 + 60_000, origin="live")
    con = sqlite3.connect(str(c.iso_ledger_dir / f"paper_{T.ARM}.sqlite3"))
    try:
        row = con.execute("SELECT direction, status FROM paper_pending WHERE signal_uid=?",
                          ("probe_nodir",)).fetchone()
    finally:
        con.close()
    line("registered", res.get("registered"))
    line("挂单(direction,status)", row if row else "无挂单")
    c.tearDown()


if __name__ == "__main__":
    print("=" * 62)
    for fn, title in ((case_resonance_exception, "A 共振抛异常"),
                      (case_test_uid, "C 测试 UID"),
                      (case_historical_no_origin, "C 历史来源 + 未传 origin"),
                      (case_watermark_unavailable, "D 水位不可用"),
                      (case_illegal_direction, "方向缺失")):
        print(f"\n[{title}]")
        try:
            fn()
        except Exception as exc:                              # noqa: BLE001
            print(f"  探针自身异常：{type(exc).__name__}: {exc}")
    print("=" * 62)
