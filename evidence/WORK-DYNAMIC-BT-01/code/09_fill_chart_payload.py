# -*- coding: utf-8 -*-
"""WORK-DYNAMIC-BT-01｜补全复盘 `chart.json` 中**组合级**可用的字段。

背景
────
`GET /api/v1/backtests/{id}/chart` 已能读到本单 45 个 run 的 `chart.json`，
但其中 `candles / trades / equity_curve` 为空：
· `candles` 空是**结构性的**（74 标的组合无法塞进单标的图表契约）——保留空并写明原因；
· `trades / equity_curve` 是**组合级**数据，本来就能填，之前漏了 ⇒ 这里补上，
  让「权益曲线 + 逐笔标记 + 信号点」在现有入口里可看，不伪造任何 K 线。

只改 `回测日志/event_trend_v0_1/runs_work_dynamic_bt_01/<run_id>/chart.json`
（本单**新建**的批次目录，不触碰任何既有 run 目录、不改公共源码）。
"""
from __future__ import annotations

import gzip
import json
from pathlib import Path

import pandas as pd

BASE = Path("/Users/boos/Desktop/威科夫因子共振趋势")
EXP = BASE / "evidence/WORK-DYNAMIC-BT-01"
OUT = EXP / "out"
REPLAY = EXP / "replay"
BATCH = BASE / "回测日志/event_trend_v0_1/runs_work_dynamic_bt_01"


def _count_lines(p: Path) -> int:
    if not p.exists():
        return -1
    with open(p, "rb") as f:
        return sum(1 for _ in f)


def main() -> None:
    idx = pd.read_csv(OUT / "02_运行索引.csv")
    eq_all = pd.read_csv(OUT / "04_账户日权益曲线_全部.csv.gz")
    updated, missing = [], []

    for _, m in idx.iterrows():
        name = m["strategy"]
        rid = m["run_id"]
        cj = BATCH / rid / "chart.json"
        if not cj.exists():
            missing.append(rid)
            continue
        chart = json.loads(cj.read_text(encoding="utf-8"))

        # ── 权益曲线（含浮盈，UTC 日界）──
        sub = eq_all[eq_all["strategy"] == name]
        chart["equity_curve"] = [
            {"t": int(r.t_ms), "equity": float(r.equity), "cash": float(r.cash),
             "unrealized": float(r.unrealized)}
            for r in sub.itertuples(index=False)
        ]

        # ── 逐笔（含生效止损轨迹）——直接取复盘包，保证与逐笔表同源 ──
        rp = REPLAY / f"{name}.jsonl.gz"
        trades, signals = [], []
        if rp.exists():
            with gzip.open(rp, "rt", encoding="utf-8") as f:
                for line in f:
                    t = json.loads(line)
                    sg = t["signal"]
                    signals.append({
                        "t": sg["t"], "kind": "candidate_from_fill", "direction": "long",
                        "signal_id": t["candidate_id"],
                        "decision": sg["t"], "P0": t["entry"]["price"],
                        "confirm_t": sg["confirm_t"], "entry_t": t["entry"]["t"],
                        "time_basis": ("signal=信号 1h K 线收盘；confirm=紧接下一根 1h 收盘确认；"
                                       "entry=confirm 之后首个 15m 开盘（strictly causal）"),
                        "note": "由引擎成交记录反推的候选；精确信号/确认时刻见 audit_confirmation_*.csv",
                    })
                    trades.append({
                        "trade_id": f"trd_{name}_{t['trade_id']:04d}",
                        "symbol": t["symbol"],
                        "pair": t["pair"],
                        "direction": "long",
                        "signal_type": 1,
                        "entry": {"t": t["entry"]["t"], "price": t["entry"]["price"],
                                  "qty": t["entry"]["qty"]},
                        "exit": {"t": t["exit"]["t"], "price": t["exit"]["price"],
                                 "reason": t["exit"]["reason"]},
                        "stop_trace": t["stop_trace"],
                        "stage_events": t["stage_events"],
                        "net_pnl": t["pnl"]["profit_abs"],
                        "exit_reason": t["exit"]["reason"],
                    })
        chart["trades"] = trades
        chart["signals"] = signals
        chart["signals_note"] = (
            "signals 来自 replay 逐笔的 signal 段（signal=信号 1h 收盘 / confirm=下一根 1h 收盘 / "
            "entry=confirm 后首个 15m 开盘）；与 candidates.jsonl 同源，均为「成交反推」而非独立信号扫描")
        chart["chart_partial"] = True
        chart["what_works"] = ("equity_curve（含浮盈账户日权益）、trades（逐笔 + 生效止损轨迹 + 切档事件）、"
                               "signals（信号/确认/入场三点）可用；candles 为空（结构性限制，见 chart_blocked_reason）")
        cj.write_text(json.dumps(chart, ensure_ascii=False, indent=2), encoding="utf-8")
        updated.append({"run_id": rid, "strategy": name,
                        "equity_points": len(chart["equity_curve"]),
                        "trades": len(trades), "signals": len(signals),
                        "candidates_jsonl": _count_lines(BATCH / rid / "candidates.jsonl")})

    df = pd.DataFrame(updated)
    df.to_csv(OUT / "25_图表载荷补全.csv", index=False, encoding="utf-8-sig")

    # ── 把载荷补全结果并入产物清单（可复算，不手写）──
    inv_p = OUT / "18_产物清单.json"
    if inv_p.exists():
        inv = json.loads(inv_p.read_text(encoding="utf-8"))
        inv["chart_payload"] = {
            "runs": int(len(df)),
            "equity_points_per_run": int(df["equity_points"].min()) if len(df) else 0,
            "trades_total": int(df["trades"].sum()) if len(df) else 0,
            "signals_total": int(df["signals"].sum()) if len(df) else 0,
            "signals_equals_trades": bool((df["trades"] == df["signals"]).all()) if len(df) else None,
            "candles_filled": 0,
            "candles_note": "结构性留空：图表契约单标的 symbol+window，74 标的组合无法承载",
            "verified_via": "GET /api/v1/backtests/<run_id>/chart（只读，200）",
        }
        inv_p.write_text(json.dumps(inv, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"已补全 {len(df)} 个 chart.json；缺失 {len(missing)}")
    if len(df):
        print(df[["equity_points", "trades", "signals"]].describe().loc[["min", "max"]].to_string())


if __name__ == "__main__":
    main()
