# -*- coding: utf-8 -*-
"""ACCUM-EARLY-02｜04 回放计算修复（七项）。

工单第二部分：**修复回放计算（七项）**。
本脚本不重复实现算术 —— 算术在 `框架/backend/replay_calc.py`，
它调用的是获批的唯一实现（`apply_order_rounding` / `settle_funding` /
`contract_limits`）。本脚本负责**取证**：

  A 两套口径的完整参数（不只是滑点不同）与身份
  B 全量真实候选回放（51 条 × 2 口径），汇总成交/拒单/退出分类/资金费
  C 七项修复逐条取证（旧算法复刻 vs 新算法，同一事件同一数据）
  D 独立复算对撞（不走 replay_calc 的路径重算入场四要素，逐位比对）

输出：evidence/ACCUM-EARLY-02/out/04_回放计算修复.json

⚠ 只读：不写生产库、不碰账本、不产生任何订单。
"""
from __future__ import annotations

import calendar
import json
import sqlite3
import sys
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve()
PROJECT = HERE.parents[3]
BACKEND = PROJECT / "框架" / "backend"
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(PROJECT))

HOT = PROJECT / "数据" / "市场数据" / "db" / "market_v2.sqlite3"
COLD = PROJECT / "数据" / "市场数据" / "parquet"
STATUS = BACKEND / "locks" / "paper_runtime_3a_status.json"
OLD07 = PROJECT / "evidence" / "ACCUM-EARLY-01" / "out" / "07_事件回放.json"
OUT = PROJECT / "evidence" / "ACCUM-EARLY-02" / "out"

M15 = 900_000
H4 = 14_400_000
DAY = 86_400_000


def iso(ms):
    if ms is None:
        return None
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(int(ms) / 1000))


def to_ms(s):
    return calendar.timegm(time.strptime(s, "%Y-%m-%dT%H:%M:%SZ")) * 1000


# ══════════════════════════ A 口径身份 ══════════════════════════

def sec_a(rep):
    import replay_calc as RC
    cal = RC.CALIBERS
    keys = sorted(set(cal["生效视图_5bps"]) | set(cal["策略包_3bps"]))
    diff = [k for k in keys
            if k not in ("来源",)
            and cal["生效视图_5bps"].get(k) != cal["策略包_3bps"].get(k)]
    same = [k for k in keys
            if k not in ("来源",)
            and cal["生效视图_5bps"].get(k) == cal["策略包_3bps"].get(k)]
    rep["A_两套口径"] = {
        "表": {k: {"生效视图_5bps": cal["生效视图_5bps"].get(k),
                   "策略包_3bps": cal["策略包_3bps"].get(k),
                   "相同": cal["生效视图_5bps"].get(k) == cal["策略包_3bps"].get(k)}
               for k in keys if k != "来源"},
        "不同的字段": diff,
        "相同的字段": same,
        "关键提醒": ("两套口径**不只是滑点不同**：本金 5000 vs 2000、单名上限 10% vs 30%、"
                     "总敞口 80% vs 90%、组合风险 5% vs 1.5%、参与率 1% vs 0.1% 都不同。"
                     "只改滑点会让读者以为差异只来自滑点，那是误导。"
                     "所以本脚本是「两套口径各自完整跑一遍」，不是「同一个跑两遍改个乘数」。"),
        "来源": {k: cal[k]["来源"] for k in cal},
    }


# ══════════════════════════ 数据装载 ══════════════════════════

def load_15m(merged, symbol, entry_ms, span_days=14):
    bars, st = merged.load(symbol, entry_ms - 2 * 3600_000,
                           entry_ms + span_days * DAY)
    return bars, st


def load_4h(symbol, entry_ms, span_days=30):
    import history_loader as hl
    mh = hl.MergedHistory(str(HOT), str(COLD), log=lambda *a, **k: None)
    data, _ = mh.load([symbol], ["4h"], entry_ms - 8 * DAY, entry_ms + span_days * DAY)
    return data[symbol]["4h"]


def load_funding(symbol, entry_ms, span_days=14):
    con = sqlite3.connect(f"file:{HOT}?mode=ro", uri=True)
    try:
        rs = con.execute(
            """SELECT funding_time, funding_rate, mark_price, fetched_at
               FROM funding_history
               WHERE exchange='binance' AND market='um' AND symbol=?
                 AND rate_type='settled' AND funding_time > ? AND funding_time <= ?
               ORDER BY funding_time""",
            (symbol, int(entry_ms), int(entry_ms + span_days * DAY))).fetchall()
    finally:
        con.close()
    return [{"time_ms": int(r[0]),
             "rate": (float(r[1]) if r[1] is not None else None),
             "mark_price": (float(r[2]) if r[2] is not None else None),
             "fetched_at": r[3]} for r in rs]


# ══════════════════════════ 旧算法复刻（仅用于对照）══════════════════════════

def old_entry_math(*, raw_open, prev_quote_volume, equity=5000.0, risk=0.005,
                   slip=0.0005, fee=0.0005):
    """**复刻 ACCUM-EARLY-01/07 的旧算术**，只为对照，不作为任何交付口径。

    旧代码四行（原文）：
        entry_px = float(fb["open"])
        stop0 = round(entry_px * 0.95, 12)
        qty = (EQUITY * RISK) / max(entry_px - stop0, 1e-12)
        notional = qty * entry_px
    然后才在成本段算 `entry_all = entry_px * (1 + SLIP)`。
    ⇒ 止损、数量、名义额都用**未含滑点**的原始开盘价算，
      而盈亏用含滑点价算，两处口径不一致（工单第 ① 项）。
    """
    entry_px = float(raw_open)
    stop0 = round(entry_px * 0.95, 12)
    qty = (equity * risk) / max(entry_px - stop0, 1e-12)
    notional = qty * entry_px
    entry_all = entry_px * (1 + slip)
    return {"entry_price_raw": entry_px, "stop0": stop0, "qty": qty,
            "notional": notional, "entry_price_after_slippage": entry_all,
            "单边手续费估算": entry_all * qty * fee,
            "两边手续费估算": (entry_all + entry_all) * qty * fee,
            "口径": "止损/数量/名义用原始开盘价；盈亏用含滑点价 —— 两处不一致"}


def old_funding_math(funding_events, notional, entry_ms, exit_ms):
    """复刻旧资金费算法：`Σ(费率) × 入场时名义额`（工单第 ③ 项）。

    **窗口必须和旧代码一致**（`funding_time > 入场 ≤ 退出`），
    否则比较的就不是「公式差异」而是「窗口差异」，那会掩盖真问题。
    """
    win = [f for f in funding_events
           if int(entry_ms) < int(f["time_ms"]) <= int(exit_ms)]
    s = sum(float(f["rate"]) for f in win if f.get("rate") is not None)
    return {"窗口内事件数": len(win), "费率求和": s,
            "成本": s * notional,
            "口径": ("费率直接求和 × **入场时**名义额；"
                     "丢符号、丢逐笔量级（价格变动后 mark×qty ≠ 入场名义额）、"
                     "缺字段静默跳过")}


# ══════════════════════════ B 全量回放 ══════════════════════════

def sec_b(rep, merged, candidates):
    import replay_calc as RC
    rows = []
    for cand in candidates:
        sym = cand["symbol"]
        snap_ms = to_ms(cand["snapshot_utc"])
        entry_ms = snap_ms + 15 * 60_000
        try:
            bars15, st15 = load_15m(merged, sym, entry_ms)
            elig = [b for b in bars15 if int(b["open_time"]) >= entry_ms]
            if not elig:
                rows.append({"symbol": sym, "candidate_id": cand["candidate_id"],
                             "status": "no_bar_after_confirm",
                             "说明": "确认后没有 15m bar（数据未覆盖该时段）"})
                continue
            entry_bar = elig[0]
            prevs = [b for b in bars15 if int(b["open_time"]) < int(entry_bar["open_time"])]
            prev_bar = prevs[-1] if prevs else None
            bars4 = load_4h(sym, entry_ms)
            fund = load_funding(sym, int(entry_bar["open_time"]))
            for cal in RC.CALIBERS:
                r = RC.replay_one(
                    symbol=sym, entry_bar=entry_bar, prev_bar=prev_bar,
                    bars_15m=elig, bars_4h=bars4, funding_events=fund,
                    caliber_name=cal)
                if r.get("status") != "ok":
                    rows.append({"symbol": sym, "candidate_id": cand["candidate_id"],
                                 "caliber": cal, "status": "rejected",
                                 "reject": r["entry"].get("reject"),
                                 "detail": r["entry"].get("detail"),
                                 "entry_bar_utc": iso(int(entry_bar["open_time"]))})
                    continue
                e, x, p, f = r["entry"], r["exit"], r["盈亏"], r["资金费"]
                rows.append({
                    "symbol": sym, "candidate_id": cand["candidate_id"],
                    "caliber": cal, "status": "ok",
                    "entry_bar_utc": iso(int(entry_bar["open_time"])),
                    "entry_bar_source": entry_bar.get("source"),
                    "entry_price": e["entry_price"],
                    "raw_open": e["raw_open"],
                    "initial_stop": e["initial_stop_price"],
                    "qty": e["qty"], "notional": round(e["notional"], 6),
                    "entry_fee": round(e["entry_fee"], 8),
                    "participation_capped": e["participation"]["capped"],
                    "exit_reason": x["exit_reason"],
                    "exit_kind": x["kind"],
                    "exit_utc": x["exit_utc"],
                    "exit_fill": x.get("exit_fill"),
                    "gap_size": round(x.get("gap_size") or 0.0, 12),
                    "stop_path_len": r["止损路径_条数"],
                    "trailed": r["止损上移过"],
                    "final_stop_in_life": r["最终止损_持仓期内"],
                    "funding_events": f["事件数"],
                    "funding_gaps": f["缺口数"],
                    "funding_cost": round(f["成本合计"], 8),
                    "funding_order_uncertain": f["顺序不确定事件数"],
                    "realized_net": p.get("realized_net"),
                    "unrealized_net": p.get("unrealized_net"),
                    "gap_sensitivity_range": r["跳空敏感性"]["净额极差"],
                })
        except Exception as exc:                                  # noqa: BLE001
            rows.append({"symbol": sym, "candidate_id": cand["candidate_id"],
                         "status": "error",
                         "error": f"{type(exc).__name__}: {exc}"})

    def agg(cal):
        sub = [r for r in rows if r.get("caliber") == cal and r.get("status") == "ok"]
        rej = [r for r in rows if r.get("caliber") == cal and r.get("status") == "rejected"]
        reasons = {}
        for r in sub:
            reasons[r["exit_reason"]] = reasons.get(r["exit_reason"], 0) + 1
        nets = [r["realized_net"] for r in sub if r.get("realized_net") is not None]
        return {
            "口径": cal,
            "可回放事件": len(sub),
            "拒单": len(rej),
            "拒单原因": {x: sum(1 for r in rej if r.get("reject") == x)
                     for x in {r.get("reject") for r in rej}},
            "无法回放（无 15m）": sum(1 for r in rows
                                if r.get("caliber") is None
                                and r.get("status") == "no_bar_after_confirm"),
            "退出分类": reasons,
            "跳空退出数": sum(1 for r in sub if r["exit_kind"] == "gap"),
            "初始止损退出数": sum(1 for r in sub
                            if r["exit_reason"] == "initial_stop_loss"),
            "跟踪止损退出数": sum(1 for r in sub
                            if r["exit_reason"] == "trailing_stop_loss"),
            "期末未平仓数": sum(1 for r in sub if r["exit_reason"] == "open_at_window_end"),
            "参与率压量数": sum(1 for r in sub if r["participation_capped"]),
            "资金费缺口数": sum(r["funding_gaps"] for r in sub),
            "资金费顺序不确定数": sum(r["funding_order_uncertain"] for r in sub),
            "已实现净额合计": (round(sum(nets), 6) if nets else None),
            "已实现净额中位": (round(sorted(nets)[len(nets) // 2], 6) if nets else None),
            "说明": "期末未平仓事件不进已实现合计（工单第 ⑤ 项）",
        }

    rep["B_全量回放"] = {
        "候选总数": len(candidates),
        "窗口": {"from": min(c["snapshot_utc"] for c in candidates),
                 "to": max(c["snapshot_utc"] for c in candidates)},
        "汇总": {cal: agg(cal) for cal in RC.CALIBERS},
        "逐条": rows,
        "读法": ("同一批真实候选、同一份行情、两套口径各自完整执行；"
                 "`拒单` 与 `期末未平仓` 都不进已实现合计。"),
    }
    return rows


# ══════════════════════════ C 七项逐条取证 ══════════════════════════

def _gap_demo(merged, symbol, entry_ms):
    """在**真实 15m K 线**里找一次真实跳空，演示三模型差异。

    为什么需要演示：51 条真实候选里一次跳空退出都没有（`跳空退出数=0`），
    于是「三模型会不会有差别」在这批数据上无法直接观察到。
    为了不让第 ⑦ 项停在「写了代码但没验证」，这里取真实存在的最大逆势跳空 bar，
    **人为把止损设在该 bar 开盘价之上**，让跳空条件成立 —— 止损位置是人造的，
    跳空本身是真的。演示不计入任何成绩。
    """
    import replay_calc as RC
    bars, _ = load_15m(merged, symbol, entry_ms)
    bars = [b for b in bars if int(b["open_time"]) >= entry_ms]
    best = None
    for i in range(1, len(bars)):
        pc = float(bars[i - 1]["close"])
        op = float(bars[i]["open"])
        if pc <= 0:
            continue
        gap = (pc - op) / pc          # 多头逆势跳空 = 开盘低于前收
        if best is None or gap > best[0]:
            best = (gap, i, pc, op)
    if best is None or best[0] <= 0:
        return {"found": False}
    gap_pct, idx, prev_close, op = best
    stop = prev_close * 0.999          # 人为：止损略低于前收 ⇒ 开盘即在止损之下
    window = bars[idx:idx + 40]
    out = {}
    for m in ("stop_price", "bar_open", "midpoint"):
        ex = RC.scan_exit(bars_15m=window, entry_ms=int(window[0]["open_time"]),
                          entry_bar_ms=int(window[0]["open_time"]),
                          stop_path=[], initial_stop=stop, gap_model=m)
        gross = ((ex.get("exit_fill") or 0) - prev_close) * 1.0 if ex.get("hit") else None
        out[m] = {"exit_fill": ex.get("exit_fill"), "kind": ex.get("kind"),
                  "毛盈亏_每单位": (None if gross is None else round(gross, 12))}
    return {
        "found": True, "symbol": symbol,
        "跳空bar_utc": iso(int(window[0]["open_time"])),
        "前一根收盘": prev_close, "该根开盘": op,
        "跳空幅度_百分比": round(gap_pct * 100, 6),
        "人造止损位置": round(stop, 12),
        "三模型": out,
        "每单位毛盈亏极差": (None if any(v["毛盈亏_每单位"] is None for v in out.values())
                     else round(max(v["毛盈亏_每单位"] for v in out.values())
                                - min(v["毛盈亏_每单位"] for v in out.values()), 12)),
        "说明": ("跳空是**真实存在**的 K 线，止损位置是人为设的；"
                 "只用于说明三种成交模型的差别，不计入任何成绩。"),
    }


def _open_at_end_demo(merged, symbol, entry_ms):
    """演示「期末未平仓只计未实现」这条路径（真实 K 线 + 截短窗口）。

    51 条真实候选全部在窗口内触发了止损，没有一条走到期末，
    所以这条分支在这批数据上观察不到。截短观察窗口让持仓活到期末，
    即可验证 `realized_net is None`、只给未实现估值。
    """
    import replay_calc as RC
    bars, _ = load_15m(merged, symbol, entry_ms)
    elig = [b for b in bars if int(b["open_time"]) >= entry_ms]
    if not elig:
        return {"found": False}
    short = elig[:24]                     # 6 小时（24 根 15m）
    entry_bar = short[0]
    prevs = [b for b in bars if int(b["open_time"]) < int(entry_bar["open_time"])]
    prev_bar = prevs[-1] if prevs else None
    bars4 = load_4h(symbol, entry_ms)
    fund = load_funding(symbol, int(entry_bar["open_time"]))
    r = RC.replay_one(symbol=symbol, entry_bar=entry_bar, prev_bar=prev_bar,
                      bars_15m=short, bars_4h=bars4, funding_events=fund,
                      caliber_name="生效视图_5bps")
    if r.get("status") != "ok":
        return {"found": False, "reject": r["entry"].get("reject")}
    return {"found": True, "symbol": symbol,
            "观察窗口": {"from": iso(int(short[0]["open_time"])),
                     "to": iso(int(short[-1]["open_time"])),
                     "bar数": len(short)},
            "退出原因": r["exit"]["exit_reason"],
            "已实现净额": r["已实现净额"],
            "未实现净额": r["未实现净额"],
            "未实现毛额": (r["盈亏"] or {}).get("unrealized_gross"),
            "期末估值价": (r["盈亏"] or {}).get("mark_price_at_end"),
            "断言_已实现必须为空": r["已实现净额"] is None,
            "说明": "窗口截短是**回放选择**，K 线是真的；只用于验证这条分支的口径",
            }


def _funding_gap_demo(fund, qty, notional, entry_ms, exit_ms):
    """把一条结算事件的 `mark_price` 人为置空，验证「缺字段不补零」。

    为什么需要演示：这批数据里资金费字段 100% 齐全（缺口数=0），
    所以「缺字段会怎样」在真实数据上观察不到。置空一条事件即可暴露差异：
      · 旧算法：`sum(rate)` 照样把这个费率加进去 ⇒ 成本里含了一条**没有价格依据**的项，
        而且看不出来；
      · 新算法：记 `funding_gap`，**不计入成本**，并在明细里写清缺的是哪个字段。
    """
    import replay_calc as RC
    win = [f for f in fund if int(entry_ms) < int(f["time_ms"]) <= int(exit_ms)]
    if not win:
        return {"found": False, "说明": "该事件窗口内没有结算事件"}
    broken = [dict(f) for f in win]
    broken[0]["mark_price"] = None
    new = RC.settle_funding_events(symbol="DEMO", qty=qty, direction=1,
                                  entry_ms=entry_ms, exit_ms=exit_ms,
                                  funding_events=broken)
    s_all = sum(float(f["rate"]) for f in win if f.get("rate") is not None)
    return {
        "found": True,
        "置空的字段": "第 1 条结算事件的 `mark_price`",
        "旧算法成本": s_all * notional,     # 与是否缺 mark 无关 ⇒ 静默算进成本
        "旧算法是否发现": False,
        "新算法成本": new["成本合计"],
        "新算法缺口数": new["缺口数"],
        "新算法缺口明细": [r for r in new["明细"] if r["status"] == "gap"],
        "差值": round(new["成本合计"] - s_all * notional, 10),
        "结论": ("旧算法对缺失字段完全无感（成本里混入没有价格依据的项）；"
                 "新算法记缺口、不计入、不补零，差异可被接口读到。"),
        "说明": "字段置空是**人为制造**的，只为验证代码路径；真实数据这批是齐全的。",
    }


def sec_c(rep, merged, candidates, rows_b):
    import replay_calc as RC
    old = json.loads(OLD07.read_text(encoding="utf-8")) if OLD07.exists() else {}
    old_by_sym = {c.get("symbol"): c for c in old.get("chains", [])
                  if isinstance(c, dict) and "error" not in c}

    picks = []
    for c in candidates:
        if c["symbol"] in ("TNSRUSDT", "AGTUSDT", "METISUSDT"):
            picks.append(c)
    if not picks:
        cs = sorted(candidates, key=lambda x: x["snapshot_utc"])
        picks = [cs[0], cs[len(cs) // 2], cs[-1]]

    items = []
    for cand in picks:
        sym = cand["symbol"]
        snap_ms = to_ms(cand["snapshot_utc"])
        entry_ms = snap_ms + 15 * 60_000
        bars15, st15 = load_15m(merged, sym, entry_ms)
        elig = [b for b in bars15 if int(b["open_time"]) >= entry_ms]
        if not elig:
            items.append({"symbol": sym, "skip": "no_bar_after_confirm"})
            continue
        entry_bar = elig[0]
        prevs = [b for b in bars15 if int(b["open_time"]) < int(entry_bar["open_time"])]
        prev_bar = prevs[-1] if prevs else None
        bars4 = load_4h(sym, entry_ms)
        fund = load_funding(sym, int(entry_bar["open_time"]))
        new = {cal: RC.replay_one(
            symbol=sym, entry_bar=entry_bar, prev_bar=prev_bar, bars_15m=elig,
            bars_4h=bars4, funding_events=fund, caliber_name=cal)
            for cal in RC.CALIBERS}
        o = old_by_sym.get(sym) or {}

        # ① 入场四要素：旧 vs 新（生效视图口径，同为 5bps / 5000U / 10%）
        nv = new["生效视图_5bps"]
        oldm = old_entry_math(raw_open=float(entry_bar["open"]),
                             prev_quote_volume=(prev_bar or {}).get("quote_volume"))
        e = nv.get("entry") or {}
        one = {
            "symbol": sym, "candidate_id": cand["candidate_id"],
            "旧算法": oldm,
            "新算法": ({k: e.get(k) for k in
                     ("entry_price", "initial_stop_price", "stop_dist", "qty",
                      "notional", "entry_fee")} if nv.get("status") == "ok" else e),
            "旧报告里的数": ({"entry_price_raw": (o.get("5_成交") or {}).get("entry_price_raw"),
                        "stop0": (o.get("6_止损") or {}).get("初始止损"),
                        "qty": (o.get("5_成交") or {}).get("qty"),
                        "notional": (o.get("5_成交") or {}).get("notional")} if o else None),
            "差异": (None if nv.get("status") != "ok" else {
                "止损差_新减旧": round(e["initial_stop_price"] - oldm["stop0"], 12),
                "止损差_百分比": round((e["initial_stop_price"] - oldm["stop0"])
                                / oldm["stop0"] * 100, 6),
                "数量差": round(e["qty"] - oldm["qty"], 8),
                "名义差": round(e["notional"] - oldm["notional"], 6),
                "单边手续费差": round(e["entry_fee"] - oldm["单边手续费估算"], 8),
            }),
            "为什么这样改": ("旧代码 `stop0` 用**原始开盘价**×0.95，而盈亏用**含滑点价**算 ⇒ "
                       "止损、数量、名义三处都与实际成交价脱钩。"
                       "新算法由 `apply_order_rounding` 从**取整后成交价**一路算到底，"
                       "两套滑点各自完整执行，不可能出现「价变了数量没变」。"),
        }

        # ② 止损路径截断：旧（+30 天）vs 新（截断到退出）
        path_full = RC.build_stop_path(entry_ms=int(entry_bar["open_time"]),
                                       initial_stop=(e.get("initial_stop_price")
                                                     if nv.get("status") == "ok" else 0.0),
                                       bars_4h=bars4, exit_ms=None)
        two = {
            "旧做法": {"条数": len((o.get("6_止损") or {}).get("跟踪路径") or []),
                    "最终止损": (o.get("6_止损") or {}).get("最终止损"),
                    "窗口": "入场后 +30 天（未截断）"},
            "不截断（复刻）": {"条数": len(path_full),
                        "最终止损": path_full[-1]["stop"] if path_full else None,
                        "注": "用**新**初始止损复刻「不截断」，条数与旧报告可能不同（旧止损不同）"},
            "新做法（截断到退出）": ({"条数": nv["止损路径_条数"],
                             "最终止损": nv["最终止损_持仓期内"],
                             "截断到": nv["止损路径_截断到"]}
                            if nv.get("status") == "ok" else None),
            "为什么": ("旧代码把入场后 30 天的 4h 全算进跟踪路径，"
                    "于是「最终止损」是**退出之后**才达到的值 —— 它既不能用来判退出，"
                    "也不能用来算盈亏，只能让人误以为这笔单最后止损收在更高位置。"
                    "新代码只取 `入场 < 4h 收盘 ≤ 退出`。"),
        }

        # ③ 资金费：旧 vs 新
        if nv.get("status") == "ok":
            fnew = nv["资金费"]
            exit_ms = to_ms(nv["exit"]["exit_utc"]) if nv["exit"].get("exit_utc") else None
            f_old = old_funding_math(fund, oldm["notional"],
                                     int(entry_bar["open_time"]),
                                     int(exit_ms) if exit_ms else int(entry_bar["open_time"]))
            three = {
                "旧算法": f_old,
                "旧报告": {"funding_events": ((o.get("7_退出与成本") or {})
                                          .get("成本模型_生效视图") or {}).get("funding_events"),
                        "funding_cost": ((o.get("7_退出与成本") or {})
                                         .get("成本模型_生效视图") or {}).get("funding_cost")},
                "新算法": {"事件数": fnew["事件数"],
                        "区间外事件数": fnew["区间外事件数"],
                        "缺口数": fnew["缺口数"],
                        "成本合计": round(fnew["成本合计"], 8),
                        "顺序不确定事件数": fnew["顺序不确定事件数"],
                        "明细": fnew["明细"][:6],
                        "公式": fnew["公式"]},
                "为什么": ("旧算法 `Σrate × 入场时名义额`：① 用入场名义额而不是"
                        "「结算标记价 × 当时数量」；② 费率求和 ⇒ 丢符号与逐笔量级；"
                        "③ 缺字段静默跳过，看起来像没有资金费。"
                        "新算法逐笔调 `settle_funding`，`>0 支付 / <0 收取`，缺口不补零。"),
                "缺口路径演示": _funding_gap_demo(
                    fund, e["qty"], e["notional"], int(entry_bar["open_time"]),
                    int(exit_ms) if exit_ms else int(entry_bar["open_time"])),
            }
        else:
            three = {"skip": "入场被拒，无持仓 ⇒ 无资金费"}

        # ④ 参与率
        four = {
            "旧做法": {"cap": 0.001,
                    "说明": "只算一个布尔标记 `参与率是否满足`，盈亏仍按全额成交算",
                    "旧报告": {"参与率上限_名义": (o.get("5_成交") or {}).get("参与率上限_名义"),
                            "参与率是否满足": (o.get("5_成交") or {}).get("参与率是否满足")}},
            "新做法": ({cal: {"cap": new[cal]["entry"]["participation"]["cap"],
                          "上限名义": new[cal]["entry"]["participation"]["cap_notional"],
                          "有 cap 的数量": new[cal]["entry"]["participation"]["qty_with_cap"],
                          "关掉 cap 的数量": new[cal]["entry"]["participation"]["qty_without_cap"],
                          "参与率是否压量": new[cal]["entry"]["participation"]["capped"]}
                     for cal in RC.CALIBERS
                     if new[cal].get("status") == "ok"}),
            "为什么": ("旧代码用 0.1% 作上限（那是策略包的值），而生成立即执行走的是"
                    "生效视图的 1% —— 上限差 10 倍。更重要的是：即使标记为「不满足」，"
                    "旧代码仍按全额成交算盈亏，那个数在真实执行里根本不会发生。"
                    "新代码让参与率进入 `apply_order_rounding` 的数量上限 ⇒ 缩量；"
                    "缩量后低于最小可下单量才整单拒绝。"),
        }

        items.append({"symbol": sym, "candidate_id": cand["candidate_id"],
                      "entry_bar_utc": iso(int(entry_bar["open_time"])),
                      "①_入场四要素": one,
                      "②_止损路径截断": two,
                      "③_资金费": three,
                      "④_参与率": four,
                      "⑤_盈亏分解": ({cal: new[cal]["盈亏"] for cal in RC.CALIBERS
                                 if new[cal].get("status") == "ok"}),
                      "⑥_退出分类": ({cal: {"退出原因": new[cal]["exit"]["exit_reason"],
                                       "触及方式": new[cal]["exit"]["kind"],
                                       "止损类型": new[cal]["exit"]["stop_kind"],
                                       "跳空幅度": new[cal]["exit"].get("gap_size"),
                                       "退出价": new[cal]["exit"].get("exit_fill")}
                                 for cal in RC.CALIBERS if new[cal].get("status") == "ok"}),
                      "⑦_跳空敏感性": ({cal: new[cal]["跳空敏感性"] for cal in RC.CALIBERS
                                 if new[cal].get("status") == "ok"}),
                      })

    # ⑥ 分类断言：不能所有退出都叫同一个名字
    all_reasons = set()
    for it in items:
        for v in (it.get("⑥_退出分类") or {}).values():
            all_reasons.add(v["退出原因"])
    # ⑦ 跳空：找真实跳空样本（全量回放里 exit_kind == gap）
    gap_samples = [r for r in rows_b
                   if r.get("status") == "ok" and r.get("exit_kind") == "gap"]
    rep["C_七项取证"] = {
        "①_统一由成交价计算": [it["①_入场四要素"] for it in items if "①_入场四要素" in it],
        "②_止损路径截断": [{"symbol": it["symbol"],
                       "candidate_id": it.get("candidate_id"),
                       "entry_bar_utc": it.get("entry_bar_utc"),
                       **it["②_止损路径截断"]}
                     for it in items if "②_止损路径截断" in it],
        "③_资金费逐笔": [{"symbol": it["symbol"],
                     "candidate_id": it.get("candidate_id"),
                     **it["③_资金费"]}
                    for it in items if "③_资金费" in it],
        "④_参与率": [{"symbol": it["symbol"],
                  "candidate_id": it.get("candidate_id"),
                  **it["④_参与率"]}
                 for it in items if "④_参与率" in it],
        "⑤_期末未平仓只计未实现": {
            "断言": "期末未平仓事件的 `realized_net` 必须为 None，只给 `unrealized_net`",
            "全量里出现次数": sum(1 for r in rows_b
                           if r.get("status") == "ok"
                           and r.get("exit_reason") == "open_at_window_end"),
            "样本": [r for r in rows_b
                   if r.get("status") == "ok"
                   and r.get("exit_reason") == "open_at_window_end"][:5],
            "通过": all(r.get("realized_net") is None for r in rows_b
                     if r.get("status") == "ok"
                     and r.get("exit_reason") == "open_at_window_end"),
            "为什么还要单独演示": ("51 条真实候选全部在观察窗口内触发止损，"
                          "没有一条走到期末 ⇒ 这条分支在真实数据上观察不到。"
                          "截短观察窗口让持仓活到期末，验证「期末未平仓不计已实现」。"),
            "演示": _open_at_end_demo(merged, "METISUSDT",
                                  to_ms("2026-10-11T20:15:00Z")),
        },
        "⑥_退出分类": {
            "本批出现的退出原因": sorted(all_reasons),
            "逐条": [{"symbol": it["symbol"], **it["⑥_退出分类"]}
                   for it in items if "⑥_退出分类" in it],
            "断言": ("退出原因必须能区分 `initial_stop_loss` / `trailing_stop_loss` / "
                   "`*_gap` / `open_at_window_end`；旧代码一律写 `trailing_stop_loss`"),
            "旧代码一律同一原因": all(
                ((c.get("7_退出与成本") or {}).get("退出") or {}).get("reason")
                == "trailing_stop_loss"
                for c in old.get("chains", []) if isinstance(c, dict) and "error" not in c),
        },
        "⑦_跳空敏感性": {
            "真实跳空样本数": len(gap_samples),
            "真实跳空样本": gap_samples[:5],
            "为什么 51 条里一次跳空都没有": (
                "初始止损在成交价下方 5%，跟踪止损取 4h 低点×0.95，"
                "而这 51 条都在 14 天内被盘中触及 ⇒ 没有一次「开盘直接越过止损」。"
                "这不是模型没问题，是**这批数据没覆盖到那种行情**。"),
            "真实 K 线上的跳空演示": _gap_demo(merged, "METISUSDT",
                                        to_ms("2026-10-11T20:15:00Z")),
            "三模型对照": [{"symbol": it["symbol"], **it["⑦_跳空敏感性"]}
                      for it in items if "⑦_跳空敏感性" in it],
            "推荐": "bar_open（按该根 15m 开盘价成交）",
            "推荐理由": (
                "① 跳空时止损单是市价单，成交在开盘价附近，"
                "不存在「价格跳过了止损却还能按止损价成交」；"
                "② `stop_price` 模型系统性高估成交价 ⇒ 系统性低估亏损，方向上永远偏乐观；"
                "③ `midpoint` 是人为插值，没有机制依据，比 `stop_price` 更不可辩护；"
                "④ 本脚本**未改默认口径** —— 改默认需裁决方批准，这里只给敏感性对照。"),
        },
        "逐条": items,
    }


# ══════════════════════════ D 独立复算对撞 ══════════════════════════

def sec_d(rep, merged, candidates):
    """不走 `replay_calc` 的封装，直接从原始表重算入场四要素并逐位比对。

    为什么必须独立复算：如果只用 `replay_calc` 自己的输出自检，
    它错在哪都发现不了。这里改成「原始 15m 开盘价 → 手算滑点 → 手算步进取整 →
    手算止损 → 手算数量」，再与 `replay_calc` 的数逐位比。
    """
    import replay_calc as RC
    from strategies.wx_execution import round_buy_price, round_protect_price, _step_floor
    rows = []
    for cand in candidates[:12]:
        sym = cand["symbol"]
        entry_ms = to_ms(cand["snapshot_utc"]) + 15 * 60_000
        bars15, _ = load_15m(merged, sym, entry_ms)
        elig = [b for b in bars15 if int(b["open_time"]) >= entry_ms]
        if not elig:
            continue
        entry_bar = elig[0]
        prevs = [b for b in bars15 if int(b["open_time"]) < int(entry_bar["open_time"])]
        prev_bar = prevs[-1] if prevs else None
        for cal in RC.CALIBERS:
            calp = RC.CALIBERS[cal]
            r = RC.plan_entry(symbol=sym, raw_open=float(entry_bar["open"]),
                              prev_quote_volume=(prev_bar or {}).get("quote_volume"),
                              equity=calp["initial_equity"],
                              gross_open_notional=0.0, free_cash=calp["initial_equity"],
                              portfolio_risk_open=0.0, caliber=calp)
            if r.get("status") != "ok":
                rows.append({"symbol": sym, "caliber": cal,
                             "status": "rejected", "reject": r.get("reject")})
                continue
            limits = r["limits"]
            ps = float(limits["price_step"])
            step = float(limits["quantity_step"])
            slip = float(calp["slippage_each_side"])
            raw = float(entry_bar["open"])
            # 手算：① 滑点 ② 买方向向上取整 ③ 止损 ④ 止损距离 ⑤ 风险数量
            h_slip = raw * (1.0 + slip)
            h_entry = round_buy_price(h_slip, ps)
            h_stop = round_protect_price(h_entry * (1.0 - RC.STOPLOSS_S), ps)
            h_dist = h_entry - h_stop
            h_qty_risk = (float(calp["initial_equity"])
                          * float(calp["risk_per_trade_fraction"]) / h_dist)
            h_qty_single = (float(calp["initial_equity"])
                            * float(calp["single_name_notional_cap"]) / h_entry)
            h_qty_cap = min(h_qty_risk, h_qty_single)
            pq = (prev_bar or {}).get("quote_volume")
            if pq is not None:
                h_qty_cap = min(h_qty_cap,
                                float(pq) * float(calp["participation_cap"]) / h_entry)
            h_qty = _step_floor(h_qty_cap, step)
            h_notional = h_qty * h_entry
            rows.append({
                "symbol": sym, "caliber": cal, "status": "ok",
                "raw_open": raw,
                "手算": {"entry": h_entry, "stop": h_stop, "dist": h_dist,
                       "qty": h_qty, "notional": h_notional},
                "replay_calc": {"entry": r["entry_price"], "stop": r["initial_stop_price"],
                                "dist": r["stop_dist"], "qty": r["qty"],
                                "notional": r["notional"]},
                "逐位一致": {
                    "entry": abs(h_entry - r["entry_price"]) < 1e-12,
                    "stop": abs(h_stop - r["initial_stop_price"]) < 1e-12,
                    "dist": abs(h_dist - r["stop_dist"]) < 1e-12,
                    "qty": abs(h_qty - r["qty"]) < 1e-12,
                    "notional": abs(h_notional - r["notional"]) < 1e-9,
                },
                "风险核对": {
                    "planned_risk": r["planned_risk"],
                    "预算": float(calp["initial_equity"]) * float(calp["risk_per_trade_fraction"]),
                    "不超预算": r["planned_risk"]
                    <= float(calp["initial_equity"]) * float(calp["risk_per_trade_fraction"]) + 1e-9,
                },
            })
    ok_rows = [r for r in rows if r.get("status") == "ok"]
    rep["D_独立复算"] = {
        "样本数": len(ok_rows),
        "全部逐位一致": all(all(r["逐位一致"].values()) for r in ok_rows) if ok_rows else None,
        "风险全部不超预算": all(r["风险核对"]["不超预算"] for r in ok_rows) if ok_rows else None,
        "rows": rows,
        "读法": ("手算路径完全不经过 `replay_calc` 的封装，只用 `wx_execution` 的三个取整原语；"
                 "两边逐位相同 ⇒ 说明 `replay_calc` 没有偷偷换一套算术。"),
    }


# ══════════════════════════ 主流程 ══════════════════════════

def sec_e(rep, rows_b):
    """旧报告（ACCUM-EARLY-01/07）与新结果的**同事件对照**。"""
    if not OLD07.exists():
        rep["E_旧新对照"] = {"skip": "旧回放产物不存在"}
        return
    old = json.loads(OLD07.read_text(encoding="utf-8"))
    new_by = {}
    for r in rows_b:
        if r.get("status") == "ok":
            new_by.setdefault((r["symbol"], r["caliber"]), r)
    rows = []
    for c in old.get("chains", []):
        if not isinstance(c, dict) or "error" in c:
            continue
        sym = c.get("symbol")
        ce = (c.get("7_退出与成本") or {}).get("成本模型_生效视图") or {}
        nv = new_by.get((sym, "生效视图_5bps"))
        pk = new_by.get((sym, "策略包_3bps"))
        rows.append({
            "symbol": sym,
            "旧_初始止损": (c.get("6_止损") or {}).get("初始止损"),
            "旧_数量": (c.get("5_成交") or {}).get("qty"),
            "旧_退出原因": ((c.get("7_退出与成本") or {}).get("退出") or {}).get("reason"),
            "旧_资金费成本": ce.get("funding_cost"),
            "旧_净额": ce.get("net"),
            "新_初始止损": (nv or {}).get("initial_stop"),
            "新_数量": (nv or {}).get("qty"),
            "新_退出原因": (nv or {}).get("exit_reason"),
            "新_资金费成本": (nv or {}).get("funding_cost"),
            "新_净额_生效视图": (nv or {}).get("realized_net"),
            "新_净额_策略包": (pk or {}).get("realized_net"),
            "净额差_新减旧": (None if (not nv or ce.get("net") is None
                                 or nv.get("realized_net") is None)
                        else round(nv["realized_net"] - float(ce["net"]), 6)),
        })
    rep["E_旧新对照"] = {
        "rows": rows,
        "读法": ("旧净额是按「原始开盘价算止损/数量 + 全额成交 + 费率求和算资金费」得到的；"
                 "新净额按获批实现逐项重算。两者不是同一个东西，差多少就是修掉多少错。"),
    }


def main() -> int:
    import research_15m as R
    rep = {"probe": "04_回放计算修复", "generated_at_utc": iso(time.time() * 1000),
           "工单": "ACCUM-EARLY-02 第二部分（回放计算七项）", "errors": []}
    merged = R.Merged15m(str(HOT), str(COLD))
    dd = json.loads(STATUS.read_text(encoding="utf-8"))[
        "signal_engine"]["bootstrap"]["discarded_detail"]
    dd = sorted(dd, key=lambda x: x["snapshot_utc"])

    for name, fn in (("A", lambda r: sec_a(r)),
                     ("B", lambda r: sec_b(r, merged, dd)),
                     ("D", lambda r: sec_d(r, merged, dd))):
        try:
            out = fn(rep)
            if name == "B":
                rep["_rows_b"] = out
        except Exception as exc:                                  # noqa: BLE001
            rep["errors"].append({"stage": name,
                                  "error": f"{type(exc).__name__}: {exc}",
                                  "trace": traceback.format_exc()[-2000:]})
    try:
        sec_c(rep, merged, dd, rep.get("_rows_b") or [])
    except Exception as exc:                                      # noqa: BLE001
        rep["errors"].append({"stage": "C",
                              "error": f"{type(exc).__name__}: {exc}",
                              "trace": traceback.format_exc()[-2000:]})
    rep.pop("_rows_b", None)
    try:
        sec_e(rep, rep.get("B_全量回放", {}).get("逐条") or [])
    except Exception as exc:                                      # noqa: BLE001
        rep["errors"].append({"stage": "E",
                              "error": f"{type(exc).__name__}: {exc}",
                              "trace": traceback.format_exc()[-2000:]})

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "04_回放计算修复.json").write_text(
        json.dumps(rep, ensure_ascii=False, indent=1, default=str), encoding="utf-8")

    print(json.dumps({
        "A_不同字段": rep.get("A_两套口径", {}).get("不同的字段"),
        "B_汇总": rep.get("B_全量回放", {}).get("汇总"),
        "D_一致": rep.get("D_独立复算", {}).get("全部逐位一致"),
        "D_样本": rep.get("D_独立复算", {}).get("样本数"),
        "C_退出原因": rep.get("C_七项取证", {}).get("⑥_退出分类", {}).get("本批出现的退出原因"),
        "C_跳空样本数": rep.get("C_七项取证", {}).get("⑦_跳空敏感性", {}).get("真实跳空样本数"),
        "C_跳空演示": (rep.get("C_七项取证", {}).get("⑦_跳空敏感性", {})
                   .get("真实 K 线上的跳空演示", {})),
        "C_未平仓断言": rep.get("C_七项取证", {}).get("⑤_期末未平仓只计未实现", {}).get("通过"),
        "C_未平仓演示": (rep.get("C_七项取证", {}).get("⑤_期末未平仓只计未实现", {})
                   .get("演示", {})),
        "E_旧新对照": rep.get("E_旧新对照", {}).get("rows"),
        "errors": [e["error"] for e in rep["errors"]],
    }, ensure_ascii=False, indent=1, default=str))
    return 0 if not rep["errors"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
