# -*- coding: utf-8 -*-
"""02｜在**修复后规则**下，从真实历史里找出真实候选（不造信号、不改门槛）。

做法（复用既有引擎，不重写条件）：
  · 数据：上一单用既有路由建成的 180 天隔离库（`MARKET_DB` 注入），1h=4320 / 4h=1079 根/标的；
  · 驱动：`unified_runtime.RealSignalEngine.process_market()` 逐根 4h 推进
          —— 与生产 `bootstrap()` 用的是**同一个方法、同一个状态机、同一份 config**；
  · 起点：`history_floor_ms = 该标的库内首根 4h open_time`（180 天窗口第 1 根）；
  · 闸门：`box_gate_bars >= atr_warmup_bars(200)` 才建档 —— 即规范「至少预热 200 根」，
          与本单冻结口径一致（**未改** `box_bars_4h=42`、`er_threshold=0.30`、`2A~8A`）。

为什么不用 `MultiSymbolSignalEngine.bootstrap()`：它的窗口被写死成
`warmup_ms = (200*4 + 360)h = 1160h`（≈48.3 天），180 天里只用最后 1160h。
本单需要「更长历史里是否真的存在候选」，因此直接驱动单标的引擎；
`A` 段用等价性检验证明「直驱」与 `bootstrap` 在**同一窗口**下结果完全一致。
"""
from __future__ import annotations

import argparse
import importlib
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
cm = importlib.import_module("00_common")


def _engine_mod():
    import unified_runtime as UR
    return UR


def drive_symbol(UR, cfg, sym, b4, b1, floor):
    """用既有引擎把某标的的 4h 序列整段推完，返回 (engine, candidates)。"""
    eng = UR.RealSignalEngine(symbol=sym, config=cfg)
    eng.history_floor_ms = int(floor)
    w = cm.FastMemWatcher(b4, b1)
    cands = []
    for bar in b4:
        cands.extend(eng.process_market(int(bar["close_time"]), w))
    return eng, cands


# ══════════════════════════════════════════════════════════════════════
# A. 数据源等价性（FastMemWatcher vs 原生 _MemWatcher）
# ══════════════════════════════════════════════════════════════════════
def equivalence_check(UR, cfg, con, symbols) -> dict:
    """逐标的对比两种 watcher 的候选集与状态计数，**必须逐位相同**。"""
    rows = []
    for sym in symbols:
        b4 = cm.load_bars(con, sym, "4h")
        b1 = cm.load_bars(con, sym, "1h")
        if not b4 or not b1:
            rows.append({"symbol": sym, "error": "no_bars"})
            continue
        floor = int(b4[0]["open_time"])
        res = {}
        for tag, wcls in (("native", UR._MemWatcher), ("fast", cm.FastMemWatcher)):
            eng = UR.RealSignalEngine(symbol=sym, config=cfg)
            eng.history_floor_ms = floor
            w = wcls(b4, b1)
            cands = []
            for bar in b4:
                cands.extend(eng.process_market(int(bar["close_time"]), w))
            res[tag] = {
                "candidates": sorted(c.candidate_id for c in cands),
                "n_cand": len(cands),
                "transitions": len(eng.sm.transitions),
                "processed_4h": len(eng.processed_4h_closes),
                "processed_1h": len(eng.processed_1h_opens),
                "atr_bars_seen": eng.atr_bars_seen,
                "box_gate_bars": eng.box_gate_bars,
                "last_atr": eng.last_atr,
                "skipped_bars": eng.data_quality.get("skipped_bars"),
            }
        same = (res["native"]["candidates"] == res["fast"]["candidates"]
                and res["native"]["transitions"] == res["fast"]["transitions"]
                and res["native"]["processed_4h"] == res["fast"]["processed_4h"]
                and res["native"]["processed_1h"] == res["fast"]["processed_1h"]
                and res["native"]["atr_bars_seen"] == res["fast"]["atr_bars_seen"]
                and res["native"]["box_gate_bars"] == res["fast"]["box_gate_bars"]
                and res["native"]["skipped_bars"] == res["fast"]["skipped_bars"])
        rows.append({"symbol": sym, "identical": bool(same),
                     "n_cand": res["fast"]["n_cand"],
                     "transitions": res["fast"]["transitions"],
                     "atr_bars_seen": res["fast"]["atr_bars_seen"],
                     "native": res["native"], "fast": res["fast"]})
    return {"symbols_checked": len(rows),
            "all_identical": all(r.get("identical") for r in rows) if rows else None,
            "rows": rows}


# ══════════════════════════════════════════════════════════════════════
# B. 全池 180 天扫描
# ══════════════════════════════════════════════════════════════════════
def scan(symbols, db, stop_after=None, progress_every=40) -> dict:
    UR = _engine_mod()
    cfg = cm.config()
    con = cm.market_conn(db)
    t0 = time.time()
    cands, per_symbol, errors = [], {}, {}
    for k, sym in enumerate(symbols, 1):
        try:
            b4 = cm.load_bars(con, sym, "4h")
            b1 = cm.load_bars(con, sym, "1h")
            if not b4 or not b1:
                errors[sym] = "no_bars"
                continue
            floor = int(b4[0]["open_time"])
            eng, got = drive_symbol(UR, cfg, sym, b4, b1, floor)
            per_symbol[sym] = {
                "bars_4h": len(b4), "bars_1h": len(b1),
                "first_4h_utc": cm.iso(floor),
                "last_4h_utc": cm.iso(b4[-1]["close_time"]),
                "n_cand": len(got),
                "atr_bars_seen": eng.atr_bars_seen,
                "box_gate_bars": eng.box_gate_bars,
                "transitions": len(eng.sm.transitions),
                "skipped_bars": eng.data_quality.get("skipped_bars"),
                "zero_flat_run": eng.data_quality.get("zero_flat_run"),
                "invalid": eng.data_quality.get("invalid"),
                "final_state": (eng.sm.active_event.current_state.value
                                if eng.sm.active_event else "IDLE"),
            }
            for c in got:
                row = cm.candidate_row(c)
                row["_scanned_floor_utc"] = cm.iso(floor)
                cands.append(row)
        except Exception as exc:                                    # noqa: BLE001
            errors[sym] = f"{type(exc).__name__}: {exc}"
        if progress_every and k % progress_every == 0:
            print(f"  [{k}/{len(symbols)}] cand={len(cands)} "
                  f"elapsed={time.time() - t0:.1f}s", flush=True)
        if stop_after is not None and len(cands) >= stop_after:
            print(f"  达到 stop_after={stop_after}，在 {k}/{len(symbols)} 处停止", flush=True)
            break
    con.close()
    cands.sort(key=lambda r: (r["available_at_ms"], r["symbol"]))
    return {
        "generated_at_utc": cm.utc_now(),
        "db": str(db),
        "n_symbols_requested": len(symbols),
        "n_symbols_scanned": len(per_symbol),
        "n_errors": len(errors), "errors": errors,
        "elapsed_sec": round(time.time() - t0, 1),
        "n_candidates": len(cands),
        "candidates": cands,
        "per_symbol": per_symbol,
        "method": {
            "driver": "unified_runtime.RealSignalEngine.process_market（既有方法，未改写）",
            "config": "EventTrendConfig()（冻结默认，未覆盖任何字段）",
            "history_floor": "该标的库内首根 4h open_time",
            "box_gate": "box_gate_bars >= atr_warmup_bars(200) ⇒ 规范「至少预热 200 根」",
            "window": "180 天（1h 4320 / 4h 1079 根每标的）",
            "note": ("这是**长历史回放**，不是生产 1160h 窗口。两者窗口不同，"
                     "候选集合不可直接互推（FIX-03 已实测：合并历史 1160h 窗口 0 候选）。"),
        },
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(cm.ISO_DB_180D))
    ap.add_argument("--limit", type=int, default=None, help="只扫前 N 个标的（调试）")
    ap.add_argument("--stop-after", type=int, default=None, help="凑够 N 个候选即停")
    ap.add_argument("--equivalence", action="store_true", help="只跑数据源等价性检验")
    ap.add_argument("--equivalence-n", type=int, default=6)
    ap.add_argument("--out", default="02_候选扫描.json")
    a = ap.parse_args()

    db = Path(a.db)
    if not db.exists():
        raise SystemExit(f"缺少 180 天隔离库：{db}（由 SIGNAL-HISTORY-FIX-02 建成）")
    syms, meta = cm.universe()
    if a.limit:
        syms = syms[:a.limit]
    print(f"库={db}  标的={len(syms)}  池来源={json.dumps(meta, ensure_ascii=False)[:200]}")

    UR = _engine_mod()
    cfg = cm.config()

    if a.equivalence:
        con = cm.market_conn(db)
        eq = equivalence_check(UR, cfg, con, syms[:a.equivalence_n])
        con.close()
        eq["config_identity"] = cm.engine_identity()
        cm.write_json("02_数据源等价性.json", eq)
        print(f"等价性：{eq['symbols_checked']} 标的，全一致={eq['all_identical']}")
        for r in eq["rows"]:
            print(f"  {r['symbol']:16s} identical={r.get('identical')} "
                  f"cand={r.get('n_cand')} trans={r.get('transitions')}")
        return 0 if eq["all_identical"] else 1

    res = scan(syms, db, stop_after=a.stop_after)
    res["pool_meta"] = meta
    res["config_identity"] = cm.engine_identity()
    cm.write_json(a.out, res)
    print(f"\n扫描完成：{res['n_symbols_scanned']} 标的，{res['n_candidates']} 个候选，"
          f"{res['elapsed_sec']}s，错误 {res['n_errors']}")
    for c in res["candidates"]:
        print(f"  {c['symbol']:16s} {c['available_at_utc']}  A={c['box_atr_a']:.6g} "
              f"box=[{c['box_low_l']:.6g},{c['box_high_h']:.6g}] "
              f"brk={c['breakout_close']:.6g} stop={c['initial_stop_price']:.6g}")
        print(f"       candidate_id={c['candidate_id']}")
        print(f"       cross_run_key={c['cross_run_key']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
