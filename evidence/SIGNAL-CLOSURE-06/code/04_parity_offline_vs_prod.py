# -*- coding: utf-8 -*-
"""SIGNAL-CLOSURE-06｜04 离线对照件 ≡ 生产代码路径（parity）。

为什么必须先做这一步：后面 02/03/05/06 的所有结论都跑在 `cm.prod_session()` 上。
只要这个对照件与生产真实路径**不是同一份代码**，全部结论都不能算数
（本项目历史上已经出现过「测试通过、生产不同」）。

做法：同一截止 `upto`、同一标的集合，跑两条路径：

  A 真实生产路径：`MultiSymbolSignalEngine(history_provider=MergedHistory(生产库))`
                  + `prepare_history()` + `bootstrap()`（watcher 也走生产 `PoolMarketWatcher`）
  B 离线对照件　：`cm.prod_session()`（同一类、同一方法，只把 provider/watcher 换成内存件）

逐币比较**全部**快照字段（不只是 `diff_key`），并断言 0 差异。

用法：python code/04_parity_offline_vs_prod.py [--symbols A,B,C] [--upto ISO]
产出：out/04_生产一致性.json、out/04_生产一致性.csv
"""
from __future__ import annotations

import importlib
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
cm = importlib.import_module("00_common")

FIELDS = ["state", "event_id", "box_formed_ms", "box_high", "box_low", "box_atr",
          "box_width_atr", "box_er", "sweep_time_ms", "sweep_low", "sweep_volume_vs",
          "retest_time_ms", "retest_low", "cooling_until_ms", "atr", "atr_bars_seen",
          "n_transitions", "n_completed", "n_candidates", "candidate_ids"]


def main() -> int:
    argv = sys.argv[1:]
    upto = None
    syms = None
    anchor_h = None
    for i, a in enumerate(argv):
        if a == "--upto" and i + 1 < len(argv):
            upto = cm.parse_utc_ms(argv[i + 1])
        if a == "--symbols" and i + 1 < len(argv):
            syms = argv[i + 1].split(",")
        if a == "--anchor-hours" and i + 1 < len(argv):
            anchor_h = int(argv[i + 1])
    if upto is None:
        upto = cm.last_closed_1h_close_ms()
    if syms is None:
        # 默认取「上一单点名的 6+1 个币 + 2 个有候选的币 + 2 个数据短的币」
        syms = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "CTRUSDT", "ADAUSDT",
                "MASKUSDT", "LUNA2USDT", "AIGENSYNUSDT"]
    rh = cm.state_replay_hours()
    anchor_ms = (int(upto) - anchor_h * cm.H1) if anchor_h else None
    tag = f"anchor{anchor_h}" if anchor_h else "relative"
    print(f"[cfg] upto={cm.utc(upto)} symbols={len(syms)} state_replay_hours={rh} "
          f"mode={tag} anchor={cm.utc(anchor_ms)}")

    # ── A 真实生产路径 ────────────────────────────────────────────────────
    import unified_runtime as UR
    import history_loader as HL
    import live_store as L
    import project_paths as PP
    t0 = time.time()
    prov = HL.MergedHistory(L.MARKET_DB, PP.MARKET_ARCHIVE, log=lambda *a: None)
    a = UR.MultiSymbolSignalEngine(list(syms), enable_oi_filter=False,
                                   config=cm.cfg(), history_provider=prov)
    # 与 `UnifiedRuntime.run_forever` 逐行同序：先定地板，再读历史，再 bootstrap。
    # （漏掉这一步会让 `process_market` 的 `open_time>=floor` 过滤失效 ⇒ 42 根上下文
    #   被当成事件处理，CTRUSDT 会凭空多出 2 条完成事件 —— 本单踩过。）
    a.history_floor_ms = max(0, int(upto) - a.replay_ms)
    a.state_anchor_ms = anchor_ms
    a.history_floor_ms = a.resolve_replay_floor(int(upto))
    a.prepare_history(int(upto), log=lambda *x: None)
    a.bootstrap(int(upto), log=lambda *x: None)
    snap_a = cm.pool_snapshot(a)
    t_a = round(time.time() - t0, 2)
    stats_a = {k: a.bootstrap_stats.get(k) for k in
               ("ready", "skipped", "history_candidates_discarded", "data_source")}
    stats_a["used"] = (a.bootstrap_stats.get("used") or {})
    stats_a["history"] = {k: (a.history_stats or {}).get(k) for k in
                          ("declared_window_hours", "replay_window_hours",
                           "replay_window_start_utc", "state_replay_hours")}
    print(f"[A] 生产路径 {t_a}s ready={stats_a['ready']} "
          f"discarded={stats_a['history_candidates_discarded']} "
          f"replay={stats_a['history'].get('replay_window_hours')}h "
          f"start={stats_a['history'].get('replay_window_start_utc')}")

    # ── B 离线对照件 ──────────────────────────────────────────────────────
    t0 = time.time()
    hist, _ = cm.load_hist(syms, upto, read_days=int(190),
                           keep_from_ms=int(upto) - max(rh, anchor_h or 0) * cm.H1)
    b = cm.prod_session(syms, hist, upto, upto, replay_hours=rh, advance="end",
                        anchor_ms=anchor_ms)
    snap_b = cm.pool_snapshot(b)
    t_b = round(time.time() - t0, 2)
    print(f"[B] 离线对照 {t_b}s ready={b.bootstrap_stats.get('ready')} "
          f"discarded={b.bootstrap_stats.get('history_candidates_discarded')}")

    # ── 逐币逐字段比较 ────────────────────────────────────────────────────
    rows, diffs = [], []
    for s in syms:
        sa, sb = snap_a.get(s), snap_b.get(s)
        if sa is None or sb is None:
            diffs.append({"symbol": s, "missing": True,
                          "in_prod": sa is not None, "in_offline": sb is not None})
            rows.append({"symbol": s, "missing": True})
            continue
        ch = [f for f in FIELDS if sa.get(f) != sb.get(f)]
        if ch:
            diffs.append({"symbol": s, "fields": ch,
                          "prod": {f: sa.get(f) for f in ch},
                          "offline": {f: sb.get(f) for f in ch}})
        rows.append({"symbol": s, "state": sa["state"],
                     "box_utc": cm.utc(sa["box_formed_ms"]),
                     "n_candidates": sa["n_candidates"],
                     "n_transitions": sa["n_transitions"],
                     "atr": sa["atr"], "n_diff_fields": len(ch),
                     "diff_fields": ",".join(ch)})

    ok = (len(diffs) == 0)
    res = {
        "generated_at_utc": cm.utc(cm.now_ms()),
        "upto_utc": cm.utc(upto),
        "state_replay_hours": rh,
        "replay_mode": tag,
        "anchor_utc": cm.utc(anchor_ms),
        "symbols": syms,
        "n_symbols": len(syms),
        "n_compared_fields": len(FIELDS),
        "n_diff_symbols": len(diffs),
        "verdict": "PASS" if ok else "FAIL",
        "judgement": ("判据：**全部字段**（不只是状态键）逐币相同、且两边都真跑出了状态"
                      "（不是两边都空）。只要有一项不同就是 FAIL —— 离线对照件不得"
                      "被用来下结论。"),
        "path_A_production": {"elapsed_sec": t_a, "bootstrap": stats_a},
        "path_B_offline": {"elapsed_sec": t_b,
                           "bootstrap": {k: b.bootstrap_stats.get(k) for k in
                                         ("ready", "skipped",
                                          "history_candidates_discarded")},
                           "used": (b.bootstrap_stats.get("used") or {})},
        "diff_detail": diffs,
        "note": ("A 与 B 是**同一个类**（MultiSymbolSignalEngine）的两次实例化；"
                 "差别只有 history_provider / watcher 是生产实现还是内存实现。"
                 "所以 PASS 表示「离线件没有引入行为差异」，FAIL 表示离线件不可用。"),
    }
    cm.dump(f"04_生产一致性_{tag}.json", res)
    cm.dump_csv(f"04_生产一致性_{tag}.csv", rows)
    print(f"[verdict] {res['verdict']}  差异标的 {len(diffs)}/{len(syms)}")
    for d in diffs[:10]:
        print("   ", d)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
