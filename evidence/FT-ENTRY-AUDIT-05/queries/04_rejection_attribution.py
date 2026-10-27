# -*- coding: utf-8 -*-
"""04 拒单归因与仓位竞争双向检查（工单第 4 条）。

目标：
1. 拆解被拒交易的盈亏：损失避免（拒掉的亏损）vs 盈利损失（拒掉的盈利），
   两者必须单列，不得只报净值；
2. 单列「仓位竞争影响」：不得把全部差异归因于过滤效果。
   双向检查（沿用 FT-FACTOR-AUDIT-01 的教训，slot_blocked_trades 是单向缺陷）：
     方向甲：被拒交易在 03 中是否遇满仓（被拒后是否释放了槽位给别的候选）？
     方向乙：04 中新增/保留的成交，是否因为 03 中槽位更满而本不该出现？
   逐笔对应主键必须用 enter_tag（候选事件 ID），禁止用 (pair, open_timestamp)；
3. 净差异的分解恒等式：
     Δ净收益(03→04) = 拒单交易的盈亏（取负） + 新增交易的盈亏 + 保留交易的变化
   残差必须为 0（允许 1e-6 级浮点误差）。

只读，不重跑回测。
"""
from __future__ import annotations

import os
import sys
from typing import List

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from _common import (  # noqa: E402
    BASELINE, CELLS_A, OUT, OUT03, OUT04, jdump, load_trades, write_csv,
)

MAX_OPEN_TRADES = 3
START_MS = 1638316800000
END_MS = 1735689600000


def slots_at(df: pd.DataFrame, ts: int) -> int:
    """在某时刻已占用的槽位数：开仓 <= ts 且尚未平仓（严格 < ts 视为已平仓）。"""
    if df.empty:
        return 0
    opened = df[(df["open_ts"] <= ts) & (df["close_ts"] > ts)]
    return len(opened)


def main() -> None:
    detail_rows: List[dict] = []
    attribution_rows: List[dict] = []
    competition_rows: List[dict] = []
    identity_rows: List[dict] = []

    for code, strat, delta, kvol in [("BASE", BASELINE, 0.0, 0.0)] + CELLS_A:
        d3 = load_trades(strat, OUT03)
        d4 = load_trades(strat, OUT04)
        if d3.empty or d4.empty:
            continue

        t3 = set(d3["enter_tag"].astype(str))
        t4 = set(d4["enter_tag"].astype(str))
        rejected_tags = t3 - t4          # 03 有、04 无 ⇒ 被拒
        added_tags = t4 - t3             # 04 有、03 无 ⇒ 04 新增
        kept_tags = t3 & t4              # 两边都有

        rej = d3[d3["enter_tag"].astype(str).isin(rejected_tags)].copy()
        add = d4[d4["enter_tag"].astype(str).isin(added_tags)].copy()
        kept3 = d3[d3["enter_tag"].astype(str).isin(kept_tags)].copy()
        kept4 = d4[d4["enter_tag"].astype(str).isin(kept_tags)].copy()

        # ── 1. 拒单交易盈亏拆解（单列：损失避免 vs 盈利损失）──
        rej_wins = rej[rej["profit_abs"] > 0]
        rej_losses = rej[rej["profit_abs"] < 0]
        loss_avoided = float(-rej_losses["profit_abs"].sum()) if len(rej_losses) else 0.0
        profit_given_up = float(rej_wins["profit_abs"].sum()) if len(rej_wins) else 0.0
        rej_net = float(rej["profit_abs"].sum()) if len(rej) else 0.0

        attribution_rows.append({
            "code": code, "strategy": strat, "route": "Baseline" if code == "BASE" else "A",
            "delta_atr": delta, "k_vol": kvol,
            "trades_03": len(d3), "trades_04": len(d4),
            "n_rejected": len(rej), "n_added": len(add), "n_kept": len(kept_tags),
            "rejected_win_rate_pct": round(100.0 * len(rej_wins) / len(rej), 4) if len(rej) else None,
            "rejected_avg_u": round(rej_net / len(rej), 4) if len(rej) else None,
            "loss_avoided_u": round(loss_avoided, 4),
            "profit_given_up_u": round(profit_given_up, 4),
            "rejected_net_u": round(rej_net, 4),
            "net_effect_of_rejection_u": round(-rej_net, 4),
            "added_net_u": round(float(add["profit_abs"].sum()), 4) if len(add) else 0.0,
            "kept_net_u_03": round(float(kept3["profit_abs"].sum()), 4) if len(kept3) else 0.0,
            "kept_net_u_04": round(float(kept4["profit_abs"].sum()), 4) if len(kept4) else 0.0,
            "kept_delta_u": round(float(kept4["profit_abs"].sum() - kept3["profit_abs"].sum()), 4)
            if len(kept3) else None,
        })

        # ── 2. 恒等式分解 ──
        total_d = float(d4["profit_abs"].sum() - d3["profit_abs"].sum())
        lhs = (-rej_net
               + (float(add["profit_abs"].sum()) if len(add) else 0.0)
               + (float(kept4["profit_abs"].sum() - kept3["profit_abs"].sum()) if len(kept3) else 0.0))
        identity_rows.append({
            "code": code, "strategy": strat,
            "total_delta_net_u": round(total_d, 4),
            "decomp_rejected_u": round(-rej_net, 4),
            "decomp_added_u": round(float(add["profit_abs"].sum()), 4) if len(add) else 0.0,
            "decomp_kept_delta_u": round(float(kept4["profit_abs"].sum() - kept3["profit_abs"].sum()), 4)
            if len(kept3) else 0.0,
            "lhs_u": round(lhs, 4),
            "residual_u": round(total_d - lhs, 6),
            "identity_holds": bool(abs(total_d - lhs) < 1e-6),
        })

        # ── 3. 仓位竞争双向检查 ──
        # 方向甲：被拒交易在 03 中开仓时刻的槽位占用
        for _, r in rej.iterrows():
            occ3 = slots_at(d3, int(r["open_ts"]))
            occ4 = slots_at(d4, int(r["open_ts"]))
            # 04 中在该时刻之后是否出现了新补入的成交（被拒释放的槽位可能被占用）
            after_add = add[add["open_ts"] > r["open_ts"]]
            nearest_add = (int((after_add["open_ts"] - r["open_ts"]).min())
                           if len(after_add) else None)
            competition_rows.append({
                "code": code, "strategy": strat, "direction": "甲_被拒交易",
                "pair": r["pair"], "enter_tag": r["enter_tag"], "event_id": r["event_id"],
                "open_ts": r["open_ts"], "profit_abs": r["profit_abs"],
                "slots_occupied_03": occ3, "slots_occupied_04": occ4,
                "was_slot_full_03": bool(occ3 >= MAX_OPEN_TRADES),
                "slot_released_yes": bool(occ3 >= MAX_OPEN_TRADES),
                "nearest_added_fill_ms_after": nearest_add,
                "interpretation": ("被拒时槽位已满 ⇒ 拒单释放的槽位可能被其它候选补入，"
                                   "这部分收益不属于过滤效果" if occ3 >= MAX_OPEN_TRADES
                                   else "被拒时槽位未满 ⇒ 不涉及槽位再分配"),
            })

        # 方向乙：04 中新增的成交（03 中不存在），检查其开仓时刻在 03 中是否因满仓而不存在
        for _, r in add.iterrows():
            occ3 = slots_at(d3, int(r["open_ts"]))
            occ4 = slots_at(d4, int(r["open_ts"]))
            competition_rows.append({
                "code": code, "strategy": strat, "direction": "乙_04新增成交",
                "pair": r["pair"], "enter_tag": r["enter_tag"], "event_id": r["event_id"],
                "open_ts": r["open_ts"], "profit_abs": r["profit_abs"],
                "slots_occupied_03": occ3, "slots_occupied_04": occ4,
                "was_slot_full_03": bool(occ3 >= MAX_OPEN_TRADES),
                "slot_released_yes": bool(occ3 >= MAX_OPEN_TRADES),
                "nearest_added_fill_ms_after": None,
                "interpretation": ("03 中该时刻槽位已满 ⇒ 该笔是因拒单释放槽位而补入，"
                                   "其收益应归因于槽位再分配而非价格过滤" if occ3 >= MAX_OPEN_TRADES
                                   else "03 中该时刻槽位未满 ⇒ 需查信号层差异（非槽位竞争）"),
            })

        # ── 4. 逐笔明细（供人工复核）──
        for _, r in rej.iterrows():
            detail_rows.append({
                "code": code, "strategy": strat, "status": "REJECTED(04无/03有)",
                "pair": r["pair"], "enter_tag": r["enter_tag"], "event_id": r["event_id"],
                "open_ts": r["open_ts"], "close_ts": r["close_ts"],
                "open_rate": r["open_rate"], "profit_abs": r["profit_abs"],
                "is_win": bool(r["is_win"]),
            })
        for _, r in add.iterrows():
            detail_rows.append({
                "code": code, "strategy": strat, "status": "ADDED(04有/03无)",
                "pair": r["pair"], "enter_tag": r["enter_tag"], "event_id": r["event_id"],
                "open_ts": r["open_ts"], "close_ts": r["close_ts"],
                "open_rate": r["open_rate"], "profit_abs": r["profit_abs"],
                "is_win": bool(r["is_win"]),
            })

    att = pd.DataFrame(attribution_rows)
    write_csv(att, "04_拒单归因与净差异分解_v1.csv")
    print("== 4.1 拒单归因（损失避免 vs 盈利损失）==")
    if not att.empty:
        print(att[["code", "trades_03", "trades_04", "n_rejected", "n_added", "n_kept",
                   "rejected_win_rate_pct", "loss_avoided_u", "profit_given_up_u",
                   "rejected_net_u", "net_effect_of_rejection_u", "added_net_u",
                   "kept_delta_u"]].to_string(index=False))

    ident = pd.DataFrame(identity_rows)
    write_csv(ident, "04_净差异恒等式核验_v1.csv")
    print("\n== 4.2 净差异恒等式（残差应恒为 0）==")
    if not ident.empty:
        print(ident.to_string(index=False))

    comp = pd.DataFrame(competition_rows)
    write_csv(comp, "04_仓位竞争双向检查_v1.csv")
    print(f"\n== 4.3 仓位竞争双向检查：{len(comp)} 条 ==")
    if not comp.empty:
        summ = comp.groupby(["code", "direction", "interpretation"]).size().reset_index(name="n")
        print(summ.to_string(index=False))

    det = pd.DataFrame(detail_rows)
    write_csv(det, "04_新旧逐笔差异明细_v1.csv")

    jdump(os.path.join(OUT, "04_归因摘要_v1.json"), {
        "attribution": att.to_dict("records") if not att.empty else [],
        "identity": ident.to_dict("records") if not ident.empty else [],
        "n_competition_rows": len(comp),
        "identity_all_hold": bool(ident["identity_holds"].all()) if not ident.empty else None,
        "notes": [
            "逐笔对应主键使用 enter_tag（候选事件 ID），不使用 (pair, open_timestamp)",
            "仓位竞争检查是双向的：既查被拒交易是否遇满仓，也查 04 新增成交是否因 03 满仓才产生",
            "槽位数按 03/04 各自的实际成交集合实时计算，MAX_OPEN_TRADES=3",
        ],
    })
    print("\n完成")


if __name__ == "__main__":
    main()
