#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""10_crosscheck_entry02_selfreport.py — FT-ENTRY-02 自报总表 vs 独立重算

目的（工单第 3 条）：
  对方批次已产出 out/01_参数探索总表.csv（自报）。本脚本不读该表的任何中间量，
  只用 out/trades_<cell>.csv 的逐笔记录独立重算，然后逐格逐指标与自报值对账。

对账项：trade_count / win_rate / net_pnl / profit_factor / expectancy_u
不轻信自报，但也不把「自报的四舍五入」当「不一致」：
  自报把 net_pnl / profit_factor / expectancy_u 保留 4 位、win_rate 保留 2 位，
  因此比较前先把独立重算值 round 到同一精度，再做严格相等判定，
  同时单独记录「舍入前」的最大绝对偏差，供人工判断量级是否合理。

输出：
  out/36_FT-ENTRY-02自报与独立重算对账_v1.csv
  out/37_FT-ENTRY-02自报缺失维度_v1.csv
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

BASE = Path(__file__).resolve().parent.parent
ROOT = BASE.parent.parent
E2 = ROOT / "evidence" / "FT-ENTRY-02"
OUT = BASE / "out"
OUT.mkdir(parents=True, exist_ok=True)

TOL = 1e-9  # 对齐到自报精度后再比较，此处只挡浮点表示噪声

# 自报精度：字段 -> 小数位
PRECISION = {
    "net_pnl": 4,
    "profit_factor": 4,
    "expectancy_u": 4,
    "win_rate": 2,
}

CELLS = ["WyckoffBaseline"] + [
    f"Wyckoff_A_L{l:02d}_V{v:02d}" for l in (6, 4, 2) for v in (8, 10, 12)
] + [
    f"Wyckoff_C_Vs{vs:02d}_Z{z:02d}" for vs in (4, 6, 8) for z in (2, 4, 6)
]


def load_trades(cell: str) -> pd.DataFrame | None:
    p = E2 / "out" / f"trades_{cell}.csv"
    if not p.exists():
        return None
    df = pd.read_csv(p)
    if df.empty:
        return df
    df["profit_abs"] = pd.to_numeric(df["profit_abs"], errors="coerce").fillna(0.0)
    return df


def recompute(df: pd.DataFrame) -> dict:
    p = df["profit_abs"]
    n = int(len(df))
    wins = p[p > 0]
    losses = p[p <= 0]
    gross_win = float(wins.sum())
    gross_loss = float(-losses.sum())
    net = float(p.sum())
    return {
        "re_trade_count": n,
        "re_win_count": int((p > 0).sum()),
        "re_loss_count": int((p <= 0).sum()),
        "re_win_rate": round(100.0 * (p > 0).sum() / n, 4) if n else float("nan"),
        "re_net_pnl": round(net, 6),
        "re_profit_factor": round(gross_win / gross_loss, 6) if gross_loss > 0 else float("inf"),
        "re_expectancy_u": round(net / n, 6) if n else float("nan"),
        "re_gross_win": round(gross_win, 4),
        "re_gross_loss": round(gross_loss, 4),
    }


def main() -> None:
    self_rep = pd.read_csv(E2 / "out" / "01_参数探索总表.csv")
    self_rep["strategy_name"] = self_rep["strategy_name"].astype(str).str.strip()
    sr = self_rep.set_index("strategy_name")

    rows = []
    missing = []
    for cell in CELLS:
        df = load_trades(cell)
        if df is None or df.empty:
            missing.append({"cell": cell, "reason": "逐笔文件缺失或为空"})
            continue
        re = recompute(df)
        if cell not in sr.index:
            missing.append({"cell": cell, "reason": "自报总表中无此行"})
            continue
        s = sr.loc[cell]

        def cmp(col_self, col_re, ndigits):
            """返回 (自报值, 重算值, 重算按自报精度舍入后的值)。"""
            try:
                a = float(s[col_self])
            except Exception:
                return float("nan"), float("nan"), float("nan")
            b = float(re[col_re])
            return a, b, round(b, ndigits)

        a_n, b_n, r_n = cmp("trade_count", "re_trade_count", 0)
        a_w, b_w, r_w = cmp("win_rate", "re_win_rate", PRECISION["win_rate"])
        a_p, b_p, r_p = cmp("net_pnl", "re_net_pnl", PRECISION["net_pnl"])
        a_f, b_f, r_f = cmp("profit_factor", "re_profit_factor", PRECISION["profit_factor"])
        a_e, b_e, r_e = cmp("expectancy_u", "re_expectancy_u", PRECISION["expectancy_u"])

        row = {
            "cell": cell,
            "self_trade_count": int(a_n) if a_n == a_n else None,
            "re_trade_count": int(b_n) if b_n == b_n else None,
            "d_trade_count": int(a_n - b_n) if a_n == a_n and b_n == b_n else None,
            "self_net_pnl": round(a_p, 6),
            "re_net_pnl": round(b_p, 6),
            "d_net_pnl_raw": round(a_p - b_p, 8),
            "d_net_pnl_rounded": round(a_p - r_p, 10),
            "self_win_rate": round(a_w, 4),
            "re_win_rate": round(b_w, 4),
            "d_win_rate_raw": round(a_w - b_w, 8),
            "d_win_rate_rounded": round(a_w - r_w, 10),
            "self_pf": round(a_f, 6),
            "re_pf": round(b_f, 6),
            "d_pf_raw": round(a_f - b_f, 8),
            "d_pf_rounded": round(a_f - r_f, 10),
            "self_expectancy": round(a_e, 6),
            "re_expectancy": round(b_e, 6),
            "d_expectancy_raw": round(a_e - b_e, 8),
            "d_expectancy_rounded": round(a_e - r_e, 10),
        }
        bad = [
            k
            for k, d in (
                ("trade_count", row["d_trade_count"]),
                ("net_pnl", row["d_net_pnl_rounded"]),
                ("win_rate", row["d_win_rate_rounded"]),
                ("pf", row["d_pf_rounded"]),
                ("expectancy", row["d_expectancy_rounded"]),
            )
            if d is not None and abs(d) > TOL
        ]
        row["mismatch_fields"] = "|".join(bad)
        row["verdict"] = "一致(含舍入)" if not bad else "不一致"
        rows.append(row)

    out = pd.DataFrame(rows)
    out.to_csv(OUT / "36_FT-ENTRY-02自报与独立重算对账_v1.csv", index=False, encoding="utf-8-sig")

    # 自报缺失的审计维度（F-02 的量化证据）
    gaps = [
        {
            "dimension": "被丢弃的基线事件数",
            "in_self_report": False,
            "in_audit": True,
            "why_matters": "A 路线净增益若不计丢弃侧，无法判断是改善还是筛选",
        },
        {
            "dimension": "被丢弃基线事件的盈亏合计",
            "in_self_report": False,
            "in_audit": True,
            "why_matters": "丢弃的多为大赢单，直接决定 A 路线符号",
        },
        {
            "dimension": "基线事件召回率",
            "in_self_report": False,
            "in_audit": True,
            "why_matters": "C 路线召回仅 5.26%~21.05%，说明它换了策略而非改时点",
        },
        {
            "dimension": "同候选交易的入场价折价",
            "in_self_report": False,
            "in_audit": True,
            "why_matters": "区分『入场更便宜』与『换了另一批交易』",
        },
        {
            "dimension": "扣费前收益",
            "in_self_report": False,
            "in_audit": True,
            "why_matters": "手续费占比过高时，负收益是成本问题还是方向问题",
        },
        {
            "dimension": "时间块置信区间 / 置换检验",
            "in_self_report": False,
            "in_audit": True,
            "why_matters": "19 格多重比较下，单点数值不可判显著",
        },
        {
            "dimension": "滑点成本",
            "in_self_report": False,
            "in_audit": True,
            "why_matters": "冻结方案要求 3bps，实现不存在（F-03）",
        },
        {
            "dimension": "lookahead / recursive 检验结果",
            "in_self_report": False,
            "in_audit": False,
            "why_matters": "脚本已写但未运行，防泄漏证据缺失",
        },
        {
            "dimension": "trade_count 之外的并发拒绝数",
            "in_self_report": False,
            "in_audit": True,
            "why_matters": "区分『规则没触发』与『槽位被占』",
        },
    ]
    pd.DataFrame(gaps).to_csv(
        OUT / "37_FT-ENTRY-02自报缺失维度_v1.csv", index=False, encoding="utf-8-sig"
    )

    n_ok = int((out["verdict"] == "一致(含舍入)").sum())
    summary = {
        "cells_compared": int(len(out)),
        "cells_consistent": n_ok,
        "cells_mismatch": int(len(out) - n_ok),
        "cells_missing": len(missing),
        "mismatch_detail": out.loc[
            out["verdict"] != "一致(含舍入)", ["cell", "mismatch_fields"]
        ].to_dict("records"),
        "missing_detail": missing,
        "max_abs_raw_deviation": {
            "net_pnl": float(out["d_net_pnl_raw"].abs().max()),
            "win_rate": float(out["d_win_rate_raw"].abs().max()),
            "profit_factor": float(out["d_pf_raw"].abs().max()),
            "expectancy_u": float(out["d_expectancy_raw"].abs().max()),
        },
        "self_report_missing_dimensions": int(len(gaps)),
        "tolerance_note": "先按自报精度(net_pnl/pf/expectancy=4位, win_rate=2位)舍入再比较; TOL=1e-9 仅挡浮点噪声",
        "tol": TOL,
    }
    (OUT / "38_FT-ENTRY-02对账摘要_v1.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"比对格数 = {len(out)}，一致 = {n_ok}，不一致 = {len(out) - n_ok}，缺失 = {len(missing)}")
    print("舍入前最大绝对偏差：")
    for k, v in summary["max_abs_raw_deviation"].items():
        print(f"  {k:16s} {v:.10f}")
    if len(out) - n_ok:
        print(out.loc[out["verdict"] != "一致(含舍入)", ["cell", "mismatch_fields"]].to_string(index=False))
    print(f"自报缺失维度 = {len(gaps)} 项 → 37_*.csv")


if __name__ == "__main__":
    main()
