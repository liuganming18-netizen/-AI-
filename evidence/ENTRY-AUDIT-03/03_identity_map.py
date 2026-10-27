# -*- coding: utf-8 -*-
"""ENTRY-AUDIT-03 / 03 · 全量身份映射与三态判定

对冻结研究范围的**全部**候选逐条建链（不是 48 条抽样）：

    候选ID → 执行决策 → 成交/取消 → 退出 → 手续费 → 资金费 → 净盈亏

同时给出三个互相独立的标记（工单要求分别记录）：
    spec_qualification  合约规格资格  verified / unverified_historical / contradicted / missing
    exec_validity       执行有效性    valid / invalid(原因)
    research_usability  研究可用性    usable / usable_with_caveat / not_usable(原因)

产出
----
03_identity_full.csv       全量逐条映射（227 行）
03_identity_chain.json     链路完整性 + 净值恒等式 + 滑点是否重复扣减的核验
03_usability_summary.json  三态分布与不可用原因清单
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
RC = pd.read_csv(OUT / "02_replay_candidates.csv")
RR = pd.read_csv(OUT / "02_replay_runs.csv")
TRADES = [json.loads(l) for l in open(OUT / "02_replay_trades.jsonl", encoding="utf-8") if l.strip()]
REJECTS = [json.loads(l) for l in open(OUT / "02_replay_rejects.jsonl", encoding="utf-8") if l.strip()]

TRADE_BY_SIGNAL = {}
TRADE_BY_ORDER = {}
for t in TRADES:
    sid = t.get("signal_id")
    if sid:
        TRADE_BY_SIGNAL[str(sid)] = t
    oid = t.get("order_id")
    if oid:
        TRADE_BY_ORDER[str(oid)] = t
REJ_BY_SIGNAL = {}
for e in REJECTS:
    pl = e.get("payload") or {}
    sid = pl.get("signal_id") or e.get("signal_id")
    if sid:
        REJ_BY_SIGNAL.setdefault(str(sid), str(pl.get("reason") or e.get("reason") or ""))

RUN_BY_TASK = {r["task_id"]: r for _, r in RR.iterrows()}


def fnum(x):
    return float(x) if x is not None and pd.notna(x) else None


def main() -> None:
    # ── 重放候选索引：连续侧按 candidate_id；窗口侧按 (symbol, snapshot_ms) ──
    rep_a = {r["candidate_id"]: r for _, r in RC[RC["generation"] == "A"].iterrows()}
    _snap_by_cid = dict(zip(FROZEN["candidate_id"], FROZEN["snapshot_time_ms"]))
    # ⚠ 同一笔交易可能同时落在多个重叠窗口里，不同窗口的起始权益/前序交易不同
    #   ⇒ qty 不同 ⇒ 净盈亏可能不同。匹配键必须带 group。
    rep_b = {}
    for _, r in RC[RC["generation"] == "B"].iterrows():
        snap = int(float(r["cand_exec_earliest_ms"]) - 900000)
        rep_b.setdefault((int(r["group"]), r["symbol"], snap), r)
    # old_cand → 它出现在哪些 group
    _OLD = [json.loads(l) for l in open(ROOT / "evidence/FULL_SWEEP_200x100/trades_rich.jsonl",
                                        encoding="utf-8") if l.strip()]
    _GROUPS_BY_TRADE = {}
    for t in _OLD:
        _GROUPS_BY_TRADE.setdefault((t["symbol"], int(t["t_in"])), set()).add(int(t["group"]))

    rows = []
    for _, r in FROZEN.iterrows():
        cid = r["candidate_id"]
        gen = r["generation"]
        t_snap = int(r["snapshot_time_ms"])
        if gen == "cand_ev":
            v = rep_a.get(cid)
        else:
            # 遍历该笔交易出现过的全部窗口，取能按身份复现的那个（记录用哪个组）
            v = None
            for g in sorted(_GROUPS_BY_TRADE.get((r["symbol"], int(r["entry_time"])), [])):
                v = rep_b.get((g, r["symbol"], t_snap))
                if v is not None:
                    break
        task_id = (v["task_id"] if v is not None else None)
        run = RUN_BY_TASK.get(task_id) if task_id else None

        # ── 引擎成交原值（按 signal_id 取，不按时间近似） ──
        # 窗口代际的 signal_id 是引擎自己生成的 candidate_id（不是 old_cand_*），
        # 因此 B 侧改按 order_id 取（order_id 由该候选的 ValidationResult 给出）。
        tr = None
        if v is not None and bool(v["executed"]):
            tr = TRADE_BY_SIGNAL.get(cid)
            if tr is None and pd.notna(v.get("order_id")):
                tr = TRADE_BY_ORDER.get(str(v["order_id"]))

        # ── 三态 ──
        spec_status = (run["spec_status"] if run is not None else None)
        spec_appl = (run["spec_applicability"] if run is not None and pd.notna(run.get("spec_applicability")) else None)
        formal_ok = (bool(run["formal_pass_allowed"]) if run is not None and pd.notna(run.get("formal_pass_allowed")) else None)
        exec_valid = (bool(run["run_valid"]) if run is not None and pd.notna(run.get("run_valid")) else None)
        invalid_reasons = (run["invalid_reasons"] if run is not None and pd.notna(run.get("invalid_reasons")) else "")
        chain_ok = (bool(run["chain_ok"]) if run is not None and pd.notna(run.get("chain_ok")) else None)

        # 规格资格：把 engine 的 spec_status（spec_applied/declared_no_spec）与
        # instrument_spec.applicability（verified/unverified_historical/contradicted/...）分开记
        if run is None:
            spec_qual = "missing"
        elif spec_status == "declared_no_spec":
            spec_qual = "missing"          # 无规格，仅显式声明步长
        else:
            spec_qual = str(spec_appl) if spec_appl else "unverified_historical"

        # 费用与净值
        fee = fnum(v["fee_paid"]) if v is not None and bool(v["executed"]) else None
        fund = fnum(v["funding_cost"]) if v is not None and bool(v["executed"]) else None
        slip = fnum(v["slippage_cost"]) if v is not None and bool(v["executed"]) else None
        gross = fnum(v["gross_pnl"]) if v is not None and bool(v["executed"]) else None
        net = fnum(v["net_pnl"]) if v is not None and bool(v["executed"]) else None

        # 净值恒等式残差（只在有成交时定义）
        resid = (net - (gross - (fee or 0.0) - (fund or 0.0))) if net is not None else None

        # 滑点是否已进成交价：从 price_audit 看 entry/exit 的取整后价是否等于成交价
        pa = (tr or {}).get("price_audit") or {}
        ent_pa = (pa.get("entry") or {})
        ext_pa = (pa.get("exit") or {})

        # 研究可用性
        reasons = []
        if v is None:
            usability = "not_usable"
            reasons.append("重放未产生该候选（无身份证据）")
        else:
            if chain_ok is False:
                reasons.append("身份链断裂")
            if exec_valid is False:
                reasons.append("执行无效:" + str(invalid_reasons))
            if spec_qual != "verified":
                reasons.append(f"规格资格={spec_qual}（历史规格不可证）")
            if not bool(v["cand_pit_proven"]) if pd.notna(v.get("cand_pit_proven")) else False:
                reasons.append("PIT 未证")
            hard = [x for x in reasons if x.startswith("重放未产生") or x.startswith("身份链断裂") or x.startswith("执行无效")]
            usability = "not_usable" if hard else ("usable_with_caveat" if reasons else "usable")

        rows.append({
            # ── 身份层 ──
            "candidate_id": cid,
            "event_id": r["event_id"],
            "generation": gen,
            "symbol": r["symbol"],
            "snapshot_time_ms": t_snap,
            "signal_time_utc": r["datetime_utc"],
            "replay_task_id": task_id,
            "replay_group": (int(v["group"]) if v is not None and pd.notna(v.get("group")) else None),
            "replay_window_start": (v["window_start"] if v is not None else None),
            "replay_window_end": (v["window_end"] if v is not None else None),
            "candidate_snapshot_hash": (v["cand_snapshot_hash"] if v is not None else None),

            # ── 候选快照（引擎侧真值） ──
            "cand_box_high": (fnum(v["cand_box_high"]) if v is not None else None),
            "cand_box_low": (fnum(v["cand_box_low"]) if v is not None else None),
            "cand_box_atr": (fnum(v["cand_box_atr"]) if v is not None else None),
            "cand_box_er": (fnum(v["cand_box_er"]) if v is not None else None),
            "cand_breakout_close": (fnum(v["cand_breakout_close"]) if v is not None else None),
            "cand_max_entry": (fnum(v["cand_max_entry"]) if v is not None else None),
            "cand_min_entry": (fnum(v["cand_min_entry"]) if v is not None else None),
            "cand_initial_stop": (fnum(v["cand_initial_stop"]) if v is not None else None),
            "cand_exec_earliest_ms": (int(v["cand_exec_earliest_ms"]) if v is not None and pd.notna(v.get("cand_exec_earliest_ms")) else None),

            # ── 执行决策 ──
            "executed": (bool(v["executed"]) if v is not None else None),
            "identity_status": (v["identity_status"] if v is not None else None),
            "order_id": (v["order_id"] if v is not None and pd.notna(v.get("order_id")) else None),
            "cancel_reason": (v["execution_canceled_reason"] if v is not None and pd.notna(v.get("execution_canceled_reason")) else None),
            "raw_signal_rejected_reason": REJ_BY_SIGNAL.get(cid),

            # ── 成交与退出 ──
            "fill_time_ms": (int(v["fill_time_ms"]) if v is not None and pd.notna(v.get("fill_time_ms")) else None),
            "entry_price": (fnum(v["entry_price"]) if v is not None else None),
            "executed_qty": (fnum(v["executed_qty"]) if v is not None else None),
            "actual_stop_price": (fnum(v["actual_stop_price"]) if v is not None else None),
            "exit_time_ms": (int(v["exit_time_ms"]) if v is not None and pd.notna(v.get("exit_time_ms")) else None),
            "exit_price": (fnum(v["exit_price"]) if v is not None else None),
            "exit_reason": (v["exit_reason"] if v is not None and pd.notna(v.get("exit_reason")) else None),
            "holding_hours": (fnum(v["holding_duration_hours"]) if v is not None else None),
            "exit_order_id": (tr or {}).get("exit_order_id"),

            # ── 费用（统一符号：正=支出，负=收入） ──
            "fee_paid": fee,
            "funding_cost": fund,
            "slippage_cost": slip,
            "slippage_pure": (fnum(tr["slippage_loss_pure"]) if tr else None),
            "rounding_loss": (fnum(tr["rounding_loss"]) if tr else None),
            "gross_pnl": gross,
            "net_pnl": net,
            "net_identity_residual": resid,

            # ── 上游表里的旧值（用于对照，不采信） ──
            "table_exec_status": r["exec_status"],
            "table_fees": float(r["fees"]),
            "table_funding": float(r["funding"]),
            "table_net_pnl": float(r["net_pnl"]),
            "table_p_entry": fnum(r["p_entry"]),
            "table_p_exit": fnum(r["p_exit"]),
            "fee_delta_vs_table": ((fee - float(r["fees"])) if fee is not None else None),
            "funding_delta_vs_table": ((fund - float(r["funding"])) if fund is not None else None),
            "net_delta_vs_table": ((net - float(r["net_pnl"])) if net is not None else None),

            # ── 三态 ──
            "spec_qualification": spec_qual,
            "spec_status_engine": spec_status,
            "formal_pass_allowed": formal_ok,
            "exec_validity": ("valid" if exec_valid else ("invalid" if exec_valid is False else "unknown")),
            "exec_invalid_reasons": invalid_reasons,
            "identity_chain_ok": chain_ok,
            "research_usability": usability,
            "usability_reasons": " | ".join(reasons),

            # ── 滑点是否已进成交价 ──
            "entry_raw_price": fnum(ent_pa.get("raw_price")),
            "entry_post_slip_price": fnum(ent_pa.get("post_slip_price")),
            "entry_post_round_price": fnum(ent_pa.get("post_round_price")),
            "entry_price_step": ent_pa.get("price_step"),
            "entry_slip_delta": fnum(ent_pa.get("slip_delta")),
            "entry_round_delta": fnum(ent_pa.get("round_delta")),
            "exit_raw_price": fnum(ext_pa.get("raw_price")),
            "exit_post_slip_price": fnum(ext_pa.get("post_slip_price")),
            "exit_post_round_price": fnum(ext_pa.get("post_round_price")),
            "exit_price_step": ext_pa.get("price_step"),
            "exit_slip_delta": fnum(ext_pa.get("slip_delta")),
            "exit_round_delta": fnum(ext_pa.get("round_delta")),
        })

    df = pd.DataFrame(rows)
    df.to_csv(OUT / "03_identity_full.csv", index=False, encoding="utf-8-sig")

    # ══════════ 链路完整性 ══════════
    filled = df[df["executed"] == True]
    unfilled = df[df["executed"] == False]
    no_rep = df[df["executed"].isna()]

    # 净值恒等式
    resid = filled["net_identity_residual"].dropna()
    identity_ok = int((resid.abs() < 1e-9).sum())
    identity_bad = int((resid.abs() >= 1e-9).sum())

    # 滑点不重复扣减的核验：
    #   ① 成交价（final）已含滑点与取整：final == 取整(post_slip)
    #   ② net_pnl 只减 fees 与 funding，没有再减 slippage
    #   ③ 若把 slippage 再减一次，净值会偏离多少（反事实）
    entry_incl = int(((filled["entry_post_round_price"] - filled["entry_price"]).abs() < 1e-12).sum()) if len(filled) else 0
    exit_incl = int(((filled["exit_post_round_price"] - filled["exit_price"]).abs() < 1e-12).sum()) if len(filled) else 0
    # 滑点是否真的发生在成交价里（raw → post_slip 有位移）
    entry_slip_moved = int((filled["entry_slip_delta"].abs() > 0).sum()) if len(filled) else 0
    exit_slip_moved = int((filled["exit_slip_delta"].abs() > 0).sum()) if len(filled) else 0
    slip_present = int(filled["slippage_cost"].notna().sum()) if len(filled) else 0
    double_deduct_gap = float(filled["slippage_cost"].fillna(0).sum()) if len(filled) else 0.0

    # 费用符号一致性：net = gross - fees - funding，若 funding 为负（收入）则 net 应比 gross-fees 更高
    fund_neg = int((filled["funding_cost"].fillna(0) < -1e-12).sum()) if len(filled) else 0
    fund_pos = int((filled["funding_cost"].fillna(0) > 1e-12).sum()) if len(filled) else 0

    # 逐笔链完整性（成交侧必须全有）
    chain_fields = ["order_id", "fill_time_ms", "entry_price", "exit_time_ms",
                    "exit_price", "fee_paid", "funding_cost", "net_pnl"]
    chain_missing = {}
    for f in chain_fields:
        chain_missing[f] = int(filled[f].isna().sum()) if len(filled) else 0

    chain = {
        "audit": "ENTRY-AUDIT-03",
        "rows_total": int(len(df)),
        "by_generation": df["generation"].value_counts().to_dict(),
        "executed_true": int(len(filled)),
        "executed_false": int(len(unfilled)),
        "no_replay_evidence": int(len(no_rep)),
        "by_generation_executed": (filled["generation"].value_counts().to_dict() if len(filled) else {}),
        "identity_chain_fields_missing_on_filled": chain_missing,
        "net_identity": {
            "formula": "net_pnl = gross_pnl − fee_paid − funding_cost",
            "checked": int(len(resid)),
            "ok_within_1e-9": identity_ok,
            "violated": identity_bad,
            "max_abs_residual": float(resid.abs().max()) if len(resid) else None,
        },
        "slippage_not_double_counted": {
            "principle": ("滑点与取整已经写进成交价（entry_price / exit_price 都是取整后的最终价），"
                          "所以 net_pnl 里只减 fees 与 funding，不再单独减 slippage"),
            "entry_price_equals_post_round_audit_price": entry_incl,
            "exit_price_equals_post_round_audit_price": exit_incl,
            "entry_slippage_moved_price": entry_slip_moved,
            "exit_slippage_moved_price": exit_slip_moved,
            "chain_rule": ("raw_price → post_slip_price（滑点）→ post_round_price（取整）= 成交价；"
                           "成交价已含滑点与取整，故 net_pnl 只再减 fees 与 funding"),
            "filled_with_slippage_recorded": slip_present,
            "counterfactual_if_slippage_deducted_again_usdt": round(double_deduct_gap, 6),
            "counterfactual_note": ("把 slippage 再减一次会让全样本净盈亏额外少这么多 USDT，"
                                    "即重复扣减的量级；本轮没有扣"),
        },
        "funding_sign": {
            "convention": "正=支出（付资金费），负=收入（收资金费）",
            "filled_with_positive_cost": fund_pos,
            "filled_with_negative_income": fund_neg,
            "net_sum": round(float(filled["net_pnl"].fillna(0).sum()), 6) if len(filled) else 0.0,
            "gross_sum": round(float(filled["gross_pnl"].fillna(0).sum()), 6) if len(filled) else 0.0,
            "fee_sum": round(float(filled["fee_paid"].fillna(0).sum()), 6) if len(filled) else 0.0,
            "funding_sum": round(float(filled["funding_cost"].fillna(0).sum()), 6) if len(filled) else 0.0,
            "sum_check": (round(float(filled["gross_pnl"].fillna(0).sum()
                                   - filled["fee_paid"].fillna(0).sum()
                                   - filled["funding_cost"].fillna(0).sum()), 6) if len(filled) else 0.0),
        },
    }
    with open(OUT / "03_identity_chain.json", "w", encoding="utf-8") as f:
        json.dump(chain, f, ensure_ascii=False, indent=2)

    # ══════════ 三态分布 ══════════
    def vc(col):
        return df[col].value_counts(dropna=False).to_dict()

    unusable = df[df["research_usability"] == "not_usable"]
    summary = {
        "audit": "ENTRY-AUDIT-03",
        "spec_qualification_counts": vc("spec_qualification"),
        "formal_pass_allowed_counts": vc("formal_pass_allowed"),
        "exec_validity_counts": vc("exec_validity"),
        "research_usability_counts": vc("research_usability"),
        "research_usability_by_generation": (
            pd.crosstab(df["generation"], df["research_usability"]).to_dict()),
        "not_usable_detail": unusable[["candidate_id", "symbol", "usability_reasons"]]
            .to_dict("records")[:200],
        "not_usable_count": int(len(unusable)),
        "caveat_reason_histogram": (filled["usability_reasons"].value_counts().to_dict()
                                    if len(filled) else {}),
        "note": ("research_usability 只看「这条记录能不能当研究样本用」，"
                 "与「这笔交易赚不赚钱」无关。usable_with_caveat 的主体原因是"
                 "「历史合约规格不可证（unverified_historical）」与「PIT 未证」，"
                 "这两条是全池共性，不是个别记录的错。"),
    }
    with open(OUT / "03_usability_summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print(json.dumps({k: summary[k] for k in
                      ("spec_qualification_counts", "exec_validity_counts",
                       "research_usability_counts", "not_usable_count")},
                     ensure_ascii=False, indent=1))
    print(json.dumps(chain["net_identity"], ensure_ascii=False))
    print(json.dumps(chain["slippage_not_double_counted"], ensure_ascii=False))
    print(json.dumps(chain["funding_sign"], ensure_ascii=False))


if __name__ == "__main__":
    main()
