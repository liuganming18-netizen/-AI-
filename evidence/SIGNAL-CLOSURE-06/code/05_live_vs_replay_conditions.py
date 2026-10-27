# -*- coding: utf-8 -*-
"""SIGNAL-CLOSURE-06｜05 实时节奏 vs 回放重建：同一闭合 K 线时点逐项条件比对。

工单第 4 条：**同一闭合 K 线时点，逐项比较箱体、ATR、扫低、回踩、突破、成交量及
启用中的过滤条件。当前零信号必须能逐币解释；已知应触发案例必须能稳定复现。**

两种到达同一个 4h 收盘 `T` 的方式：

  L 实时节奏  一个引擎从 `T_start` 起逐 1h `step()`，一路跑到 `T_end`；
              到 `T` 时它「刚刚处理完这根 4h」，手里是连续演化的状态。
  R 回放重建  在 `T` 现场新建引擎，按 `[T-W, T]` 重建 —— 生产重启就是这条路。

比对内容（**不是只比终态**）：
  ① 状态指纹：阶段 / 事件号 / 箱体身份 / 冷却 / 候选身份；
  ② 迁移流水：`trigger_bar_close_ms > T-W` 的那一段，逐条比
     `from_state / to_state / trigger_bar_close_ms / reason`；
  ③ 每条迁移的 `metrics_snapshot`：这是状态机**自己记下的判据输入**
     （箱体高低、ATR、宽度、ER、扫低价、回踩价、突破收盘、成交量比、各闸门结果），
     逐键逐值比 —— 这才是「逐项比较条件」，不是我另写一份公式再算一遍。

用法：python code/05_live_vs_replay_conditions.py [--n4h 30] [--symbols A,B] [--depth 2880]
产出：out/05_实时与回放条件比对.json、out/05_逐时点比对.csv、out/05_零信号逐币解释.csv
"""
from __future__ import annotations

import importlib
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
cm = importlib.import_module("00_common")

H1, H4 = cm.H1, cm.H4

#: 默认标的：ZERO-CAUSE-05 点名的 6 个 + 深度敏感里有候选/丢候选的 4 个 + 2 个参照
DEFAULT_SYMS = ["BATUSDT", "BTWUSDT", "CTRUSDT", "EDUUSDT", "GRVTUSDT", "TAKEUSDT",
                "AIGENSYNUSDT", "LUNA2USDT", "MASKUSDT", "XRPUSDT", "ADAUSDT",
                "BTCUSDT"]


def fp(eng, sym):
    e = eng.engines.get(sym)
    if e is None:
        return None
    d = cm.fingerprint(e)
    d["candidate_ids"] = [c.candidate_id for c in e.sm.candidates]
    d.pop("n_transitions", None)     # 累计量，按构造随重放深度增长，不是状态
    # 自证：两边 1h 处理根数必须相等。否则「状态一样」可能只是因为
    # 两边都没处理到那几根 1h（空转），而不是真的构造上相等。
    # 属性在**状态机**上：`sm.processed_1h_bar_opens`。
    d["n_1h_processed"] = len(getattr(e.sm, "processed_1h_bar_opens", set()) or set())
    return d


def tr_sig(eng, sym, floor_ms):
    e = eng.engines.get(sym)
    if e is None:
        return []
    out = []
    for t in e.sm.transitions:
        if int(t.trigger_bar_close_ms) <= int(floor_ms):
            continue
        out.append({
            "from": t.from_state, "to": t.to_state,
            "close_ms": int(t.trigger_bar_close_ms),
            "reason": t.reason,
            "metrics": dict(t.metrics_snapshot or {}),
        })
    return out


def _flat(d, prefix=""):
    """把 metrics_snapshot 摊平成 {key: value}，便于逐项比较与计数。"""
    out = {}
    for k, v in (d or {}).items():
        kk = f"{prefix}{k}"
        if isinstance(v, dict):
            out.update(_flat(v, kk + "."))
        elif isinstance(v, (list, tuple)):
            out[kk] = repr(v)
        else:
            out[kk] = v
    return out


def main() -> int:
    argv = sys.argv[1:]
    upto = None
    n4h = 30
    depth = None
    anchor_h = None
    with_blockers = False
    syms = DEFAULT_SYMS
    for i, a in enumerate(argv):
        if a == "--upto" and i + 1 < len(argv):
            upto = cm.parse_utc_ms(argv[i + 1])
        if a == "--n4h" and i + 1 < len(argv):
            n4h = int(argv[i + 1])
        if a == "--depth" and i + 1 < len(argv):
            depth = int(argv[i + 1])
        if a == "--symbols" and i + 1 < len(argv):
            syms = argv[i + 1].split(",")
        if a == "--anchor-hours" and i + 1 < len(argv):
            anchor_h = int(argv[i + 1])
        if a == "--with-blockers":
            with_blockers = True
    if upto is None:
        upto = cm.last_closed_1h_close_ms()
    W_h = int(depth) if depth else cm.state_replay_hours()
    W = W_h * H1
    anchor_ms = (int(upto) - anchor_h * H1) if anchor_h else None
    tag = f"anchor{anchor_h}" if anchor_h else "relative"
    print(f"[cfg] upto={cm.utc(upto)} symbols={len(syms)} n4h={n4h} depth={W_h}h "
          f"mode={tag} anchor={cm.utc(anchor_ms)}")

    t0 = time.time()
    # 比对窗口只有几天，但「已知案例复现」要回到候选自己的快照时刻，
    # 所以 1h 一律保留满读入范围（190 天），不按深度裁 —— 裁了会让复现假失败。
    hist, _ = cm.load_hist(syms, upto, read_days=190,
                           keep_from_ms=int(upto) - 190 * cm.DAY)
    print(f"[load] {time.time()-t0:.1f}s")

    # 4h 收盘序列（取全池共有、且 <= upto 的最近 n4h 根）
    closes = sorted({int(b["close_time"]) for s in syms
                     for b in (hist[s]["4h"] or []) if int(b["close_time"]) <= upto})
    closes = closes[-n4h:]
    T_start = closes[0] - 4 * H1
    T_end = closes[-1]
    print(f"[window] 4h 收盘 {len(closes)} 根：{cm.utc(closes[0])} → {cm.utc(closes[-1])}")

    # ── L 实时节奏：一个引擎逐 1h 推进，在每根 4h 收盘抓快照 ──────────────
    live_fp, live_tr = {}, {}
    want = set(closes)

    def hook(eng, t_ms):
        if t_ms in want:
            live_fp[t_ms] = {s: fp(eng, s) for s in syms}
            live_tr[t_ms] = {s: tr_sig(eng, s, t_ms - W) for s in syms}

    t0 = time.time()
    eng_live = cm.prod_session(syms, hist, T_start, T_end, replay_hours=W_h,
                               advance="1h", hook=hook, anchor_ms=anchor_ms)
    t_live = round(time.time() - t0, 1)
    print(f"[L] 实时节奏 {t_live}s，抓到 {len(live_fp)} 个 4h 时点")
    cands_live = {s: [c.candidate_id for c in e.sm.candidates]
                  for s, e in eng_live.engines.items()}

    # ── R 回放重建：每个 4h 收盘现场新建引擎 ──────────────────────────────
    t0 = time.time()
    replay_fp, replay_tr = {}, {}
    for T in closes:
        # `force_step=True` 不能省：R 是 `init == end`，`bootstrap` 只按 4h 收盘
        # 驱动，最后一根 4h 之后已闭合的 1h 不会被喂进状态机；而 L 是逐 1h
        # `step()` 过来的，最后那几根都处理过。不补这一步，两边天生差 3 根 1h，
        # 「0 差异」就可能是假的。
        eng = cm.prod_session(syms, hist, T, T, replay_hours=W_h, advance="end",
                              anchor_ms=anchor_ms, force_step=True)
        replay_fp[T] = {s: fp(eng, s) for s in syms}
        replay_tr[T] = {s: tr_sig(eng, s, T - W) for s in syms}
    t_replay = round(time.time() - t0, 1)
    print(f"[R] 回放重建 {t_replay}s（{len(closes)} 次 bootstrap）")

    # ── 比对 ──────────────────────────────────────────────────────────────
    rows, diffs = [], []
    n_cmp = n_tr_cmp = n_metric_cmp = 0
    n_metric_bad = 0
    for T in closes:
        for s in syms:
            a, b = live_fp[T].get(s), replay_fp[T].get(s)
            n_cmp += 1
            if a is None or b is None:
                diffs.append({"close_utc": cm.utc(T), "symbol": s,
                              "kind": "missing", "live": a is not None,
                              "replay": b is not None})
                continue
            ch = [k for k in a if a.get(k) != b.get(k)]
            ta, tb = live_tr[T].get(s) or [], replay_tr[T].get(s) or []
            n_tr_cmp += max(len(ta), len(tb))
            tr_ch = []
            if len(ta) != len(tb):
                tr_ch.append("n_transitions")
            for i in range(min(len(ta), len(tb))):
                x, y = ta[i], tb[i]
                for k in ("from", "to", "close_ms", "reason"):
                    if x[k] != y[k]:
                        tr_ch.append(f"tr[{i}].{k}")
                fx, fy = _flat(x["metrics"]), _flat(y["metrics"])
                n_metric_cmp += len(set(fx) | set(fy))
                for k in sorted(set(fx) | set(fy)):
                    if fx.get(k) != fy.get(k):
                        n_metric_bad += 1
                        tr_ch.append(f"tr[{i}].metrics.{k}")
            if ch or tr_ch:
                diffs.append({"close_utc": cm.utc(T), "symbol": s,
                              "kind": "state" if ch else "transitions",
                              "state_fields": ch, "transition_fields": tr_ch[:12],
                              "live_state": a.get("state"), "replay_state": b.get("state"),
                              "live_box_utc": cm.utc(a.get("box_formed_ms")),
                              "replay_box_utc": cm.utc(b.get("box_formed_ms"))})
            rows.append({"close_utc": cm.utc(T), "symbol": s,
                         "live_state": a.get("state"), "replay_state": b.get("state"),
                         "live_box_utc": cm.utc(a.get("box_formed_ms")),
                         "replay_box_utc": cm.utc(b.get("box_formed_ms")),
                         "live_n_cand": len(a.get("candidate_ids") or []),
                         "replay_n_cand": len(b.get("candidate_ids") or []),
                         "n_state_diff": len(ch),
                         "n_transition_diff": len(tr_ch)})

    # ── 已知应触发案例的稳定复现 ──────────────────────────────────────────
    # 候选的 `snapshot_time_ms` 是**候选产生那一刻的 1h 收盘**（如 07:00），
    # 而 `bootstrap` 以 4h 收盘为驱动单位。若直接拿 07:00 当重建截止，
    # 它所属的那根 4h（04:00–08:00）还没闭合 ⇒ bootstrap 扫不到这根 1h
    # ⇒ 候选不出现（假失败，实测 AIGENSYNUSDT 09-07 就是这样）。
    #
    # 正确做法：从「最近一个 <= 快照时刻的 4h 收盘」bootstrap，再**逐 1h 推进**
    # 到快照时刻。既不引入快照之后的行情（无前视），又覆盖 1h 判定。
    def _prev_4h(sym: str, t_ms: int) -> int:
        c4 = [int(b["close_time"]) for b in (hist[sym]["4h"] or [])
              if int(b["close_time"]) <= int(t_ms)]
        return c4[-1] if c4 else int(t_ms)

    repro = []
    for s in syms:
        for cid in cands_live.get(s) or []:
            e = eng_live.engines[s]
            c = next((x for x in e.sm.candidates if x.candidate_id == cid), None)
            if c is None:
                continue
            T = int(c.snapshot_time_ms)
            T0 = _prev_4h(s, T)
            eng = cm.prod_session([s], hist, T0, T, replay_hours=W_h, advance="1h",
                                  anchor_ms=anchor_ms, force_step=True)
            ids = [x.candidate_id for x in eng.engines[s].sm.candidates] if s in eng.engines else []
            repro.append({"symbol": s, "candidate_id": cid,
                          "snapshot_utc": cm.utc(T),
                          "replay_bootstrap_utc": cm.utc(T0),
                          "breakout_close": c.breakout_close,
                          "volume_ratio": c.volume_ratio,
                          "reproduced": cid in ids,
                          "replay_candidates": ids})
    print(f"[repro] 候选 {len(repro)} 个，复现 "
          f"{sum(1 for r in repro if r['reproduced'])}/{len(repro)}")

    # ── 零信号逐币解释（全池；用同一份终态）────────────────────────────────
    # 注意：全池 526 币 190 天历史 + 526 个引擎在本机内存里会顶到上限，
    # 与前面的 12 币比对同进程跑会被 OOM 杀掉（实测 exit 137）。
    # 所以默认跳过，由 `--with-blockers` 显式开启；常规归因走独立件
    # `05b_zero_signal_explain.py`（read_days 按深度裁，内存减半）。
    blockers = []
    if with_blockers:
        try:
            syms_all = cm.universe()[0]
            hist_all, _ = cm.load_hist(syms_all, upto,
                                       read_days=W_h // 24 + 5,
                                       keep_from_ms=int(upto) - W)
            eng_all = cm.prod_session(syms_all, hist_all, upto, upto,
                                      replay_hours=W_h, advance="end", anchor_ms=anchor_ms)
            blockers = cm.explain_zero_signal(eng_all, syms_all, upto)
            print(f"[blockers] {len(blockers)} 币已逐币归因")
        except Exception as exc:                          # noqa: BLE001
            print(f"[blockers] 全池归因失败（不掩盖）：{type(exc).__name__}: {exc}")

    ok = (len(diffs) == 0 and n_cmp > 0
          and all(r["reproduced"] for r in repro))

    # 自证落表：两边 1h 处理根数的取值范围。`n_1h_processed` 已参与上面的
    # 字段比对（不等就会进 `state_fields`），这里再显式列一次，便于人工核对
    # 「不是两边都 0 才算相等」。
    def _n1h_range(src):
        vals = [v["n_1h_processed"] for T in closes
                for v in (src.get(T) or {}).values() if v]
        return {"n": len(vals), "min": min(vals) if vals else None,
                "max": max(vals) if vals else None} if vals else {"n": 0}

    res = {
        "generated_at_utc": cm.utc(cm.now_ms()),
        "upto_utc": cm.utc(upto),
        "depth_hours": W_h,
        "n4h": len(closes),
        "window": {"first_4h_close_utc": cm.utc(closes[0]),
                   "last_4h_close_utc": cm.utc(closes[-1])},
        "symbols": syms,
        "n_symbols": len(syms),
        "n_state_comparisons": n_cmp,
        "n_transitions_compared": n_tr_cmp,
        "n_metric_items_compared": n_metric_cmp,
        "n_metric_items_mismatched": n_metric_bad,
        "n_1h_processed_live": _n1h_range(live_fp),
        "n_1h_processed_replay": _n1h_range(replay_fp),
        "n_diff": len(diffs),
        "verdict": "PASS" if ok else "FAIL",
        "judgement": ("判据：① 每个 (标的, 4h 收盘) 的状态指纹与迁移流水完全相同；"
                      "② 迁移流水里的 metrics_snapshot 逐键逐值相同；"
                      "③ 全部候选都能在「其快照时刻现场重建」中复现出同一个 candidate_id。"
                      "三项全中才是 PASS；比对条数为 0 也算 FAIL（不能空过）。"),
        "elapsed_sec": {"live_cadence": t_live, "replay_rebuild": t_replay},
        "candidate_reproduction": repro,
        "diff_detail": diffs[:120],
        "zero_signal_explanation_n": len(blockers),
        "runner": "MultiSymbolSignalEngine（生产类）via 00_common.prod_session",
    }
    res["replay_mode"] = tag
    res["state_anchor_utc"] = cm.utc(anchor_ms)
    cm.dump(f"05_实时与回放条件比对_{tag}.json", res)
    cm.dump_csv(f"05_逐时点比对_{tag}.csv", rows)
    if blockers:
        cm.dump_csv("05_零信号逐币解释.csv", blockers)
    print(f"[verdict] {res['verdict']}  状态差异 {len(diffs)} / 比对 {n_cmp} 对；"
          f"指标项 {n_metric_cmp} 项，不符 {n_metric_bad} 项")
    for d in diffs[:6]:
        print("   ", d)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
