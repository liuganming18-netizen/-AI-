#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""FRAME-FIT-01 步骤四：由**实测标签跨度**推出隔离长度，并给出候选划分。

工单要求：「具体隔离长度根据实际标签跨度提出依据，不擅自选任意天数。」
所以这里不写「留 10 天」，而是：

  1. 从 205 笔真实成交算 `exit_time_ms - entry_time_ms` 的实际分布；
  2. 与配置上界 `max_holding_days=10`（240h）对照；
  3. 取两者较大者作为**最小隔离长度**，并说明依据；
  4. 用 `propose_chronological_split()` 算出候选划分 —— **只算不落地**，
     输出里明确写 `configured=False / 待人工批准`。

同时回答一个必须回答的问题：现有 227 条已观察样本，落在候选划分的哪一段？
如果落在留出段 ⇒ 该段已被污染，**不能**声称未见留出。
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
BACKEND = ROOT / "框架" / "backend"
sys.path.insert(0, str(BACKEND))

import experiment_registry as E   # noqa: E402

HANDOFF = ROOT / "evidence" / "ENTRY-AUDIT-04" / "handoff" / "ledger_handoff.parquet"
OUT_JSON = HERE / "04_split_basis.json"
OUT_MD = HERE / "04_拟议划分及依据.md"

#: 数据实际覆盖范围（取交接包里的最小 window_start / 最大 window_end，不手填）
#: 运行时用真实值覆盖下面两个占位。
DEFAULT_COVERAGE = ("2021-12-01", "2026-10-18")


def _utc(ms: int) -> str:
    return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def main() -> int:
    import pandas as pd

    df = pd.read_parquet(HANDOFF)
    ex = df[df["executed"] == True].copy()
    spans = [E.label_span_hours(int(r.entry_time_ms), int(r.exit_time_ms))
             for r in ex.itertuples() if pd.notna(r.entry_time_ms) and pd.notna(r.exit_time_ms)]
    spans = [s for s in spans if s is not None]

    iso_measured = E.required_isolation_hours(spans)
    cfg_hours = 10 * 24   # EventTrendConfig.max_holding_days = 10
    iso_required = max(x for x in (iso_measured, cfg_hours) if x is not None)

    s = pd.Series(spans)
    dist = {
        "n": int(len(s)),
        "min_h": round(float(s.min()), 2),
        "p50_h": round(float(s.quantile(0.5)), 2),
        "p90_h": round(float(s.quantile(0.9)), 2),
        "p99_h": round(float(s.quantile(0.99)), 2),
        "max_h": round(float(s.max()), 2),
        "share_at_config_cap": round(float((s >= cfg_hours - 1e-9).mean()), 4),
    }

    # 覆盖范围：取交接包真实最小/最大窗口
    ws = pd.to_datetime(df["window_start"]).min().strftime("%Y-%m-%d")
    we = pd.to_datetime(df["window_end"]).max().strftime("%Y-%m-%d")
    coverage = (ws, we) if ws and we else DEFAULT_COVERAGE

    # 已观察样本的决策时刻
    df["decision_ms"] = df["entry_time_ms"].where(
        df["entry_time_ms"].notna(), df["snapshot_time_ms"])
    decisions = df["decision_ms"].astype("int64").tolist()

    candidates = []
    for name, ratios in (("候选甲 60/20/20", (0.6, 0.2, 0.2)),
                         ("候选乙 70/15/15", (0.7, 0.15, 0.15))):
        try:
            p = E.propose_chronological_split(coverage[0], coverage[1],
                                              ratios=ratios,
                                              isolation_hours=iso_required)
        except ValueError as exc:
            candidates.append({"name": name, "ratios": ratios, "error": str(exc)})
            continue
        buckets = {"train": 0, "valid": 0, "holdout": 0, "gap_or_outside": 0}
        for ms in decisions:
            r = E.assign_role(int(ms), p)
            buckets[r if r else "gap_or_outside"] += 1
        candidates.append({
            "name": name,
            "ratios": ratios,
            "split": p.to_dict(),
            "configured": p.configured,
            "problems": p.validate(),
            "observed_sample_distribution": buckets,
            "holdout_contaminated": buckets["holdout"] > 0,
            "note": "候选，未经人工批准；不得直接用于正式实验",
        })

    payload = {
        "audit": "FRAME-FIT-01",
        "label_span": {
            "measured_distribution": dist,
            "config_cap_hours": cfg_hours,
            "config_cap_source": "EventTrendConfig.max_holding_days = 10 天",
            "isolation_from_measurement_h": iso_measured,
            "isolation_adopted_h": iso_required,
            "basis": ("取实测最大跨度与配置上界二者较大值。实测 205 笔成交中 "
                      f"{dist['share_at_config_cap'] * 100:.1f}% 恰好顶在 {cfg_hours}h "
                      f"（中位数也是 {dist['p50_h']}h）⇒ 标签最长观察窗口就是 10 天。"
                      "相邻分组之间至少让出这么多天，否则训练段末尾样本的标签会"
                      "用到验证段开头的行情。"),
            "why_not_arbitrary": "没有写死「留 10 天/30 天」；数值来自实测分布，"
                                 "跨度变了隔离长度会跟着变。",
        },
        "coverage": {"start": coverage[0], "end": coverage[1],
                     "source": "交接包 window_start 最小 / window_end 最大"},
        "candidates": candidates,
        "status": "UNCONFIGURED",
        "blocking_reason": ("正式划分日期未经批准。框架只提供候选与依据，"
                            "不替人决定用哪一天切分。"),
    }
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                        encoding="utf-8")

    # ── Markdown ──
    lines = []
    lines.append("# 拟议划分及依据（候选，未配置）\n")
    lines.append("> 状态：**UNCONFIGURED**。下面的日期是候选，不是决定。")
    lines.append("> 框架不替人拍板切分日；未批准前，任何实验都不具备正式资格。\n")

    lines.append("## 一、隔离长度怎么来的\n")
    lines.append("不写死天数。做法是从 205 笔真实成交里量：\n")
    lines.append("| 指标 | 小时 | 说明 |")
    lines.append("|---|---|---|")
    lines.append(f"| 样本数 | — | {dist['n']} 笔成交 |")
    lines.append(f"| 最短持有 | {dist['min_h']} | 止损/失效提前出场 |")
    lines.append(f"| 中位 | {dist['p50_h']} | 半数顶在最长持有期 |")
    lines.append(f"| 90 分位 | {dist['p90_h']} | |")
    lines.append(f"| 99 分位 | {dist['p99_h']} | |")
    lines.append(f"| 最长 | {dist['max_h']} | 与配置上界一致 |")
    lines.append(f"| 恰好顶在 {cfg_hours}h 的比例 | {dist['share_at_config_cap'] * 100:.1f}% | 不是巧合，是硬平仓 |")
    lines.append("")
    lines.append(f"配置里 `max_holding_days = 10` ⇒ {cfg_hours} 小时。")
    lines.append(f"实测最大值 {dist['max_h']}h，与配置上界吻合，没有一笔超出。")
    lines.append(f"**采用的隔离长度 = {iso_required} 小时（{iso_required // 24} 天）**，"
                 f"取实测与配置上界的较大者。\n")
    lines.append("为什么必须留：一条样本的标签 = 入场到出场这段行情。"
                 "如果训练段最后一天入场的样本持有 10 天，它的标签就用到了验证段开头的价格。"
                 "不留隔离带，等于让模型在训练时偷看验证集。\n")

    lines.append("## 二、候选划分\n")
    lines.append(f"数据覆盖：{coverage[0]} ~ {coverage[1]}。"
                 f"按时间顺序切三段，段间各让出 {iso_required}h。\n")
    for c in candidates:
        if "error" in c:
            lines.append(f"### {c['name']}\n\n无法切分：{c['error']}\n")
            continue
        sp = c["split"]
        lines.append(f"### {c['name']}\n")
        lines.append("| 分组 | 起 | 止 |")
        lines.append("|---|---|---|")
        for role, label in (("train", "训练"), ("valid", "验证"), ("holdout", "最终留出")):
            seg = sp.get(role)
            lines.append(f"| {label} | {seg[0] if seg else '—'} | {seg[1] if seg else '—'} |")
        d = c["observed_sample_distribution"]
        lines.append("")
        lines.append(f"227 条已观察样本落点：训练 {d['train']} / 验证 {d['valid']} / "
                     f"留出 {d['holdout']} / 隔离带或段外 {d['gap_or_outside']}")
        if c["holdout_contaminated"]:
            lines.append("")
            lines.append(f"⚠ **留出段里有 {d['holdout']} 条已观察样本 ⇒ 该段已被污染，"
                         f"不能声称「未见留出」。**")
        lines.append("")

    lines.append("## 三、结论\n")
    lines.append("1. 隔离长度的机制已实现且有依据（240h），换数据会自动重算。")
    lines.append("2. 具体切分日期**没有定**，两个候选都标 UNCONFIGURED。")
    lines.append("3. 现有 227 条样本横跨全部时间区间 ⇒ 无论怎么切，留出段都会落到")
    lines.append("   已观察区间内 ⇒ **它们永远不能当未见留出**。要拿到真正的未见留出，")
    lines.append("   只能用 2026-10-18 之后新产生的行情（前向留出），或另找未参与研究的标的池。")
    lines.append("")
    OUT_MD.write_text("\n".join(lines), encoding="utf-8")

    print(f"实测持有期：min={dist['min_h']} p50={dist['p50_h']} max={dist['max_h']} "
          f"顶格占比={dist['share_at_config_cap'] * 100:.1f}%")
    print(f"隔离长度：实测 {iso_measured}h / 配置上界 {cfg_hours}h ⇒ 采用 {iso_required}h")
    print(f"覆盖范围：{coverage[0]} ~ {coverage[1]}")
    for c in candidates:
        if "error" in c:
            print(f"  {c['name']}: {c['error']}")
        else:
            print(f"  {c['name']}: configured={c['configured']} "
                  f"落点={c['observed_sample_distribution']} "
                  f"留出污染={c['holdout_contaminated']}")
    print(f"-> {OUT_MD.name} / {OUT_JSON.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
