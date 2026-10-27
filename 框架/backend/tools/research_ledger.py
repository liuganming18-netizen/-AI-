#!/usr/bin/env python3
"""研究台账：回答「到底试了多少次」「留出段被谁看过」。

为什么需要这个工具
──────────────────
`experiment_registry` 的记账能力早就写好了，但**没人看账**。结果是
2,125 个回测产物目录躺在磁盘上，没有任何一份材料能回答两个最基本的问题：

  1. 这些结果是从多少次尝试里挑出来的？（多重检验惩罚算不出来）
  2. 最终留出段被碰过没有？（干净验证还成不成立）

本工具把账本翻译成人能读的三段：**试验总量 / 设计重复度 / 留出段状态**。

只读
────
不改登记库、不改回测产物、不写任何文件。要打印到文件请自行重定向。

用法
────
    python tools/research_ledger.py            # 台账总览
    python tools/research_ledger.py --json     # 同上，机器可读
    python tools/research_ledger.py --verify   # 只校验哈希链完整性
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_BACKEND = _HERE.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


def _pct(a: int, b: int) -> str:
    return f"{(100.0 * a / b):.1f}%" if b else "—"


def build_report() -> dict:
    """汇总台账。只读。"""
    import experiment_registry as EXP
    from infrastructure.guard import holdout as HOLD

    reg = EXP.get_registry()

    verify = {"ok": None, "error": None}
    try:
        v = reg.verify()
        verify["ok"] = bool(v.get("ok", True))
        verify["detail"] = {k: val for k, val in v.items() if k != "ok"}
    except Exception as exc:                                # noqa: BLE001
        verify["ok"] = False
        verify["error"] = f"{type(exc).__name__}: {exc}"

    st = reg.state()
    exps = st.get("experiments") or {}
    observed = st.get("observed") or {}

    total_exp = len(exps)
    total_trials = 0
    trial_hist = Counter()
    outcome_hist = Counter()
    frozen = 0
    by_mode = Counter()
    by_symbols = Counter()
    rows = []

    for eid, e in exps.items():
        n = len(e.get("trials") or [])
        total_trials += n
        trial_hist[n] += 1
        if e.get("frozen_at"):
            frozen += 1
        by_mode[e.get("mode") or "?"] += 1
        scope = e.get("sample_scope") or {}
        syms = tuple(sorted(str(s) for s in (scope.get("symbols") or [])))
        if syms:
            by_symbols[syms] += 1
        for t in e.get("trials") or []:
            outcome_hist[t.get("outcome") or "?"] += 1
        rows.append({
            "experiment_id": eid,
            "design_identity": (e.get("design_identity") or "")[:12],
            "mode": e.get("mode"),
            "frozen": bool(e.get("frozen_at")),
            "trials": n,
            "symbols": list(syms),
            "window_start": scope.get("window_start"),
            "window_end": scope.get("window_end"),
            "registered_at": e.get("registered_at") or e.get("at"),
        })

    # 「试了多少次」的两种口径：登记条数（≈设计数）与试验次数（≈运行数）
    return {
        "registry": {
            "root": str(getattr(reg, "root", "")),
            "verify_ok": verify["ok"],
            "verify_error": verify["error"],
        },
        "experiments": total_exp,
        "frozen": frozen,
        "trials": total_trials,
        "trials_per_design": (round(total_trials / total_exp, 2) if total_exp else None),
        "trial_count_histogram": dict(sorted(trial_hist.items())),
        "outcomes": dict(outcome_hist),
        "modes": dict(by_mode),
        "symbol_sets": [
            {"symbols": list(k), "experiments": v}
            for k, v in by_symbols.most_common(15)
        ],
        "observed_sample_sets": [
            {"sample_set_id": sid,
             "rows": s.get("rows"),
             "keys": len(s.get("keys") or []),
             "purpose": s.get("purpose"),
             "at": s.get("at"),
             "by": s.get("by")}
            for sid, s in observed.items()
        ],
        "holdout": HOLD.describe(),
        "experiment_rows": sorted(rows, key=lambda r: (r["registered_at"] or "")),
    }


def _print_text(rep: dict) -> None:
    print("═" * 66)
    print("研究台账")
    print("═" * 66)

    reg = rep["registry"]
    print(f"登记库        {reg['root']}")
    if reg["verify_ok"] is True:
        print("哈希链        OK（未被篡改）")
    elif reg["verify_ok"] is False:
        print(f"哈希链        ✗ 校验失败：{reg['verify_error'] or reg.get('verify_error')}")
    else:
        print("哈希链        未校验")

    print()
    print(f"登记实验数    {rep['experiments']}")
    print(f"其中已冻结    {rep['frozen']}（{_pct(rep['frozen'], rep['experiments'])}）")
    print(f"试验总次数    {rep['trials']}")
    print(f"每设计平均    {rep['trials_per_design'] if rep['trials_per_design'] is not None else '—'} 次")
    print(f"结论分布      {rep['outcomes'] or '（无）'}")

    hist = rep["trial_count_histogram"]
    if hist:
        print()
        print("试验次数分布（按设计）")
        for n, c in hist.items():
            label = "0 次（只登记没跑）" if n == 0 else f"{n} 次"
            print(f"  {label:<18s} {c} 个设计")

    print()
    print("留出段")
    h = rep["holdout"]
    if not h.get("defined"):
        print("  未定义 ⇒ 守卫放行一切窗口")
        print(f"  定义文件：{h.get('file')}")
    else:
        print(f"  区间 {h.get('start')} ~ {h.get('end')}   标的 {h.get('symbols')}")
        print(f"  定义人 {h.get('defined_by')}   时间 {h.get('defined_at')}")

    obs = rep["observed_sample_sets"]
    print()
    print(f"已观察样本集  {len(obs)} 个")
    for o in obs:
        print(f"  {o['sample_set_id']}  {o['rows']} 行 / {o['keys']} 键  "
              f"{o['at']}  {o['purpose'] or ''}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="研究台账（只读）")
    ap.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    ap.add_argument("--verify", action="store_true", help="只校验哈希链")
    args = ap.parse_args(argv)

    rep = build_report()

    if args.verify:
        ok = rep["registry"]["verify_ok"]
        print("哈希链 OK" if ok else f"哈希链校验失败：{rep['registry']['verify_error']}")
        return 0 if ok else 1

    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=2))
    else:
        _print_text(rep)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
