#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""60_changed_symbols.py — 把「这一根小时线上换过阶段的币」与「它的条件实际值/阈值」拼在一起。

为什么要有这个脚本（SIGNAL-LIVE-CHECK-09 要求 2）：
    生产接口只给 `state_counter`（全池各阶段多少个）和 `idle_breakdown`
    （**只有 IDLE 的币**、且每类只给一个样例）。要回答「哪几个币换了阶段、
    换的时候箱体多少、离下一关差多少」，生产没有任何直读入口。
    本单的做法：逐币阶段取**生产直读**（采样器每 60s 抓 `per_symbol_state`），
    条件值取**同一水位的隔离回放**（`30_iso_replay.py` 导出），
    并且只在 `compare.per_symbol_diff_n == 0` 时才允许拼——否则就是拿两份不同的
    东西硬凑，结论不成立。脚本会自己核这个前置条件，不靠人记。

输入：
    --analysis   `50_analyze_samples.py` 的输出（含逐段 per_symbol_changes）
    --conditions `30_iso_replay.py` 的 `*_per_symbol_conditions.json`（同水位）
    --replay     `30_iso_replay.py` 的主输出（读 compare 做前置断言）

输出：
    JSON + Markdown 两张表（JSON 给机器核，Markdown 给人看）
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def fmt(v, nd: int = 8):
    if v is None:
        return "—"
    if isinstance(v, bool):
        return "是" if v else "否"
    if isinstance(v, (int,)):
        return str(v)
    if isinstance(v, float):
        a = abs(v)
        # 大数（成交量、名义额）用千分位 2 位小数：`:.8f` 会把 2.88e8 打成
        # 288483969.60000002（浮点 ulp 决定，不是计算错），看着像 bug。
        if a >= 1000:
            return f"{v:,.2f}"
        s = f"{v:.{nd}f}".rstrip("0").rstrip(".")
        return s or "0"
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(fmt(x, nd) for x in v) + "]"
    return str(v)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--analysis", default=str(BASE / "out" / "50_sample_analysis.json"))
    ap.add_argument("--conditions", required=True,
                    help="同水位的 *_per_symbol_conditions.json")
    ap.add_argument("--replay", required=True, help="同水位的回放主输出 JSON")
    ap.add_argument("--out", default=str(BASE / "out" / "60_changed_symbols.json"))
    args = ap.parse_args()

    ana = json.loads(Path(args.analysis).read_text(encoding="utf-8"))
    cond = json.loads(Path(args.conditions).read_text(encoding="utf-8"))
    rep = json.loads(Path(args.replay).read_text(encoding="utf-8"))

    # ── 前置断言：回放必须与生产逐币零差异，否则条件值不能代表生产 ──────────
    cmp_ = rep.get("compare") or {}
    gate = {
        "per_symbol_diff_n": cmp_.get("per_symbol_diff_n"),
        "state_counter_equal": cmp_.get("state_counter_equal"),
        "watermark_equal": cmp_.get("watermark_equal"),
        "replay_upto_utc": (rep.get("clock") or {}).get("upto_utc"),
        "conditions_upto_utc": cond.get("upto_utc"),
    }
    ok = (gate["per_symbol_diff_n"] == 0
          and gate["state_counter_equal"] is True
          and gate["watermark_equal"] is True
          and gate["replay_upto_utc"] == gate["conditions_upto_utc"])
    gate["pass"] = ok
    if not ok:
        print("前置断言失败：回放与生产不是同一水位/同一状态，条件值不能拼。")
        print(json.dumps(gate, ensure_ascii=False, indent=1))
        return 2

    ps = cond["per_symbol"]
    th = cond["thresholds"]

    segs = []
    for s in ana.get("segments") or []:
        ch = s.get("per_symbol_changes") or []
        if not ch:
            continue
        rows = []
        for c in ch:
            sym, a, b = c["symbol"], c["from"], c["to"]
            p = ps.get(sym) or {}
            ae = p.get("active_event") or {}
            box = ae.get("box") or {}
            ng = p.get("next_gate") or {}
            rows.append({
                "symbol": sym,
                "from": a,
                "to": b,
                "state_now": p.get("state"),
                "atr": p.get("last_atr"),
                "atr_bars_seen": p.get("atr_bars_seen"),
                "box": {
                    "H": box.get("box_high_h"), "L": box.get("box_low_l"),
                    "ATR": box.get("box_atr_a"), "ER": box.get("box_er"),
                    "width_atr": box.get("width_atr"),
                    "formation_utc": box.get("formation_time_utc"),
                    "bars": box.get("bars_count"),
                } if box else None,
                "event": {
                    "event_id": ae.get("event_id"),
                    "S": ae.get("sweep_low_s"), "Vs": ae.get("sweep_volume_vs"),
                    "sweep_utc": ae.get("sweep_time_utc"),
                    "Lr": ae.get("retest_low_lr"), "retest_utc": ae.get("retest_time_utc"),
                    "breakout_close": ae.get("breakout_close_price"),
                    "breakout_vol": ae.get("breakout_volume"),
                    "v20": ae.get("v20_median_volume"),
                    "oi_growth_6h": ae.get("oi_growth_6h"),
                    "candidate_id": ae.get("candidate_id"),
                } if ae else None,
                "next_gate": ng or None,
                "last_terminated_event": p.get("last_terminated_event"),
                "cooling_until_utc": p.get("cooling_until_utc"),
                "data_quality_invalid": p.get("data_quality_invalid"),
            })
        segs.append({
            "wm_utc": s.get("watermark_1h_utc"),
            "latest_1h_close_utc": s.get("latest_1h_close_utc"),
            "n_samples_in_seg": s.get("n_samples_in_seg"),
            "advance_bars": (s.get("delta_vs_prev") or {}).get("watermark_advance_bars"),
            "changed_n": len(rows),
            "rows": rows,
        })

    out = {
        "probe": "SIGNAL-LIVE-CHECK-09/60_changed_symbols",
        "generated_real_utc": utc_now(),
        "前置断言": gate,
        "口径": ("逐币阶段＝生产直读（采样器）；条件实际值与阈值＝同水位隔离回放导出。"
                 "两者能拼的唯一依据是 per_symbol_diff_n==0。"),
        "thresholds": th,
        "segments": segs,
    }

    p = Path(args.out)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")

    # ── Markdown：给人看的一张表 ────────────────────────────────────────────
    md = ["# 逐根新增小时线：换了阶段的币（含条件实际值与下一关门槛）", "",
          f"生成时刻 {out['generated_real_utc']}｜回放截止 {gate['replay_upto_utc']}｜"
          f"逐币差异 {gate['per_symbol_diff_n']}（前置断言通过）", ""]
    for s in segs:
        md += [f"## 水位推进到 {s['wm_utc']}（新增 {s['advance_bars']} 根 1h，"
               f"{s['changed_n']} 个币换阶段）", "",
               "| 币 | 迁移 | 箱体 H / L | 宽(ATR) | ER | 扫损 S / 回踩 Lr | 下一关要看什么 |",
               "|---|---|---|---|---|---|---|"]
        for r in s["rows"]:
            b = r["box"] or {}
            e = r["event"] or {}
            ng = r["next_gate"] or {}
            gate_txt = ng.get("结论") or "—"
            lt = r.get("last_terminated_event") or {}
            # 事件终止时刻记的是「那根 K 的收盘时刻」= 水位 + 1h；用 ISO 字符串比较即可
            if (lt.get("termination_reason")
                    and (lt.get("ended_at_utc") or "") >= (s["wm_utc"] or "")):
                gate_txt = (f"刚在 {lt.get('ended_at_utc')} 结束："
                            f"{lt.get('termination_reason')}（终态 {lt.get('final_state')}）")
            kl = ng.get("门槛") or {}
            if isinstance(kl, dict) and kl:
                parts = []
                for k, v in kl.items():
                    if k in ("时限(h)",):
                        continue
                    parts.append(f"{k} {fmt(v)}")
                gate_txt += "｜" + "；".join(parts)
            md.append(
                f"| {r['symbol']} | {r['from']}→{r['to']} | "
                f"{fmt(b.get('H'))} / {fmt(b.get('L'))} | {fmt(b.get('width_atr'), 3)} | "
                f"{fmt(b.get('ER'), 4)} | "
                f"{fmt(e.get('S'))} / {fmt(e.get('Lr'))} | {gate_txt} |")
        md.append("")
    md += ["## 冻结阈值（本次生效值，只读不改）", "", "```",
           json.dumps(th, ensure_ascii=False, indent=1), "```", ""]
    md_p = p.parent / (p.stem + ".md")
    md_p.write_text("\n".join(md), encoding="utf-8")

    print(f"前置断言通过：逐币差异 0｜水位 {gate['replay_upto_utc']}")
    for s in segs:
        print(f"  水位 {s['wm_utc']}：{s['changed_n']} 个币换阶段")
    print(f"写出：{p}")
    print(f"      {md_p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
