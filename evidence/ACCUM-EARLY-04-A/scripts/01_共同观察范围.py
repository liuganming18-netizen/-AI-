# -*- coding: utf-8 -*-
"""ACCUM-EARLY-04-A｜01 共同观察范围扫描（只读，不写库、不下单）。

做什么
------
在**同一批箱体候选**上，同时跑四条规则的生命周期：
  · 基线 = 原策略价格链（BOX→SWEEP 破 L→RETEST→BREAKOUT，含量能 1.5×V20）
  · 甲 4a / 乙 4b / 丙 4c = ACCUM-EARLY-04-A 规则接口 v1

产出每个方案的漏斗（候选池→扫低→压制位合格→确认→成交）与成交结果，
并按「是否与原策略候选重叠」分开统计新增机会。

为什么必须同一批候选
--------------------
如果三条新规则各自在自己的活跃事件上建档，观察范围就各不相同，
「新增了多少机会」这句话就没有分母。所以先列**原始箱体候选池**，
四条规则都从这个池子里取，各自套自己的单活跃事件 + 24h 冷却。

用法
----
    PY=.../python
    $PY 01_共同观察范围.py --from 2025-01-01 --to 2026-07-01 --tag research
    $PY 01_共同观察范围.py --from 2026-07-01 --to 2026-10-12 --tag holdout
    $PY 01_共同观察范围.py --from 2025-01-01 --to 2026-07-01 --tag research --limit 20
"""
from __future__ import annotations

import argparse
import calendar
import json
import os
import statistics
import sys
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve()
ROOT = HERE.parents[3]
sys.path.insert(0, str(HERE.parents[1] / "rules"))
sys.path.insert(0, str(ROOT / "框架" / "backend"))

import accum_early_rules as R                      # noqa: E402
import research_15m as RM                          # noqa: E402

HOT = ROOT / "数据" / "市场数据" / "db" / "market_v2.sqlite3"
COLD = ROOT / "数据" / "市场数据" / "parquet"
CANDLES = COLD / "candles" / "binance" / "um"
OUT = HERE.parents[1] / "out"

MS_H = 3_600_000
MS_4H = 14_400_000
DAY = 86_400_000

#: 池 = 币安 USD-M 加密币 526（排除两个指数 BTCDOMUSDT / ALLUSDT）
EXCLUDED = {"BTCDOMUSDT", "ALLUSDT"}


def iso(ms):
    if ms is None:
        return None
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(int(ms) / 1000))


def to_ms(s):
    return calendar.timegm(time.strptime(s, "%Y-%m-%dT%H:%M:%SZ")) * 1000


def pool() -> list:
    syms = sorted(p.name for p in CANDLES.iterdir() if p.is_dir())
    return [s for s in syms if s not in EXCLUDED]


def load_bars(tf: str, symbol: str, start_ms: int, end_ms: int):
    m = RM.Merged15m(str(HOT), str(COLD), timeframe=tf)
    bars, st = m.load(symbol, start_ms, end_ms)
    return bars, st


def summarize_trades(trades: list) -> dict:
    filled = [t for t in trades if t.get("status") == "filled"]
    r = [t["r_multiple"] for t in filled]
    exits = {}
    for t in filled:
        exits[t["exit_reason"]] = exits.get(t["exit_reason"], 0) + 1
    return {
        "确认事件": len(trades),
        "成交": len(filled),
        "未成交": len(trades) - len(filled),
        "未成交原因": _count(t.get("reason") for t in trades if t.get("status") != "filled"),
        "退出分类": exits,
        "R合计": round(sum(r), 4) if r else 0.0,
        "R均值": round(sum(r) / len(r), 4) if r else None,
        "R中位": round(statistics.median(r), 4) if r else None,
        "R为正": sum(1 for x in r if x > 0),
        "R≤-1（止损全亏）": sum(1 for x in r if x <= -1.0),
        "持仓小时中位": (round(statistics.median(
            [t["held_hours"] for t in filled if t.get("held_hours") is not None]), 1)
            if any(t.get("held_hours") is not None for t in filled) else None),
    }


def _count(seq) -> dict:
    out = {}
    for x in seq:
        out[x] = out.get(x, 0) + 1
    return out


def _bisect(opens: list, ms: int) -> int:
    import bisect
    return bisect.bisect_left(opens, int(ms))


def scan_symbol(symbol: str, w0: int, w1: int, p: dict, slip: float,
                slim: bool = False) -> dict:
    # 4h 预热：多读 400 天，让 Wilder ATR 收敛（递推起点不同会有极小残差）
    b4, st4 = load_bars("4h", symbol, w0 - 400 * DAY, w1 + 40 * DAY)
    b1, st1 = load_bars("1h", symbol, w0 - 30 * DAY, w1 + 40 * DAY)
    if len(b4) < 260 or len(b1) < 400:
        return {"symbol": symbol, "skip": "insufficient_history",
                "bars_4h": len(b4), "bars_1h": len(b1)}
    pre4 = R.precompute_4h(b4, p)
    pre1 = R.precompute_1h(b1, p)
    boxes = R.list_box_candidates(b4, p, (w0, w1), pre4)
    opens1 = [b["open_time"] for b in b1]
    # 15m 整段只读一次（按事件切片）；逐事件重复扫 parquet 会把扫描拖到不可用
    b15_all, _ = load_bars("15m", symbol, w0 - 2 * DAY, w1 + 45 * DAY)
    o15 = [b["open_time"] for b in b15_all]
    res = {"symbol": symbol, "box_candidates": len(boxes),
           "bars_4h": len(b4), "bars_1h": len(b1),
           "gaps_4h": st4["continuity"]["gap_segment_count"],
           "gaps_1h": st1["continuity"]["gap_segment_count"],
           "schemes": {}}

    # ── 基线：原策略价格链 ──
    base_events = []
    active_until = 0
    for box in boxes:
        if box["created_ms"] < active_until:
            continue
        i0 = R._first_index_after(opens1, box["created_ms"] - MS_H)
        if i0 is None:
            continue
        r = R.baseline_price_chain(b1, box, p, i0)
        base_events.append({"symbol": symbol, "scheme": "baseline",
                            "box": box, "state": r.get("state"),
                            "decided_ms": r.get("decided_ms"),
                            "close": r.get("close"),
                            "why": r.get("why")})
        active_until = (r.get("decided_ms") or box["created_ms"]) + 24 * MS_H
    base_cands = [e for e in base_events if e["state"] == "CANDIDATE"]
    base_trades = []
    for e in base_cands:
        cms = e["decided_ms"]
        lo = max(0, _bisect(o15, cms - 2 * DAY))
        hi = min(len(b15_all), _bisect(o15, cms + 45 * DAY))
        cf = {"decided_ms": cms, "close": e.get("close"), "stage": "breakout"}
        tr = R.simulate_trade(b15_all[lo:hi], b4, cf, e["box"], p, slip)
        tr.update({"symbol": symbol, "scheme": "baseline", "confirm_ms": cms,
                   "box_created_ms": e["box"]["created_ms"],
                   "H": e["box"]["H"], "L": e["box"]["L"], "A": e["box"]["A"]})
        base_trades.append(tr)
    res["schemes"]["baseline"] = {
        "state_counts": _count(e["state"] for e in base_events),
        "candidates": [{"symbol": symbol, "decided_ms": e["decided_ms"],
                        "box_created_ms": e["box"]["created_ms"]}
                       for e in base_cands],
        "trades": base_trades,
        "summary": summarize_trades(base_trades),
    }

    # ── 三组新方案 ──
    for sch in ("4a", "4b", "4c"):
        evs = R.run_scheme(symbol, boxes, b1, sch, p, pre1)
        sc = {"state_counts": _count(e["state"] for e in evs),
              "events": len(evs), "trades": []}
        confirmed = [e for e in evs if e["state"] == R.ST_CONFIRMED]
        for e in confirmed:
            cms = e["confirm"]["decided_ms"]
            lo = max(0, _bisect(o15, cms - 2 * DAY))
            hi = min(len(b15_all), _bisect(o15, cms + 45 * DAY))
            b15 = b15_all[lo:hi]
            tr = R.simulate_trade(b15, b4, e["confirm"], e["box"], p, slip)
            tr.update({"symbol": symbol, "scheme": sch,
                       "confirm_ms": e["confirm"]["decided_ms"],
                       "box_created_ms": e["box"]["created_ms"],
                       "R_loc": e["resistance"]["R"],
                       "H": e["box"]["H"], "L": e["box"]["L"], "A": e["box"]["A"],
                       "S_loc": (e["sweep"] or {}).get("S_loc"),
                       "tbr_ratio": (e["absorption"] or {}).get("tbr_ratio")})
            sc["trades"].append(tr)
        sc["summary"] = summarize_trades(sc["trades"])
        # 事件明细（不塞全文，只留可复核的关键点）
        sc["event_digest"] = [] if slim else [{
            "box_created_ms": e["box"]["created_ms"],
            "H": e["box"]["H"], "L": e["box"]["L"], "A": e["box"]["A"],
            "state": e["state"], "why": e.get("why"),
            "sweep_ms": (e["sweep"] or {}).get("decided_ms"),
            "S_loc": (e["sweep"] or {}).get("S_loc"),
            "R_loc": (e["resistance"] or {}).get("R"),
            "R_method": (e["resistance"] or {}).get("method"),
            "R_fallback": (e["resistance"] or {}).get("fallback"),
            "R_reason": (e["resistance"] or {}).get("reason_q"),
            "absorb_pass": (e["absorption"] or {}).get("pass"),
            "confirm_ms": (e.get("confirm") or {}).get("decided_ms"),
            "confirm_stage": (e.get("confirm") or {}).get("stage"),
        } for e in evs]
        res["schemes"][sch] = sc
    return res


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="w0", default="2025-01-01")
    ap.add_argument("--to", dest="w1", default="2026-07-01")
    ap.add_argument("--tag", default="research")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--slippage", type=float, default=3.0)
    ap.add_argument("--slim", action="store_true",
                    help="不写 event_digest（文件小一个数量级；案例另用 03 脚本重算）")
    ap.add_argument("--param", action="append", default=[],
                    help="覆盖参数，形如 key=value（json 解析）")
    args = ap.parse_args()

    w0, w1 = to_ms(args.w0 + "T00:00:00Z"), to_ms(args.w1 + "T00:00:00Z")
    over = {"slippage_bps": args.slippage, "risk_config": "research_r_only",
            "participation_cap": 0.001}
    for kv in args.param:
        k, _, v = kv.partition("=")
        try:
            over[k] = json.loads(v)
        except Exception:                                  # noqa: BLE001
            over[k] = v
    p = R.resolve_params(over)

    syms = pool()
    if args.limit:
        syms = syms[:args.limit]
    t0 = time.time()
    rows, errors = [], []
    for n, s in enumerate(syms, 1):
        try:
            rows.append(scan_symbol(s, w0, w1, p, args.slippage, args.slim))
        except Exception as exc:                            # noqa: BLE001
            errors.append({"symbol": s, "error": f"{type(exc).__name__}: {exc}",
                           "trace": traceback.format_exc()[-800:]})
        if n % 25 == 0:
            print(f"  {n}/{len(syms)}  {round(time.time() - t0, 1)}s", flush=True)

    agg = aggregate(rows)
    rep = {
        "probe": "01_共同观察范围",
        "rules_version": R.RULES_VERSION,
        "generated_at_utc": iso(time.time() * 1000),
        "window": {"from": args.w0 + "T00:00:00Z", "to": args.w1 + "T00:00:00Z",
                   "from_ms": w0, "to_ms": w1},
        "params_used": p,
        "param_overrides": over,
        "slippage_bps": args.slippage,
        "pool": {"n": len(syms), "excluded": sorted(EXCLUDED),
                 "source": "数据/市场数据/parquet/candles/binance/um 目录（528）减 2 个指数"},
        "aggregate": agg,
        "per_symbol": rows,
        "errors": errors,
        "elapsed_sec": round(time.time() - t0, 1),
        "discipline": [
            "只读：不写库、不下单、不改生产；15m 与 1h/4h 全部走 research_15m 冷热合并读取器",
            "四条规则共用同一批箱体候选（list_box_candidates），不各自筛选",
            "基线不含 OI 门槛：历史 OI 可得时点不可证明，带它等于用不可证的量做闸门",
            "R 倍数 = 净收益率 / 5%（初始止损幅度），绕开本金与仓位口径冲突",
            "不含资金费：对比只算价格路径 + 手续费 + 滑点；资金费已在 replay_calc 实现，正式回测必须加",
        ],
    }
    OUT.mkdir(parents=True, exist_ok=True)
    fp = OUT / f"01_共同观察范围_{args.tag}.json"
    fp.write_text(json.dumps(rep, ensure_ascii=False, indent=1, default=str),
                  encoding="utf-8")
    print(json.dumps(agg, ensure_ascii=False, indent=1)[:3000])
    print("written", fp)
    return 0


def aggregate(rows: list) -> dict:
    ok = [r for r in rows if "skip" not in r]
    out = {
        "symbols_scanned": len(rows),
        "symbols_with_data": len(ok),
        "symbols_skipped": len(rows) - len(ok),
        "box_candidates_total": sum(r["box_candidates"] for r in ok),
        "schemes": {},
    }
    for sch in ("baseline", "4a", "4b", "4c"):
        states = {}
        trades = []
        for r in ok:
            sc = r["schemes"].get(sch) or {}
            for k, v in (sc.get("state_counts") or {}).items():
                states[k] = states.get(k, 0) + v
            trades.extend(sc.get("trades") or [])
        d = {"state_counts": states,
             "events": sum(states.values()) if sch != "baseline" else
             sum(v for k, v in states.items()),
             "confirmed": states.get("CONFIRMED", 0),
             "trade_summary": summarize_trades(trades)}
        if sch == "baseline":
            d["candidates"] = sum(
                len((r["schemes"]["baseline"].get("candidates") or [])) for r in ok)
        out["schemes"][sch] = d
    return out


if __name__ == "__main__":
    raise SystemExit(main())
