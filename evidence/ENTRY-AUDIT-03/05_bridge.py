# -*- coding: utf-8 -*-
"""ENTRY-AUDIT-03 / 05 · 新旧样本桥接

要回答的只有一件事：163 条（连续回放）与 42 条（窗口回放）能不能拼成一个样本。
本脚本不主张「拼」或「不拼」，而是把拼接的后果量化出来，并把桥接关系逐条写清。

产出
----
05_bridge.csv          42 条逐条桥接（是否有连续孪生 / 差异 / 可否替换）
05_bridge.json         分代际真实成本统计 + 拼接风险量化 + 样本定义
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

ROOT = Path("/Users/boos/Desktop/威科夫因子共振趋势")
OUT = ROOT / "evidence" / "ENTRY-AUDIT-03"

ID = pd.read_csv(OUT / "03_identity_full.csv")
FROZEN = pd.read_parquet(ROOT / "evidence/FACTOR-ENTRY-03/candidate_features_enriched.parquet")
FROZEN["generation"] = FROZEN["candidate_id"].str.extract(r"^(cand_ev|old_cand)", expand=False)
MIS = pd.read_csv(OUT / "01_old_cand_mismatch.csv")


def main() -> None:
    filled = ID[ID["executed"] == True].copy()
    gen_a = filled[filled["generation"] == "cand_ev"]
    gen_b = filled[filled["generation"] == "old_cand"]

    # ── 逐条桥接 ──
    rows = []
    for _, r in gen_b.iterrows():
        m = MIS[MIS["candidate_id"] == r["candidate_id"]]
        m = m.iloc[0] if len(m) else None
        rows.append({
            "candidate_id": r["candidate_id"],
            "symbol": r["symbol"],
            "signal_time_utc": r["signal_time_utc"],
            "replay_group": r["replay_group"],
            "replay_window": f"{r['replay_window_start']} ~ {r['replay_window_end']}",
            "has_continuous_twin_within_4h": (bool(m["cont_has_candidate_within_4h"]) if m is not None else None),
            "continuous_nearest_hours": (float(m["cont_nearest_candidate_hours"])
                                         if m is not None and pd.notna(m["cont_nearest_candidate_hours"]) else None),
            "days_after_window_start": (float(m["days_after_window_start"])
                                        if m is not None and pd.notna(m["days_after_window_start"]) else None),
            "reproduced_in_own_window": (bool(m["replay_B_reproduced_by_identity"]) if m is not None else None),
            "entry_price": r["entry_price"],
            "net_pnl_replay": r["net_pnl"],
            "fees_replay": r["fee_paid"],
            "funding_replay": r["funding_cost"],
            "bridge_status": ("有连续孪生（可直接对照）" if (m is not None and bool(m["cont_has_candidate_within_4h"]))
                              else "无连续孪生（只能作为另一代际样本单独统计）"),
            "usable_as_substitute_for_continuous": False,
            "substitute_reason": ("窗口回放的箱体/入场闸门与连续回放不同（同一信号可一取消一成交），"
                                  "不能替换成连续样本"),
        })
    bdf = pd.DataFrame(rows)
    bdf.to_csv(OUT / "05_bridge.csv", index=False, encoding="utf-8-sig")

    # ── 分代际真实成本统计 ──
    def stat(d):
        return {
            "trades": int(len(d)),
            "gross_sum": round(float(d["gross_pnl"].sum()), 6),
            "fee_sum": round(float(d["fee_paid"].sum()), 6),
            "funding_sum": round(float(d["funding_cost"].sum()), 6),
            "net_sum": round(float(d["net_pnl"].sum()), 6),
            "fee_pct_of_gross": (round(float(d["fee_paid"].sum()) / abs(float(d["gross_pnl"].sum())) * 100, 4)
                                 if abs(float(d["gross_pnl"].sum())) > 0 else None),
            "funding_pct_of_gross": (round(float(d["funding_cost"].sum()) / abs(float(d["gross_pnl"].sum())) * 100, 4)
                                     if abs(float(d["gross_pnl"].sum())) > 0 else None),
            "win_count": int((d["net_pnl"] > 0).sum()),
            "loss_count": int((d["net_pnl"] <= 0).sum()),
            "funding_income_trades": int((d["funding_cost"] < -1e-12).sum()),
            "funding_cost_trades": int((d["funding_cost"] > 1e-12).sum()),
        }

    a, b = stat(gen_a), stat(gen_b)

    # ── 拼接风险量化：把两代际相加会得到什么、正确做法是什么 ──
    naive = {
        "trades": a["trades"] + b["trades"],
        "gross_sum": round(a["gross_sum"] + b["gross_sum"], 6),
        "fee_sum": round(a["fee_sum"] + b["fee_sum"], 6),
        "funding_sum": round(a["funding_sum"] + b["funding_sum"], 6),
        "net_sum": round(a["net_sum"] + b["net_sum"], 6),
        "win_rate_pct": round((a["win_count"] + b["win_count"]) / max(1, a["trades"] + b["trades"]) * 100, 4),
    }

    # 用上游旧表（163 条费用=0）算出来的「成本拖累」会错多少
    table_fee_on_a = float(FROZEN[(FROZEN["generation"] == "cand_ev")
                                  & (FROZEN["exec_status"] == "FILLED")]["fees"].sum())
    true_fee_on_a = a["fee_sum"]

    out = {
        "audit": "ENTRY-AUDIT-03",
        "sample_definition": {
            "rows_227": int(len(FROZEN)),
            "unique_signals_226": ("227 行按「标的+快照时刻」去重后 226（COTI 同一快照被记两次，"
                                   "一次判取消一次判成交）"),
            "generation_A_continuous": {
                "label": "连续回放（cand_ev_*）",
                "rows": int((FROZEN["generation"] == "cand_ev").sum()),
                "filled": int(len(gen_a)),
                "unfilled": int(((FROZEN["generation"] == "cand_ev")
                                 & (FROZEN["exec_status"] != "FILLED")).sum()),
                "window": "2021-12-01 ~ 2026-10-18",
                "is_single_generation": True,
            },
            "generation_B_windows": {
                "label": "365 天窗口回放（old_cand_*）",
                "rows": int((FROZEN["generation"] == "old_cand").sum()),
                "filled": int(len(gen_b)),
                "unfilled": 0,
                "windows": int(bdf["replay_group"].nunique()),
                "is_single_generation": True,
            },
            "verdict": ("「205 笔」作为成交记录数成立；作为「当前策略在连续回放下的成交集」不成立"
                        "（应为 163）。任何比率必须写明分母属于哪一代际。"),
        },
        "stats_by_generation_true_cost": {"generation_A": a, "generation_B": b},
        "naive_pool_if_summed": naive,
        "pooling_risk": {
            "cost_drag_error_before_fix": {
                "table_fee_sum_on_163": round(table_fee_on_a, 6),
                "true_fee_sum_on_163": round(true_fee_on_a, 6),
                "understated_by": round(true_fee_on_a - table_fee_on_a, 6),
                "note": "修之前 163 条费用全 0 ⇒ 任何「手续费拖累」都会被低估这个量",
            },
            "same_signal_two_verdicts": ("COTI 同一快照在两侧一取消一成交；"
                                         "把它计入 205 会让「连续回放成交集」多一笔不该有的成交"),
            "cannot_average_across": ("两代际的箱体、入场闸门、持仓路径都不同；"
                                      "跨代际求平均/求胜率不是「更稳健」，而是把两个总体混成一个"),
        },
        "bridge_rule": {
            "primary_sample": "generation_A（163 条成交 + 22 条未成交）—— 与连续回放口径一致，可作主样本",
            "secondary_sample": "generation_B（42 条）—— 只能作为「另一套切分下的独立样本」单独报告",
            "allowed_use_of_B": ["作为机制稳健性旁证（方向一致/不一致）",
                                 "作为箱体敏感性的反例（同一信号可一取消一成交）"],
            "forbidden_use_of_B": ["并入 A 计算胜率/期望/成本占比",
                                   "用 B 的成交替换 A 的取消（或反之）"],
            "twin_count": int(bdf["has_continuous_twin_within_4h"].sum()),
            "no_twin_count": int((~bdf["has_continuous_twin_within_4h"]).sum()),
        },
        "handover_to_factor_entry_06": {
            "status": "待接收",
            "required_from_them": ["冻结版本的代码/配置指纹",
                                   "唯一事件 ID（应能与 candidate_id 或 event_id 对齐）",
                                   "特征表"],
            "checks_ready": ["candidate_id ↔ event_id 是否一一对应",
                             "同一 (symbol, snapshot_time_ms) 是否唯一",
                             "特征值能否用本目录 03_identity_full.csv 的候选快照复算"],
            "rule": "无法匹配的不得强拼；在拿到可信样本前，不重新宣称任何指标组收益有效",
        },
        "conclusion_guard": ("本轮只建立样本，不评价策略收益。"
                             "在规格资格（全池 unverified_historical）与 PIT 未证未解决前，"
                             "任何基于本样本的收益结论都必须带这两个限定。"),
    }
    with open(OUT / "05_bridge.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    print(json.dumps({"A": a, "B": b}, ensure_ascii=False, indent=1))
    print(json.dumps(naive, ensure_ascii=False))
    print("桥接: 有连续孪生", out["bridge_rule"]["twin_count"],
          "无孪生", out["bridge_rule"]["no_twin_count"])


if __name__ == "__main__":
    main()
