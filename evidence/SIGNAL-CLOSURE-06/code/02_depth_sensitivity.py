# -*- coding: utf-8 -*-
"""SIGNAL-CLOSURE-06｜02 重放深度敏感性：找「状态不再随历史长度变化」的最小深度。

诊断结论（01）：重启后状态不一致，**不是 ATR 漂移**（数值分歧全为 None），
而是「窗口起点那一刻手里有没有一个更早建立的活跃事件」。窗口起点前 1 根建的箱，
到窗口起点时仍然活跃 ⇒ 它会吃掉窗口内的第一个建档机会，后续冷却与事件链跟着错位。

于是问题变成：**要重放多深，截止时刻的状态才不再随历史长度变化？**

做法：固定截止 `T1`，把重放地板放在 `T1-D`，D 从 1160h 起逐档加深，
逐币比较终态（阶段 / 箱体身份 / 冷却 / 迁移数 / 候选身份）。
找到最小的 `D*`，使得**所有币**在 `D >= D*` 的每一档都完全一致。

为什么这能解决重启：重启在 `Tr` 重建，覆盖 `[Tr-D, T1]`，相对 `T1` 的深度是
`D + (T1-Tr) > D`。只要 `D >= D*`，重启与持续运行都落在「状态不变」的区间里 ⇒ 自动一致。

**本版口径更正（相对上一版）**：
  · 跑的是生产的 `MultiSymbolSignalEngine`（见 `00_common.prod_session`），
    不是手搭的 `RealSignalEngine`；
  · 1h 的保留起点与 4h 的事件地板**同为 `T1 - max(grid)`**。上一版 1h 只保留到声明窗口
    （1160h），而 4h 深到 2400h ⇒ 早段箱体没有 1h 可扫，状态机卡 BOX，深度结论被污染。

用法：python code/02_depth_sensitivity.py [--upto ISO] [--symbols A,B] [--grid 1160,1800,2880]
产出：out/02_深度敏感性.json、out/02_深度敏感性.csv
"""
from __future__ import annotations

import importlib
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
cm = importlib.import_module("00_common")

H1 = cm.H1
#: 深度网格（小时）。1160 = 当前声明窗口；逐档加深直到状态稳定
GRID_H = [1160, 1800, 2400, 2880, 3360]


def main() -> int:
    argv = sys.argv[1:]
    upto = None
    syms_arg = None
    grid = GRID_H
    tag = "main"
    for i, a in enumerate(argv):
        if a == "--upto" and i + 1 < len(argv):
            upto = cm.parse_utc_ms(argv[i + 1])
        if a == "--symbols" and i + 1 < len(argv):
            syms_arg = argv[i + 1].split(",")
        if a == "--grid" and i + 1 < len(argv):
            grid = [int(x) for x in argv[i + 1].split(",")]
        if a == "--tag" and i + 1 < len(argv):
            tag = argv[i + 1]
    if upto is None:
        upto = cm.last_closed_1h_close_ms()
    syms = syms_arg or cm.universe()[0]
    deepest = max(grid)
    print(f"[cfg] upto={cm.utc(upto)} symbols={len(syms)} grid={grid} "
          f"declared={cm.warmup_ms()/H1:.0f}h")

    t0 = time.time()
    # 1h 保留起点必须与「最深一档的事件地板」一致，否则深档缺 1h 会假性卡 BOX
    hist, _ = cm.load_hist(syms, upto, read_days=190,
                           keep_from_ms=int(upto) - deepest * H1)
    print(f"[load] {time.time()-t0:.1f}s")

    states = {}
    for D in grid:
        t1 = time.time()
        eng = cm.prod_session(syms, hist, upto, upto, replay_hours=D, advance="end")
        states[D] = cm.pool_snapshot(eng)
        n_ready = len(eng.engines)
        print(f"  [D={D}h] ready={n_ready} {time.time()-t1:.1f}s")

    # 逐档比较。**两层必须分开**，否则结论会被「追加型流水」污染：
    #   ① 有效状态：阶段 / 箱体身份 / 扫低 / 回踩 / 冷却 —— 这些必须与深度无关才谈得上重启一致；
    #   ② 追加型流水：`n_transitions`（累计迁移数）与 `n_candidates/candidate_ids`
    #      （候选日志）按构造随重放深度单调增长 —— 历史更长自然见过更多次，
    #      把它们算进「状态差异」会让每一次加深都显得「有变化」，且永远不收敛。
    keys = ["state", "box_formed_ms", "box_high", "box_low", "sweep_time_ms",
            "retest_time_ms", "cooling_until_ms"]
    LOG_KEYS = ["n_candidates", "candidate_ids"]
    diffs, n_trans_changed, rows = {}, {}, []
    for i in range(1, len(grid)):
        a, b = grid[i - 1], grid[i]
        d, nt = [], 0
        for sym in syms:
            sa, sb = states[a].get(sym), states[b].get(sym)
            if sa is None or sb is None:
                continue
            if sa.get("n_transitions") != sb.get("n_transitions"):
                nt += 1
            ch = [k for k in keys if sa.get(k) != sb.get(k)]
            if ch:
                d.append({"symbol": sym, "fields": ch,
                          "from": {k: sa.get(k) for k in ch},
                          "to": {k: sb.get(k) for k in ch},
                          "from_box_utc": cm.utc(sa.get("box_formed_ms")),
                          "to_box_utc": cm.utc(sb.get("box_formed_ms"))})
        diffs[f"{a}->{b}"] = d
        n_trans_changed[f"{a}->{b}"] = nt
        n_log = sum(1 for sym in syms
                    if (states[a].get(sym) or {}).get("candidate_ids")
                    != (states[b].get(sym) or {}).get("candidate_ids"))
        print(f"  {a}h -> {b}h: 有效状态变化 {len(d)} 币"
              f"（另：累计迁移数不同 {nt} 币、候选日志不同 {n_log} 币，均不计入）")

    stable_from = None
    for i, D in enumerate(grid):
        later = [f"{grid[j-1]}->{grid[j]}" for j in range(i + 1, len(grid))]
        if later and all(len(diffs[k]) == 0 for k in later):
            stable_from = D
            break

    for sym in syms:
        row = {"symbol": sym}
        for D in grid:
            s = states[D].get(sym) or {}
            row[f"D{D}_state"] = s.get("state")
            row[f"D{D}_box_utc"] = cm.utc(s.get("box_formed_ms"))
            row[f"D{D}_n_cand"] = s.get("n_candidates")
            row[f"D{D}_n_trans"] = s.get("n_transitions")
            row[f"D{D}_cand_ids"] = ",".join(s.get("candidate_ids") or [])
        rows.append(row)

    res = {
        "generated_at_utc": cm.utc(cm.now_ms()),
        "upto_utc": cm.utc(upto),
        "tag": tag,
        "declared_window_hours": round(cm.warmup_ms() / H1, 1),
        "grid_hours": grid,
        "n_symbols": len(syms),
        "n_ready_per_depth": {D: len(states[D]) for D in grid},
        "n_changed_between_adjacent": {k: len(v) for k, v in diffs.items()},
        "n_transitions_changed_between_adjacent": n_trans_changed,
        "state_key_note": ("有效状态 = 阶段/箱体/扫低/回踩/冷却；"
                           "**不含** n_transitions（累计迁移数）与 n_candidates/candidate_ids"
                           "（候选日志）—— 两者按构造随重放深度单调增长，不是状态。"),
        "candidate_total_per_depth": {D: sum((states[D].get(s) or {}).get("n_candidates", 0)
                                            for s in syms) for D in grid},
        "state_distribution_per_depth": {
            D: {st: sum(1 for s in syms if (states[D].get(s) or {}).get("state") == st)
                for st in sorted({(states[D].get(s) or {}).get("state") for s in syms})}
            for D in grid},
        "stable_from_hours": stable_from,
        "adjacent_diff_detail": {k: v[:60] for k, v in diffs.items()},
        "runner": "MultiSymbolSignalEngine（生产类）via 00_common.prod_session",
        "note": ("判据：找到最小的 D，使得 D 之后**每一档相邻比较都 0 变化**。"
                 "这表示「状态在 D 处已经稳定，再加深历史也不会变」。"
                 "只有稳定了，重启（深度更大）与持续运行才可能一致。"),
    }
    cm.dump(f"02_深度敏感性_{tag}.json", res)
    cm.dump_csv(f"02_深度敏感性_{tag}.csv", rows)
    print(f"[stable] D* = {stable_from}h（声明窗口 {cm.warmup_ms()/H1:.0f}h）")
    print("[cand] " + " / ".join(f"{D}h={res['candidate_total_per_depth'][D]}"
                                 for D in grid))
    return 0


if __name__ == "__main__":
    sys.exit(main())
