# -*- coding: utf-8 -*-
"""SIGNAL-NOW-01-ANTIGRAVITY｜步骤8：段 A / 段 B 采样对比（只读两份产物，不碰生产）。

为什么单独写一个脚本
--------------------
README 里要写「段 A 与段 B 的关键差异」，如果手抄数字，一旦抄错就没人能发现。
本脚本直接从两份原始产物里取值、算差、生成 markdown 表，**README 只引用它的输出**。

段 A 由脚本 r1 产出（无 `script_sha256`、无跟踪止损字段），段 B 由 r2 产出。
缺字段一律输出 `None`，**不补 0、不推断**。

产物：out/08_分段对比.json
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

ROOT = Path("/Users/boos/Desktop/威科夫因子共振趋势/evidence/SIGNAL-NOW-01-ANTIGRAVITY")
OUT = ROOT / "out"
A = OUT / "05_运行采样_A.json"
B = OUT / "05_运行采样_B.json"


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def load(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


def dig(d, *keys, default=None):
    o = d
    for k in keys:
        if not isinstance(o, dict):
            return default
        o = o.get(k)
    return default if o is None else o


def span(d: dict) -> dict:
    ss = d.get("samples") or []
    if not ss:
        return {}
    return {"n": len(ss), "first_ts": ss[0].get("ts"), "last_ts": ss[-1].get("ts"),
            "span_sec": d.get("span_sec")}


def track(d: dict) -> dict:
    """把一个分段的「首值 / 末值 / 是否推进」抽成统一结构。"""
    dv = d.get("derived") or {}
    keys = [
        ("cycle_count", ["cycle_count"]),
        ("watermark_1h_utc", ["watermark_1h_utc"]),
        ("watermark_4h_utc", ["watermark_4h_utc"]),
        ("latest_15m_close_utc", ["latest_15m_close_utc"]),
        ("latest_4h_close_utc", ["latest_4h_close_utc"]),
        ("candidates_since_start", ["candidates_since_start"]),
        ("funnel_since_start", ["funnel_since_start"]),
        ("state_counter", ["state_counter"]),
        ("live_signal_total", ["live_signal_total"]),
        ("pending_status", ["pending_status"]),
        ("position_status", ["position_status"]),
        ("fill_count", ["fill_count"]),
        ("position_max_id", ["position_max_id"]),
        ("orders_routed_total", ["orders_routed_total"]),
        ("trailing_updates_total", ["trailing_updates_total"]),
        ("stop_history_total", ["stop_history_total"]),
        ("open_position_stops", ["open_position_stops"]),
        ("scan_errors_total", ["scan_errors_total"]),
    ]
    out = {}
    for name, path in keys:
        blk = dig(dv, *path, default=None)
        if not isinstance(blk, dict):
            out[name] = {"first": None, "last": None, "advanced": None,
                         "absent": True}
            continue
        out[name] = {"first": blk.get("first"), "last": blk.get("last"),
                     "advanced": blk.get("advanced"),
                     "n_distinct": blk.get("n_distinct")}
    return out


def main() -> int:
    R = {"ticket": "SIGNAL-NOW-01-ANTIGRAVITY",
         "step": "8 段 A / 段 B 采样对比（只读两份原始产物）",
         "at_utc": utc()}
    if not A.exists() or not B.exists():
        print(f"缺产物：A存在={A.exists()} B存在={B.exists()}")
        return 2

    da, db = load(A), load(B)
    R["inputs"] = {
        "A": {"file": A.name, "sha256": sha(A), "size": A.stat().st_size,
              "span": span(da), "verdict": da.get("verdict_facts"),
              "script_sha256": da.get("script_sha256")},
        "B": {"file": B.name, "sha256": sha(B), "size": B.stat().st_size,
              "span": span(db), "verdict": db.get("verdict_facts"),
              "script_sha256": db.get("script_sha256")},
    }
    R["script_version_note"] = (
        "段 A 由脚本 r1 产出，无 script_sha256 / 跟踪止损字段；"
        "段 B 由 r2 产出。两段字段不完全同构，对比时缺项输出 None，不补 0。")

    ta, tb = track(da), track(db)
    R["per_segment"] = {"A": ta, "B": tb}

    # 跨边界判定：段 B 是否真的跨过 20:00Z
    b_first, b_last = span(db).get("first_ts"), span(db).get("last_ts")
    crosses_2000 = bool(b_first and b_last and b_first < "2026-10-27T20:00:00Z" <= b_last)
    R["boundary"] = {
        "target_utc": "2026-10-27T20:00:00Z",
        "B_window": [b_first, b_last],
        "B_crosses_1h_and_4h_boundary": crosses_2000,
        "why": "19:00Z 开盘的 1h bar 与 16:00Z 开盘的 4h bar 都在 20:00Z 收盘 ⇒ 跨过这一点的窗口"
               "才能同时检验两个水位。",
    }

    # 只列「有变化或本单特别关心」的项，避免把 18 项全摊开
    focus = ["cycle_count", "watermark_1h_utc", "watermark_4h_utc",
             "latest_15m_close_utc", "latest_4h_close_utc",
             "candidates_since_start", "state_counter", "funnel_since_start",
             "live_signal_total", "pending_status", "position_status",
             "fill_count", "trailing_updates_total", "stop_history_total",
             "open_position_stops"]
    rows = []
    for k in focus:
        a, b = ta.get(k, {}), tb.get(k, {})
        rows.append({
            "item": k,
            "A_first": a.get("first"), "A_last": a.get("last"),
            "A_advanced": a.get("advanced"), "A_absent": a.get("absent", False),
            "B_first": b.get("first"), "B_last": b.get("last"),
            "B_advanced": b.get("advanced"), "B_absent": b.get("absent", False),
        })
    R["compare_rows"] = rows

    # 过程健康：两段的进程重启与健康
    R["process_health"] = {
        "A_restarted": da.get("process_restarted_during_sampling"),
        "B_restarted": db.get("process_restarted_during_sampling"),
        "A_health": dig(da, "verdict_facts", "health_last"),
        "B_health": dig(db, "verdict_facts", "health_last"),
    }

    # 自然行为：两段都只认「自然候选」这一个硬指标
    R["natural_behavior"] = {
        "A_candidates": dig(da, "verdict_facts", "natural_candidates_in_window"),
        "B_candidates": dig(db, "verdict_facts", "natural_candidates_in_window"),
        "A_new_positions": dig(da, "verdict_facts", "new_positions_in_window"),
        "B_new_positions": dig(db, "verdict_facts", "new_positions_in_window"),
        "A_new_fills": dig(da, "verdict_facts", "new_fills_in_window"),
        "B_new_fills": dig(db, "verdict_facts", "new_fills_in_window"),
        "reading": "两段都是 0 就记 0。**不得**用隔离正例、也不得用段 B 跨了边界就推断"
                   "「策略一定能出候选」。跨边界只说明这次观测**有机会**看到候选。",
    }

    R["not_observed"] = [
        "自然候选→建单→成交闭环：若两段均未出现自然候选，则该项仍为**未验证**。",
        "真实挂钟自动成交：仍只有 1 笔既有挂单成交（test_probe_succ），非自然候选产生。",
    ]

    OUT.mkdir(parents=True, exist_ok=True)
    p = OUT / "08_分段对比.json"
    p.write_text(json.dumps(R, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    # ── 人读版 markdown（README 直接引用，不手抄）──
    print("=== 输入 ===")
    for k in ("A", "B"):
        i = R["inputs"][k]
        print(f"  {k}: {i['file']}  {i['size']} B  sha256={i['sha256'][:12]}…")
        print(f"     窗口 {i['span'].get('first_ts')} → {i['span'].get('last_ts')}"
              f"  {i['span'].get('n')} 样本  {i['span'].get('span_sec')} s")
    print(f"\n=== 跨边界判定 ===")
    print(f"  目标 {R['boundary']['target_utc']}  段B窗口 {R['boundary']['B_window']}")
    print(f"  段B 跨过 1h+4h 双边界：{R['boundary']['B_crosses_1h_and_4h_boundary']}")

    print("\n=== 分段对比 ===")
    print(f"{'项':<26}{'A首':<22}{'A末':<22}{'B首':<22}{'B末':<22}")
    for r in rows:
        def s(v):
            if v is None:
                return "None"
            t = str(v)
            return t if len(t) <= 20 else t[:18] + ".."
        print(f"{r['item']:<26}{s(r['A_first']):<22}{s(r['A_last']):<22}"
              f"{s(r['B_first']):<22}{s(r['B_last']):<22}")

    print("\n=== 自然行为（唯一硬指标）===")
    for k, v in R["natural_behavior"].items():
        if k != "reading":
            print(f"  {k}: {v}")
    print("\n=== 过程健康 ===")
    print(f"  A 重启={R['process_health']['A_restarted']}  健康={R['process_health']['A_health']}")
    print(f"  B 重启={R['process_health']['B_restarted']}  健康={R['process_health']['B_health']}")
    print(f"\n产物：{p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
