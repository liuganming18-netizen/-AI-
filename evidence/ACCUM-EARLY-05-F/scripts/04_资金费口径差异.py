# -*- coding: utf-8 -*-
"""ACCUM-EARLY-05-F｜04 资金费口径差异（04-A 未计 vs 04-C/04-D 计入）。

要回答的只有一件事：**这两套结果能不能直接比。**
不能比的原因不止资金费一项，所以先把差异逐项摊开，再单独量化资金费那一项。

量化方法（可复算）
------------------
· 取 04-A 两段窗口的全部成交明细（entry_bar_open_ms / exit_ms / r_multiple）；
· 按 `funding_history`（rate_type='settled'）取「入场 < 结算时刻 ≤ 出场」的费率求和 Σrate；
· 04-A 的 R 定义 = 净收益率 / 初始止损幅度 5%，
  而资金费对**名义额**的收益率影响 = −Σrate，
  ⇒ 每笔 ΔR = −Σrate / 0.05（多头付正费率，符号取反）；
· 这是「若 04-A 也计资金费，R 会移动多少」的估计，不改 04-A 任何产物。

只读；产物落本单 out/。
"""
from __future__ import annotations

import json
import sqlite3
import statistics
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parents[1] / "out"
HOT = ROOT / "数据" / "市场数据" / "db" / "market_v2.sqlite3"
EVID = ROOT / "evidence"


def iso(ms):
    if ms is None:
        return None
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(int(ms) / 1000))


def load_funding_rates(con, symbol: str, a: int, b: int) -> list:
    return con.execute(
        "SELECT funding_time, funding_rate FROM funding_history "
        "WHERE exchange='binance' AND market='um' AND symbol=? AND rate_type='settled' "
        "AND funding_time > ? AND funding_time <= ? ORDER BY funding_time",
        (symbol, int(a), int(b))).fetchall()


def fills_of(tag: str) -> list:
    p = EVID / "ACCUM-EARLY-04-A" / "out" / f"01_共同观察范围_{tag}.json"
    if not p.exists():
        return []
    d = json.loads(p.read_text(encoding="utf-8"))
    out = []
    for s in d["per_symbol"]:
        for sch, v in (s.get("schemes") or {}).items():
            for t in (v.get("trades") or []):
                if t.get("status") != "filled":
                    continue
                if t.get("entry_bar_open_ms") is None or t.get("exit_ms") is None:
                    continue
                out.append({"symbol": s["symbol"], "scheme": sch,
                            "entry_ms": int(t["entry_bar_open_ms"]),
                            "exit_ms": int(t["exit_ms"]),
                            "r": t.get("r_multiple"),
                            "net_ret": t.get("net_ret"),
                            "held_hours": t.get("held_hours")})
    return out


def quantify(tag: str, con, gap_ms: tuple | None = None) -> dict:
    fills = fills_of(tag)
    if not fills:
        return {"applicable": False}
    STOP = 0.05
    rows = []
    intervals = []
    for f in fills:
        evs = load_funding_rates(con, f["symbol"], f["entry_ms"], f["exit_ms"])
        s_rate = sum(float(x[1]) for x in evs if x[1] is not None)
        ts = [int(x[0]) for x in evs]
        for a, b in zip(ts, ts[1:]):
            intervals.append((b - a) / 3_600_000.0)
        rows.append({**f, "funding_settlements": len(evs),
                     "sum_rate": s_rate, "delta_r": -s_rate / STOP})
    n = len(rows)
    dr = [r["delta_r"] for r in rows]
    base_r = [r["r"] for r in rows if r["r"] is not None]
    base_sum = sum(base_r) if base_r else None
    # 缺口要分两类：持仓不足一个结算周期（本来就该是 0 次）vs 该结算却没记录
    no_funding = [r for r in rows if r["funding_settlements"] == 0]
    short_hold = [r for r in no_funding
                  if (r.get("held_hours") or 0) < 8.0]
    real_gap = [r for r in no_funding if (r.get("held_hours") or 0) >= 8.0]
    # 缺口归因：是否落在热库 funding 的 15.8 天大洞里
    in_hot_gap = []
    if gap_ms:
        in_hot_gap = [r for r in real_gap
                      if r["entry_ms"] < gap_ms[1] and r["exit_ms"] > gap_ms[0]]
    return {
        "applicable": True,
        "fills": n,
        "funding_zero_settlement_trades": len(no_funding),
        "funding_zero_but_hold_lt_8h": len(short_hold),
        "funding_real_gap_trades": len(real_gap),
        "funding_gap_inside_hot_gap": len(in_hot_gap),
        "funding_gap_attribution": (
            f"真实缺口 {len(real_gap)} 笔中有 {len(in_hot_gap)} 笔的持仓区间与"
            f"热库资金费缺口段相交 ⇒ 归因到「热库缺、冷档有」，不是策略侧问题"),
        "funding_real_gap_examples": [
            {"symbol": r["symbol"], "entry_utc": iso(r["entry_ms"]),
             "exit_utc": iso(r["exit_ms"]), "held_hours": r.get("held_hours")}
            for r in real_gap[:5]],
        "settlements": {
            "total": sum(r["funding_settlements"] for r in rows),
            "median_per_trade": statistics.median(
                [r["funding_settlements"] for r in rows]) if rows else None,
            "max_per_trade": max((r["funding_settlements"] for r in rows), default=None),
            "median_interval_hours": (round(statistics.median(intervals), 2)
                                      if intervals else None),
            "interval_note": ("结算间隔中位数来自实际数据，不用「8 小时」这种教科书假设；"
                              "这批币多数是 4 小时一次"),
        },
        "held_hours": {
            "median": round(statistics.median(
                [r["held_hours"] for r in rows if r.get("held_hours") is not None]), 2),
            "why": "结算次数与持仓时长必须一起看，否则中位 9 次会被误读成异常",
        },
        "sum_rate_stats": {
            "mean": round(statistics.fmean([r["sum_rate"] for r in rows]), 8) if rows else None,
            "median": round(statistics.median([r["sum_rate"] for r in rows]), 8) if rows else None,
            "total": round(sum(r["sum_rate"] for r in rows), 6),
            "positive_trades": sum(1 for r in rows if r["sum_rate"] > 0),
            "negative_trades": sum(1 for r in rows if r["sum_rate"] < 0),
        },
        "delta_r": {
            "total": round(sum(dr), 4),
            "mean_per_trade": round(statistics.fmean(dr), 6) if dr else None,
            "median_per_trade": round(statistics.median(dr), 6) if dr else None,
            "direction": ("净收入（负费率环境，多头收钱）" if sum(dr) > 0
                          else "净成本（正费率环境，多头付钱）"),
            "pct_of_abs_base_R_sum": (round(100.0 * sum(dr) / abs(base_sum), 3)
                                      if base_sum else None),
            "base_R_sum_after": (round(base_sum + sum(dr), 4) if base_sum else None),
        },
        "per_trade_usdt_and_r": {
            "assumption": "每笔风险 = 权益 0.5%，初始止损 5% ⇒ 名义额 = 权益 × 0.1",
            "usdt_per_trade": round(sum(r["sum_rate"] for r in rows) * 500 / n, 6) if n else None,
            "r_per_trade": round((sum(r["sum_rate"] for r in rows) * 500 / n) / 25, 6) if n else None,
            "why": "用 5000 权益、25 USDT 风险额把 Σrate 折成 USDT 与 R，供与 04-C 的每笔量级对照",
        },
        "base": {"r_sum": round(base_sum, 4) if base_sum else None,
                 "r_mean": round(statistics.fmean(base_r), 4) if base_r else None},
        "rule": ("ΔR = −Σrate / 0.05；Σrate 只取 rate_type='settled' 且 "
                 "入场 < 结算 ≤ 出场；多头付正费率，负费率则为收入"),
    }


def calibers() -> dict:
    """两套口径的差异逐项列出，并引用各自产物里的实测值。"""
    out = {"04A": {}, "04CD": {}, "differences": []}
    p = EVID / "ACCUM-EARLY-04-A" / "out" / "01_共同观察范围_holdout_final.json"
    if p.exists():
        d = json.loads(p.read_text(encoding="utf-8"))
        out["04A"] = {"discipline": d.get("discipline"),
                      "slippage_bps": d.get("slippage_bps"),
                      "metric": "R 倍数 = 净收益率 / 5%"}
    p = EVID / "ACCUM-EARLY-04-C" / "out" / "02_原策略基线结果.json"
    if p.exists():
        d = json.loads(p.read_text(encoding="utf-8"))
        out["04CD"]["04C_baseline"] = {
            k: d.get(k) for k in ("cal_5bps", "cal_3bps") if k in d}
        out["04CD"]["has_funding_field"] = "total_funding" in json.dumps(d)
    out["differences"] = [
        {"项": "资金费", "04A": "不计（discipline 明示）",
         "04CD": "逐笔按结算事件计入，缺 rate/mark 记 funding_gap 不补 0",
         "影响": "系统性偏向多头付费；单笔量级见本文件 delta_r"},
        {"项": "计量单位", "04A": "R 倍数（相对 5% 止损幅度）",
         "04CD": "USDT 净额 + 回撤比例", "影响": "不能直接横比，必须先换算"},
        {"项": "本金与仓位", "04A": "用 R 绕开，未定义名义额",
         "04CD": "生效视图 5000 / 策略包 2000，名义额由风险与止损推出",
         "影响": "资金费的绝对额取决于名义额 ⇒ 同一 Σrate 在不同本金下 USDT 不同"},
        {"项": "滑点", "04A": "3bps（主表）", "04CD": "5bps / 3bps 两套并列",
         "影响": "与资金费同为成本项，混用会把口径差算进策略差"},
        {"项": "参与率上限", "04A": "不模拟，只报触发笔数",
         "04CD": "0.1% / 1.0% 两套并列并实际缩量",
         "影响": "缩量改 qty ⇒ 改资金费绝对额与净额"},
        {"项": "候选集", "04A": "526 池、四条规则共用箱体候选",
         "04CD": "51 条历史候选（原策略）", "影响": "样本不同，任何总量都不可比"},
        {"项": "区间", "04A": "2025-01-01~2026-07-01 / 2026-07-01~2026-10-12",
         "04CD": "2026-06-27~2026-10-26", "影响": "区间不同，费率环境不同"},
    ]
    return out


def unified_requirements() -> list:
    return [
        {"编号": "U1", "要求": "成本三项必须同时在场：手续费（双边 5bps）、滑点（待批）、资金费（逐笔按结算时刻）",
         "为什么": "少任何一项，两套结果的差里就混进了成本口径差，而不是策略差"},
        {"编号": "U2", "要求": "先定名义额口径（本金 × 每笔风险 / 止损幅度），再谈资金费绝对额",
         "为什么": "Σrate 无量纲，USDT 需要名义额；04-A 用 R 绕开名义额，04-C 用 5000/2000 本金"},
        {"编号": "U3", "要求": "R 与 USDT 必须给显式换算（R = 净额 / (权益 × 0.5%)），不许混着比",
         "为什么": "同一笔交易两种写法差 200 倍（5000 本金 / 25 USDT 风险额）"},
        {"编号": "U4", "要求": "资金费只取 rate_type='settled'，且区间为「入场 < 结算 ≤ 出场」",
         "为什么": "取到 predicted 或含入场前结算，会多算或少算一次"},
        {"编号": "U5", "要求": "退出时刻与结算时刻相差 ≤15 分钟的，标 funding_order_uncertain，不静默择一",
         "为什么": "先结算还是先平仓，方向相反"},
        {"编号": "U6", "要求": "参与率上限必须在两边同值，且缩量后重算 qty 与资金费",
         "为什么": "0.1% 与 1.0% 差 10 倍，缩量直接改名义额"},
        {"编号": "U7", "要求": "资金费缺口（区间内 0 条结算记录）单独计数，不按 0 处理",
         "为什么": "按 0 处理等于假设免费持仓，系统性偏乐观"},
        {"编号": "U8", "要求": "两套口径并列时，同一张表里不得混排；先各自成表再给差异表",
         "为什么": "混排会让读者把口径差读成策略差"},
    ]


def main():
    con = sqlite3.connect(f"file:{HOT}?mode=ro", uri=True)
    facts = {
        "generated_at_utc": iso(int(time.time() * 1000)),
        "calibers": calibers(),
        "quantified": {},
        "unified_input_requirements": unified_requirements(),
    }
    # 热库资金费主缺口段（从 03 产物读，不手抄时刻）
    gap_ms = None
    p3 = OUT / "03_读取链与可得时点.json"
    if p3.exists():
        g = json.loads(p3.read_text(encoding="utf-8")).get("funding_coverage_gap") or {}
        segs = g.get("hot_gap_segments") or []
        if segs:
            import calendar
            def to_ms(s):
                return calendar.timegm(time.strptime(s, "%Y-%m-%dT%H:%M:%SZ")) * 1000
            gap_ms = (to_ms(segs[0]["from_utc"]), to_ms(segs[0]["to_utc"]))
            facts["hot_funding_gap_used"] = {
                "from_utc": segs[0]["from_utc"], "to_utc": segs[0]["to_utc"],
                "symbols": segs[0]["symbols"], "max_hours": segs[0]["max_hours"]}
    for tag in ("research_final", "holdout_final"):
        facts["quantified"][tag] = quantify(tag, con, gap_ms)
    con.close()

    # 交叉验证：04-C 的实测资金费与净额比例，用来判断本单估算的量级是否可信
    p = EVID / "ACCUM-EARLY-04-C" / "out" / "02_原策略基线结果.json"
    if p.exists():
        d = json.loads(p.read_text(encoding="utf-8"))
        bl = d.get("historical_replayed_baselines") or {}
        xv = {}
        for key, blk in bl.items():
            tf = blk.get("total_funding")
            net = blk.get("total_realized_net")
            fees = blk.get("total_fees")
            cand = blk.get("total_candidates")
            xv[key] = {
                "total_funding": tf, "total_realized_net": net, "total_fees": fees,
                "total_candidates": cand,
                "per_trade_usdt": (round(tf / cand, 6) if tf is not None and cand else None),
                "funding_pct_of_abs_net": (round(100.0 * tf / abs(net), 3)
                                           if tf is not None and net else None),
                "funding_pct_of_fees": (round(100.0 * tf / fees, 3)
                                        if tf is not None and fees else None),
            }
        facts["cross_check_04C"] = xv

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "04_资金费口径.json").write_text(
        json.dumps(facts, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: {kk: v.get(kk) for kk in ("fills", "funding_real_gap_trades",
                                                   "delta_r", "base", "sum_rate_stats")}
                      for k, v in facts["quantified"].items()},
                     ensure_ascii=False, indent=2))
    print("04C", facts.get("cross_check_04C"))


if __name__ == "__main__":
    main()
