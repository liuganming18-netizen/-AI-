# -*- coding: utf-8 -*-
"""ACCUM-EARLY-04-A｜07 仓位约束下的可比对照（只读）。

为什么必须补这一步
------------------
`01` 算出的「R 合计」是**无仓位上限**的累加：只要信号出现就记一笔。
研究区间里四组笔数是 149 / 3209 / 2279 / 2164，相差 20 倍以上，
把它们放在一起比 R 合计，等于比「谁开仓次数多」——这没有意义，也不可实现。

冻结参数里真正生效的约束是：
    max_active_positions = 3
    max_total_risk_budget = 0.015，每笔 0.005  ⇒ 同时最多 3 笔（正好用满）
    max_notional_per_position = 0.30，max_total_notional = 0.90  ⇒ 名义也正好卡 3 笔

所以这里按「同一套 3 仓上限」重放四组，得到可横比的指标。

分配规则（必须先说清，否则就是拿未来信息挑好单）
--------------------------------------------
**先到先得（FCFS）**：把所有标的的成交按入场时刻排序，遇到就尝试占用一个仓位；
占满 3 个就跳过，直到有仓位空出来。
不按 R 大小、不按标的白名单、不按信号强弱排序——那些都需要未来信息。

这个规则对四组完全相同，因此组间可比；但它是**一种**占用顺序，
换一种同样合法的顺序结果会变，所以结论只能写成「在该规则下」，不能写成「真实可实现收益」。

输入：out/01_共同观察范围_<tag>.json
输出：out/07_仓位约束对照_<tag>.json
"""
from __future__ import annotations

import argparse
import datetime
import json
from pathlib import Path

HERE = Path(__file__).resolve()
ROOT = HERE.parents[3]
OUT = HERE.parents[1] / "out"

MAX_POS = 3                 # 冻结参数 max_active_positions
MAX_RISK = 0.015            # max_total_risk_budget
RISK_PER = 0.005            # risk_budget_per_trade


def iso(ms):
    return datetime.datetime.fromtimestamp(ms / 1000, datetime.UTC).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def load_trades(tag: str) -> dict:
    """{scheme: [trade, ...]}，只保留已成交。

    数据末尾仍未平仓的（exit_reason=open_at_data_end）**不能丢**：它一直占着仓位。
    这里把出场时刻补成窗口终点，并打上 unclosed 标记，统计时如实报笔数。
    """
    p = OUT / f"01_共同观察范围_{tag}.json"
    d = json.loads(p.read_text(encoding="utf-8"))
    end_ms = d["window"]["to_ms"]
    bag = {}
    for row in d["per_symbol"]:
        sym = row["symbol"]
        for name, sc in (row.get("schemes") or {}).items():
            for t in sc.get("trades") or []:
                if t.get("status") != "filled":
                    continue
                bag.setdefault(name, []).append({
                    "symbol": sym,
                    "entry_ms": t["entry_bar_open_ms"],
                    "exit_ms": t["exit_ms"] if t["exit_ms"] is not None else end_ms,
                    "unclosed": t["exit_ms"] is None,
                    "r": t["r_multiple"],
                    "held_h": t.get("held_hours"),
                    "reason": t.get("exit_reason"),
                })
    return d, bag


def replay_fcfs(trades: list, max_pos: int = MAX_POS) -> dict:
    """先到先得占仓：返回被执行的子集与统计。

    同一条时间轴：入场时刻升序；仓位在 exit_ms 之后才释放。
    入场与出场同刻时，先释放再占用（保守：不假设可以无缝滚动）。
    """
    seq = sorted(trades, key=lambda x: (x["entry_ms"], x["exit_ms"], x["symbol"]))
    busy = []                       # 各仓位当前占用到什么时候
    taken, skipped = [], []
    for t in seq:
        busy = [b for b in busy if b > t["entry_ms"]]     # 先释放
        if len(busy) >= max_pos:
            skipped.append(t)
            continue
        busy.append(t["exit_ms"])
        taken.append(t)

    def stats(rows):
        n = len(rows)
        if not n:
            return {"笔数": 0, "R合计": 0.0, "R均值": None, "R中位": None,
                    "R为正": 0, "胜率": None, "止损全亏": 0}
        rs = sorted(x["r"] for x in rows)
        mid = rs[n // 2] if n % 2 else (rs[n // 2 - 1] + rs[n // 2]) / 2
        return {"笔数": n,
                "R合计": round(sum(rs), 4),
                "R均值": round(sum(rs) / n, 4),
                "R中位": round(mid, 4),
                "R为正": sum(1 for r in rs if r > 0),
                "胜率": round(sum(1 for r in rs if r > 0) / n, 4),
                "止损全亏": sum(1 for r in rs if r <= -0.999)}

    n_unclosed = sum(1 for x in seq if x.get("unclosed"))
    allst = stats(seq)
    tk = stats(taken)
    # 仓位占用率：被占用的仓位·小时 / (总时长·仓位上限)
    if seq:
        t0 = min(x["entry_ms"] for x in seq)
        t1 = max(x["exit_ms"] for x in seq)
        span_h = max((t1 - t0) / 3_600_000, 1e-9)
        busy_h = sum((x["exit_ms"] - x["entry_ms"]) / 3_600_000 for x in taken)
    else:
        span_h, busy_h = 0.0, 0.0
    return {
        "全部信号": allst,
        "3仓约束下可执行": tk,
        "被挤掉": stats(skipped),
        "执行率": round(len(taken) / len(seq), 4) if seq else None,
        "仓位时间利用率": round(busy_h / (span_h * max_pos), 4) if span_h else None,
        "时间跨度小时": round(span_h, 1),
        "未平仓笔数（按占仓到窗口终点处理）": n_unclosed,
        "明细": taken,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("tags", nargs="+", help="如 research_final holdout_final")
    args = ap.parse_args()

    rep = {"probe": "07_仓位约束对照",
           "max_active_positions": MAX_POS,
           "max_total_risk_budget": MAX_RISK,
           "risk_budget_per_trade": RISK_PER,
           "分配规则": ("先到先得：全部标的按入场时刻升序，仓位在出场时刻之后才释放；"
                        "不按 R 大小或信号强弱排序（那需要未来信息）。"
                        "对四组用同一规则，因此组间可比；但它只是**一种**合法占用顺序，"
                        "不能当成真实可实现收益。"),
           "runs": {}}

    for tag in args.tags:
        d, bag = load_trades(tag)
        run = {"window": d.get("window"), "slippage_bps": d.get("slippage_bps"),
               "schemes": {}}
        for name in ("baseline", "4a", "4b", "4c"):
            if name not in bag:
                continue
            r = replay_fcfs(bag[name])
            r.pop("明细", None)
            run["schemes"][name] = r
        # 四组合并后的占仓竞争：同一时刻只有一个策略在跑，所以分开算才是对的；
        # 这里额外给出「若把四组信号混在一起抢 3 个仓位」的上限参考。
        merged = [x for v in bag.values() for x in v]
        mr = replay_fcfs(merged)
        mr.pop("明细", None)
        run["四组混合抢仓_参考"] = mr
        rep["runs"][tag] = run

    OUT.mkdir(parents=True, exist_ok=True)
    for tag in args.tags:
        single = {"probe": rep["probe"],
                  "max_active_positions": MAX_POS,
                  "分配规则": rep["分配规则"],
                  "window": rep["runs"][tag]["window"],
                  "schemes": rep["runs"][tag]["schemes"],
                  "四组混合抢仓_参考": rep["runs"][tag]["四组混合抢仓_参考"]}
        (OUT / f"07_仓位约束对照_{tag}.json").write_text(
            json.dumps(single, ensure_ascii=False, indent=1), encoding="utf-8")

    for tag, run in rep["runs"].items():
        print("=" * 66)
        print(tag, run["window"]["from"], "~", run["window"]["to"])
        print(f'{"组":10s}{"信号":>6s}{"可执行":>7s}{"R合计":>10s}'
              f'{"R均值":>9s}{"胜率":>8s}{"利用率":>8s}')
        for name, s in run["schemes"].items():
            a, t = s["全部信号"], s["3仓约束下可执行"]
            print(f'{name:10s}{a["笔数"]:6d}{t["笔数"]:7d}{t["R合计"]:10.4f}'
                  f'{t["R均值"]:9.4f}{(t["胜率"] or 0):8.4f}'
                  f'{(s["仓位时间利用率"] or 0):8.4f}')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
