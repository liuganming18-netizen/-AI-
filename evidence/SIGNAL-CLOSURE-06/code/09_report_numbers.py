# -*- coding: utf-8 -*-
"""SIGNAL-CLOSURE-06｜09 报告数字汇总（保证主报告里每个数字都能机械复算）。

纪律：主报告不手抄数字。所有关键数字由本件从 `out/*.json` 的原始字段提取，
人工抄写已多次出错（见 MEMORY 记录）。缺件就显式标 `missing`，不猜、不沿用旧值。

用法：python code/09_report_numbers.py
产出：out/09_报告数字.json（并打印）
"""
from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
cm = importlib.import_module("00_common")


def load(name: str):
    p = cm.OUT / name
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:                                  # noqa: BLE001
        return {"__error__": f"{type(exc).__name__}: {exc}"}


def pick(d, *keys, default=None):
    cur = d
    for k in keys:
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return cur


def main() -> int:
    out = {"generated_at_utc": cm.utc(cm.now_ms()), "items": {}, "missing": []}

    def put(key, val, src):
        out["items"][key] = {"value": val, "source": src}

    # ── 02 深度敏感性：加深窗口是否收敛 ────────────────────────────────────
    d = load("02_深度敏感性_effective.json")
    if d:
        put("深度敏感性.各档相邻有效状态差异", d.get("n_changed_between_adjacent"),
            "02_深度敏感性_effective.json")
        put("深度敏感性.各档相邻迁移数差异（不并入状态）",
            d.get("n_transitions_changed_between_adjacent"),
            "02_深度敏感性_effective.json")
        put("深度敏感性.是否收敛(稳定起点)", d.get("stable_from_hours"),
            "02_深度敏感性_effective.json")
        put("深度敏感性.候选总数", d.get("candidate_total_per_depth"),
            "02_深度敏感性_effective.json")
        put("深度敏感性.状态键口径", d.get("state_key_note"),
            "02_深度敏感性_effective.json")
    else:
        out["missing"].append("02_深度敏感性_effective.json")

    # ── 03 重启一致性（锚点档 / 相对窗口档）────────────────────────────────
    for tag, fn in (("锚点4000h", "03_重启一致性_D2880_anchor4000.json"),
                    ("锚点2880h", "03_重启一致性_D2880_anchor2880.json"),
                    ("相对窗口2880", "03_重启一致性_D2880.json"),
                    ("相对窗口1160", "03_重启一致性_D1160.json")):
        d = load(fn)
        if not d:
            out["missing"].append(fn)
            continue
        put(f"重启一致性[{tag}].币数", d.get("n_symbols"), fn)
        put(f"重启一致性[{tag}].C与各重启点差异", d.get("n_diff_vs_continuous"), fn)
        put(f"重启一致性[{tag}].重启次数无关", d.get("restart_count_irrelevant"), fn)
        put(f"重启一致性[{tag}].重启本身改变状态", d.get("restart_state_change_at_Tr"), fn)
        put(f"重启一致性[{tag}].候选总数", d.get("total_candidates"), fn)
        put(f"重启一致性[{tag}].锚点", d.get("anchor_utc"), fn)

    # ── 04 生产路径 vs 离线件 parity ───────────────────────────────────────
    for tag, fn in (("全池无锚点", "04_生产一致性.json"),
                    ("锚点2880", "04_生产一致性_anchor2880.json")):
        d = load(fn)
        if not d:
            out["missing"].append(fn)
            continue
        put(f"生产parity[{tag}].判定", d.get("verdict"), fn)
        put(f"生产parity[{tag}].币数", d.get("n_symbols"), fn)
        put(f"生产parity[{tag}].比较字段数", d.get("n_compared_fields"), fn)
        put(f"生产parity[{tag}].不一致币数", d.get("n_diff_symbols"), fn)

    # ── 05 实时 vs 回放逐项条件 ────────────────────────────────────────────
    d = load("05_实时与回放条件比对_anchor2880.json")
    if d:
        for k in ("verdict", "n_state_comparisons", "n_diff",
                  "n_transitions_compared", "n_metric_items_compared",
                  "n_metric_items_mismatched", "n4h", "replay_mode",
                  "state_anchor_utc"):
            put(f"实时vs回放.{k}", d.get(k), "05_实时与回放条件比对_anchor2880.json")
        rp = d.get("candidate_reproduction") or []
        put("实时vs回放.候选复现", f"{sum(1 for r in rp if r['reproduced'])}/{len(rp)}",
            "05_实时与回放条件比对_anchor2880.json")
    else:
        out["missing"].append("05_实时与回放条件比对_anchor2880.json")

    # ── 05b 全池零信号归因 ─────────────────────────────────────────────────
    d = load("05b_零信号汇总_anchor2880.json")
    if d:
        for k in ("n_symbols", "state_distribution", "n_candidates_total"):
            put(f"零信号归因.{k}", d.get(k), "05b_零信号汇总_anchor2880.json")
    else:
        out["missing"].append("05b_零信号汇总_anchor2880.json")

    # ── 07b 水位探针（07 的 lag 字段路径有缺陷，以本件为准）────────────────
    d = load("07b_水位探针.json")
    if d:
        for k in ("verdict", "lag_1h_bars", "aligned_1h",
                  "processed_1h_max_utc", "db_1h_max_in_status_utc",
                  "state_counter", "funnel_since_start", "candidates_since_start"):
            put(f"水位探针.{k}", d.get(k), "07b_水位探针.json")
        ps = d.get("per_symbol") or {}
        put("水位探针.逐币lag_max", ps.get("lag_1h_max"), "07b_水位探针.json")
        put("水位探针.逐币lag分布", ps.get("lag_1h_histogram"), "07b_水位探针.json")
        put("水位探针.逐币阶段数", d.get("n_per_symbol_state"), "07b_水位探针.json")
        put("水位探针.逐项判定", d.get("checks"), "07b_水位探针.json")
    else:
        out["missing"].append("07b_水位探针.json")

    # ── 06 恢复候选进入执行 ────────────────────────────────────────────────
    d = load("06_恢复候选执行.json")
    if d:
        ch = d.get("checks") or {}
        put("恢复候选.判定", d.get("verdict"), "06_恢复候选执行.json")
        put("恢复候选.通过项", f"{sum(1 for v in ch.values() if v)}/{len(ch)}",
            "06_恢复候选执行.json")
    else:
        out["missing"].append("06_恢复候选执行.json")

    # ── 07 生产加载观察 ────────────────────────────────────────────────────
    d = load("07_生产加载观察.json")
    if d:
        put("生产加载.判定", d.get("verdict"), "07_生产加载观察.json")
        put("生产加载.新增1h收盘", d.get("new_1h_closes"), "07_生产加载观察.json")
        put("生产加载.重建模式", d.get("replay_mode"), "07_生产加载观察.json")
        put("生产加载.锚点", d.get("state_anchor_utc"), "07_生产加载观察.json")
        put("生产加载.逐项判定", d.get("checks"), "07_生产加载观察.json")
    else:
        out["missing"].append("07_生产加载观察.json")

    # ── 01b 分歧后续影响 ───────────────────────────────────────────────────
    d = load("01_后续影响.json")
    if d:
        for k in ("n_comparisons", "n_propagated_to_candidates",
                  "n_propagated_to_terminal_only", "n_transient_only"):
            put(f"分歧后续影响.{k}", d.get(k), "01_后续影响.json")
    else:
        out["missing"].append("01_后续影响.json")

    cm.dump("09_报告数字.json", out)

    print(f"[报告数字] {len(out['items'])} 项，缺件 {len(out['missing'])}")
    for k, v in out["items"].items():
        s = json.dumps(v["value"], ensure_ascii=False)
        print(f"  {k:44s} = {s[:150]}")
    if out["missing"]:
        print("[缺件] " + ", ".join(out["missing"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
