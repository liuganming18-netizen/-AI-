# -*- coding: utf-8 -*-
"""SIGNAL-NOW-01-ANTIGRAVITY｜步骤2：按 UID 穿透 289 研究行 + `test_probe_succ` 时间线。

反重力纪律：
- **全部只读**（`mode=ro`）。不写账本、不调用路由接口、不动仓位。
- 自己从库里取字段，不抄 Work 的 JSON 结论。
- 注意字段真相：`live_signal` 表**没有** `approved` / `role` 列，它们在 `payload_json` 里
  （`payload.research.approved`、`payload.role`）。API 的顶层 `approved` 也是派生的。
  ⇒ 「approved=false 仍受保护」必须在 payload 层核对，只看列会得出错误结论。
"""
from __future__ import annotations

import json
import sqlite3
import time
from collections import Counter
from pathlib import Path

PROJ = Path("/Users/boos/Desktop/威科夫因子共振趋势")
ROOT = PROJ / "evidence" / "SIGNAL-NOW-01-ANTIGRAVITY"
OUT = ROOT / "out"
LIVE = PROJ / "框架/backend/live/live_signals.sqlite3"
MARKET = PROJ / "数据/市场数据/db/market_v2.sqlite3"
ARMS = {a: PROJ / f"数据/paper/paper_{a}.sqlite3" for a in ("3a", "3b", "3c", "3d")}


def utc(ms):
    if ms in (None, ""):
        return None
    try:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(int(ms) / 1000))
    except Exception:
        return f"非数值:{ms!r}"


def ro(p: Path):
    return sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=25)


def main() -> int:
    R = {"ticket": "SIGNAL-NOW-01-ANTIGRAVITY",
         "step": "2 289 研究行 + test_probe_succ 穿透（只读）", "at_utc": utc(time.time() * 1000)}

    # ══════════ A. 289 研究行身份（payload 层） ══════════
    con = ro(LIVE)
    try:
        rows = con.execute("""
            SELECT signal_uid, origin, executable_state, executable_reason,
                   event_time, confirmed_at, available_at, bar_open_time, bar_close_time,
                   payload_json, first_seen_at, updated_at
            FROM live_signal""").fetchall()
        R["live_run"] = {k: {"value": v, "updated_at": utc(u)}
                         for k, v, u in con.execute(
                             "SELECT key, value_json, updated_at FROM live_run")}
    finally:
        con.close()

    R["research_rows"] = {"n": len(rows)}
    agg = {"origin": Counter(), "executable_state": Counter(), "display_state": Counter(),
           "available_at_null": 0, "payload_role": Counter(), "payload_approved": Counter(),
           "payload_available_at_flat": Counter(),
           "payload_ts_available_at_status": Counter(),
           "payload_ts_available_at_reason": Counter(),
           "payload_arm": Counter(),
           "payload_position_multiplier": Counter(), "identity_repair_present": 0}
    details = []
    for (uid, origin, exe, exe_reason, et, ct, av, bot, bct, pj, fsa, ua) in rows:
        try:
            pl = json.loads(pj) if pj else {}
        except Exception:
            pl = {}
        ts = pl.get("timestamps") or {}
        agg["origin"][origin] += 1
        agg["executable_state"][exe] += 1
        if av is None:
            agg["available_at_null"] += 1
        role = pl.get("role")
        agg["payload_role"][str(role)] += 1
        agg["payload_approved"][str(pl.get("approved"))] += 1
        agg["payload_available_at_flat"][str(pl.get("available_at"))] += 1
        agg["payload_ts_available_at_status"][str(ts.get("available_at_status"))] += 1
        agg["payload_ts_available_at_reason"][str(ts.get("available_at_reason"))] += 1
        agg["payload_arm"][str(pl.get("arm"))] += 1
        agg["payload_position_multiplier"][str(pl.get("position_multiplier"))] += 1
        if pl.get("identity_repair"):
            agg["identity_repair_present"] += 1
        details.append({"uid": uid, "origin": origin, "executable_state": exe,
                        "available_at": av, "event_time": et, "confirmed_at": ct,
                        "bar_open_time": bot, "bar_close_time": bct,
                        "payload_role": role, "payload_approved": pl.get("approved"),
                        "payload_available_at_flat": pl.get("available_at"),
                        "payload_ts_available_at_status": ts.get("available_at_status"),
                        "payload_ts_available_at_reason": ts.get("available_at_reason"),
                        "payload_arm": pl.get("arm"),
                        "payload_position_multiplier": pl.get("position_multiplier"),
                        "payload_origin_inner": pl.get("origin"),
                        "payload_execution_state_inner": pl.get("execution_state"),
                        "payload_available_at_inner": pl.get("available_at"),
                        "executable_reason": exe_reason,
                        "first_seen_at": fsa, "updated_at": ua})
    R["research_rows"]["aggregate"] = {k: dict(v) if isinstance(v, Counter) else v
                                       for k, v in agg.items()}
    R["research_rows"]["sample_3"] = details[:3]
    R["research_rows"]["uid_list_sha1_hint"] = {
        "n_unique_uid": len({d["uid"] for d in details}),
        "all_origin_historical_research": set(agg["origin"]) == {"historical_research"},
        "all_executable_historical_not_executable":
            set(agg["executable_state"]) == {"historical_not_executable"},
        "all_available_at_null": agg["available_at_null"] == len(rows),
        "all_payload_role_research_only": set(agg["payload_role"]) == {"research_only"},
        "all_payload_approved_false": set(agg["payload_approved"]) == {"False"},
        "identity_repair_present_n": agg["identity_repair_present"],
        "note": "API 的 research.approved 取自 payload.approved（live_api.py:524），"
                "不是接口现算的默认值 ⇒ approved=false 是**库里存的事实**。",
    }

    # payload 内外是否自相矛盾（01 单的历史教训：列与 payload 互相打架）
    inner_conflict = []
    for d in details:
        oi, ei, ai = (d["payload_origin_inner"], d["payload_execution_state_inner"],
                      d["payload_available_at_inner"])
        if oi not in (None, "historical_research"):
            inner_conflict.append({"uid": d["uid"], "field": "payload.origin", "value": oi})
        if ei not in (None, "historical_not_executable"):
            inner_conflict.append({"uid": d["uid"], "field": "payload.execution_state", "value": ei})
        if ai is not None:
            inner_conflict.append({"uid": d["uid"], "field": "payload.available_at", "value": ai})
    R["research_rows"]["payload_inner_conflicts"] = {"n": len(inner_conflict),
                                                    "sample": inner_conflict[:5]}

    # ══════════ B. 研究行是否污染了纸面账本 ══════════
    research_uids = {d["uid"] for d in details}
    R["ledger_contamination"] = {}
    for arm, path in ARMS.items():
        if not path.exists():
            R["ledger_contamination"][arm] = {"db": str(path), "exists": False}
            continue
        c = ro(path)
        try:
            uids = set()
            for t, col in (("paper_pending", "signal_uid"), ("paper_order", "event_uid"),
                           ("paper_fill", "signal_uid"), ("paper_position", "signal_uid"),
                           ("paper_processed", "signal_uid")):
                try:
                    uids |= {r[0] for r in c.execute(f"SELECT DISTINCT {col} FROM {t}")}
                except sqlite3.OperationalError:
                    pass
            hit = sorted(uids & research_uids)
            R["ledger_contamination"][arm] = {
                "db": str(path), "exists": True,
                "ledger_uids_n": len(uids), "research_uid_hits": hit,
                "contaminated": bool(hit),
            }
        finally:
            c.close()

    # ══════════ C. test_probe_succ 全链穿透（3a 臂） ══════════
    UID = "test_probe_succ"
    c = ro(ARMS["3a"])
    try:
        def one(sql, args=()):
            try:
                cur = c.execute(sql, args)
                cols = [d[0] for d in cur.description]
                return [dict(zip(cols, r)) for r in cur.fetchall()]
            except sqlite3.OperationalError as e:
                return [{"_error": str(e)}]

        R["probe_chain"] = {
            "uid": UID,
            "paper_pending": one("SELECT * FROM paper_pending WHERE signal_uid=?", (UID,)),
            "paper_order": one("SELECT * FROM paper_order WHERE event_uid=?", (UID,)),
            "paper_fill": one("SELECT * FROM paper_fill WHERE signal_uid=?", (UID,)),
            "paper_position": one("SELECT * FROM paper_position WHERE signal_uid=?", (UID,)),
            "paper_processed": one("SELECT * FROM paper_processed WHERE signal_uid=?", (UID,)),
            "paper_request_anchor": one("SELECT * FROM paper_request_anchor WHERE event_uid=?", (UID,)),
            "paper_stop_history": one("SELECT * FROM paper_stop_history WHERE position_id=4"),
            "paper_realized": one("SELECT * FROM paper_realized WHERE position_id=4"),
        }
        # 同批探针（相邻 id）
        R["probe_chain"]["neighbour_orders"] = one(
            "SELECT order_id,event_uid,status,reject_reason,ts,event_time,requested_at,"
            "bar_open_time,fill_price FROM paper_order ORDER BY order_id")
        R["probe_chain"]["all_pending"] = one(
            "SELECT pending_id,symbol,signal_uid,status,reason,event_time,available_at,"
            "first_request_ms,earliest_fill_ms,created_at,resolved_at,epoch_ms,origin "
            "FROM paper_pending ORDER BY pending_id")
        R["probe_chain"]["all_positions"] = one(
            "SELECT position_id,symbol,status,entry_ts,exit_ts,entry_price,stop_price,"
            "qty,planned_risk,initial_stop_price,signal_uid,origin FROM paper_position "
            "ORDER BY position_id")
        R["probe_chain"]["table_counts"] = {}
        for t in ("paper_pending", "paper_order", "paper_fill", "paper_position",
                  "paper_processed", "paper_request_anchor", "paper_stop_history",
                  "paper_realized", "paper_equity", "paper_blocked"):
            try:
                R["probe_chain"]["table_counts"][t] = c.execute(
                    f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            except sqlite3.OperationalError as e:
                R["probe_chain"]["table_counts"][t] = f"ERR:{e}"
    finally:
        c.close()

    # ══════════ D. 行情库 bar 取证（成交价是不是 bar 历史值） ══════════
    pe = (R["probe_chain"]["paper_pending"] or [{}])[0]
    fl = (R["probe_chain"]["paper_fill"] or [{}])[0]
    bot = fl.get("bar_open_time") or pe.get("earliest_fill_ms")
    R["market_bar"] = {}
    if bot:
        try:
            mc = ro(MARKET)
            try:
                # 注意：字段名是 symbol（不是 market_id），market='um'
                cur = mc.execute(
                    "SELECT exchange,market,symbol,timeframe,open_time,close_time,open,high,low,"
                    "close,is_closed,available_at,source_id FROM candles "
                    "WHERE timeframe='15m' AND symbol='BTCUSDT' AND open_time=?", (int(bot),))
                cols = [d[0] for d in cur.description]
                R["market_bar"] = {"queried_open_time": int(bot),
                                   "queried_open_time_utc": utc(bot),
                                   "rows": [dict(zip(cols, r)) for r in cur.fetchall()]}
                # 全池同一根 bar 的 available_at 分布 —— 判断「fill 早于可得」是否孤例
                cur2 = mc.execute(
                    "SELECT available_at FROM candles WHERE timeframe='15m' AND open_time=? "
                    "AND is_closed=1", (int(bot),))
                vals = sorted(v for (v,) in cur2.fetchall() if v)
                fill_ts = chain_fill_ts = (fl.get("fill_ts") if isinstance(fl, dict) else None)
                R["market_bar"]["pool_available_at_dist"] = {
                    "n": len(vals),
                    "min_utc": utc(vals[0]) if vals else None,
                    "median_utc": utc(vals[len(vals) // 2]) if vals else None,
                    "max_utc": utc(vals[-1]) if vals else None,
                    "n_later_than_fill_ts": (sum(1 for v in vals if fill_ts and v > fill_ts)
                                             if fill_ts else None),
                    "fill_ts": fill_ts, "fill_ts_utc": utc(fill_ts),
                    "note": "该 15m bar 全池各标的的 available_at；若大量标的晚于 fill_ts，"
                            "说明「记账早于数据自报可得」是系统性的，不是单笔异常",
                }
            finally:
                mc.close()
        except Exception as e:
            R["market_bar"] = {"error": str(e)}

    # ══════════ E. 时间链 + 单调关系 ══════════
    def g(d, k):
        return d.get(k) if isinstance(d, dict) else None

    chain = {
        "event_time": g(pe, "event_time"),
        "available_at": g(pe, "available_at"),
        "first_request_ms": g(pe, "first_request_ms"),
        "earliest_fill_ms": g(pe, "earliest_fill_ms"),
        "created_at(pending登记)": g(pe, "created_at"),
        "bar_open_time": g(fl, "bar_open_time"),
        "bar_close_time": g(fl, "bar_close_time"),
        "fill_ts": g(fl, "fill_ts"),
        "resolved_at": g(pe, "resolved_at"),
        "entry_ts": g((R["probe_chain"]["paper_position"] or [{}])[0], "entry_ts"),
        "order_ts": g((R["probe_chain"]["paper_order"] or [{}])[0], "ts"),
        "order_requested_at": g((R["probe_chain"]["paper_order"] or [{}])[0], "requested_at"),
    }
    R["time_chain"] = {"raw_ms": chain,
                       "utc": {k: utc(v) for k, v in chain.items()}}

    def cmp(a, b):
        if chain.get(a) is None or chain.get(b) is None:
            return None
        return int(chain[b]) - int(chain[a])

    R["monotonic"] = {
        "available_at 是否为空（应为空 ⇒ unproven）": chain["available_at"] is None,
        "event_time <= first_request": (cmp("event_time", "first_request_ms") or 0) >= 0,
        "first_request <= created_at(登记)": (cmp("first_request_ms", "created_at(pending登记)") or 0) >= 0,
        "first_request <= earliest_fill(已批准15分钟延迟)": (cmp("first_request_ms", "earliest_fill_ms") or 0) >= 0,
        "bar_open >= earliest_fill（执行bar不早于应成交点）":
            (cmp("earliest_fill_ms", "bar_open_time") or 0) >= 0,
        "bar_open < bar_close": (cmp("bar_open_time", "bar_close_time") or 0) > 0,
        "fill_ts > bar_close（闭bar后才记账）": (cmp("bar_close_time", "fill_ts") or 0) > 0,
        "fill_ts == resolved_at": chain["fill_ts"] == chain["resolved_at"],
        "entry_ts == bar_open（回填事实）": chain["entry_ts"] == chain["bar_open_time"],
        "entry_ts < fill_ts（回填早于真实记账）": (cmp("entry_ts", "fill_ts") or 0) > 0,
        "order_ts == fill_ts": chain["order_ts"] == chain["fill_ts"],
        "bar_open - event_time（信号年龄，ms）": (cmp("event_time", "bar_open_time") or 0),
        "fill_ts - bar_open（回填量，ms）": (cmp("bar_open_time", "fill_ts") or 0),
        "fill_ts - bar_close（闭bar后延迟，ms）": (cmp("bar_close_time", "fill_ts") or 0),
    }

    # 价格取证：成交价是不是就是 bar 的 open
    mb = (R["market_bar"].get("rows") or [{}])[0] if R["market_bar"].get("rows") else {}

    def _num(x):
        try:
            return float(x)
        except (TypeError, ValueError):
            return None

    _raw, _open = _num(g(fl, "raw_price")), _num(mb.get("open"))
    R["price_provenance"] = {
        "fill.raw_price": g(fl, "raw_price"),
        "market_bar.open": mb.get("open"),
        "market_bar.high": mb.get("high"),
        "market_bar.low": mb.get("low"),
        "market_bar.close": mb.get("close"),
        # 数值比较（库里 open 是 '80429.20' 文本，账本 raw_price 是 80429.2 浮点，字符串比会假阴性）
        "raw_price_equals_bar_open": (_raw is not None and _open is not None
                                      and abs(_raw - _open) < 1e-9),
        "raw_price_minus_bar_open": (None if (_raw is None or _open is None) else _raw - _open),
        "fill_price": g(fl, "fill_price"),
        "fill_price_minus_raw": (None if (_num(g(fl, "fill_price")) is None or _raw is None)
                                 else _num(g(fl, "fill_price")) - _raw),
        "slippage_cost": g(fl, "slippage_cost"),
        "market_bar_available_at": mb.get("available_at"),
        "market_bar_available_at_utc": utc(mb.get("available_at")),
        "fill_ts_vs_bar_available_at_ms":
            (int(mb["available_at"]) - int(chain["fill_ts"]))
            if mb.get("available_at") and chain.get("fill_ts") else None,
        "note": "若 fill_ts 早于该 bar 的 available_at ⇒ 记账时刻早于数据自报可得时刻（PIT 疑点）",
    }

    # ══════════ F. 仓位当前是否仍被运行时管理 ══════════
    pos = (R["probe_chain"]["paper_position"] or [{}])[0]
    R["position_status"] = {
        "position_id": pos.get("position_id"), "symbol": pos.get("symbol"),
        "status": pos.get("status"), "exit_ts": pos.get("exit_ts"),
        "entry_ts": pos.get("entry_ts"), "entry_ts_utc": utc(pos.get("entry_ts")),
        "stop_price": pos.get("stop_price"), "qty": pos.get("qty"),
        "planned_risk": pos.get("planned_risk"),
        "initial_stop_price": pos.get("initial_stop_price"),
        "is_open": pos.get("status") == "open",
        "stop_history_rows": len(R["probe_chain"]["paper_stop_history"] or []),
        "realized_rows": len(R["probe_chain"]["paper_realized"] or []),
    }

    OUT.mkdir(parents=True, exist_ok=True)
    p = OUT / "02_UID穿透.json"
    p.write_text(json.dumps(R, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    # ── 摘要 ──
    rr = R["research_rows"]
    print("=== A. 289 研究行（payload 层） ===")
    print(f"  行数={rr['n']}  origin={dict(agg['origin'])}")
    print(f"  executable_state={dict(agg['executable_state'])}")
    print(f"  payload.role={dict(agg['payload_role'])}  payload.approved={dict(agg['payload_approved'])}")
    print(f"  payload.available_at(平铺)={dict(agg['payload_available_at_flat'])}")
    print(f"  payload.timestamps.available_at_status={dict(agg['payload_ts_available_at_status'])}")
    print(f"  payload.timestamps.available_at_reason={dict(agg['payload_ts_available_at_reason'])}")
    print(f"  available_at 为 NULL = {agg['available_at_null']}/{rr['n']}")
    print(f"  payload.arm={dict(agg['payload_arm'])}")
    print(f"  payload.position_multiplier={dict(agg['payload_position_multiplier'])}")
    print(f"  身份判据: {json.dumps(rr['uid_list_sha1_hint'], ensure_ascii=False)}")
    print(f"  payload 内外矛盾 = {rr['payload_inner_conflicts']['n']}")
    print("\n=== B. 账本是否被研究行污染 ===")
    for a, v in R["ledger_contamination"].items():
        print(f"  {a}: {v}")
    print("\n=== C. test_probe_succ 链路 ===")
    print(f"  pending={len(R['probe_chain']['paper_pending'])} order={len(R['probe_chain']['paper_order'])} "
          f"fill={len(R['probe_chain']['paper_fill'])} position={len(R['probe_chain']['paper_position'])}")
    print(f"  表计数={R['probe_chain']['table_counts']}")
    print("\n=== E. 时间链（UTC） ===")
    for k, v in R["time_chain"]["utc"].items():
        print(f"  {k:26s} = {v}")
    print("\n=== E. 单调关系 ===")
    for k, v in R["monotonic"].items():
        print(f"  {k}: {v}")
    print("\n=== D/价格取证 ===")
    print(f"  raw_price={R['price_provenance']['fill.raw_price']} "
          f"bar.open={R['price_provenance']['market_bar.open']} "
          f"相等={R['price_provenance']['raw_price_equals_bar_open']}")
    print(f"  bar available_at={R['price_provenance']['market_bar_available_at_utc']} "
          f"vs fill_ts={R['time_chain']['utc'].get('fill_ts')} "
          f"差={R['price_provenance']['fill_ts_vs_bar_available_at_ms']} ms")
    print("\n=== F. 仓位状态 ===")
    print(f"  {json.dumps(R['position_status'], ensure_ascii=False)}")
    print(f"\n产物：{p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
