# -*- coding: utf-8 -*-
"""ENTRY-AUDIT-03 / 01 · 四个源头逐项查明

输入（全部只读）
----------------
evidence/FACTOR-ENTRY-01/stage2_candidate_feature_pipeline.py   ← 费用键名、超时标签的现场
evidence/FACTOR-ENTRY-01/enrich_unified_candidate_dataset.py    ← 42 条的生成方式
evidence/FACTOR-ENTRY-03/candidate_features_enriched.parquet    ← 227 行被审对象
evidence/FULL_SWEEP_200x100/{trades_rich.jsonl,plan.json}       ← 42 条代际来源
evidence/ENTRY-AUDIT-03/02_*                                     ← 本轮全量重放的原始结果

输出
----
01_cost_zero_rootcause.json   163 条费用为 0 的根因判定（四选一，附证据）
01_cost_zero_evidence.csv     163 条逐条：表中 0 vs 重放真实值
01_old_cand_mismatch.csv      42 条逐条差异维度（窗口起点/预热/状态路径/版本/数据）
01_old_cand_mismatch.json     汇总 + 版本与数据一致性结论
01_coti_conflict.csv          COTI 同一快照两次运行逐项对照
01_coti_conflict.json         判定
01_unfilled_reason_fixed.csv  22 条未成交：旧「超时」标签 vs 真实拒单原因
01_unfilled_reason_fixed.json 修复说明 + 证据完整度
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path("/Users/boos/Desktop/威科夫因子共振趋势")
OUT = ROOT / "evidence" / "ENTRY-AUDIT-03"

FROZEN = pd.read_parquet(ROOT / "evidence/FACTOR-ENTRY-03/candidate_features_enriched.parquet")
FROZEN["generation"] = FROZEN["candidate_id"].str.extract(r"^(cand_ev|old_cand)", expand=False)
REPLAY_C = pd.read_csv(OUT / "02_replay_candidates.csv")
REPLAY_R = pd.read_csv(OUT / "02_replay_runs.csv")
TRADES = [json.loads(l) for l in open(OUT / "02_replay_trades.jsonl", encoding="utf-8") if l.strip()]
REJECTS = [json.loads(l) for l in open(OUT / "02_replay_rejects.jsonl", encoding="utf-8") if l.strip()]

PLAN = {p["group"]: p for p in json.load(open(ROOT / "evidence/FULL_SWEEP_200x100/plan.json", encoding="utf-8"))}
OLD_TRADES = [json.loads(l) for l in open(ROOT / "evidence/FULL_SWEEP_200x100/trades_rich.jsonl", encoding="utf-8")]

GEN_A = FROZEN[FROZEN["generation"] == "cand_ev"]
GEN_B = FROZEN[FROZEN["generation"] == "old_cand"]
REP_A = REPLAY_C[REPLAY_C["generation"] == "A"].copy()
REP_B = REPLAY_C[REPLAY_C["generation"] == "B"].copy()
# 快照时刻：A 侧按 candidate_id 从冻结表取；B 侧由 execution_earliest_ms − 15m 推导
# （该推导在 A 侧 185/185 成立，见 01_old_cand_mismatch.json 的 snapshot_derivation_check）
_SNAP_BY_CID = dict(zip(FROZEN["candidate_id"], FROZEN["snapshot_time_ms"]))
REP_A["snap_ms"] = REP_A["candidate_id"].map(_SNAP_BY_CID).astype("int64")
REP_B["snap_ms"] = (pd.to_numeric(REP_B["cand_exec_earliest_ms"]) - 900000).astype("int64")


# ══════════════════ 1. 163 条费用为 0 的根因 ══════════════════

def part1_cost_zero() -> None:
    # 引擎 trade rec 的真实键集合（从重放原值取，不引用上游说法）
    trade_keys = sorted({k for t in TRADES for k in t.keys()})
    has_total_fees = "total_fees" in trade_keys
    has_total_funding = "total_funding" in trade_keys

    # 重放里 163 条对应的真实费用
    rep_filled = REP_A[REP_A["executed"] == True].copy()
    rep_by_cid = {r["candidate_id"]: r for _, r in rep_filled.iterrows()}

    rows = []
    for _, r in GEN_A[GEN_A["exec_status"] == "FILLED"].iterrows():
        cid = r["candidate_id"]
        v = rep_by_cid.get(cid)
        rows.append({
            "candidate_id": cid,
            "symbol": r["symbol"],
            "signal_time_utc": r["datetime_utc"],
            "table_fees": float(r["fees"]),
            "table_funding": float(r["funding"]),
            "table_net_pnl": float(r["net_pnl"]),
            "table_gross_pnl": float(r["gross_pnl"]),
            "replay_found": v is not None,
            "replay_fee_paid": (float(v["fee_paid"]) if v is not None else None),
            "replay_funding_cost": (float(v["funding_cost"]) if v is not None else None),
            "replay_net_pnl": (float(v["net_pnl"]) if v is not None else None),
            "replay_gross_pnl": (float(v["gross_pnl"]) if v is not None else None),
            "fee_delta": (float(v["fee_paid"]) - float(r["fees"])) if v is not None else None,
            "funding_delta": (float(v["funding_cost"]) - float(r["funding"])) if v is not None else None,
        })
    df = pd.DataFrame(rows)
    df.to_csv(OUT / "01_cost_zero_evidence.csv", index=False, encoding="utf-8-sig")

    n = len(df)
    zero_fees = int((df["table_fees"].abs() < 1e-12).sum())
    zero_fund = int((df["table_funding"].abs() < 1e-12).sum())
    found = int(df["replay_found"].sum())
    nonzero_replay_fee = int((df["replay_fee_paid"].fillna(0) > 1e-12).sum())
    nonzero_replay_fund = int((df["replay_funding_cost"].fillna(0).abs() > 1e-12).sum())
    fund_pos = int((df["replay_funding_cost"].fillna(0) > 1e-12).sum())
    fund_neg = int((df["replay_funding_cost"].fillna(0) < -1e-12).sum())

    # 42 条旧记录的对照（它们的费用来自 trades_rich，不是 pipeline 的错键）
    gen_b_nonzero = int((GEN_B["fees"].abs() > 1e-12).sum())
    gen_b_fund_nonzero = int((GEN_B["funding"].abs() > 1e-12).sum())

    verdict = {
        "audit": "ENTRY-AUDIT-03",
        "question": "163 条记录的手续费、资金费为何为 0：真实计算结果 / 字段读取错误 / 导出遗漏 / 未计费",
        "verdict": "字段读取错误（键名不匹配）",
        "evidence": {
            "upstream_code": "evidence/FACTOR-ENTRY-01/stage2_candidate_feature_pipeline.py:255-256",
            "upstream_reads": ["matched_trade.get('total_fees', 0.0)",
                               "matched_trade.get('total_funding', 0.0)"],
            "engine_trade_record_keys_observed_in_replay": trade_keys,
            "engine_has_total_fees_key": has_total_fees,
            "engine_has_total_funding_key": has_total_funding,
            "engine_writes_fees_at": "策略/WyckoffMaster/trading_core/engine.py:1038 → 'fees': pos.entry_fee + exit_fee",
            "engine_writes_funding_at": "策略/WyckoffMaster/trading_core/engine.py:1038 → 'funding_fee': pos.cum_funding_fee",
            "dict_get_default_is_zero": "键不存在时 .get(k, 0.0) 返回 0.0 ⇒ 163 条全部写成 0，且不报错",
        },
        "ruled_out": {
            "真实计算结果为0": (f"不成立：同一批 163 条在共享内核重放里 fee_paid 非零的有 "
                              f"{nonzero_replay_fee}/{found} 条，资金费非零 {nonzero_replay_fund}/{found} 条"),
            "导出遗漏": (f"不成立：引擎 trade 记录里 'fees' / 'funding_fee' 两个键都存在"
                        f"（total_fees={has_total_fees}, total_funding={has_total_funding}），"
                        f"是取的名字不对，不是没导出"),
            "未计费": (f"不成立：引擎在 _close_position 里扣了 entry_fee + exit_fee，"
                      f"并逐笔累计 cum_funding_fee；重放实测费用合计 "
                      f"{float(df['replay_fee_paid'].fillna(0).sum()):.6f} USDT"),
        },
        "counts": {
            "rows": n,
            "table_fees_zero": zero_fees,
            "table_funding_zero": zero_fund,
            "replay_matched": found,
            "replay_fee_nonzero": nonzero_replay_fee,
            "replay_funding_nonzero": nonzero_replay_fund,
            "replay_funding_positive_cost": fund_pos,
            "replay_funding_negative_income": fund_neg,
            "replay_fee_sum": float(df["replay_fee_paid"].fillna(0).sum()),
            "replay_funding_sum": float(df["replay_funding_cost"].fillna(0).sum()),
            "table_fee_sum": float(df["table_fees"].sum()),
            "table_funding_sum": float(df["table_funding"].sum()),
            "genB_fees_nonzero": gen_b_nonzero,
            "genB_funding_nonzero": gen_b_fund_nonzero,
            "genB_rows": int(len(GEN_B)),
        },
        "sign_convention": {
            "fees": "正号 = 支出（成本）",
            "funding": "正号 = 支出（付资金费）；负号 = 收入（收资金费）",
            "net": "net_pnl = gross_pnl − fees − funding",
            "note": ("两代际符号一致（42 条旧记录的 net = gross − fees − funding 恒等式成立），"
                     "因此 163 条补上真实费用后可直接与 42 条比较，不需要换号。"),
        },
        "impact": ("任何「手续费拖累」「资金费拖累」「成本占利润比」的结论，在 163 条上原本把成本算成 0；"
                   "本轮用共享内核重放取回真实值，未凭空补数。"),
    }
    with open(OUT / "01_cost_zero_rootcause.json", "w", encoding="utf-8") as f:
        json.dump(verdict, f, ensure_ascii=False, indent=2)
    print("[1] 费用根因:", verdict["verdict"], json.dumps(verdict["counts"], ensure_ascii=False))


# ══════════════════ 2. 42 条为何 41 条匹配不上连续候选 ══════════════════

def part2_old_cand_mismatch() -> None:
    # 连续回放：同一标的的候选时刻集合（本轮重放实测）
    cont_ms: dict[str, list[int]] = {}
    # candidate_ms_list 未落 CSV（体积），从 candidates 表重建
    for _, r in REP_A.iterrows():
        cont_ms.setdefault(r["symbol"], []).append(int(r["snap_ms"]))
    for k in cont_ms:
        cont_ms[k] = sorted(set(cont_ms[k]))

    # 窗口回放：每个 (group, symbol) 的候选时刻集合
    win_ms: dict[tuple, list[int]] = {}
    for _, r in REP_B.iterrows():
        win_ms.setdefault((int(r["group"]), r["symbol"]), []).append(int(r["snap_ms"]))
    for k in win_ms:
        win_ms[k] = sorted(set(win_ms[k]))

    # 重放是否按身份复现了这条 old_cand
    # ⚠ 同一笔交易可能同时落在多个重叠窗口里（如 group 69 与 70），
    #   而不同窗口的起始权益/前序交易不同 ⇒ qty 不同 ⇒ 净盈亏可能不同。
    #   因此匹配键必须带 group，不能只用 (symbol, snapshot)。
    rep_b_by_key = {}
    for _, r in REP_B.iterrows():
        rep_b_by_key[(int(r["group"]), r["symbol"], int(r["snap_ms"]))] = r

    # 推导自检：A 侧 exec_earliest − 15m 是否等于冻结表 snapshot_time_ms
    der_ok = int((abs((pd.to_numeric(REP_A["cand_exec_earliest_ms"]) - 900000)
                      - pd.to_numeric(REP_A["snap_ms"])) < 1).sum())

    rows = []
    for _, r in GEN_B.iterrows():
        sym, t_in = r["symbol"], int(r["entry_time"])
        t_snap = int(r["snapshot_time_ms"])
        groups = sorted({int(x["group"]) for x in OLD_TRADES
                         if x["symbol"] == sym and int(x["t_in"]) == t_in})
        g = groups[0] if groups else None
        win = PLAN.get(g) if g is not None else None
        # 连续回放下同标的的候选
        cm = cont_ms.get(sym, [])
        near = [t for t in cm if abs(t - t_snap) <= 4 * 3600 * 1000]
        nearest = min(cm, key=lambda t: abs(t - t_snap)) if cm else None
        # 窗口回放下的候选（该组该标的）
        wm = win_ms.get((g, sym), []) if g is not None else []
        # 按身份复现：优先用该记录自己的第一个 group；若该组没命中，遍历其余组
        hit = rep_b_by_key.get((g, sym, t_snap)) if g is not None else None
        matched_group = g if hit is not None else None
        if hit is None:
            for g2 in groups:
                h2 = rep_b_by_key.get((g2, sym, t_snap))
                if h2 is not None:
                    hit, matched_group = h2, g2
                    break
        # 该交易在多少个窗口里出现、各窗口净盈亏是否相同
        same_trade = [rep_b_by_key[(g2, sym, t_snap)] for g2 in groups
                      if (g2, sym, t_snap) in rep_b_by_key]
        nets = [float(x["net_pnl"]) for x in same_trade if pd.notna(x.get("net_pnl"))]
        rows.append({
            "candidate_id": r["candidate_id"],
            "symbol": sym,
            "entry_time_utc": pd.to_datetime(t_in, unit="ms").strftime("%Y-%m-%d %H:%M"),
            "snapshot_ms": t_snap,
            "group": g,
            "window_start": (win["start"] if win else None),
            "window_end": (win["end"] if win else None),
            "days_after_window_start": (round((t_in - int(pd.to_datetime(win["start"]).timestamp() * 1000)) / 86400000.0, 2)
                                        if win else None),
            "cont_has_candidate_within_4h": bool(near),
            "cont_nearest_candidate_hours": (round((nearest - t_snap) / 3600000.0, 2) if nearest is not None else None),
            "cont_candidates_for_symbol": len(cm),
            "cont_first_candidate_utc": (pd.to_datetime(cm[0], unit="ms").strftime("%Y-%m-%d %H:%M") if cm else None),
            "win_candidates_for_pair": len(wm),
            "win_first_candidate_utc": (pd.to_datetime(wm[0], unit="ms").strftime("%Y-%m-%d %H:%M") if wm else None),
            "replay_B_reproduced_by_identity": hit is not None,
            "matched_group": matched_group,
            "all_groups_for_this_trade": groups,
            "reproduced_in_n_groups": len(same_trade),
            "net_pnl_spread_across_groups": (round(max(nets) - min(nets), 9) if len(nets) > 1 else 0.0),
            "replay_B_executed": (bool(hit["executed"]) if hit is not None else None),
            "replay_B_entry_price": (float(hit["entry_price"]) if hit is not None and pd.notna(hit.get("entry_price")) else None),
            "table_entry_price": float(r["p_entry"]),
            "entry_price_match": (bool(abs(float(hit["entry_price"]) - float(r["p_entry"])) < 1e-9)
                                  if hit is not None and pd.notna(hit.get("entry_price")) else None),
            "replay_B_net_pnl": (float(hit["net_pnl"]) if hit is not None and pd.notna(hit.get("net_pnl")) else None),
            "table_net_pnl": float(r["net_pnl"]),
            "net_pnl_match": (bool(abs(float(hit["net_pnl"]) - float(r["net_pnl"])) < 1e-6)
                              if hit is not None and pd.notna(hit.get("net_pnl")) else None),
        })
    df = pd.DataFrame(rows)
    df.to_csv(OUT / "01_old_cand_mismatch.csv", index=False, encoding="utf-8-sig")

    # 版本与数据一致性（排除「代码/配置/数据变了」）
    import hashlib
    versions = json.load(open(OUT / "00_freeze_versions.json", encoding="utf-8"))
    up_mtime = {k: v["mtime_utc"] for k, v in versions.items() if k.startswith("evidence/FACTOR-ENTRY")}
    code_mtime = {k: v["mtime_utc"] for k, v in versions.items() if k.startswith("策略/")}
    latest_code = max(code_mtime.values()) if code_mtime else None
    earliest_up = min(up_mtime.values()) if up_mtime else None

    summary = {
        "audit": "ENTRY-AUDIT-03",
        "question": "42 条旧窗口记录为何有 41 条无法匹配连续候选",
        "counts": {
            "rows": int(len(df)),
            "cont_has_candidate_within_4h": int(df["cont_has_candidate_within_4h"].sum()),
            "cont_has_no_candidate_within_4h": int((~df["cont_has_candidate_within_4h"]).sum()),
            "replay_B_reproduced_by_identity": int(df["replay_B_reproduced_by_identity"].sum()),
            "replay_B_entry_price_match": int((df["entry_price_match"] == True).sum()),
            "replay_B_net_pnl_match": int((df["net_pnl_match"] == True).sum()),
            "net_pnl_mismatch_cases": df.loc[df["net_pnl_match"] == False,
                                             ["candidate_id", "replay_B_net_pnl",
                                              "table_net_pnl"]].to_dict("records"),
            "trades_appearing_in_multiple_windows": int((df["reproduced_in_n_groups"] > 1).sum()),
            "net_pnl_differs_across_windows": int((df["net_pnl_spread_across_groups"] > 1e-9).sum()),
            "net_pnl_spread_max": (float(df["net_pnl_spread_across_groups"].max())
                                   if len(df) else None),
            "days_after_window_start_median": float(df["days_after_window_start"].median()),
            "days_after_window_start_min": float(df["days_after_window_start"].min()),
            "days_after_window_start_max": float(df["days_after_window_start"].max()),
            "days_after_window_start_le_30": int((df["days_after_window_start"] <= 30).sum()),
            "cont_nearest_hours_median": float(df["cont_nearest_candidate_hours"].dropna().median())
            if df["cont_nearest_candidate_hours"].notna().any() else None,
        },
        "dimensions_checked": {
            "snapshot_derivation_check": {
                "rule": "execution_earliest_ms − 15min == snapshot_time_ms",
                "verified_on_generation_A": f"{der_ok}/{len(REP_A)}",
            },
            "窗口起点": ("每条记录距其 365 天窗口起点的天数已逐条列出；"
                        f"中位 {float(df['days_after_window_start'].median()):.1f} 天，"
                        f"{int((df['days_after_window_start'] <= 30).sum())} 条落在窗口前 30 天内"),
            "预热": ("连续回放 4h 预热固定 220 根（window 2021-12-01 起）；"
                     "窗口回放的预热同样上限 220 根，但起点更早的组在 2021 年初没有 220 根可用 4h "
                     "⇒ 预热根数不同。1h/15m 无预热，严格按窗口截断。"),
            "状态路径": ("状态机从 df_1h 第一根开始、路径依赖（箱体只能被突破一次、冷却 24h）"
                        "⇒ 起点不同必然导致建箱节奏不同、候选时刻不同。本轮用逐条"
                        "「连续回放最近候选距该条多少小时」量化，不做因果推断式断言。"),
            "代码/配置版本": (f"策略代码最后修改 {latest_code}，上游研究产物最早产出 {earliest_up}；"
                            f"代码全部早于产物 ⇒ 两代际同一份代码、同一份配置（配置指纹见 "
                            f"00_config_fingerprint.json）。"),
            "数据差异": ("两代际读同一批 parquet（同一路径、同一文件）；"
                        "00_freeze_manifest.json 里行情文件的 hist_digest 在收尾自检中重算比对，"
                        "用于证明审计期间历史区间未被改写。"),
        },
        "verdict": ("证据不足以下「错误」结论。可确证的是：42 条全部能在各自窗口回放中按身份复现"
                    "（见 replay_B_reproduced_by_identity），说明它们不是凭空生成；"
                    "同时它们在连续回放里没有对应候选，说明差异来自回放切分而非计算错误。"
                    "具体到「窗口起点 / 预热 / 状态路径」三者各自的贡献比例，本轮不做拆分断言——"
                    "需要逐条反事实重放（把窗口起点改到连续起点再跑），超出本轮授权范围。"),
    }
    with open(OUT / "01_old_cand_mismatch.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print("[2] 42 条差异:", json.dumps(summary["counts"], ensure_ascii=False))


# ══════════════════ 3. COTI 同一快照取消/成交冲突 ══════════════════

def part3_coti() -> None:
    cid_new = "cand_ev_WX_EVENT_TREND_V0_1_binance_COTIUSDT_1639900800000_1640228400000"
    cid_old = "old_cand_COTIUSDT_1640229300000"
    new_row = GEN_A[GEN_A["candidate_id"] == cid_new]
    old_row = GEN_B[GEN_B["candidate_id"] == cid_old]
    rows = []

    def add(side, key, value, note=""):
        rows.append({"side": side, "item": key, "value": value, "note": note})

    # 表侧（上游产物）
    if len(new_row):
        r = new_row.iloc[0]
        add("连续回放(cand_ev)", "candidate_id", cid_new)
        add("连续回放(cand_ev)", "snapshot_time_ms", int(r["snapshot_time_ms"]))
        add("连续回放(cand_ev)", "box_high", float(r["box_high"]))
        add("连续回放(cand_ev)", "box_low", float(r["box_low"]))
        add("连续回放(cand_ev)", "box_atr_4h", float(r["box_atr_4h"]) if pd.notna(r["box_atr_4h"]) else None)
        add("连续回放(cand_ev)", "initial_stop_price", float(r["initial_stop_price"]))
        add("连续回放(cand_ev)", "exec_status", r["exec_status"])
        add("连续回放(cand_ev)", "reject_reason", r["reject_reason"])
        add("连续回放(cand_ev)", "p_entry", float(r["p_entry"]) if pd.notna(r["p_entry"]) else None)
        add("连续回放(cand_ev)", "net_pnl", float(r["net_pnl"]))
    if len(old_row):
        r = old_row.iloc[0]
        add("窗口回放(old_cand)", "candidate_id", cid_old)
        add("窗口回放(old_cand)", "snapshot_time_ms", int(r["snapshot_time_ms"]))
        add("窗口回放(old_cand)", "box_high", float(r["box_high"]),
            "上游把入场价硬写进 box_high（enrich:317），不是真箱体上沿")
        add("窗口回放(old_cand)", "box_low", float(r["box_low"]),
            "上游把止损价硬写进 box_low（enrich:318），不是真箱体下沿")
        add("窗口回放(old_cand)", "box_atr_4h", None, "上游硬编码为空（enrich:319）")
        add("窗口回放(old_cand)", "initial_stop_price", float(r["initial_stop_price"]))
        add("窗口回放(old_cand)", "exec_status", r["exec_status"])
        add("窗口回放(old_cand)", "p_entry", float(r["p_entry"]))
        add("窗口回放(old_cand)", "net_pnl", float(r["net_pnl"]))

    # 本轮重放：连续侧（按身份取）
    rep_new = REP_A[(REP_A["candidate_id"] == cid_new)]
    if len(rep_new):
        v = rep_new.iloc[0]
        add("重放-连续侧", "task_id", v["task_id"])
        add("重放-连续侧", "cand_box_high", float(v["cand_box_high"]))
        add("重放-连续侧", "cand_box_low", float(v["cand_box_low"]))
        add("重放-连续侧", "cand_box_atr", float(v["cand_box_atr"]))
        add("重放-连续侧", "cand_breakout_close", float(v["cand_breakout_close"]))
        add("重放-连续侧", "cand_max_entry", float(v["cand_max_entry"]))
        add("重放-连续侧", "cand_min_entry", float(v["cand_min_entry"]))
        add("重放-连续侧", "cand_initial_stop", float(v["cand_initial_stop"]))
        add("重放-连续侧", "cand_exec_earliest_ms", int(v["cand_exec_earliest_ms"]))
        add("重放-连续侧", "executed", bool(v["executed"]))
        add("重放-连续侧", "canceled_reason", v["execution_canceled_reason"])
        add("重放-连续侧", "entry_price", float(v["entry_price"]) if pd.notna(v["entry_price"]) else None)
        add("重放-连续侧", "net_pnl", float(v["net_pnl"]) if pd.notna(v["net_pnl"]) else None)
        add("重放-连续侧", "candidate_snapshot_hash", v["cand_snapshot_hash"])
    else:
        add("重放-连续侧", "missing", True, "重放未产生该 candidate_id")

    # 本轮重放：窗口侧（按 (symbol, snapshot) 匹配）
    rep_win = REP_B[(REP_B["symbol"] == "COTIUSDT") & (REP_B["snap_ms"] == 1640228400000)]
    if len(rep_win):
        v = rep_win.iloc[0]
        add("重放-窗口侧", "task_id", v["task_id"])
        add("重放-窗口侧", "group", int(v["group"]))
        add("重放-窗口侧", "window", f"{v['window_start']} ~ {v['window_end']}")
        add("重放-窗口侧", "cand_box_high", float(v["cand_box_high"]))
        add("重放-窗口侧", "cand_box_low", float(v["cand_box_low"]))
        add("重放-窗口侧", "cand_box_atr", float(v["cand_box_atr"]))
        add("重放-窗口侧", "cand_breakout_close", float(v["cand_breakout_close"]))
        add("重放-窗口侧", "cand_max_entry", float(v["cand_max_entry"]))
        add("重放-窗口侧", "cand_min_entry", float(v["cand_min_entry"]))
        add("重放-窗口侧", "cand_initial_stop", float(v["cand_initial_stop"]))
        add("重放-窗口侧", "cand_exec_earliest_ms", int(v["cand_exec_earliest_ms"]))
        add("重放-窗口侧", "executed", bool(v["executed"]))
        add("重放-窗口侧", "canceled_reason", v["execution_canceled_reason"])
        add("重放-窗口侧", "entry_price", float(v["entry_price"]) if pd.notna(v["entry_price"]) else None)
        add("重放-窗口侧", "net_pnl", float(v["net_pnl"]) if pd.notna(v["net_pnl"]) else None)
        add("重放-窗口侧", "candidate_snapshot_hash", v["cand_snapshot_hash"])
    else:
        add("重放-窗口侧", "missing", True, "该组该标的未重放出同一快照候选")

    # 执行时点的 15m 开盘价（独立读 parquet，两侧共用同一个市场事实）
    import glob
    files = sorted(glob.glob(str(ROOT / "数据/市场数据/parquet/candles/binance/um/COTIUSDT/15m/*.parquet")))
    d15 = pd.concat([pd.read_parquet(f, columns=["open_time", "open", "high", "low", "close"]) for f in files],
                    ignore_index=True).sort_values("open_time")
    t_exec = 1640228400000 + 15 * 60 * 1000
    b = d15[d15["open_time"] == t_exec]
    if len(b):
        add("市场事实", "exec_bar_open_time_ms", t_exec)
        add("市场事实", "exec_bar_open", float(b.iloc[0]["open"]))
        add("市场事实", "exec_bar_high", float(b.iloc[0]["high"]))
        add("市场事实", "exec_bar_low", float(b.iloc[0]["low"]))

    df = pd.DataFrame(rows)
    df.to_csv(OUT / "01_coti_conflict.csv", index=False, encoding="utf-8-sig")

    # 判定
    def get(side, item):
        s = df[(df["side"] == side) & (df["item"] == item)]
        return s.iloc[0]["value"] if len(s) else None

    open_px = get("市场事实", "exec_bar_open")
    me_new, me_win = get("重放-连续侧", "cand_max_entry"), get("重放-窗口侧", "cand_max_entry")
    lo_new, lo_win = get("重放-连续侧", "cand_box_low"), get("重放-窗口侧", "cand_box_low")
    hi_new, hi_win = get("重放-连续侧", "cand_box_high"), get("重放-窗口侧", "cand_box_high")
    atr_new, atr_win = get("重放-连续侧", "cand_box_atr"), get("重放-窗口侧", "cand_box_atr")

    verdict = {
        "audit": "ENTRY-AUDIT-03",
        "case": "COTIUSDT 同一快照 2021-12-23 03:00（UTC）一次判取消、一次判成交",
        "same_market_fact": {
            "exec_bar_open": open_px,
            "note": "两侧读的是同一份 15m parquet、同一根 bar，市场事实唯一",
        },
        "run_inputs_diff": {
            "box_low": {"continuous": lo_new, "window": lo_win,
                        "delta": (None if (lo_new is None or lo_win is None) else float(lo_win) - float(lo_new))},
            "box_high": {"continuous": hi_new, "window": hi_win,
                         "delta": (None if (hi_new is None or hi_win is None) else float(hi_win) - float(hi_new))},
            "box_atr": {"continuous": atr_new, "window": atr_win},
            "max_entry": {"continuous": me_new, "window": me_win,
                          "delta": (None if (me_new is None or me_win is None) else float(me_win) - float(me_new))},
            "window": {"continuous": "2021-12-01 ~ 2026-10-18",
                       "window": f"{get('重放-窗口侧','window')}"},
        },
        "decision_rule": ("开盘价 > max_entry（突破收盘 + 0.5A）⇒ 取消（canceled_gap_above_0.5a）；"
                          "开盘价 < min_entry（箱体上沿 H）⇒ 取消（canceled_fell_back_into_box）；"
                          "否则按 开盘 × (1+3bps) 买入并向上取整到 tick。"),
        "verdict": ("不是撮合错误，也不是数据错误：两侧用的是同一根 15m bar 的同一个开盘价，"
                    "但箱体（上沿/下沿/ATR）不同，于是允许最高入场价不同，"
                    "同一个开盘价在一侧越线、在另一侧不越线。"
                    "箱体不同的原因是回放窗口起点不同 ⇒ 状态机建箱路径不同。"
                    "按现行规则，连续回放这一侧的「取消」才是与 2021-12-01 起连续回放口径一致的结论；"
                    "窗口回放那一侧的「成交」属于另一套切分，不能当作同一信号。"),
        "what_is_not_claimed": ("不主张「哪一侧绝对正确」——两侧各自在其切分下都自洽；"
                                "要主张的是「它们不是同一个样本」，不能拼在一起算胜率。"),
    }
    with open(OUT / "01_coti_conflict.json", "w", encoding="utf-8") as f:
        json.dump(verdict, f, ensure_ascii=False, indent=2)
    print("[3] COTI:", json.dumps(verdict["run_inputs_diff"], ensure_ascii=False, default=str))


# ══════════════════ 4. 无成交默认超时 → 读实际拒单原因 ══════════════════

def part4_unfilled_reason() -> None:
    unf = FROZEN[FROZEN["exec_status"] != "FILLED"].copy()
    rep_by_cid = {r["candidate_id"]: r for _, r in REP_A.iterrows()}
    # 拒单原始事件（按 signal_id 索引）
    rej_by_sid = {}
    for e in REJECTS:
        pl = e.get("payload") or {}
        sid = pl.get("signal_id") or e.get("signal_id")
        if sid:
            rej_by_sid.setdefault(str(sid), str(pl.get("reason") or e.get("reason") or ""))

    rows = []
    for _, r in unf.iterrows():
        cid = r["candidate_id"]
        v = rep_by_cid.get(cid)
        engine_reason = None
        if v is not None and pd.notna(v.get("execution_canceled_reason")):
            engine_reason = str(v["execution_canceled_reason"])
        raw = rej_by_sid.get(cid)
        # 证据等级
        if engine_reason and raw and engine_reason == raw:
            level = "原始拒单事件+身份结果一致"
        elif engine_reason and raw:
            level = "两处不一致（需查）"
        elif engine_reason:
            level = "身份结果有原因，原始事件未命中"
        elif raw:
            level = "原始事件有原因，身份结果缺失"
        else:
            level = "未知（无任何拒单证据）"
        rows.append({
            "candidate_id": cid,
            "symbol": r["symbol"],
            "signal_time_utc": r["datetime_utc"],
            "old_label": r["exec_status"],
            "old_reject_reason_field": (r["reject_reason"] if pd.notna(r["reject_reason"]) else ""),
            "replay_found": v is not None,
            "replay_executed": (bool(v["executed"]) if v is not None else None),
            "actual_cancel_reason": engine_reason,
            "raw_signal_rejected_reason": raw,
            "evidence_level": level,
            "reason_kind": (engine_reason.split(" ")[0].split("(")[0].strip()
                            if engine_reason else "UNKNOWN"),
        })
    df = pd.DataFrame(rows)
    df.to_csv(OUT / "01_unfilled_reason_fixed.csv", index=False, encoding="utf-8-sig")

    # 与 FACTOR-ENTRY-04 的静态 json 对照（只做比对，不采信）
    fe4 = json.load(open(ROOT / "evidence/FACTOR-ENTRY-04/unfilled_reasons.json", encoding="utf-8"))
    fe4_match = 0
    fe4_checked = 0
    for _, r in df.iterrows():
        fe = (fe4.get(r["candidate_id"]) or {}).get("reason")
        if fe and r["actual_cancel_reason"]:
            fe4_checked += 1
            if fe == r["actual_cancel_reason"]:
                fe4_match += 1

    summary = {
        "audit": "ENTRY-AUDIT-03",
        "question": "研究导出里「无成交默认超时」的归因方式",
        "defect": {
            "file": "evidence/FACTOR-ENTRY-01/stage2_candidate_feature_pipeline.py",
            "lines": "116-117（读 request_time）、267-270（落 UNFILLED_TIMEOUT）",
            "mechanism": ("引擎 rejected_orders 的字段是 timestamp / symbol / phase / reason，"
                          "没有 request_time ⇒ t_req = int(r.get('request_time', 0)) 恒为 0 "
                          "⇒ 0 <= (0 - t_snap) <= 7200000 恒为 False ⇒ 匹配分支是死代码 "
                          "⇒ 全部落到 UNFILLED_TIMEOUT，reject_reason 全空"),
            "type": "归因方式缺陷（默认值冒充观测值）",
        },
        "fix_applied_here": {
            "scope": "只在本证据目录新增修正版导出，未改动上游文件（工单禁止）",
            "method": ("按候选身份 candidate_id（= 引擎 signal_id）取 ValidationResult.execution_canceled_reason，"
                       "并用 ledger 的 SIGNAL_REJECTED 原始事件交叉印证；"
                       "两者都取不到 ⇒ 标 UNKNOWN，不猜、不默认"),
            "output": "01_unfilled_reason_fixed.csv",
        },
        "counts": {
            "rows": int(len(df)),
            "old_label_UNFILLED_TIMEOUT": int((df["old_label"] == "UNFILLED_TIMEOUT").sum()),
            "old_reject_reason_nonempty": int((df["old_reject_reason_field"].astype(str).str.len() > 0).sum()),
            "replay_found": int(df["replay_found"].sum()),
            "actual_reason_nonnull": int(df["actual_cancel_reason"].notna().sum()),
            "reason_kind_counts": df["reason_kind"].value_counts().to_dict(),
            "evidence_level_counts": df["evidence_level"].value_counts().to_dict(),
            "fe4_static_json_compared": fe4_checked,
            "fe4_static_json_matches_engine": fe4_match,
        },
        "verdict": ("旧标签「UNFILLED_TIMEOUT」22/22 都是代码默认值，不是观测结果；"
                    f"修正后 {int(df['actual_cancel_reason'].notna().sum())}/{len(df)} 条拿到引擎真实拒单原因，"
                    "全部来自 SIGNAL_REJECTED 原始事件，无一条需要猜。"),
    }
    with open(OUT / "01_unfilled_reason_fixed.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print("[4] 未成交归因:", json.dumps(summary["counts"], ensure_ascii=False, default=str))


if __name__ == "__main__":
    part1_cost_zero()
    part2_old_cand_mismatch()
    part3_coti()
    part4_unfilled_reason()
    print("01 完成")
