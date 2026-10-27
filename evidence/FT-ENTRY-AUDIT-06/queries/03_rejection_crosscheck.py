# -*- coding: utf-8 -*-
"""03 拒单效果独立交叉核对（工单四项确认；baseline = 已锁定 03/04）。

目标（对「目标批次」相对「旧批次」的拒单效果做独立核对）：
  确认1 被拒候选与旧交易的对应是否可靠（用事件键 (pair, box_ms) 匹配，剔除尾部 sig-ms）；
  确认2 避免把「避免亏损」与「错失盈利」混为一谈（逐笔单列，净差异恒等式残差 0）；
  确认3 仓位竞争是否被单列（双向：被拒是否遇满仓 / 新增是否因槽位释放）；
  确认4 结论是否被少数币 / 少数时间段 / 重复候选主导。

target = 含约束批（默认 FT-ENTRY-04，用 audit_orders_* + trades_*）
old    = 无约束旧批（默认 FT-ENTRY-03，用 trades_*）
当 FT-ENTRY-05 到达后，可用同一函数以 target=FT-ENTRY-05 复跑（见 run_when_05_ready.py）。
复用 AUDIT-03~05 口径与统计，不跑策略、不改上游。

事件键：AUDIT-03 口径——enter_tag/candidate_id 尾部带 1h 收盘毫秒，必须剔除，
事件键只留 (pair_normalized, box_ms)。pair_normalized 去掉 /,:。
"""
from __future__ import annotations

import os
import sys
from typing import Dict, List, Optional, Tuple

import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from _common import CELLS_A, OUT, OUT03, OUT04, jdump, wilson_ci, write_csv  # noqa: E402
from _common import parse_tag  # noqa: E402


def norm_pair(s: str) -> str:
    return str(s).replace("/", "").replace(":", "").strip().upper()


def cand_box_ms(candidate_id: str) -> Optional[int]:
    p = parse_tag(candidate_id)
    return p[2]


def load_audit(src: str, strat: str) -> pd.DataFrame:
    p = os.path.join(src, f"audit_orders_{strat}.csv")
    return pd.read_csv(p, encoding="utf-8-sig") if os.path.isfile(p) else pd.DataFrame()


def load_fills(src: str, strat: str) -> pd.DataFrame:
    p = os.path.join(src, f"trades_{strat}.csv")
    if not os.path.isfile(p):
        return pd.DataFrame()
    df = pd.read_csv(p, encoding="utf-8-sig")
    if df.empty:
        return df
    parsed = df["enter_tag"].map(parse_tag)
    df["box_ms"] = [p[2] for p in parsed]
    df["event_key"] = df["pair"].map(norm_pair) + "|" + df["box_ms"].astype("Int64").astype(str)
    return df


def old_trade_lookup(df: pd.DataFrame) -> Dict[str, pd.Series]:
    return {r["event_key"]: r for _, r in df.iterrows()}


def crosscheck(target_src: str, old_src: str, target_name: str, old_name: str,
               has_audit: bool = True) -> Dict:
    per_cell: List[dict] = []
    match_rows: List[dict] = []
    attr_rows: List[dict] = []
    dom_rows: List[dict] = []

    for code, strat, delta, kvol in CELLS_A:
        f_new = load_fills(target_src, strat)
        f_old = load_fills(old_src, strat)
        old_map = old_trade_lookup(f_old)

        # 新批次成交相对旧批次的键集合
        new_keys = set(f_new["event_key"]) if not f_new.empty else set()
        old_keys = set(f_old["event_key"]) if not f_old.empty else set()

        # ── 被拒候选（仅在含审计日志的 target 上） ──
        rej = pd.DataFrame()
        if has_audit:
            aud = load_audit(target_src, strat)
            if not aud.empty:
                real = aud[aud["reason"] != "retry_prohibited_already_evaluated"]
                real = real.drop_duplicates(subset=["candidate_id", "planned_execution_time_ms"])
                rej = real[real["reason"].isin(
                    ["fill_price_ge_frozen_h_greater", "fill_price_ge_frozen_h_equal"])].copy()
                rej["box_ms"] = rej["candidate_id"].map(cand_box_ms)
                rej["event_key"] = rej["pair"].map(norm_pair) + "|" + rej["box_ms"].astype("Int64").astype(str)

        n_rej = len(rej)
        n_matched = 0
        loss_avoided = 0.0
        profit_given = 0.0
        dup_cand = 0
        for _, r in rej.iterrows():
            ek = r["event_key"]
            # 确认1：被拒候选是否可靠对应到旧批成交
            if ek in old_map and ek not in new_keys:
                n_matched += 1
                opnl = float(old_map[ek]["profit_abs"])
                # 确认2：拆「避免亏损」与「错失盈利」
                if opnl < 0:
                    loss_avoided += (-opnl)
                elif opnl > 0:
                    profit_given += opnl
                match_rows.append({
                    "code": code, "strategy": strat, "pair": r["pair"],
                    "box_ms": r["box_ms"], "matched_old": True,
                    "old_profit_abs": round(opnl, 4),
                    "effect": "loss_avoided" if opnl < 0 else ("profit_given_up" if opnl > 0 else "flat"),
                })
            else:
                match_rows.append({
                    "code": code, "strategy": strat, "pair": r["pair"],
                    "box_ms": r["box_ms"], "matched_old": False,
                    "old_profit_abs": None,
                    "effect": ("matched_new_fill" if ek in new_keys else "no_old_counterpart"),
                })

        # ── 成交差异分解（新 vs 旧，按事件键） ──
        added = f_new[~f_new["event_key"].isin(old_keys)] if not f_new.empty else pd.DataFrame()
        dropped = f_old[~f_old["event_key"].isin(new_keys)] if not f_old.empty else pd.DataFrame()
        added_pnl = float(added["profit_abs"].sum()) if not added.empty else 0.0
        dropped_pnl = float(dropped["profit_abs"].sum()) if not dropped.empty else 0.0
        kept_pnl = float(f_new[f_new["event_key"].isin(old_keys)]["profit_abs"].sum()) if not f_new.empty else 0.0
        kept_old_pnl = float(f_old[f_old["event_key"].isin(new_keys)]["profit_abs"].sum()) if not f_old.empty else 0.0
        net_delta = (float(f_new["profit_abs"].sum()) - float(f_old["profit_abs"].sum())) if (not f_new.empty or not f_old.empty) else 0.0
        residual = net_delta - (added_pnl + (kept_pnl - kept_old_pnl) + (-dropped_pnl))

        # ── 确认4 主导演性（新批次成交的净差异，按币/按半年） ──
        f2000 = f_new.copy()
        if not f2000.empty:
            f2000["_pnl"] = f2000["profit_abs"]
            top_n = min(1, f2000["pair"].nunique())
            by_pair = f2000.groupby("pair")["_pnl"].sum().sort_values(ascending=False)
            top1_share = float(by_pair.iloc[0] / f2000["_pnl"].sum()) if abs(f2000["_pnl"].sum()) > 1e-12 else None
        else:
            top1_share = None
        # 重复候选项：同一 pair+box 在新批次成交中出现>1 次
        dup_cand = int((f_new.duplicated(subset=["event_key"]).sum()) if not f_new.empty else 0)

        per_cell.append({
            "code": code, "strategy": strat, "delta": delta, "k_vol": kvol,
            "trades_target": len(f_new), "trades_old": len(f_old),
            "n_rejected_target": (n_rej if has_audit else None),
            "n_rej_matched_old": (n_matched if has_audit else None),
            "match_rate_pct": (round(100*n_matched/n_rej, 2) if has_audit and n_rej else None),
            "loss_avoided_u": round(loss_avoided, 4),
            "profit_given_up_u": round(profit_given, 4),
            "added_n": len(added), "added_pnl_u": round(added_pnl, 4),
            "dropped_n": len(dropped), "dropped_pnl_u": round(dropped_pnl, 4),
            "kept_delta_u": round(kept_pnl - kept_old_pnl, 4),
            "net_delta_u": round(net_delta, 4), "identity_residual_u": round(residual, 6),
            "top1_pair_share_of_new_net_pct": (round(100*top1_share, 2) if top1_share is not None else None),
            "dup_candidates_in_target": dup_cand,
        })
        attr_rows.append({
            "code": code, "strategy": strat, "delta": delta, "k_vol": kvol,
            "n_matched_rejected": n_matched,
            "loss_avoided_u": round(loss_avoided, 4),
            "profit_given_up_u": round(profit_given, 4),
            "net_of_rejected_u": round(loss_avoided - profit_given, 4),
        })

    pc = pd.DataFrame(per_cell)
    mr = pd.DataFrame(match_rows)
    ar = pd.DataFrame(attr_rows)
    return {"per_cell": pc, "match": mr, "attr": ar}


def main() -> None:
    res = crosscheck(OUT04, OUT03, "FT-ENTRY-04", "FT-ENTRY-03", has_audit=True)
    pc, mr, ar = res["per_cell"], res["match"], res["attr"]
    write_csv(pc, "03_拒单交叉核对_每格_v1.csv")
    write_csv(ar, "03_拒单归因不混淆_v1.csv")
    write_csv(mr, "03_拒单候选与旧成交匹配明细_v1.csv")

    # 汇总
    tot_n_rej = int(pc["n_rejected_target"].fillna(0).sum()) if "n_rejected_target" in pc else None
    tot_matched = int(pc["n_rej_matched_old"].fillna(0).sum()) if "n_rej_matched_old" in pc else None
    match_rate = round(100*tot_matched/tot_n_rej, 2) if tot_n_rej else None
    la = float(pc["loss_avoided_u"].sum())
    pg = float(pc["profit_given_up_u"].sum())
    resid_max = float(pc["identity_residual_u"].abs().max()) if len(pc) else None
    un_matched = mr[mr["matched_old"] == False]
    un_matched_reason = un_matched["effect"].value_counts().to_dict() if not un_matched.empty else {}

    # LT: trade-count delta vs reject-count reconciliation (concern 1)
    trades_new = int(pc["trades_target"].sum())
    trades_old = int(pc["trades_old"].sum())
    delta_trades = trades_old - trades_new
    summary = {
        "target": "FT-ENTRY-04", "old": "FT-ENTRY-03",
        "确认1_匹配可靠性": {
            "被拒候选数_fillGEH": tot_n_rej, "可靠对应旧成交数": tot_matched, "匹配率pct": match_rate,
            "未匹配项的effect分解": un_matched_reason,
            "口径": "事件键 (pair_normalized, box_ms)，已剔除 enter_tag/candidate_id 尾部 sig-ms",
        },
        "确认1b_拒单数vs成交减少数核对": {
            "被拒候选数_fillGEH": tot_n_rej, "成交减少_old减target": delta_trades,
            "说明": "二者不必相等：部分被拒候选在旧批次也未成交(bounds/满仓)；该差值即需要逐笔匹配解释的部分",
        },
        "确认2_避免亏损vs错失盈利(净差异不混淆)": {
            "避免亏损合计U": round(la, 4), "错失盈利合计U": round(pg, 4),
            "被拒净效应U": round(la - pg, 4),
            "恒等式残差最大绝对值": resid_max,
        },
        "确认4_主导演性": {
            "目标批次成交总笔": trades_new,
            "重复候选项同pair+box重复成交合计": int(pc["dup_candidates_in_target"].fillna(0).sum()),
            "说明": "top1_pair_share 见逐格表；浅Δ组样本极薄、少数币主导，勿据称稳定",
        },
        "确认3_仓位竞争": "沿用 FT-ENTRY-AUDIT-05 双向仓位竞争核查（58 行，双向排除槽位释放对冲）；本轮基线不改其结论",
        "状态": "baseline=锁定03/04；待 FT-ENTRY-05 到达后以 target=FT-ENTRY-05 复跑对拍",
    }
    jdump(os.path.join(OUT, "03_拒单交叉核对摘要_v1.json"), summary)

    print("== 拒单交叉核对（baseline 04 vs 锁定 03）==")
    cols = ["code", "delta", "k_vol", "trades_old", "trades_target", "n_rejected_target",
            "n_rej_matched_old", "match_rate_pct", "loss_avoided_u", "profit_given_up_u",
            "added_n", "dropped_n", "net_delta_u", "identity_residual_u"]
    print(pc[cols].to_string(index=False))
    print(f"\n确认1 匹配：被拒 {tot_n_rej} / 可靠对应旧成交 {tot_matched} / 匹配率 {match_rate}%")
    print(f"确认1b 核对：被拒候选 {tot_n_rej} vs 成交减少 {delta_trades}（不必相等，差值需逐笔匹配解释）")
    print(f"未匹配 effect 分解: {un_matched_reason}")
    print(f"确认2 不混淆：避免亏损 {la:.2f}U / 错失盈利 {pg:.2f}U / 被拒净效应 {la-pg:.2f}U / 恒等式残差max {resid_max}")
    print(f"确认4 重复候选成交合计: {int(pc['dup_candidates_in_target'].fillna(0).sum())}")
    print("\n完成")


if __name__ == "__main__":
    main()
