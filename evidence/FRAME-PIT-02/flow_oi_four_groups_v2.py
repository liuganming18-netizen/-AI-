#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""FRAME-PIT-02｜FLOW-OI 四组实验的受控数据入口（v2）。

定位
────
`evidence/FLOW-OI-01/generate_flow_oi_01.py` 是 FLOW-OI 四组实验的**真实因子入口**，
FRAME-PIT-02 已把它改成受控委托（见该文件 `extract_features_for_candidate`）。
本脚本是四组实验这一侧的**唯一数据入口**：

  · 四组（G1 基线 / G2 主动成交 / G3 +OI / G4 +大户）全部从这里取特征，
    不允许各自去调旧路径；
  · 取到的特征必须带 `pit_status` / `pit_qualified` / `formal_eligible`，
    缺字段的（= 旧路径产物）一律拒收；
  · 本脚本**永远**把 `formal_eligible` 写成 False 并把原因写出来 ——
    在 Metrics / OI 没有发布时点证据之前，FLOW-OI 四组结果只能是研究结论，
    不能是正式回测结论。

不做的事
────────
不改阈值、不改公式、不改费用、不改撮合、不改决策时刻；不重跑 227 笔四组回测
（工单要求）。`--limit` 只用于冒烟核对入口行为。
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

WORKSPACE = "/Users/boos/Desktop/威科夫因子共振趋势"
FE09_PARQUET = os.path.join(WORKSPACE, "evidence/FACTOR-ENTRY-09/reconstructed_candidate_features_08.parquet")
FLOW_OI_SCRIPT = os.path.join(WORKSPACE, "evidence/FLOW-OI-01/generate_flow_oi_01.py")
OUT_DIR = os.path.join(WORKSPACE, "evidence/FRAME-PIT-02/runs_v2")

#: 旧路径产物没有这些列 ⇒ 直接判为"未经受控入口"，不允许进四组
REQUIRED_PIT_COLUMNS = (
    "feature_contract_version",
    "pit_status",
    "pit_qualified",
    "formal_eligible",
    "coverage_status",
    "formal_block_reasons",
)


class UncontrolledFeatureError(RuntimeError):
    """特征表不是受控入口产出的 —— 拒绝用于四组实验。"""


def load_flow_module():
    """加载真实因子入口脚本（同一份实现，不复制）。"""
    spec = importlib.util.spec_from_file_location("flow_oi_01_entry", FLOW_OI_SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def assert_controlled(df: pd.DataFrame) -> None:
    """缺 PIT 字段 = 旧路径产物，直接拒收。"""
    missing = [c for c in REQUIRED_PIT_COLUMNS if c not in df.columns]
    if missing:
        raise UncontrolledFeatureError(
            f"特征表缺少 PIT 字段 {missing}：这不是受控入口的产物，"
            f"不得用于四组实验（旧路径不得生成正式合格结果）")


def extract_all(df_cand: pd.DataFrame, *, limit: Optional[int] = None,
                mode: Optional[str] = None,
                flow_mod=None) -> pd.DataFrame:
    """逐候选调用真实因子入口，返回带 PIT 字段的特征表。"""
    mod = flow_mod or load_flow_module()
    rows = df_cand.head(limit) if limit else df_cand
    records: List[Dict[str, Any]] = []
    for _, row in rows.iterrows():
        records.append(mod.extract_features_for_candidate(row, mode=mode))
    df = pd.DataFrame(records)
    assert_controlled(df)
    return df


def compute_group_metrics(df_sub: pd.DataFrame, group_name: str) -> dict:
    """与原 FLOW-OI-01 完全一致的口径（未改）。"""
    total = len(df_sub)
    if total == 0:
        return {"group": group_name, "trades": 0, "win_rate": 0.0, "net_pnl": 0.0,
                "gross_pnl": 0.0, "fees": 0.0, "funding_cost": 0.0,
                "profit_factor": 0.0, "expectancy": 0.0,
                "early_weakness_count": 0, "early_weakness_rate": 0.0,
                "sustained_trend_count": 0, "mfe_pullback_count": 0, "choppy_count": 0}
    wins = df_sub[df_sub["net_pnl"] > 0]
    losses = df_sub[df_sub["net_pnl"] <= 0]
    win_pnl = float(wins["net_pnl"].sum())
    loss_pnl = abs(float(losses["net_pnl"].sum()))
    pf = (win_pnl / loss_pnl) if loss_pnl > 0 else (999.0 if win_pnl > 0 else 0.0)
    net_pnl = float(df_sub["net_pnl"].sum())
    cats = df_sub["entry_quality_category"].value_counts().to_dict()
    ew = cats.get("EARLY_WEAKNESS", 0)
    return {
        "group": group_name, "trades": total,
        "win_rate": round(len(wins) / total * 100, 2),
        "net_pnl": round(net_pnl, 4),
        "gross_pnl": round(float(df_sub["gross_pnl"].sum()), 4),
        "fees": round(float(df_sub["fee_paid"].sum()), 4),
        "funding_cost": round(float(df_sub["funding_cost"].sum()), 4),
        "profit_factor": round(pf, 3),
        "expectancy": round(net_pnl / total, 4),
        "early_weakness_count": ew,
        "early_weakness_rate": round(ew / total * 100, 2),
        "sustained_trend_count": cats.get("SUSTAINED_TREND", 0),
        "mfe_pullback_count": cats.get("MFE_PULLBACK", 0),
        "choppy_count": cats.get("CHOPPY_OR_OTHER", 0),
    }


def run_four_groups(df_feat: pd.DataFrame) -> dict:
    """四组切片。**四组全部消费同一张受控特征表**，不允许任何一组另取数据。"""
    assert_controlled(df_feat)
    filled = df_feat[df_feat["is_filled"]].copy()
    groups = {
        "G1_baseline": filled[filled["pass_g1"]],
        "G2_taker_flow": filled[filled["pass_g2"]],
        "G3_plus_oi": filled[filled["pass_g3"]],
        "G4_plus_whale": filled[filled["pass_g4"]],
    }
    overall = [compute_group_metrics(sub, name) for name, sub in groups.items()]

    pit = summarize(df_feat)
    return {
        "overall": overall,
        "feature_contract_version": str(df_feat["feature_contract_version"].iloc[0])
        if len(df_feat) else None,
        "pit": pit,
        # 硬约束：只要不是全部 formal_eligible，就不许把结果称作正式结论
        "formal_eligible": bool(pit.get("formal_eligible", False)),
        "formal_block_reasons": pit.get("formal_block_reason_counts", {}),
        "formal_statement": (
            "正式回测结论（可提交 Gate）" if pit.get("formal_eligible")
            else "研究结论，不具备正式资格（不得用于 Gate / 上线 / 正式回测通过判定）"),
        "groups_consumed_from": "controlled_entry_only",
    }


def summarize(df_feat: pd.DataFrame) -> dict:
    """把逐候选 PIT 字段汇总（覆盖状态与 PIT 资格分开报）。"""
    n = len(df_feat)
    if n == 0:
        return {"candidates": 0, "formal_eligible": False}
    cov = df_feat["coverage_status"].value_counts().to_dict()
    pit = df_feat["pit_status"].value_counts().to_dict()
    reasons: Dict[str, int] = {}
    for v in df_feat["formal_block_reasons"]:
        for x in (v if isinstance(v, (list, tuple)) else []):
            k = str(x).split(":", 1)[0]
            reasons[k] = reasons.get(k, 0) + 1
    return {
        "candidates": n,
        "coverage_status_counts": {str(k): int(v) for k, v in cov.items()},
        "pit_status_counts": {str(k): int(v) for k, v in pit.items()},
        "pit_qualified_count": int(df_feat["pit_qualified"].sum()),
        "formal_eligible_count": int(df_feat["formal_eligible"].sum()),
        "formal_block_reason_counts": dict(sorted(reasons.items(), key=lambda kv: -kv[1])),
        "formal_eligible": bool(df_feat["formal_eligible"].all()),
        "feature_contract_version": str(df_feat["feature_contract_version"].iloc[0]),
        "pit_guard_version": str(df_feat["pit_guard_version"].iloc[0]),
        "flow_pit_version": str(df_feat["flow_pit_version"].iloc[0]),
    }


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None,
                    help="只跑前 N 笔（冒烟核对入口用；不给则全量 227 笔）")
    ap.add_argument("--mode", default=None,
                    help="strict / declared_assumption（缺省取 FLOW_OI_PIT_MODE）")
    ap.add_argument("--out", default=OUT_DIR)
    ap.add_argument("--dry-run", action="store_true", help="不落盘，只打印汇总")
    args = ap.parse_args(argv)

    os.makedirs(args.out, exist_ok=True)
    df_cand = pd.read_parquet(FE09_PARQUET)
    df_feat = extract_all(df_cand, limit=args.limit, mode=args.mode)
    summary = run_four_groups(df_feat)

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if not args.dry_run:
        df_feat.to_parquet(os.path.join(args.out, "flow_oi_features_v2.parquet"), index=False)
        with open(os.path.join(args.out, "four_groups_v2_summary.json"), "w",
                  encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)
        print(f"[写出] {args.out}")
    if not summary["formal_eligible"]:
        print("!! 本次结果不具备正式资格，仅作研究结论。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
