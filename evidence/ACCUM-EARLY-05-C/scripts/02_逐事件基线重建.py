# -*- coding: utf-8 -*-
"""ACCUM-EARLY-05-C｜02 逐事件基线重建

目标（任务第 2 条 + 第 3 条的前半）
------------------------------------
1. 用**当前** `replay_calc.py`（身份见 01 产物里的 SHA256）重跑 51 条候选 × 2 套口径；
2. 与两个上游逐笔对撞：
   * `04-D/recalc_records`（102 笔，脱离封装的手工复算）；
   * `04-C/02_原策略基线结果.json`（只有汇总 + 3 条样本 ⇒ 只能对汇总）；
3. **保留**拒单、未成交、期末持仓，不补齐、不调参；
4. 把每笔落到 05-A 定的三段区间里（研究集 / 已泄露验证集 / 真样本外）；
5. 只给「逐事件盈亏」与「逐事件求和」，**不给任何叫「组合回撤」的数**。

为什么必须重建（而不是沿用 04-C 的数字）
------------------------------------------
`replay_calc.py` 的修改时间 2026-10-26 22:11 与 04-C 基线产物落盘时间 22:11 同分钟，
而 04-D 的时序整改在 22:11 写入、22:17 才出复算结果 ⇒ 04-C 基线跑在整改前还是整改后
从产物里看不出来。本脚本用当前文件重跑，并把哈希写进产物，从此可复算。
"""
from __future__ import annotations

import json
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path("/Users/boos/Desktop/威科夫因子共振趋势")
BACKEND = ROOT / "框架" / "backend"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(BACKEND))

import replay_calc as RC
import research_15m as R15

HOT_DB = ROOT / "数据" / "市场数据" / "db" / "market_v2.sqlite3"
COLD_DIR = ROOT / "数据" / "市场数据" / "parquet"
CAND_SRC = ROOT / "evidence" / "ACCUM-EARLY-02" / "out" / "04_回放计算修复.json"
UPD_04C = ROOT / "evidence" / "ACCUM-EARLY-04-C" / "out" / "02_原策略基线结果.json"
UPD_04D = ROOT / "evidence" / "ACCUM-EARLY-04-D" / "out" / "recalc_audit_04d.json"
UPD_04D_MANIFEST = ROOT / "evidence" / "ACCUM-EARLY-04-D" / "out" / "window3_usability_manifest.json"
OUT_DIR = ROOT / "evidence" / "ACCUM-EARLY-05-C" / "out"

# 05-A 定义的三段区间（UTC）
INTERVALS = [
    ("区间一_研究集", 1735689600000, 1782864000000, "2025-01-01 ~ 2026-07-01，参数生成地"),
    ("区间二_已泄露验证集", 1782864000000, 1791763200000, "2026-07-01 ~ 2026-10-12，04-A 已完整算过并公开 ⇒ 不得当独立验证"),
    ("区间三_真样本外", 1791763200000, 4102444800000, "2026-10-12 之后，规则冻结后一次性盲测才算数"),
]


def to_ms(s: str) -> int:
    return int(datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ")
               .replace(tzinfo=timezone.utc).timestamp() * 1000)


def iso(ms) -> str | None:
    if ms is None:
        return None
    return datetime.fromtimestamp(int(ms) / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def interval_of(ms: int) -> str:
    for name, lo, hi, _ in INTERVALS:
        if lo <= ms < hi:
            return name
    return "区间外"


def load_funding(con, symbol: str, entry_ms: int, span_days: int = 14):
    rs = con.execute(
        """SELECT funding_time, funding_rate, mark_price, fetched_at
           FROM funding_history
           WHERE exchange='binance' AND market='um' AND symbol=?
             AND rate_type='settled' AND funding_time > ? AND funding_time <= ?
           ORDER BY funding_time""",
        (symbol, int(entry_ms), int(entry_ms + span_days * 86400000))).fetchall()
    return [{"time_ms": int(r[0]),
             "rate": (float(r[1]) if r[1] is not None else None),
             "mark_price": (float(r[2]) if r[2] is not None else None),
             "fetched_at": r[3]} for r in rs]


def main():
    t_all = time.time()
    out = {
        "task": "ACCUM-EARLY-05-C",
        "probe": "02_逐事件基线重建",
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "engine": {
            "module": "框架/backend/replay_calc.py",
            "note": "身份以 01_输入快照与版本锁定.json 的 code_identity 为准；本脚本不重复算哈希",
        },
        "calibers": {},
        "crosscheck": {},
        "interval_definition": [
            {"name": n, "start_ms": lo, "end_ms": hi, "start_utc": iso(lo), "end_utc": iso(hi), "定性": d}
            for n, lo, hi, d in INTERVALS
        ],
        "errors": [],
    }

    # ── 1. 候选清单（上游 = ACCUM-EARLY-02 的 102 条）─────────────────
    acc2 = json.loads(CAND_SRC.read_text(encoding="utf-8"))
    rows_all = acc2["B_全量回放"]["逐条"]
    rows_51 = [r for r in rows_all if r.get("caliber") == "生效视图_5bps"]
    out["candidate_source"] = {
        "path": str(CAND_SRC.relative_to(ROOT)),
        "rows_total": len(rows_all),
        "rows_5bps": len(rows_51),
        "note": "候选由上游状态机在生产运行中产生；本单不重新生成候选（那是 04 脚本的观察母集工作）。",
    }

    merged_15m = R15.Merged15m(str(HOT_DB), str(COLD_DIR))
    merged_4h = R15.Merged15m(str(HOT_DB), str(COLD_DIR), timeframe="4h")
    con = sqlite3.connect(f"file:{HOT_DB}?mode=ro", uri=True)

    per_caliber = {}
    try:
        for cal_name in ["生效视图_5bps", "策略包_3bps"]:
            print(f">>> 重跑口径 {cal_name} ...")
            events = []
            for cand in rows_51:
                sym = cand["symbol"]
                cid = cand["candidate_id"]
                entry_ms = to_ms(cand["entry_bar_utc"])

                bars15, _ = merged_15m.load(sym, entry_ms - 2 * 3600_000,
                                            entry_ms + 14 * 86400000)
                elig15 = [b for b in bars15 if int(b["open_time"]) >= entry_ms]
                if not elig15:
                    events.append({"candidate_id": cid, "symbol": sym, "status": "rejected",
                                   "reject_reason": "no_bars_after_entry",
                                   "entry_utc": iso(entry_ms), "entry_ms": entry_ms,
                                   "interval": interval_of(entry_ms)})
                    continue
                entry_bar = elig15[0]
                prevs = [b for b in bars15 if int(b["open_time"]) < int(entry_bar["open_time"])]
                prev_bar = prevs[-1] if prevs else None

                bars4, _ = merged_4h.load(sym, entry_ms - 8 * 86400000,
                                          entry_ms + 30 * 86400000)
                funding = load_funding(con, sym, int(entry_bar["open_time"]))

                res = RC.replay_one(symbol=sym, entry_bar=entry_bar, prev_bar=prev_bar,
                                    bars_15m=elig15, bars_4h=bars4,
                                    funding_events=funding, caliber_name=cal_name)

                ent = res.get("entry", {}) or {}
                exi = res.get("exit", {}) or {}
                pnl = res.get("盈亏", {}) or {}
                fund = res.get("资金费", {}) or {}
                ok = res.get("status") == "ok"

                # 期末未平仓：exit 未命中 ⇒ 记 is_open=True，realized 为 None，未实现单列
                is_open = ok and not exi.get("hit")
                exit_ms = to_ms(exi["exit_utc"]) if exi.get("exit_utc") else None

                events.append({
                    "candidate_id": cid,
                    "symbol": sym,
                    "status": res.get("status"),
                    "reject_reason": (None if ok else ent.get("reject")),
                    "reject_detail": (None if ok else ent.get("detail")),
                    "entry_utc": iso(int(entry_bar["open_time"])),
                    "entry_ms": int(entry_bar["open_time"]),
                    "entry_price": (float(ent["entry_price"]) if ent.get("entry_price") is not None else None),
                    "initial_stop": (float(ent["initial_stop_price"]) if ent.get("initial_stop_price") is not None else None),
                    "qty": (float(ent["qty"]) if ent.get("qty") is not None else None),
                    "notional": (float(ent["notional"]) if ent.get("notional") is not None else None),
                    "participation_capped": bool(ent.get("participation_capped")),
                    "exit_utc": exi.get("exit_utc"),
                    "exit_ms": exit_ms,
                    "exit_price": (float(exi["exit_fill"]) if exi.get("exit_fill") is not None else None),
                    "exit_kind": exi.get("kind"),
                    "exit_reason": exi.get("exit_reason"),
                    "is_open_at_window_end": is_open,
                    "hold_ms": (exit_ms - int(entry_bar["open_time"])) if exit_ms else None,
                    "hold_hours": (round((exit_ms - int(entry_bar["open_time"])) / 3600000.0, 3)
                                   if exit_ms else None),
                    "gross": (float(pnl.get("gross")) if pnl.get("gross") is not None else None),
                    "entry_fee": (float(pnl.get("entry_fee")) if pnl.get("entry_fee") is not None else None),
                    "exit_fee": (float(pnl.get("exit_fee")) if pnl.get("exit_fee") is not None else None),
                    "funding_cost": (float(fund.get("成本合计")) if fund.get("成本合计") is not None else None),
                    "funding_events": fund.get("事件数"),
                    "funding_order_uncertain": bool(fund.get("顺序不确定")),
                    "realized_net": (float(pnl.get("realized_net")) if pnl.get("realized_net") is not None else None),
                    "unrealized_net": (float(pnl.get("unrealized_net")) if pnl.get("unrealized_net") is not None else None),
                    "trailed": bool(res.get("止损上移过")),
                    "stop_path_len": res.get("止损路径_条数"),
                    "interval": interval_of(int(entry_bar["open_time"])),
                })
            per_caliber[cal_name] = events
    finally:
        con.close()

    # ── 2. 统计（严格区分「逐事件」与「组合」）────────────────────────
    for cal_name, events in per_caliber.items():
        ok = [e for e in events if e["status"] == "ok"]
        rejected = [e for e in events if e["status"] != "ok"]
        closed = [e for e in ok if not e["is_open_at_window_end"]]
        still_open = [e for e in ok if e["is_open_at_window_end"]]
        winners = [e for e in closed if (e["realized_net"] or 0) > 0]
        losers = [e for e in closed if (e["realized_net"] or 0) < 0]
        flats = [e for e in closed if (e["realized_net"] or 0) == 0]

        sum_realized = sum(e["realized_net"] for e in closed if e["realized_net"] is not None)
        sum_unrealized = sum(e["unrealized_net"] for e in still_open if e["unrealized_net"] is not None)

        by_interval = {}
        for iv, _, _, _ in INTERVALS:
            sub = [e for e in events if e["interval"] == iv]
            sub_closed = [e for e in sub if e["status"] == "ok" and not e["is_open_at_window_end"]]
            by_interval[iv] = {
                "candidates": len(sub),
                "executed": len([e for e in sub if e["status"] == "ok"]),
                "rejected": len([e for e in sub if e["status"] != "ok"]),
                "closed": len(sub_closed),
                "open_at_end": len([e for e in sub if e["is_open_at_window_end"]]),
                "sum_realized_net": round(sum(e["realized_net"] for e in sub_closed
                                              if e["realized_net"] is not None), 6),
                "note": "逐事件求和，不是账户收益；不得当组合成绩",
            }

        out["calibers"][cal_name] = {
            "caliber_params": RC.CALIBERS[cal_name],
            "counts": {
                "candidates": len(events),
                "executed": len(ok),
                "rejected": len(rejected),
                "closed": len(closed),
                "open_at_window_end": len(still_open),
                "winners": len(winners),
                "losers": len(losers),
                "flats": len(flats),
                "win_rate_of_closed_pct": (round(len(winners) / len(closed) * 100, 4)
                                           if closed else None),
            },
            "sum_realized_net_of_closed": round(sum_realized, 6),
            "sum_unrealized_net_of_open": round(sum_unrealized, 6),
            "sum_realized_plus_unrealized": round(sum_realized + sum_unrealized, 6),
            "sum_fees": round(sum((e["entry_fee"] or 0) + (e["exit_fee"] or 0) for e in closed), 6),
            "sum_funding_cost": round(sum(e["funding_cost"] or 0 for e in closed), 6),
            "rejections": [{"symbol": e["symbol"], "candidate_id": e["candidate_id"],
                            "reason": e["reject_reason"], "detail": e.get("reject_detail"),
                            "interval": e["interval"]} for e in rejected],
            "open_positions": [{"symbol": e["symbol"], "candidate_id": e["candidate_id"],
                                "entry_utc": e["entry_utc"],
                                "unrealized_net": e["unrealized_net"],
                                "hold_hours": e["hold_hours"],
                                "interval": e["interval"]} for e in still_open],
            "by_interval": by_interval,
            "account_level": {
                "组合回撤": "UNVERIFIED",
                "组合收益率": "UNVERIFIED",
                "原因": "replay_one 对每一笔都假设「账户有完整初始本金、零已占用敞口」"
                        "（equity=None / gross_open_notional=0 / portfolio_risk_open=0），"
                        "51 笔之间没有并发资金与仓位约束 ⇒ 求和只是逐事件累加，"
                        "既不是账户净值曲线，也不能算回撤。详见 03 脚本。",
                "要做组合账户需要什么": [
                    "按时间顺序推进、同时持仓数上限（冻结规则 3 仓，但 05-A 决策 5 未批）",
                    "总风险预算上限 1.5% 与总名义上限 90% 的实时占用",
                    "同一时刻多笔候选时的先后取舍规则（先到先得？按什么排序？）——未批准",
                    "账户权益随已实现盈亏滚动，而非每笔都用初始本金",
                ],
            },
            "per_event": events,   # 全量逐笔，不抽样（04-C 只存了 3 条样本，无法复算）
        }

    # ── 3. 与上游逐笔对撞 ─────────────────────────────────────────────
    print(">>> 与 04-D 102 笔逐笔对撞...")
    d04 = json.loads(UPD_04D.read_text(encoding="utf-8"))
    d04_map = {(r["candidate_id"], r["caliber"]): r for r in d04["recalc_records"]}

    field_pairs = [
        ("entry_price", "entry_price"),
        ("initial_stop", "initial_stop"),
        ("qty", "qty"),
        ("notional", "notional"),
        ("entry_fee", "entry_fee"),
        ("exit_utc", "exit_utc"),
        ("exit_price", "exit_fill"),
        ("realized_net", "realized_net"),
    ]
    # 对撞容差说明（踩坑记录）：
    # 04-D 的 recalc_records 把浮点存成了**舍入后**的值（实测 7 位小数），
    # 本脚本存全精度 ⇒ 用 1e-9 逐位比会把 100/102 条判成「不一致」的假失败。
    # 正确做法：给容差 + 同时报最大绝对差，让读者自己判断「是否只是舍入」。
    TOL = 1e-6
    cmp_rows = []
    max_abs = {}
    for cal_name, events in per_caliber.items():
        for e in events:
            key = (e["candidate_id"], cal_name)
            ref = d04_map.get(key)
            if ref is None:
                cmp_rows.append({"candidate_id": e["candidate_id"], "caliber": cal_name,
                                 "symbol": e["symbol"], "in_04d": False})
                continue
            diffs = {}
            for mine_k, ref_k in field_pairs:
                a, b = e.get(mine_k), ref.get(ref_k)
                if a is None and b is None:
                    diffs[mine_k] = "both_null"
                elif a is None or b is None:
                    diffs[mine_k] = f"one_null(mine={a},ref={b})"
                elif isinstance(a, str) or isinstance(b, str):
                    diffs[mine_k] = "match" if str(a) == str(b) else f"diff(mine={a},ref={b})"
                else:
                    d = abs(float(a) - float(b))
                    max_abs[mine_k] = max(max_abs.get(mine_k, 0.0), d)
                    diffs[mine_k] = ("match" if d <= TOL
                                     else f"diff(mine={a},ref={b},abs={d:.3e})")
            bad = {k: v for k, v in diffs.items() if v not in ("match", "both_null")}
            cmp_rows.append({
                "candidate_id": e["candidate_id"], "caliber": cal_name, "symbol": e["symbol"],
                "in_04d": True, "is_filled": (e["status"] == "ok"),
                "all_fields_match": (len(bad) == 0), "diffs": bad,
            })

    matched = [r for r in cmp_rows if r.get("in_04d")]
    mismatch = [r for r in matched if not r["all_fields_match"]]

    # ⚠ 空对空不算验证（05-D 指出的口径问题，本单据此修正）
    # 拒单记录没有成交价/数量/手续费/退出时间/盈亏 ⇒ 两侧都是 null，
    # 逐字段比会全部落进 "both_null" 然后被判成「一致」。这是自比断言，等于没测。
    # 正确口径：成交字段验证只统计真正成交的记录；拒单只验证「状态一致」。
    filled_rows = [r for r in matched if r.get("is_filled")]
    rejected_rows = [r for r in matched if not r.get("is_filled")]
    out["crosscheck"]["vs_04D_recalc_records"] = {
        "ref_path": str(UPD_04D.relative_to(ROOT)),
        "compared": len(cmp_rows),
        "found_in_ref": len(matched),
        "filled_records": len(filled_rows),
        "rejected_records": len(rejected_rows),
        "filled_all_fields_match": len([r for r in filled_rows if r["all_fields_match"]]),
        "filled_mismatch": len([r for r in filled_rows if not r["all_fields_match"]]),
        "rejected_status_only_checked": len(rejected_rows),
        "mismatch_count": len([r for r in matched if not r["all_fields_match"]]),
        "tolerance": TOL,
        "max_abs_diff_by_field": {k: round(v, 12) for k, v in max_abs.items()},
        "coverage_statement": (
            f"成交字段全量验证 {len([r for r in filled_rows if r['all_fields_match']])}"
            f"/{len(filled_rows)} 笔；"
            f"拒单 {len(rejected_rows)} 笔无成交字段，只验证「状态一致」，"
            f"不计入成交字段验证。"
        ),
        "tolerance_reason": "参考产物存的是舍入后的浮点（实测 7 位小数），本脚本存全精度。"
                            "用 1e-9 会得到 100/102 条假失败；1e-6 容差下若全过、"
                            "且 max_abs_diff 全部 ≤ 1e-6，即可判定「只是舍入，不是算术分歧」。",
        "mismatch_rows": mismatch[:20],
        "verdict": ("逐笔逐字段一致（差异仅为参考产物的舍入；拒单另按状态口径）"
                    if not mismatch and len(matched) == len(cmp_rows)
                    else "存在超容差不一致，见 mismatch_rows"),
    }

    # 与 04-C 汇总对撞（04-C 只存汇总 + 3 条样本，故只能对汇总）
    print(">>> 与 04-C 汇总对撞...")
    c04 = json.loads(UPD_04C.read_text(encoding="utf-8"))["historical_replayed_baselines"]
    agg = {}
    for cal_name, block in out["calibers"].items():
        ref = c04.get(cal_name, {})
        agg[cal_name] = {
            "04C": {
                "executed_count": ref.get("executed_count"),
                "positive_count": ref.get("positive_count"),
                "negative_count": ref.get("negative_count"),
                "total_realized_net": ref.get("total_realized_net"),
                "max_drawdown_amount": ref.get("max_drawdown_amount"),
                "max_drawdown_pct": ref.get("max_drawdown_pct"),
            },
            "本单": {
                "executed_count": block["counts"]["executed"],
                "positive_count": block["counts"]["winners"],
                "negative_count": block["counts"]["losers"],
                "total_realized_net": block["sum_realized_net_of_closed"],
                "max_drawdown_amount": None,
                "max_drawdown_pct": None,
            },
            "对撞": {},
        }
        a, b = agg[cal_name]["04C"], agg[cal_name]["本单"]
        for k in ("executed_count", "positive_count", "negative_count"):
            agg[cal_name]["对撞"][k] = "一致" if a[k] == b[k] else f"不一致(04C={a[k]},本单={b[k]})"
        same_net = (a["total_realized_net"] is not None and b["total_realized_net"] is not None
                    and abs(float(a["total_realized_net"]) - float(b["total_realized_net"])) < 1e-6)
        agg[cal_name]["对撞"]["total_realized_net"] = "一致" if same_net else \
            f"不一致(04C={a['total_realized_net']},本单={b['total_realized_net']})"
        agg[cal_name]["对撞"]["max_drawdown"] = (
            "本单不产出该字段：04-C 的「最大回撤」是按候选顺序累加 realized_net 得到的伪回撤，"
            "不是组合账户回撤。见 03 脚本的顺序敏感性与并发重叠证据。"
        )
    out["crosscheck"]["vs_04C_summary"] = agg

    # ── 4. 与 04-D 的窗口三分流清单对齐 ─────────────────────────────
    print(">>> 与 04-D 窗口三分流对齐...")
    if UPD_04D_MANIFEST.exists():
        man = json.loads(UPD_04D_MANIFEST.read_text(encoding="utf-8"))
        man_rows = man if isinstance(man, list) else man.get("items", man.get("records", []))
        by_usability = {}
        for r in man_rows:
            by_usability.setdefault(r.get("usability"), []).append(r)
        out["crosscheck"]["vs_04D_usability_manifest"] = {
            "ref_path": str(UPD_04D_MANIFEST.relative_to(ROOT)),
            "rows": len(man_rows),
            "by_usability": {k: len(v) for k, v in by_usability.items()},
            "rejected_items": [{"symbol": r.get("symbol"), "caliber": r.get("caliber"),
                                "usability": r.get("usability"), "reason": r.get("reason")}
                               for r in man_rows if r.get("usability") == "HOLD_REJECTED"],
            "口径说明": "04-D 的产物清单（不是它的正文）。04-D 正文写「2 笔拒单 / 37 笔暂缓」，"
                        "产物实测是 1 笔拒单 / 38 笔暂缓；以产物为准。",
        }
    else:
        out["errors"].append("04-D 分流清单缺失")

    # ── 5. 与 05-D 对撞（任务第 2 条要求的「拒单差异与计算版本」）────
    print(">>> 与 05-D 对撞（拒单差异与计算版本）...")
    d05d_path = ROOT / "evidence" / "ACCUM-EARLY-05-D" / "out" / "01_拒单差异与全量复算审计.json"
    if d05d_path.exists():
        d5 = json.loads(d05d_path.read_text(encoding="utf-8"))
        cov = d5.get("coverage_audit_102_trades", {})
        rc = d5.get("rejection_discrepancy_root_cause", {})
        gp = d5.get("window3_classification_integrity", {}).get(
            "group_performance_comparison", {})

        blk5 = out["calibers"]["生效视图_5bps"]
        blkb = out["calibers"]["策略包_3bps"]
        my_rej = blkb["rejections"]
        my_rej_sym = [r["symbol"] for r in my_rej]
        my_rej_cal = ["策略包_3bps" for _ in my_rej]
        my_total_net = round(blk5["sum_realized_net_of_closed"]
                             + blkb["sum_realized_net_of_closed"], 6)
        ref_total = (gp.get("TOTAL_ALL") or {}).get("total_realized_net")

        out["crosscheck"]["vs_05D"] = {
            "ref_path": str(d05d_path.relative_to(ROOT)),
            "计算版本": d5.get("version_identifiers", {}).get("replay_calc_version"),
            "版本标识": d5.get("version_identifiers", {}),
            "拒单对撞": {
                "05D判定": f"{rc.get('actual_rejection_count')} 笔 / "
                          f"{rc.get('rejected_symbol')} / {rc.get('rejected_caliber')}",
                "本单实测": f"{len(my_rej)} 笔 / {my_rej_sym} / {my_rej_cal}",
                "一致": (rc.get("actual_rejection_count") == len(my_rej)
                         and rc.get("rejected_symbol") in my_rej_sym),
                "05D根因": rc.get("reasons_alignment", {}).get("underlying_cause"),
            },
            "覆盖口径对撞": {
                "05D": {"total": cov.get("total_records"),
                        "filled": cov.get("filled_records"),
                        "rejected": cov.get("rejected_records"),
                        "unrealized": cov.get("unrealized_records"),
                        "funding_uncertain": cov.get("funding_uncertain_records")},
                "本单": {"total": out["crosscheck"]["vs_04D_recalc_records"]["compared"],
                         "filled": out["crosscheck"]["vs_04D_recalc_records"]["filled_records"],
                         "rejected": out["crosscheck"]["vs_04D_recalc_records"]["rejected_records"],
                         "unrealized": sum(v["counts"]["open_at_window_end"]
                                           for v in out["calibers"].values())},
                "说明": "05-D 的口径纠正了「102 笔全部吻合」的说法："
                        "其中 1 笔是拒单，没有成交字段可验，不能算成交字段验证样本。"
                        "本单已按此口径重算（见 vs_04D_recalc_records.coverage_statement）。",
            },
            "全量净额对撞": {
                "05D全量合计": ref_total,
                "本单两口径求和": my_total_net,
                "一致": (ref_total is not None and abs(float(ref_total) - my_total_net) < 1e-2),
                "注意": "这是**两套口径相加**的结果，只用于核对底账，"
                        "不代表任何真实账户收益；跨口径相加本身不可作成绩。",
            },
            "05D的分流净额": {k: {"net": v.get("total_realized_net"),
                                 "win_rate_pct": v.get("win_rate_pct"),
                                 "count": v.get("count"),
                                 "valid_trades": v.get("valid_trades_count")}
                             for k, v in gp.items()},
            "05D的一句话": gp.get("TOTAL_ALL"),
        }
    else:
        out["crosscheck"]["vs_05D"] = {
            "status": "05-D 产物在本脚本运行时不存在",
            "替代": "已用 04-D 的 102 笔复算记录代替，见 vs_04D_recalc_records。",
        }

    out["elapsed_sec"] = round(time.time() - t_all, 1)
    dst = OUT_DIR / "02_逐事件基线.json"
    dst.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"✔ 已落盘 {dst.relative_to(ROOT)} ({out['elapsed_sec']}s)")
    for cal in out["calibers"]:
        c = out["calibers"][cal]["counts"]
        print(f"  {cal}: 候选{c['candidates']} 成交{c['executed']} 拒单{c['rejected']} "
              f"期末持仓{c['open_at_window_end']} 盈{c['winners']} 亏{c['losers']} "
              f"累加净额={out['calibers'][cal]['sum_realized_net_of_closed']}")
    print("  对撞 04-D:", out["crosscheck"]["vs_04D_recalc_records"]["verdict"],
          out["crosscheck"]["vs_04D_recalc_records"]["mismatch_count"])


if __name__ == "__main__":
    main()
