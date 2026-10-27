# -*- coding: utf-8 -*-
"""SIGNAL-CLOSURE-06｜05b 全池零信号逐币解释（独立件，避免与比对同进程 OOM）。

工单第 4 条：「当前零信号必须能逐币解释」。

为什么单独一件：全池 526 币历史 + 526 个引擎同进程跑会被 OOM 杀掉（实测 exit 137）。
本件按重放深度裁读入天数（默认 `depth/24 + 5` 天），内存减半，且只取终态，
不逐根推进 —— 归因只需要「此刻卡在哪一关」。

用法：python code/05b_zero_signal_explain.py [--depth 2880] [--anchor-hours 2880] [--upto ...]
产出：out/05b_零信号逐币解释.csv、out/05b_零信号汇总.json
"""
from __future__ import annotations

import importlib
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
cm = importlib.import_module("00_common")
H1 = cm.H1


def main() -> int:
    argv = sys.argv[1:]
    upto = None
    depth = None
    anchor_h = None
    for i, a in enumerate(argv):
        if a == "--upto" and i + 1 < len(argv):
            upto = cm.parse_utc_ms(argv[i + 1])
        if a == "--depth" and i + 1 < len(argv):
            depth = int(argv[i + 1])
        if a == "--anchor-hours" and i + 1 < len(argv):
            anchor_h = int(argv[i + 1])
    if upto is None:
        upto = cm.last_closed_1h_close_ms()
    W_h = int(depth) if depth else cm.state_replay_hours()
    W = W_h * H1
    anchor_ms = (int(upto) - anchor_h * H1) if anchor_h else None
    tag = f"anchor{anchor_h}" if anchor_h else "relative"
    days = W_h // 24 + 5
    print(f"[cfg] upto={cm.utc(upto)} depth={W_h}h mode={tag} "
          f"anchor={cm.utc(anchor_ms)} read_days={days}")

    syms, scope_meta = cm.universe()
    print(f"[universe] {len(syms)} 币（scope meta: {len(scope_meta)} 项）")
    hist, _ = cm.load_hist(syms, upto, read_days=days,
                           keep_from_ms=int(upto) - W)
    eng = cm.prod_session(syms, hist, upto, upto, replay_hours=W_h,
                          advance="end", anchor_ms=anchor_ms)
    rows = cm.explain_zero_signal(eng, syms, upto)

    by_state = Counter(r["state"] for r in rows)
    by_why = Counter(r["why"] for r in rows)
    n_cand = sum(int(r.get("n_candidates") or 0) for r in rows)
    res = {
        "generated_at_utc": cm.utc(cm.now_ms()),
        "upto_utc": cm.utc(upto),
        "depth_hours": W_h, "mode": tag, "anchor_utc": cm.utc(anchor_ms),
        "n_symbols": len(syms), "n_scope_meta": len(scope_meta),
        "state_distribution": dict(by_state),
        "why_distribution": dict(by_why.most_common()),
        "n_candidates_total": n_cand,
        "replay_mode_effective": getattr(eng, "_replay_mode", None),
        "state_anchor_ms_effective": getattr(eng, "state_anchor_ms", None),
        "judgement": ("每个币给出「此刻卡在哪一关 + 为什么」。阶段分布之和必须等于币数；"
                      "候选总数为 0 时必须能从 why 分布解释掉每一个币，不允许出现空 why。"),
    }
    cm.dump(f"05b_零信号汇总_{tag}.json", res)
    cm.dump_csv(f"05b_零信号逐币解释_{tag}.csv", rows)

    print(f"[阶段分布] {dict(by_state)}  合计 {sum(by_state.values())}")
    print(f"[原因分布]")
    for k, v in by_why.most_common():
        print(f"   {v:4d}  {k}")
    print(f"[候选] 全池候选合计 {n_cand}")
    bad = [r for r in rows if not r.get("why")]
    print(f"[自证] 空 why 的币 {len(bad)} 个"
          f"{'（不允许）' if bad else '（OK）'}")
    return 0 if not bad and sum(by_state.values()) == len(syms) else 1


if __name__ == "__main__":
    sys.exit(main())
