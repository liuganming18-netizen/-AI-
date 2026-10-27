# -*- coding: utf-8 -*-
"""ACCUM-EARLY-05-B｜01 核查 paper_engine 退出差异是否影响本研究入口。

工单第 2 条要求「先核查 04-D 指出的 paper_engine 退出差异是否影响本研究入口」：
  · 4h 最低价判退出（`advance_position_trailing` 的 `low_4h <= cur_stop` 分支）；
  · 15m 退出时刻（退出时刻到底记在 4h 整点还是 15m bar）；
  · 跟踪止损生效顺序（新止损是「4h 收盘那一刻」生效，还是「收盘后下一根 15m」生效）。

做法（**不写账本、不调 paper_engine 的退出函数**，纯算术复算）：
  1. 取真实入场样本两组：
     a. `04-B` 隔离链路的 6 笔真实持仓（有账本原始 exit_ts / exit_price 可对撞）；
     b. `04-B` 观察库的 44 条真实候选（有成交 bar 开盘价与入场参考价）。
  2. 同一批样本跑两条退出臂：
     A 臂 = 复刻 paper_engine 现行语义（04-B 的实际驱动方式：4h 收盘即生效、4h 低点判退出）；
     B 臂 = 按《用户要求与决策》与 04-D 的时序整改口径（4h 收盘后**下一根** 15m 生效、
            只用 15m 最低价判退出、退出时刻记在 15m bar）。
  3. **先验证 A 臂是不是 paper_engine**：6 笔真实持仓的 exit_ts / exit_price 必须逐位复现。
     复现不了 ⇒ 本脚本对「影响」的任何结论都不成立，直接报错退出（不许当通过）。
  4. 输出差异：退出时刻差、退出价差、毛盈亏差、退出路径（4h / 15m）、
     以及差异是否跨过资金费结算整点（跨过 ⇒ 资金费也会变，需另计）。

产物：`out/01_退出差异影响.json`
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common as C                                            # noqa: E402

EVID04 = C.PROJECT / "evidence" / "ACCUM-EARLY-04-B"
RUNS04 = EVID04 / "runs" / "04_chain"
OBS04 = EVID04 / "runs" / "03_observe" / "observe.sqlite3"

M15 = C.M15
H4 = C.H4


# ══════════════════════════════════════════════════════════════════════════
# 样本
# ══════════════════════════════════════════════════════════════════════════
def ledger_positions() -> list:
    """04-B 隔离链路的真实持仓（含账本原始退出字段，用于验证 A 臂）。"""
    out = []
    for run in ("run_cold", "run_hot"):
        for arm in ("4a", "4b", "4c"):
            p = RUNS04 / run / "paper" / f"paper_{arm}.sqlite3"
            if not p.exists():
                continue
            con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
            con.row_factory = sqlite3.Row
            try:
                rows = con.execute(
                    "SELECT position_id,symbol,entry_ts,entry_price,initial_stop_price,"
                    "stop_price,status,exit_ts,exit_price,exit_reason,trail_d "
                    "FROM paper_position ORDER BY position_id").fetchall()
            finally:
                con.close()
            for r in rows:
                d = dict(r)
                d.update({"source": "ledger", "run": run, "arm": arm})
                out.append(d)
    return out


def candidate_entries() -> list:
    """04-B 观察库的 44 条真实候选（成交 bar 开盘价 = 入场参考价）。"""
    if not OBS04.exists():
        return []
    con = sqlite3.connect(f"file:{OBS04}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            "SELECT scheme,symbol,confirm_close_ms,entry_bar_open_ms,entry_ref_price,status "
            "FROM accum04_candidate WHERE entry_bar_open_ms IS NOT NULL "
            "AND entry_ref_price IS NOT NULL ORDER BY confirm_close_ms").fetchall()
    finally:
        con.close()
    return [dict(r) for r in rows]


# ══════════════════════════════════════════════════════════════════════════
# 两条退出臂（纯算术，只用冻结包的取整原语）
# ══════════════════════════════════════════════════════════════════════════
def _round_protect(px: float, step: float) -> float:
    from strategies.wx_execution import round_protect_price
    return float(round_protect_price(px, step))


def _fill_out(raw: float, step: float, slip: float) -> float:
    from 策略.WyckoffMaster.trading_core.fill import price_breakdown
    return float(price_breakdown(raw_price=float(raw), is_buy=False,
                                price_step=float(step), slippage_rate=float(slip)
                                )["post_round_price"])


def _limits(symbol: str) -> dict:
    import paper_engine as PE
    return PE.contract_limits(symbol) or {}


def arm_a_legacy(*, bars15, bars4, entry_ts, initial_stop, step, trail_d, slip):
    """复刻 paper_engine 现行语义 + 04-B 的驱动顺序。

    顺序（与 `_worker04.py` 逐行对应）：
      每根 15m bar 收盘时刻 clk：
        ① 把「close_time <= clk」的 4h bar 全部推进：候选止损 = low×0.95，只上移；
           若该 4h bar 的 low <= 当前止损 ⇒ **按 4h 粒度判退出**，退出时刻 = clk；
        ② 再用本根 15m 的 low <= 当前止损 ⇒ 退出，退出时刻 = 本根 15m 收盘。
    """
    stop = float(initial_stop)
    i4 = 0
    updates = 0
    for b in bars15:
        clk = int(b["close_time"])
        if clk < int(entry_ts):
            continue
        while i4 < len(bars4) and int(bars4[i4]["close_time"]) <= clk:
            b4 = bars4[i4]
            i4 += 1
            if int(b4["close_time"]) <= int(entry_ts):
                continue                              # paper_engine 的因果守卫
            cand = _round_protect(float(b4["low"]) * (1.0 - trail_d), step)
            if cand > stop:
                stop = cand
                updates += 1
            if float(b4["low"]) <= stop:
                return {"hit": True, "path": "4h", "exit_ts": clk,
                        "bar_open_ms": int(b["open_time"]), "stop_used": stop,
                        "raw_price": stop, "updates": updates,
                        "exit_bar_low": float(b4["low"])}
        if float(b["low"]) <= stop:
            return {"hit": True, "path": "15m", "exit_ts": clk,
                    "bar_open_ms": int(b["open_time"]), "stop_used": stop,
                    "raw_price": stop, "updates": updates,
                    "exit_bar_low": float(b["low"])}
    return {"hit": False, "path": None, "exit_ts": None, "stop_used": stop,
            "raw_price": None, "updates": updates}


def arm_b_fixed(*, bars15, bars4, entry_ts, initial_stop, step, trail_d, slip,
                gap_model="stop_price"):
    """按已定时序：4h 收盘后**下一根** 15m 才生效；只用 15m 判退出。"""
    stop = float(initial_stop)
    i4 = 0
    updates = 0
    for b in bars15:
        ot = int(b["open_time"])
        if int(b["close_time"]) < int(entry_ts):
            continue
        while i4 < len(bars4) and int(bars4[i4]["close_time"]) + M15 <= ot:
            b4 = bars4[i4]
            i4 += 1
            if int(b4["close_time"]) <= int(entry_ts):
                continue
            cand = _round_protect(float(b4["low"]) * (1.0 - trail_d), step)
            if cand > stop:
                stop = cand
                updates += 1
        o, lo = float(b["open"]), float(b["low"])
        if o <= stop:
            raw = {"stop_price": stop, "bar_open": o,
                   "midpoint": (o + stop) / 2.0}[gap_model]
            return {"hit": True, "path": "15m", "kind": "gap", "exit_ts": int(b["close_time"]),
                    "bar_open_ms": ot, "stop_used": stop, "raw_price": raw,
                    "updates": updates, "exit_bar_low": lo}
        if lo <= stop:
            return {"hit": True, "path": "15m", "kind": "intrabar",
                    "exit_ts": int(b["close_time"]), "bar_open_ms": ot,
                    "stop_used": stop, "raw_price": stop, "updates": updates,
                    "exit_bar_low": lo}
    return {"hit": False, "path": None, "exit_ts": None, "stop_used": stop,
            "raw_price": None, "updates": updates}


# ══════════════════════════════════════════════════════════════════════════
# 资金费结算整点是否被跨过
# ══════════════════════════════════════════════════════════════════════════
def funding_events(symbol: str, lo_ms: int, hi_ms: int) -> list:
    import paper_engine as PE
    con_mkt = PE.open_market()
    try:
        return [dict(r) for r in PE.funding_events(con_mkt, symbol, int(lo_ms), int(hi_ms))]
    except Exception:                                          # noqa: BLE001
        return []
    finally:
        con_mkt.close()


def funding_boundaries(symbol: str, lo_ms: int, hi_ms: int) -> list:
    if hi_ms is None or hi_ms <= lo_ms:
        return []
    return [int(e.get("funding_time") or e.get("time") or 0)
            for e in funding_events(symbol, lo_ms, hi_ms)]


def main() -> int:
    import accum_early_entry as AE
    import paper_engine as PE

    feed = AE.DataFeed()
    params = PE.approved_params()
    slip = float(params["slippage_each_side"])
    trail_d = float(params["trail_d"])
    out = {
        "generated_utc": C.iso(int(__import__("time").time() * 1000)),
        "why": ("核查 04-D 指出的 paper_engine 退出差异是否**真实影响**本研究入口。"
                "纯算术复算，不写账本、不改代码。"),
        "params": {"slippage_each_side": slip, "trail_d": trail_d,
                   "fee_each_side": params["fee_each_side"],
                   "signal_execution_delay_minutes": params["signal_execution_delay_minutes"],
                   "view_sha256": params.get("view_sha256")},
        "arm_a": "paper_engine 现行语义（4h 收盘即生效 + 4h 低点判退出）",
        "arm_b": "已定时序（4h 收盘后下一根 15m 生效 + 只用 15m 判退出）",
        "validation": {}, "samples": [], "summary": {},
    }

    # ── ① 验证 A 臂确实是 paper_engine：6 笔真实持仓必须逐位复现 ──────────────
    pos = [p for p in ledger_positions() if p.get("status") == "closed"]
    val = []
    for p in pos:
        sym = p["symbol"]
        step = float(_limits(sym).get("price_step") or 0.0)
        entry_ts = int(p["entry_ts"])
        end = int(p["exit_ts"] or entry_ts) + 3 * C.DAY
        b15 = feed.load("15m", sym, entry_ts - M15, end)
        b4 = feed.load("4h", sym, entry_ts - H4, end)
        a = arm_a_legacy(bars15=b15, bars4=b4, entry_ts=entry_ts,
                         initial_stop=float(p["initial_stop_price"]), step=step,
                         trail_d=trail_d, slip=slip)
        a["exit_price"] = None if a["raw_price"] is None else _fill_out(a["raw_price"], step, slip)
        val.append({
            "run": p["run"], "arm": p["arm"], "symbol": sym,
            "ledger_exit_ts": int(p["exit_ts"]), "model_exit_ts": a["exit_ts"],
            "ledger_exit_price": float(p["exit_price"]), "model_exit_price": a["exit_price"],
            "ledger_stop_at_exit": float(p["stop_price"]),
            "ts_match": a["exit_ts"] == int(p["exit_ts"]),
            "price_match": (a["exit_price"] is not None
                            and abs(a["exit_price"] - float(p["exit_price"])) < 1e-12),
            "path": a["path"],
        })
    out["validation"] = {
        "rows": val,
        "all_ts_match": all(r["ts_match"] for r in val) and bool(val),
        "all_price_match": all(r["price_match"] for r in val) and bool(val),
        "n": len(val),
    }
    if not (out["validation"]["all_ts_match"] and out["validation"]["all_price_match"]):
        out["summary"] = {"verdict": "A 臂无法复现 paper_engine 的真实退出 ⇒ 本脚本对影响不作结论"}
        C.dump("01_退出差异影响.json", out)
        print("FAIL: A 臂复现不了 paper_engine 的真实退出（见 validation）", file=sys.stderr)
        return 2

    # ── ② 两组样本各跑两臂 ────────────────────────────────────────────────
    samples = []
    for p in pos:
        samples.append({"source": "ledger", "key": f"{p['run']}:{p['arm']}:{p['symbol']}",
                        "symbol": p["symbol"], "entry_ts": int(p["entry_ts"]),
                        "entry_price": float(p["entry_price"]),
                        "initial_stop": float(p["initial_stop_price"])})
    for c in candidate_entries():
        step = float(_limits(c["symbol"]).get("price_step") or 0.0)
        from strategies.wx_execution import apply_order_rounding
        # 入场价必须用同一套取整（买入向上），否则两臂的初始止损会不同源
        from 策略.WyckoffMaster.trading_core.fill import price_breakdown as _pb
        entry_px = float(_pb(raw_price=float(c["entry_ref_price"]), is_buy=True,
                             price_step=step, slippage_rate=slip)["post_round_price"])
        stop0 = _round_protect(entry_px * (1.0 - float(params["stoploss_s"])), step)
        samples.append({"source": "candidate", "key": f"cand:{c['scheme']}:{c['symbol']}:{c['confirm_close_ms']}",
                        "scheme": c["scheme"], "symbol": c["symbol"],
                        "entry_ts": int(c["entry_bar_open_ms"]), "entry_price": entry_px,
                        "initial_stop": stop0})

    rows = []
    for s in samples:
        sym = s["symbol"]
        step = float(_limits(sym).get("price_step") or 0.0)
        if step <= 0:
            rows.append({**s, "skipped": "missing_contract_spec"})
            continue
        end = s["entry_ts"] + 30 * C.DAY
        b15 = feed.load("15m", sym, s["entry_ts"] - M15, end)
        b4 = feed.load("4h", sym, s["entry_ts"] - H4, end)
        a = arm_a_legacy(bars15=b15, bars4=b4, entry_ts=s["entry_ts"],
                         initial_stop=s["initial_stop"], step=step, trail_d=trail_d, slip=slip)
        b = arm_b_fixed(bars15=b15, bars4=b4, entry_ts=s["entry_ts"],
                        initial_stop=s["initial_stop"], step=step, trail_d=trail_d,
                        slip=slip, gap_model="stop_price")
        for r in (a, b):
            r["exit_price"] = None if r["raw_price"] is None else _fill_out(r["raw_price"], step, slip)
            r["gross_pnl"] = (None if r["exit_price"] is None
                              else (r["exit_price"] - s["entry_price"]))
        row = {**s,
               "a": a, "b": b,
               "delta": {
                   "exit_ts_min": (None if (a["exit_ts"] is None or b["exit_ts"] is None)
                                   else round((b["exit_ts"] - a["exit_ts"]) / 60000.0, 3)),
                   "exit_price": (None if (a["exit_price"] is None or b["exit_price"] is None)
                                  else round(b["exit_price"] - a["exit_price"], 12)),
                   "gross_pnl_per_unit": (None if (a["gross_pnl"] is None or b["gross_pnl"] is None)
                                          else round(b["gross_pnl"] - a["gross_pnl"], 12)),
                   "stop_used": (None if (a["stop_used"] is None or b["stop_used"] is None)
                                 else round(b["stop_used"] - a["stop_used"], 12)),
               }}
        if a["exit_ts"] is not None and b["exit_ts"] is not None and a["exit_ts"] != b["exit_ts"]:
            lo, hi = min(a["exit_ts"], b["exit_ts"]), max(a["exit_ts"], b["exit_ts"])
            row["funding_boundaries_between"] = len(funding_boundaries(sym, lo, hi))
        else:
            row["funding_boundaries_between"] = 0
        rows.append(row)

    out["samples"] = rows

    def _summ(tag: str) -> dict:
        sub = [r for r in rows if r.get("source") == tag and "skipped" not in r]
        hit = [r for r in sub if r["a"]["hit"] or r["b"]["hit"]]
        diff_ts = [r for r in sub if r["delta"]["exit_ts_min"] not in (None, 0)]
        diff_px = [r for r in sub if r["delta"]["exit_price"] not in (None, 0)]
        diff_stop = [r for r in sub if r["delta"]["stop_used"] not in (None, 0)]
        path4h = [r for r in sub if r["a"]["path"] == "4h"]
        fb = [r for r in sub if r.get("funding_boundaries_between")]
        return {
            "n": len(sub), "exited": len(hit),
            "exit_ts_diff": len(diff_ts), "exit_price_diff": len(diff_px),
            "stop_used_diff": len(diff_stop),
            "arm_a_4h_exit": len(path4h),
            "max_abs_ts_min": max([abs(r["delta"]["exit_ts_min"]) for r in diff_ts], default=0),
            "max_abs_gross_per_unit": max([abs(r["delta"]["gross_pnl_per_unit"])
                                           for r in sub
                                           if r["delta"]["gross_pnl_per_unit"] is not None],
                                          default=0),
            "funding_boundary_crossed": len(fb),
            "examples": [{"key": r["key"], "symbol": r["symbol"],
                          "a_exit_utc": C.iso(r["a"]["exit_ts"]), "b_exit_utc": C.iso(r["b"]["exit_ts"]),
                          "a_path": r["a"]["path"], "b_path": r["b"]["path"],
                          "delta": r["delta"]} for r in diff_ts[:5]],
        }

    out["summary"] = {
        "ledger": _summ("ledger"),
        "candidate": _summ("candidate"),
        "verdict": None,
    }
    tot_diff = out["summary"]["ledger"]["exit_ts_diff"] + out["summary"]["candidate"]["exit_ts_diff"]
    tot_4h = out["summary"]["ledger"]["arm_a_4h_exit"] + out["summary"]["candidate"]["arm_a_4h_exit"]
    out["summary"]["verdict"] = (
        "有真实影响：存在退出时刻/退出价/生效止损差异" if (tot_diff or tot_4h)
        else "未观测到差异（样本内两臂结果相同）")
    out["summary"]["needs_fix"] = bool(tot_diff or tot_4h)
    C.dump("01_退出差异影响.json", out)
    print(json.dumps({"verdict": out["summary"]["verdict"],
                      "ledger": out["summary"]["ledger"],
                      "candidate": out["summary"]["candidate"]},
                     ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
