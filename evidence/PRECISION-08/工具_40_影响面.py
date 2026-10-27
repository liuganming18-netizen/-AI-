#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PRECISION-08 工具 40：R6 / R7 对既有 93 项门禁的影响面（影子，不改生产代码）。

四个影子（全部进程内子类 + 运行时打补丁，进程退出即失效）：
  S0  基线：什么都不加                      → 期望 93 / 0
  S1  R6 挂在 _guard（取整前，P06 做法）     → 期望 91 / 2
  S2  R6 挂在取整之后（R5 之后，P07 建议）   → 期望 92 / 1
  S3  R7（OHLC 硬边界）                     → **代理测**，见下

R7 为什么只能代理测
──────────────────
`FillAdapter.quote_entry/quote_exit`（fill.py:179-183）不收 bar_low/bar_high，
适配器在结构上拿不到区间。本工具改用另一条路：**在 `prep_symbol` 上挂钩子**，
把每个 run 的 (open, high, low) 记下来，再让影子适配器按「raw_price 匹配 open」
去查区间；查不到时退回「全部区间并集」（最宽松）⇒ 得到的是 R7 影响的
**下界**（真实落地后只会更严，不会更松）。这条必须在报告里写明，不能当成实测。

输出：原始输出/pytest_既有93项_<tag>.txt × 4 / 影响面.json / 影响面原始输出.txt
"""
from __future__ import annotations

import json
import os
import sys
import time
from decimal import Decimal
from typing import Dict, List, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "策略"))

import pytest                                                              # noqa: E402
from WyckoffMaster.trading_core.fill import (                              # noqa: E402
    FillRejected, SimulatedBarFillAdapter,
)

TARGETS = [os.path.join(ROOT, "策略/WyckoffMaster/event_trend_v0_1/tests/"),
           os.path.join(ROOT, "策略/WyckoffMaster/test_work02_core_contract.py")]

D = Decimal
# repr(float(raw)) -> [(low, high), ...]，由 prep_symbol 钩子填充
RANGES: Dict[str, List[Tuple[float, float]]] = {}


# ────────────────────────── 影子：R6 ──────────────────────────
class ShadowR6Before(SimulatedBarFillAdapter):
    """S1：R6 在 _guard（取整前）。与 P06 等价。"""

    def _guard(self, *, raw_price, price_step, kind):
        super()._guard(raw_price=raw_price, price_step=price_step, kind=kind)
        raw, step = D(str(raw_price)), D(str(price_step))
        if step > raw:
            raise FillRejected("price_step_coarser_than_price",
                               {"raw_price": str(raw), "price_step": str(step),
                                "stage": kind})


class ShadowR6After(SimulatedBarFillAdapter):
    """S2：R6 在取整之后（R5 之后）。"""

    def _r6(self, raw_price, price_step, kind):
        raw, step = D(str(raw_price)), D(str(price_step))
        if step > raw:
            raise FillRejected("price_step_coarser_than_price",
                               {"raw_price": str(raw), "price_step": str(step),
                                "stage": kind + "_post_round"})

    def quote_entry(self, *, raw_price, direction, price_step, stop_price=None):
        q = super().quote_entry(raw_price=raw_price, direction=direction,
                                price_step=price_step, stop_price=stop_price)
        self._r6(raw_price, price_step, "entry")
        return q

    def quote_exit(self, *, target_px, direction, price_step, reason):
        px = super().quote_exit(target_px=target_px, direction=direction,
                                price_step=price_step, reason=reason)
        self._r6(target_px, price_step, "exit")
        return px


# ────────────────────────── 影子：R7（代理） ──────────────────────────
class _R7Mixin:
    """按 bar 区间判 fill。区间来源：raw 命中 open ⇒ 该 open 的区间集合；
    否则退回全部区间并集（最宽松）。Decimal 比较，闭区间。"""

    def _r7(self, fill, raw, kind):
        if not RANGES:
            return
        key = repr(float(raw))
        rs = RANGES.get(key)
        scope = "matched_open"
        if not rs:
            rs = [r for v in RANGES.values() for r in v]
            scope = "union_all"
        lo = min(r[0] for r in rs)
        hi = max(r[1] for r in rs)
        f, lo_d, hi_d = D(str(fill)), D(str(lo)), D(str(hi))
        if lo_d <= f <= hi_d:
            return
        raise FillRejected("fill_outside_bar_range", {
            "fill_price": str(f), "low": str(lo_d), "high": str(hi_d),
            "over": str(max(D(0), f - hi_d) if f > hi_d else max(D(0), lo_d - f)),
            "raw_price": str(raw), "stage": kind, "range_scope": scope})


class ShadowR7(_R7Mixin, SimulatedBarFillAdapter):
    def quote_entry(self, *, raw_price, direction, price_step, stop_price=None):
        q = super().quote_entry(raw_price=raw_price, direction=direction,
                                price_step=price_step, stop_price=stop_price)
        self._r7(q.entry_fill, raw_price, "entry")
        return q

    def quote_exit(self, *, target_px, direction, price_step, reason):
        px = super().quote_exit(target_px=target_px, direction=direction,
                                price_step=price_step, reason=reason)
        self._r7(px, target_px, "exit")
        return px


class ShadowR6AfterPlusR7(_R7Mixin, ShadowR6After):
    def quote_entry(self, *, raw_price, direction, price_step, stop_price=None):
        q = ShadowR6After.quote_entry(self, raw_price=raw_price, direction=direction,
                                      price_step=price_step, stop_price=stop_price)
        self._r7(q.entry_fill, raw_price, "entry")
        return q

    def quote_exit(self, *, target_px, direction, price_step, reason):
        px = ShadowR6After.quote_exit(self, target_px=target_px, direction=direction,
                                      price_step=price_step, reason=reason)
        self._r7(px, target_px, "exit")
        return px


# ────────────────────────── 打补丁 ──────────────────────────
def make_plugin(cls, hook_ranges: bool):
    class PatchPlugin:
        def pytest_sessionstart(self, session):
            import WyckoffMaster.event_trend_v0_1.engine_adapter as EA
            EA.SimulatedBarFillAdapter = cls
            if hook_ranges:
                import WyckoffMaster.trading_core.engine as E
                if not hasattr(E, "_ORIG_PREP"):
                    E._ORIG_PREP = E.prep_symbol
                orig = E._ORIG_PREP

                def prep_hooked(sym, sig_bars, bar_bars, *a, **kw):
                    out = orig(sym, sig_bars, bar_bars, *a, **kw)
                    try:
                        for o, h, l in zip(out["open"], out["high"], out["low"]):
                            RANGES.setdefault(repr(float(o)), []).append(
                                (float(l), float(h)))
                    except Exception:
                        pass
                    return out

                E.prep_symbol = prep_hooked

        def pytest_runtest_setup(self, item):
            RANGES.clear()
            m = getattr(item, "module", None)
            if m is not None and hasattr(m, "SimulatedBarFillAdapter"):
                m.SimulatedBarFillAdapter = cls
    return PatchPlugin()


class ResultCollector:
    def __init__(self):
        self.failed, self.passed = [], 0

    def pytest_runtest_logreport(self, report):
        if report.when != "call":
            return
        if report.passed:
            self.passed += 1
        elif report.failed:
            self.failed.append({"nodeid": report.nodeid.split("::")[-1],
                                "module": report.nodeid.split("::")[0].split("/")[-1],
                                "longrepr": str(report.longrepr)[-1200:]})
        elif report.skipped:
            self.failed.append({"nodeid": report.nodeid.split("::")[-1],
                                "module": report.nodeid.split("::")[0].split("/")[-1],
                                "xfail": True, "longrepr": "skipped/xfail"})


def run_shadow(cls, tag: str, hook_ranges: bool) -> Dict:
    outdir = os.path.join(HERE, "原始输出")
    os.makedirs(outdir, exist_ok=True)
    log = os.path.join(outdir, f"pytest_既有93项_{tag}.txt")
    col = ResultCollector()
    args = ["-q", "--basetemp=" + os.path.join(HERE, "tmp_pytest"),
            "-p", "no:cacheprovider"] + TARGETS
    with open(log, "w", encoding="utf-8") as f:
        old = sys.stdout
        sys.stdout = f
        try:
            code = pytest.main(args, plugins=[make_plugin(cls, hook_ranges), col])
        finally:
            sys.stdout = old
    return {"tag": tag, "class": cls.__name__, "exit_code": int(code),
            "passed": col.passed, "failed_count": len(col.failed),
            "failed": col.failed, "log": os.path.relpath(log, ROOT)}


def static_flat_bar_count() -> Dict[str, int]:
    """不依赖运行时的旁证：测试里有多少处用了默认 flat bar 构造。"""
    import re
    files = []
    for p in (os.path.join(ROOT, "策略/WyckoffMaster/event_trend_v0_1/tests"),
              os.path.join(ROOT, "策略/WyckoffMaster")):
        for fn in os.listdir(p):
            if fn.startswith("test_") and fn.endswith(".py"):
                files.append(os.path.join(p, fn))
    n_entry_scenario = n_bars_15m = n_bars_with_hl = 0
    for fp in files:
        src = open(fp, encoding="utf-8").read()
        n_entry_scenario += len(re.findall(r"_entry_scenario\(", src))
        for m in re.finditer(r"_bars_15m\(([^)]*)\)", src):
            n_bars_15m += 1
            if "high=" in m.group(1) or "low=" in m.group(1):
                n_bars_with_hl += 1
    return {"files": len(files), "_entry_scenario_calls": n_entry_scenario,
            "_bars_15m_calls": n_bars_15m,
            "_bars_15m_calls_with_explicit_high_or_low": n_bars_with_hl,
            "_bars_15m_calls_default_flat": n_bars_15m - n_bars_with_hl}


def main() -> int:
    t0 = time.time()
    s0 = run_shadow(SimulatedBarFillAdapter, "S0_基线", False)
    s1 = run_shadow(ShadowR6Before, "S1_R6前置", False)
    s2 = run_shadow(ShadowR6After, "S2_R6后置", False)
    s3 = run_shadow(ShadowR7, "S3_R7代理", True)
    s4 = run_shadow(ShadowR6AfterPlusR7, "S4_R6后置+R7", True)

    doc = {
        "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "elapsed_sec": round(time.time() - t0, 1),
        "targets": [os.path.relpath(t, ROOT) for t in TARGETS],
        "S0_baseline": s0, "S1_R6_before": s1, "S2_R6_after": s2,
        "S3_R7_proxy": s3, "S4_R6_after_plus_R7": s4,
        "R7_proxy_caveat": (
            "FillAdapter 协议不收 bar_low/bar_high（fill.py:179-183），"
            "影子只能靠 prep_symbol 钩子记区间、按 raw==open 匹配；"
            "匹配不到时退回「全部区间并集」（最宽松）。"
            "因此 S3/S4 的失败数是 R7 影响的**下界**，不是实测值。"),
        "static_flat_bar": static_flat_bar_count(),
    }
    with open(os.path.join(HERE, "影响面.json"), "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)

    L = ["PRECISION-08 / 工具 40 · R6 / R7 对既有 93 项的影响面（影子）", "=" * 78, ""]
    for key, s in (("S0 基线（无闸门）", s0), ("S1 R6 前置（_guard）", s1),
                   ("S2 R6 后置（取整后）", s2), ("S3 R7 代理", s3),
                   ("S4 R6 后置 + R7 代理", s4)):
        L.append(f"{key:<24} {s['passed']:>3} passed / {s['failed_count']:>2} failed")
        for f_ in s["failed"]:
            tag = "xfail" if f_.get("xfail") else "✗"
            L.append(f"     {tag} [{f_['module']}] {f_['nodeid']}")
        L.append("")
    st = doc["static_flat_bar"]
    L += ["静态旁证（不依赖运行时）：",
          f"  扫描测试文件 {st['files']} 个；_entry_scenario 调用 {st['_entry_scenario_calls']} 处；",
          f"  _bars_15m 调用 {st['_bars_15m_calls']} 处，其中显式给 high/low 的 "
          f"{st['_bars_15m_calls_with_explicit_high_or_low']} 处，"
          f"默认 flat 的 {st['_bars_15m_calls_default_flat']} 处。",
          "",
          "R7 的测法限制：",
          "  " + doc["R7_proxy_caveat"], ""]
    txt = "\n".join(L)
    with open(os.path.join(HERE, "影响面原始输出.txt"), "w", encoding="utf-8") as f:
        f.write(txt + "\n")
    print(txt)

    ok = (s0["passed"] == 93 and s0["failed_count"] == 0
          and s1["passed"] == 91 and s1["failed_count"] == 2
          and s2["passed"] == 92 and s2["failed_count"] == 1)
    print(f"自检：S0=93/0、S1=91/2（与 P06 一致）、S2=92/1（与 P07 一致）→ "
          f"{'通过' if ok else '失败'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
