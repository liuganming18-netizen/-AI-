#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PRECISION-08 工具 50：R6 / R7 端到端验收清单（**逐条实测，未实现就写待实现**）。

三层严格分开，不混称通过：
  L1 离线公式     —— 只有算术，没有引擎
  L2 影子         —— 进程内注入规则，生产文件未改
  L3 真实端到端   —— 生产代码里真有闸门、真跑一次 run

本工具只做 L3 的「现在能不能验」判定：
  · 能验的，真跑，给 pass / fail；
  · 不能验的（闸门还不存在），写 `待实现` + 缺哪一行代码，**不算通过**。

输出：验收清单.json / 表3_端到端验收清单.csv / 验收清单原始输出.txt
"""
from __future__ import annotations

import json
import os
import sys
import time
from decimal import Decimal
from typing import Any, Dict, List

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
for p in (ROOT, os.path.join(ROOT, "策略")):
    if p not in sys.path:
        sys.path.insert(0, p)

from WyckoffMaster.event_trend_v0_1.engine_adapter import (                  # noqa: E402
    run_event_trend_backtest,
)
from WyckoffMaster.event_trend_v0_1.tests.test_fill_precision import (      # noqa: E402
    _entry_scenario, _spec,
)

D = Decimal


def _run(open_px=100.0, stop=99.0, *, high=None, low=None, spec=None,
         price_step=None):
    cand, df_1h, df_15m = _entry_scenario(open_px, stop)
    if high is not None or low is not None:
        df_15m["high"] = high if high is not None else df_15m["high"]
        df_15m["low"] = low if low is not None else df_15m["low"]
    return run_event_trend_backtest("SOLUSDT", df_1h, df_15m, [cand], [],
                                    variant="A", instrument_spec=spec,
                                    price_step=price_step)


def probe_e1_entry_price_reject() -> Dict[str, Any]:
    """E1 入场「价格」精度拒绝 ⇒ 无成交、无持仓、写 invalid_reasons、run_valid=False。

    现状：**构造不出来**。入场侧唯一能让适配器抛 FillRejected 的输入是
    raw<=0 / step<=0 / 取整后<=0，而前两者在 engine_adapter 入口就被拦，
    买侧取整后恒 > 0。所以这条必须等 R6/R7 落地后才能验 ⇒ 待实现。
    同时 engine.py:452-462 的 except 分支**没有** invalid_reasons.append，
    落地后若只加闸门不改这里，会得到「0 成交 + run_valid=True」。
    """
    return {"id": "E1", "layer": "L3", "case": "入场价格精度拒绝的完整后置状态",
            "status": "待实现",
            "why": "入场侧目前无法产生 FillRejected（raw/step 非法在入口即拦，"
                   "买侧取整后恒>0）；需 R6 或 R7 先落地",
            "gap": "engine.py:452-462 except FillRejected 分支无 "
                   "invalid_reasons.append ⇒ 落地后会「0 成交但 run_valid=True」",
            "must_assert": "trades_count=0 ∧ open_positions_remaining=[] ∧ "
                           "invalid_reasons 非空 ∧ run_valid=False"}


def probe_e2_entry_qty_reject() -> Dict[str, Any]:
    """E2 入场「数量」精度拒绝（**现在就可达**）⇒ 同样要求写 invalid_reasons。

    这是 M3 的现成反例：不需要新闸门，今天就能跑出「0 成交 + run_valid=True」。
    """
    spec = _spec(symbol="SOLUSDT", price_step="0.01", qty_step="0.01",
                 min_qty="0.01", min_notional="1000000")
    res = _run(spec=spec)
    p = res["precision"]
    got = {"trades_count": res["engine_result"]["trades_count"],
           "open_positions": len(res["engine_result"]["open_positions_remaining"]),
           "rejects_count": p["rejects_count"],
           "invalid_reasons": p["invalid_reasons"],
           "run_valid": p["run_valid"],
           "rejected_reasons": [r["reason"]
                                for r in res["engine_result"]["rejected_orders"]]}
    ok = (got["trades_count"] == 0 and got["open_positions"] == 0
          and got["invalid_reasons"] and got["run_valid"] is False)
    return {"id": "E2", "layer": "L3",
            "case": "入场数量精度拒绝的完整后置状态",
            "status": "pass" if ok else "fail",
            "why": ("现状：0 成交、无持仓，但 invalid_reasons 为空、run_valid=True "
                    "⇒ 与「资格约定」不符（拒绝发生了，运行却算有效）"
                    if not ok else "符合"),
            "gap": "engine.py:537-567 两处数量拒绝同样没有 invalid_reasons.append",
            "must_assert": "trades_count=0 ∧ open_positions=[] ∧ "
                           "invalid_reasons 含数量原因 ∧ run_valid=False",
            "observed": got}


def probe_e3_exit_reject() -> Dict[str, Any]:
    """E3 出场拒绝 ⇒ 仓位保留、账本恒等、run_valid=False、不伪造平仓。"""
    res = _run(open_px=0.004, stop=0.003, price_step="0.01")
    eng, p = res["engine_result"], res["precision"]
    got = {"trades_count": eng["trades_count"],
           "open_positions": len(eng["open_positions_remaining"]),
           "unclosed": p["unclosed_positions"],
           "invalid_reasons": p["invalid_reasons"],
           "run_valid": p["run_valid"],
           "cash_identity_ok": eng["reconcile"]["cash_identity_ok"]}
    ok = (got["trades_count"] == 0 and got["open_positions"] == 1
          and got["run_valid"] is False and got["cash_identity_ok"] is True
          and any("exit" in r for r in got["invalid_reasons"]))
    return {"id": "E3", "layer": "L3", "case": "出场拒绝后的持仓与账本状态",
            "status": "pass" if ok else "fail",
            "why": "现有行为已满足（93 项里 test_exit_failure_* 覆盖）",
            "gap": "—", "must_assert": "trades_count=0 ∧ 仓位仍在 ∧ "
                                       "cash_identity_ok ∧ run_valid=False",
            "observed": got}


def probe_e4_normal() -> Dict[str, Any]:
    """E4 正常入场 / 正常出场不被新闸门破坏（有 headroom 的真实形状 bar）。"""
    spec = _spec(symbol="SOLUSDT", price_step="0.01", qty_step="0.01",
                 min_qty="0.01", min_notional="5")
    res = _run(high=100.5, low=99.5, spec=spec)
    eng, p = res["engine_result"], res["precision"]
    tr = eng["trades"]
    ok = bool(tr) and p["run_valid"] is True
    got = {"trades_count": eng["trades_count"],
           "entry": (tr[0]["entry_price"] if tr else None),
           "exit": (tr[0]["exit_price"] if tr else None),
           "run_valid": p["run_valid"],
           "bar": [99.5, 100.5]}
    if tr:
        e, x = D(str(tr[0]["entry_price"])), D(str(tr[0]["exit_price"]))
        ok = ok and D("99.5") <= e <= D("100.5") and D("99.5") <= x <= D("100.5")
    return {"id": "E4", "layer": "L3", "case": "正常交易不受破坏",
            "status": "pass" if ok else "fail",
            "why": "成交价落在 [99.5, 100.5] 内，A 与 B 都放行",
            "gap": "—", "must_assert": "有成交 ∧ 入场/出场都 ∈ [low, high] ∧ "
                                       "run_valid=True",
            "observed": got}



# ══════════════ 前瞻验证：夹具修正后在「R6 后置 + R7」下是否转绿 ══════════════
def _load_shadow():
    """按路径加载 工具_40 里的影子类（不复制实现，避免两份定义漂移）。"""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "p08_shadow", os.path.join(HERE, "工具_40_影响面.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run_under_shadow(open_px: float, stop: float, high=None, low=None,
                      close=None, price_step="0.01"):
    """在「R6 后置 + R7」影子下跑一次（打完补丁立刻还原）。"""
    import WyckoffMaster.event_trend_v0_1.engine_adapter as EA
    import WyckoffMaster.trading_core.engine as E
    sh = _load_shadow()
    orig_adapter, orig_prep = EA.SimulatedBarFillAdapter, E.prep_symbol

    def hooked(sym, sig_bars, bar_bars, *a, **kw):
        out = orig_prep(sym, sig_bars, bar_bars, *a, **kw)
        for o, h, l in zip(out["open"], out["high"], out["low"]):
            sh.RANGES.setdefault(repr(float(o)), []).append((float(l), float(h)))
        return out

    try:
        sh.RANGES.clear()
        E.prep_symbol = hooked
        EA.SimulatedBarFillAdapter = sh.ShadowR6AfterPlusR7
        cand, df_1h, df_15m = _entry_scenario(open_px, stop)
        if high is not None or low is not None:
            df_15m["high"] = high if high is not None else df_15m["high"]
            df_15m["low"] = low if low is not None else df_15m["low"]
        if close is not None:
            df_15m["close"] = close
        return run_event_trend_backtest("SOLUSDT", df_1h, df_15m, [cand], [],
                                        variant="A", price_step=price_step)
    finally:
        EA.SimulatedBarFillAdapter = orig_adapter
        E.prep_symbol = orig_prep
        sh.RANGES.clear()


def probe_e8_fixture_fix_minimal() -> Dict[str, Any]:
    """E8 「出场失败」用例在 R6+R7 下的正确夹具：入场有 headroom，出场仍 sub-tick。

    只把入场价从 0.004 抬到 0.0495 是**不够的**（实测 E9：flat bar 上买向
    fill 恒 > high）。必须同时把 bar 形状改成「入场够得着、出场够不着」：
      open 0.0495 / high 0.0501 / low 0.0005 / close 0.0005，stop 0.003
    ⇒ 入场 fill 0.05 ∈ [0.0005, 0.0501] 放行；
       intrabar 与 EOD 的卖出目标都 < step ⇒ floor 成 0 ⇒ R5 拒；
       ⇒ 0 成交、仓位仍在、run_valid=False，**用例原意完整保住**。
    """
    res = _run_under_shadow(0.0495, 0.003, high=0.0501, low=0.0005, close=0.0005)
    eng, p = res["engine_result"], res["precision"]
    got = {"trades_count": eng["trades_count"],
           "open_positions": len(eng["open_positions_remaining"]),
           "invalid_reasons": p["invalid_reasons"],
           "run_valid": p["run_valid"],
           "rejects": [r.get("reason") for r in p.get("rejects", [])]
           if isinstance(p.get("rejects"), list) else p.get("rejects_count")}
    ok = (got["trades_count"] == 0 and got["open_positions"] == 1
          and got["run_valid"] is False)
    return {"id": "E8", "layer": "L2→L3 前瞻",
            "case": "夹具修正（入场 0.0495）后在 R6+R7 下保住用例原意",
            "status": "pass" if ok else "fail",
            "why": "入场 fill 0.05 ∈ [0.0005, 0.0501] 放行；intrabar/EOD 卖出目标均 <step → R5 拒",
            "gap": "—",
            "must_assert": "0 成交 ∧ 仓位仍在 ∧ run_valid=False",
            "observed": got}


def probe_e9_fixture_fix_naive() -> Dict[str, Any]:
    """E9 flat bar（O==H）上**任何**买向入场都会被 R7 拒 —— 与价格高低无关。

    `_entry_scenario` 的 15m bar 是 flat（high=low=open）。买向成交价
    = ceil(open×(1+3bps)) 恒 > open = high ⇒ 越界量恒 > 0。所以
    「把入场价从 0.004 抬到 0.05」（P07 的 M4 写法）在 R7 落地后**仍然红**：
    价格换了，flat bar 没换。**M4 一条改不完，必须同时改夹具形状。**
    """
    res = _run_under_shadow(0.05, 0.003)
    eng = res["engine_result"]
    got = {"trades_count": eng["trades_count"],
           "open_positions": len(eng["open_positions_remaining"]),
           "entry_fill_would_be": "0.06", "bar_high": "0.05"}
    ok = got["open_positions"] == 0        # 证明「仍然红」：仓位建不起来
    return {"id": "E9", "layer": "L2→L3 前瞻",
            "case": "只改入场价 0.05（不加 bar 余量）在 R7 下仍失效",
            "status": "pass" if ok else "fail",
            "why": "flat bar 下 buy fill 恒 > high（0.05→0.06 > 0.05）⇒ R7 拒入场",
            "gap": "M4 必须同时给 bar 形状：open 0.0495 / high 0.0501 / low 0.0005 / close 0.0005",
            "must_assert": "该写法下仓位建不起来 ⇒ 用例原意不成立",
            "observed": got}


def structural_items() -> List[Dict[str, Any]]:
    """还没落地的两条闸门，逐条标「待实现」，并写清缺哪一行。"""
    return [
        {"id": "E5", "layer": "L3", "case": "R6：price_step > raw_price 的入场被拒",
         "status": "待实现",
         "why": "fill.py 的 _guard 只查「有限且 >0」，没有 step 与 raw 的大小关系",
         "gap": "fill.py:220-241（_guard）缺 `if step > raw: raise FillRejected("
                "\"price_step_coarser_than_price\")`，且须放在 R5 之后",
         "must_assert": "入场被拒 ∧ 无成交 ∧ invalid_reasons 含该 reason ∧ "
                        "run_valid=False"},
        {"id": "E6", "layer": "L3", "case": "R7：成交价越出该 bar [low, high] 被拒",
         "status": "待实现",
         "why": "引擎四个成交点都没有区间校验；协议也不收 bar_low/bar_high",
         "gap": "engine.py:448 / 385 / 658 / 720 四个成交点后缺区间判定；"
                "且 engine.py:452-462 缺 invalid_reasons.append",
         "must_assert": "越界腿被拒 ∧ detail 含 over/slip_over/round_over ∧ "
                        "run_valid=False ∧ 成交价**未被裁剪**"},
        {"id": "E7", "layer": "L3", "case": "旧 run 不因新闸门自动变合格",
         "status": "结构性成立",
         "why": "闸门只在 run 时执行，不回写既有产物；62 腿来源 31 个 run 的"
                "文件哈希在本窗口前后未变（见基线_开工/收工.json）",
         "gap": "—",
         "must_assert": "31 个 run 的 6 类产物 SHA256 不变"},
    ]


def main() -> int:
    items = [probe_e1_entry_price_reject(), probe_e2_entry_qty_reject(),
             probe_e3_exit_reject(), probe_e4_normal(),
             probe_e8_fixture_fix_minimal(), probe_e9_fixture_fix_naive()] \
        + structural_items()
    doc = {"generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "note": "pass/fail 是**当前生产代码**的实测；待实现 = 闸门还不存在，不得算通过",
           "items": items}
    with open(os.path.join(HERE, "验收清单.json"), "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)

    df = pd.DataFrame([{"编号": i["id"], "层": i["layer"], "验收案例": i["case"],
                        "状态": i["status"], "实测/原因": i["why"],
                        "缺口（要改哪一行）": i["gap"],
                        "必须断言什么": i["must_assert"]} for i in items])
    df.to_csv(os.path.join(HERE, "表3_端到端验收清单.csv"),
              index=False, encoding="utf-8-sig")

    L = ["PRECISION-08 / 工具 50 · 端到端验收清单（逐条实测）", "=" * 78,
         "pass/fail = 当前生产代码实测；待实现 = 闸门不存在，不算通过", ""]
    for i in items:
        L.append(f"[{i['id']}] {i['status']:<8} {i['case']}")
        L.append(f"      原因：{i['why']}")
        if i["gap"] != "—":
            L.append(f"      缺口：{i['gap']}")
        if "observed" in i:
            L.append(f"      实测：{json.dumps(i['observed'], ensure_ascii=False)}")
        L.append("")
    txt = "\n".join(L)
    with open(os.path.join(HERE, "验收清单原始输出.txt"), "w", encoding="utf-8") as f:
        f.write(txt + "\n")
    print(txt)

    ok = (items[1]["status"] == "fail"      # E2 必须暴露现成缺陷
          and items[2]["status"] == "pass"  # E3 现状应通过
          and items[3]["status"] == "pass"  # E4 现状应通过
          and items[4]["status"] == "pass"  # E8 夹具修正后可保住原意
          and items[5]["status"] == "pass")  # E9 只改价格仍失效
    print(f"自检：E2 暴露「0 成交但 run_valid=True」={items[1]['status']=='fail'}；"
          f"E3/E4 现状通过={items[2]['status']=='pass' and items[3]['status']=='pass'} → "
          f"{'通过' if ok else '失败'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
