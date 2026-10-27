# -*- coding: utf-8 -*-
"""SIGNAL-HISTORY-FIX-03｜04 隔离一致性：连续回放 / 初始化后追加 / 重启重建 + 1h 节奏。

工单「四」「三」：
  A. 两组对照，变量分离（比较项 = 阶段 + 箱体建档时刻 + 上下沿 + ATR + 水位）：
       ① **喂法等价**（同窗口，只换「怎么喂」）
          S1(F) = 一台 RealSignalEngine 一次把窗口内所有 4h 喂完，末尾补一次
                  `process_market(now)` 把最后一根 4h 之后才收盘的 1h 补齐；
          S2    = `MultiSymbolSignalEngine` 用合并历史 bootstrap 到 now-72h（窗口起点
                  f2 = now-1232h），再用时间切片 watcher 按 15 分钟步长 `step()` 到 now
                  —— 这是**生产真实的推进方式**（不是手工喂 bar）；
          S3    = 同 S2，但 bootstrap 到 now-12h（窗口起点 f3 = now-1172h）。
          于是 S1(f2)≡S2、S1(f3)≡S3 各自证明「连续回放 == 建档后逐轮追加」。
       ② **窗口移动**：S2 vs S3 只差窗口起点 60h ⇒ 直接量化「移动历史窗口」的影响。
     `export_state` 明确 `restore_supported=False`，重启的真实语义就是**全量重放**
     （见 FIX-01 取舍记录）；S3 验证的正是「重放重建」的一致性。

  B. 1h 节奏（样本 100 标的，含 9 个状态变化标的 + 6 个占位行标的 + ZEREBRO）：
     用同一套时间切片 watcher 把生产 `step()` 放回历史时点，按 5 分钟步长推进 72 小时，
     记录每根 1h 被状态机处理的时刻 ⇒ 延迟 = 处理时刻 − 收盘时刻。
     若引擎要「等下一根 4h」，延迟会出现 60~240 分钟的堆积；本口径直接看这个堆积在不在。

产出：out/04_隔离一致性.json、out/04_1h节奏.csv
"""
from __future__ import annotations

import bisect
import csv
import importlib
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
cm = importlib.import_module("00_common")

import history_loader as HL
import live_store as L
import project_paths as PP
import unified_runtime as UR

H1, H4 = cm.H1, cm.H4
M15 = 15 * 60 * 1000


def g10(x):
    """10 位有效数字（ATR 只做递推，S3 的预热起点差 3 根 ⇒ 末位可能有 1e-16 级差异）。"""
    return None if x is None else float(f"{float(x):.10g}")


# ══════════════════════════════════════════════════════════════════════
# S1：单标的一次性连续回放
# ══════════════════════════════════════════════════════════════════════
def replay(b4, b1, upto, floor_ms, pre=None):
    eng = UR.RealSignalEngine(symbol="x")
    eng.history_floor_ms = int(floor_ms)
    for x in (pre or ()):
        eng.atr_bars_seen += 1
        eng.last_atr = eng.atr.update(float(x["high"]), float(x["low"]), float(x["close"]))
    w = UR._MemWatcher(b4, b1)
    for x in b4:
        if int(x["close_time"]) <= int(upto):
            eng.process_market(int(x["close_time"]), w)
    # 收尾：最后一根 4h 收盘之后才开盘的那根 1h 只有靠这一步才会被处理
    eng.process_market(int(upto), w)
    return eng


def st_of(eng):
    ev = eng.sm.active_event
    return {"state": (ev.current_state.value if ev else "IDLE"),
            "box_formed_ms": (int(ev.box.formation_time_ms) if ev else None),
            "box_high": (g10(ev.box.high_h) if ev else None),
            "box_low": (g10(ev.box.low_l) if ev else None),
            "atr": g10(eng.last_atr),
            "processed_1h_max": (max(eng.processed_1h_opens) if eng.processed_1h_opens else None),
            "processed_4h_max": (max(eng.processed_4h_opens) if eng.processed_4h_opens else None)}


# ══════════════════════════════════════════════════════════════════════
# 时间切片 watcher：把「某一历史时点可见的 bar」模拟出来
# ══════════════════════════════════════════════════════════════════════
class SimWatcher:
    """与 `PoolMarketWatcher` 同签名，但只读内存、由 `set_now()` 决定可见范围。

    为什么需要它：`step()` 只吃 `global_last_open` / `latest_open_by_symbol` /
    `get_closed_bars` 三样东西。把这三样换成「按模拟时钟切片」的实现，就能让
    生产 `step()` 回到过去任意一刻运行 —— 这是检验 1h 推进节奏的唯一干净办法。

    性能：`now` 只前进 ⇒ 每个 (周期, 标的) 维护一个单调指针，避免每步重扫全窗口
    （否则 100 标的 × 864 步会退化成分钟级）。
    """

    def __init__(self, d1, d4):
        self.d1, self.d4 = d1, d4
        self.now = 0
        self._rows, self._opens, self._closes, self._ptr = {}, {}, {}, {}
        for tf, src in (("1h", d1), ("4h", d4)):
            for sym, rows in src.items():
                self._rows[(tf, sym)] = rows
                self._opens[(tf, sym)] = [int(r["open_time"]) for r in rows]
                self._closes[(tf, sym)] = [int(r["close_time"]) for r in rows]
                self._ptr[(tf, sym)] = 0

    def set_now(self, ms: int):
        self.now = int(ms)

    def _prefix(self, tf, sym):
        key = (tf, sym)
        rows = self._rows.get(key) or []
        p = self._ptr.get(key, 0)
        closes = self._closes.get(key) or []
        n = len(rows)
        while p < n and closes[p] <= self.now:
            p += 1
        self._ptr[key] = p
        return rows, p

    def get_closed_bars(self, sym, tf, start_open_ms, end_open_ms):
        """与 `_MemWatcher` / `PoolMarketWatcher` 同口径：open>=start 且 **close<=end**。"""
        rows, p = self._prefix(tf, sym)
        if not p:
            return []
        lo = bisect.bisect_left(self._opens[(tf, sym)], int(start_open_ms), 0, p)
        hi = bisect.bisect_right(self._closes[(tf, sym)], int(end_open_ms), lo, p)
        return rows[lo:hi]

    def global_last_open(self, tf):
        mx = None
        for sym in (self.d1 if tf == "1h" else self.d4):
            rows, p = self._prefix(tf, sym)
            if p:
                v = int(rows[p - 1]["open_time"])
                mx = v if mx is None else max(mx, v)
        return mx

    def latest_open_by_symbol(self, tf):
        out = {}
        for sym in (self.d1 if tf == "1h" else self.d4):
            rows, p = self._prefix(tf, sym)
            if p:
                out[sym] = int(rows[p - 1]["open_time"])
        return out


def drive(msig, sw, t_from, t_to, step_ms, on_bar=None):
    """让一台 `MultiSymbolSignalEngine` 在模拟时钟下从 `t_from` 推进到 `t_to`。"""
    cur = t_from - (t_from % step_ms) + step_ms      # 对齐到 step 边界，且严格晚于起点
    while cur <= t_to + step_ms:
        sw.set_now(cur)
        msig.step(cur)
        if on_bar:
            on_bar(cur)
        cur += step_ms


def main() -> int:
    t_start = time.time()
    syms, _ = cm.universe()
    now = cm.now_ms()
    now = now - (now % H1)                            # 对齐到整点，便于读节奏
    start = now - 180 * cm.DAY
    win = now - 1160 * H1
    t72 = now - 72 * H1
    t12 = now - 12 * H1
    f2 = t72 - 1160 * H1                              # S2 的窗口起点
    f3 = t12 - 1160 * H1                              # S3 的窗口起点（比 S1 早 12h）
    # 1h 必须覆盖最靠前的那个窗口起点，否则「窗口移动」的对照会被「1h 没读到」污染
    h1_keep = min(win, f2, f3) - 24 * H1

    prov = HL.MergedHistory(L.MARKET_DB, PP.MARKET_ARCHIVE, log=lambda *a: None)
    h4, st4 = prov.load(syms, ("4h",), start, now, keep_from_ms=start)
    h1, st1 = prov.load(syms, ("1h",), start, now, keep_from_ms=h1_keep)

    hist = {"4h": {s: (h4.get(s) or {}).get("4h") or [] for s in syms},
            "1h": {s: (h1.get(s) or {}).get("1h") or [] for s in syms}}

    res = {"generated_at_utc": cm.utc(cm.now_ms()), "universe": len(syms),
           "now_utc": cm.utc(now), "s2_split_utc": cm.utc(t72), "s3_split_utc": cm.utc(t12),
           "window_start_utc": cm.utc(win),
           "s2_window_start_utc": cm.utc(f2), "s3_window_start_utc": cm.utc(f3),
           "history_load": {"4h": {k: v for k, v in st4.items() if not isinstance(v, list)},
                            "1h": {k: v for k, v in st1.items() if not isinstance(v, list)}}}

    # ── A. 三场景 ────────────────────────────────────────────────────────
    # 对照设计（关键）：
    #   S1(F) = 一次性连续回放，窗口起点 F；
    #   S2    = bootstrap 到 t72（窗口起点 f2）+ step() 推进到 now；
    #   S3    = bootstrap 到 t12（窗口起点 f3）+ step() 推进到 now。
    # 于是：
    #   S1(f2) vs S2  → **同一份历史、只换喂法**（一次喂完 vs 先建档再逐轮追加）；
    #   S1(f3) vs S3  → 同上，换另一个起点再验一次；
    #   S2 vs S3      → **只换窗口起点**（差 60h），这才是「移动历史窗口」的影响。
    # 上一版把 S1 固定成 floor=win 去和 S2/S3 比，把「喂法」和「窗口」两个变量混在一起，
    # 差异无法归因 —— 那是我自己的对照缺陷。
    t0 = time.time()

    def s1_for(floor_ms):
        out = {}
        for s in syms:
            b4all = hist["4h"][s]
            b1 = hist["1h"][s]
            pre = [x for x in b4all if int(x["open_time"]) < floor_ms]
            b4 = [x for x in b4all if int(x["open_time"]) >= floor_ms]
            out[s] = st_of(replay(b4, b1, now, floor_ms, pre))
        return out

    s1_f2 = s1_for(f2)
    t_s1 = time.time() - t0
    t0 = time.time()
    s1_f3 = s1_for(f3)
    t_s1b = time.time() - t0

    def run_msig(split_ms):
        m = UR.MultiSymbolSignalEngine(syms)
        m.watcher = SimWatcher({s: hist["1h"][s] for s in syms},
                               {s: hist["4h"][s] for s in syms})
        # `history_floor_ms` 必须与 bootstrap 的窗口起点同源（生产 run_forever 也是
        # `started_at_ms - warmup_ms`）。若写成固定的 `win`，S2/S3 的前若干根 4h 会被
        # `process_market` 的 `open >= floor` 挡在门外而**永不处理**，ATR 就差这几根。
        m.history_floor_ms = int(split_ms) - m.warmup_ms
        m._hist = hist                       # 走「合并历史」分支，与生产 prepare_history 同构
        m.history_stats = {"data_source": "merged_cold_hot(injected)"}
        m.bootstrap(split_ms, log=lambda *a: None)
        return m

    def finish(m, split_ms):
        drive(m, m.watcher, split_ms, now, M15)
        return {s: st_of(m.engines[s]) for s in m.engines}

    t0 = time.time()
    m2 = run_msig(t72)
    s2_states = finish(m2, t72)
    t_s2 = time.time() - t0
    t0 = time.time()
    m3 = run_msig(t12)
    s3_states = finish(m3, t12)
    t_s3 = time.time() - t0

    def cmp(a_map, b_map, label_a, label_b):
        same, diffs, missing = 0, [], []
        for s in syms:
            a, b = a_map.get(s), b_map.get(s)
            if a is None or b is None:
                missing.append(s)
                continue
            same += int(a == b)
            if a != b:
                diffs.append({"symbol": s, label_a: a, label_b: b,
                              "diff_keys": sorted(k for k in a if a.get(k) != b.get(k))})
        return {"same": same, "n": len(syms), "diff_n": len(diffs),
                "missing_n": len(missing), "diffs": diffs[:40], "missing": missing[:40]}

    res["scenarios"] = {
        "n": len(syms), "elapsed_sec": round(time.time() - t_start, 1),
        "s1_f2_sec": round(t_s1, 1), "s1_f3_sec": round(t_s1b, 1),
        "s2_sec": round(t_s2, 1), "s3_sec": round(t_s3, 1),
        # ① 喂法等价（同窗口、只换「一次喂完」vs「建档后 step 追加」）
        "feed_path_S1f2_vs_S2": cmp(s1_f2, s2_states, "S1_f2", "S2"),
        "feed_path_S1f3_vs_S3": cmp(s1_f3, s3_states, "S1_f3", "S3"),
        # ② 窗口移动（只换窗口起点，喂法都是 bootstrap+step）
        "window_shift_S2_vs_S3": cmp(s2_states, s3_states, "S2", "S3"),
        "S2_ready": (m2.bootstrap_stats or {}).get("ready"),
        "S3_ready": (m3.bootstrap_stats or {}).get("ready"),
        "S2_used": (m2.bootstrap_stats or {}).get("used"),
        "S3_used": (m3.bootstrap_stats or {}).get("used"),
        "S2_atr_prewarm": [(m2.bootstrap_stats or {}).get("used", {}).get("atr_prewarm_bars_min"),
                           (m2.bootstrap_stats or {}).get("used", {}).get("atr_prewarm_bars_max")],
        "S3_atr_prewarm": [(m3.bootstrap_stats or {}).get("used", {}).get("atr_prewarm_bars_min"),
                           (m3.bootstrap_stats or {}).get("used", {}).get("atr_prewarm_bars_max")],
        "note": ("比较项 = 当前阶段 + 箱体建档时刻 + 上下沿(10 位有效数字) + 最新 ATR(10 位有效数字)"
                 " + 已处理 1h/4h 最大 open。"
                 "S1(F) 是「一台引擎一次喂完 F→now」；S2/S3 是「bootstrap 到某个过去时刻，"
                 "再用生产 step() 按 15 分钟步长推进到 now」。"
                 "feed_path_* 两两对比 = 同一窗口只换喂法；window_shift_S2_vs_S3 = 只换窗口起点(差 60h)。"),
    }

    # ── B. 1h 节奏（样本 100）─────────────────────────────────────────────
    special = ["BANKUSDT", "BATUSDT", "BTWUSDT", "FLUIDUSDT", "IOTXUSDT", "KAVAUSDT",
               "MAGMAUSDT", "SSVUSDT", "TIAUSDT", "ZEREBROUSDT",
               "1000000BOBUSDT", "PROMPTUSDT", "PUMPBTCUSDT", "EPICUSDT", "MITOUSDT",
               "PTBUSDT"]
    sample = [s for s in special if s in hist["4h"]]
    for s in syms:
        if len(sample) >= 100:
            break
        if s not in sample:
            sample.append(s)

    sw = SimWatcher({s: hist["1h"][s] for s in sample},
                    {s: hist["4h"][s] for s in sample})
    ms = UR.MultiSymbolSignalEngine(sample)
    ms.watcher = sw
    ms.history_floor_ms = int(t72) - ms.warmup_ms
    ms._hist = {tf: {s: hist[tf][s] for s in sample} for tf in ("4h", "1h")}
    ms.history_stats = {"data_source": "merged_cold_hot(injected)"}
    ms.bootstrap(t72, log=lambda *a: None)

    processed_at = {}
    orig = UR.RealSignalEngine.process_market

    def patched(self, upto_ms, watcher):
        before = set(self.processed_1h_opens)
        got = orig(self, upto_ms, watcher)
        for ot in (self.processed_1h_opens - before):
            processed_at.setdefault((self.symbol, int(ot)), int(upto_ms))
        return got

    UR.RealSignalEngine.process_market = patched
    try:
        drive(ms, sw, t72, now, 5 * 60 * 1000)
    finally:
        UR.RealSignalEngine.process_market = orig

    rows, per_sym = [], {}
    for s in sample:
        lags = []
        for r in hist["1h"][s]:
            ot, ct = int(r["open_time"]), int(r["close_time"])
            if ct <= t72 or ct > now:
                continue
            got = processed_at.get((s, ot))
            lags.append(None if got is None else round((got - ct) / 60000.0, 2))
        vals = [x for x in lags if x is not None]
        per_sym[s] = {
            "bars": len(lags), "processed": len(vals),
            "unprocessed": len(lags) - len(vals),
            "lag_min_median": (round(statistics.median(vals), 2) if vals else None),
            "lag_min_max": (round(max(vals), 2) if vals else None),
            "n_lag_gt_60min": sum(1 for x in vals if x > 60),
            "n_lag_gt_240min": sum(1 for x in vals if x > 240),
        }
        rows.append({"symbol": s, **per_sym[s]})
    all_med = [v["lag_min_median"] for v in per_sym.values() if v["lag_min_median"] is not None]
    res["cadence_1h"] = {
        "sample_n": len(sample), "hours": 72, "step_min": 5,
        "bars_checked": sum(v["bars"] for v in per_sym.values()),
        "bars_processed": sum(v["processed"] for v in per_sym.values()),
        "bars_unprocessed": sum(v["unprocessed"] for v in per_sym.values()),
        "median_of_symbol_median_lag_min": (round(statistics.median(all_med), 2)
                                            if all_med else None),
        "max_lag_min": max((v["lag_min_max"] or 0) for v in per_sym.values()),
        "n_symbols_with_lag_gt_60min": sum(1 for v in per_sym.values() if v["n_lag_gt_60min"]),
        "n_symbols_with_lag_gt_240min": sum(1 for v in per_sym.values() if v["n_lag_gt_240min"]),
        "unprocessed_symbols": [s for s, v in per_sym.items() if v["unprocessed"]],
        "per_symbol": per_sym,
        "note": ("延迟 = 该 1h 收盘后到「被状态机处理」的分钟数（5 分钟步长 ⇒ 理论上限 5 分钟）。"
                 "若引擎要等下一根 4h，延迟会出现 60~240 分钟的堆积；本口径直接看这个堆积在不在。"
                 "bars_unprocessed 是窗口末端边界：最后一根 1h 收盘后才到达的 step 不再存在。"),
    }
    res["elapsed_sec_total"] = round(time.time() - t_start, 1)
    cm.dump("04_隔离一致性.json", res)
    p = cm.OUT / "04_1h节奏.csv"
    with p.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"[out] {p}")
    S = res["scenarios"]
    for k, label in (("feed_path_S1f2_vs_S2", "喂法等价(窗口 f2)"),
                     ("feed_path_S1f3_vs_S3", "喂法等价(窗口 f3)"),
                     ("window_shift_S2_vs_S3", "只换窗口起点(差 60h)")):
        d = S[k]
        print(f"[{label}] same {d['same']}/{d['n']} diff {d['diff_n']} missing {d['missing_n']}")
        if d["diffs"]:
            print("     前 5:", [x["symbol"] + str(x["diff_keys"]) for x in d["diffs"][:5]])
    c = res["cadence_1h"]
    print(f"[cadence] sample={c['sample_n']} bars={c['bars_checked']} "
          f"unprocessed={c['bars_unprocessed']} median_lag={c['median_of_symbol_median_lag_min']}min "
          f"max_lag={c['max_lag_min']}min gt60={c['n_symbols_with_lag_gt_60min']} "
          f"gt240={c['n_symbols_with_lag_gt_240min']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
