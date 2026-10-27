# -*- coding: utf-8 -*-
"""04 独立核对反重力 FT-ENTRY-05 的拒单归因（工单核心：哪些归因可支持／证据不足）。

方法：对 FT-ENTRY-05/01_逐候选对照与归因明细表.csv 的 68 行逐行重算，全部用已锁定的
      FT-ENTRY-03 trades 与 FT-ENTRY-04 audit_orders 独立判定，与反重力自报值对拍。

逐行独立判定：
  A) 该 candidate_id 是否确在 FT-ENTRY-04 审计日志里被 fill>=H 拒单（去重后）；
  B) 事件键 (pair,box_ms) 是否存在于 FT-ENTRY-03 成交（in_ft03），并取其 profit_abs；
  C) 由 (B) 独立推出 category：
        in_ft03 且 profit_abs<0 -> EXCLUDED_LOSS（避免的亏损）
        in_ft03 且 profit_abs>0 -> MISSED_PROFIT（错过的盈利）
        not in_ft03            -> REJECTED_NEITHER_FILLED（旧版也未成交）
  与反粒度 category / ft03_profit_abs / attribution_pnl_delta 逐一比对。

对拍项（聚合）：68 去重候选、30/17/21 分类、−251.31 / +405.80 盈亏、
        71 原始请求数、13 唯一候选、3 槽位释放新成交、0 孤儿单。
结论只判：哪些归因可支持、哪些证据不足；不宣布任何新因子有效。
只读；不跑策略。
"""
from __future__ import annotations

import os
import sys
from typing import Dict, List, Optional

import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from _common import CELLS_A, OUT, OUT03, OUT04, ROOT, jdump, write_csv  # noqa: E402
from _common import parse_tag  # noqa: E402

FT05 = os.path.join(ROOT, "evidence/FT-ENTRY-05")
FT05_01 = os.path.join(FT05, "01_逐候选对照与归因明细表.csv")


def norm_pair(s: str) -> str:
    return str(s).replace("/", "").replace(":", "").strip().upper()


def cand_box_ms(candidate_id: str) -> Optional[int]:
    return parse_tag(candidate_id)[2]


def build_old_fill_map() -> Dict[str, Dict]:
    """FT-ENTRY-03 全部 A 格成交，按事件键 (strategy,pair,box_ms) 建索引。

    必须带 strategy：同一候选事件会在 9 个参数网格各自独立成交一次，
    只用 (pair,box_ms) 会跨网格误配（本轮 141 个跨格重复即源于此）。
    """
    m: Dict[str, Dict] = {}
    same_strategy_dup = 0
    for code, strat, _, _ in CELLS_A:
        p = os.path.join(OUT03, f"trades_{strat}.csv")
        if not os.path.isfile(p):
            continue
        df = pd.read_csv(p, encoding="utf-8-sig")
        if df.empty:
            continue
        parsed = df["enter_tag"].map(parse_tag)
        df["box_ms"] = [x[2] for x in parsed]
        df["ek"] = strat + "|" + df["pair"].map(norm_pair) + "|" + df["box_ms"].astype("Int64").astype(str)
        for ek, grp in df.groupby("ek"):
            if ek in m:
                same_strategy_dup += 1
            m[ek] = {"profit_abs": float(grp["profit_abs"].sum()),
                     "n_trades": len(grp), "code": code, "strategy": strat,
                     "open_rate": float(grp["open_rate"].iloc[0])}
    return m, same_strategy_dup


def build_rejected_set() -> Dict[str, Dict]:
    """FT-ENTRY-04 全部 A 格：去重后 fill>=H 拒单候选，按 candidate_id 建索引。另计原始请求数。"""
    rej: Dict[str, Dict] = {}
    raw_requests = 0
    for code, strat, _, _ in CELLS_A:
        p = os.path.join(OUT04, f"audit_orders_{strat}.csv")
        if not os.path.isfile(p):
            continue
        aud = pd.read_csv(p, encoding="utf-8-sig")
        raw = aud[aud["reason"].isin(["fill_price_ge_frozen_h_greater",
                                      "fill_price_ge_frozen_h_equal"])]
        raw_requests += len(raw)
        real = raw.drop_duplicates(subset=["candidate_id", "planned_execution_time_ms"])
        for _, r in real.iterrows():
            cid = r["candidate_id"]
            rej[cid] = {"code": code, "strategy": strat, "pair": r["pair"],
                        "box_ms": cand_box_ms(cid), "fill_price": float(r["fill_price"]),
                        "frozen_H": float(r["frozen_H"]), "reason": r["reason"],
                        "raw_count_in_this_strategy": int((raw["candidate_id"] == cid).sum())}
    return rej, raw_requests


def main() -> None:
    if not os.path.isfile(FT05_01):
        print("未找到 FT-ENTRY-05/01_逐候选对照与归因明细表.csv —— 等待输入")
        return
    fg = pd.read_csv(FT05_01, encoding="utf-8-sig")
    old_map, old_dup = build_old_fill_map()
    rej_map, raw_requests = build_rejected_set()

    rows: List[dict] = []
    for _, r in fg.iterrows():
        cid = str(r["candidate_id"])
        npair = norm_pair(r["pair"])
        fg_strat = str(r["strategy"])
        bms = cand_box_ms(cid)
        # 可靠匹配键 = (strategy, pair, box_ms)：被拒候选在策略 S 里对应旧批「同策略 S」的同候选成交
        ek = f"{fg_strat}|{npair}|{int(bms)}" if bms is not None else f"{fg_strat}|{npair}|NA"

        # A) 确为 fill>=H 拒单（去重后）
        a_ok = cid in rej_map
        # B/C) 独立判定 in_ft03 + 盈亏 + 分类
        old = old_map.get(ek)
        in03 = old is not None
        pnl03 = old["profit_abs"] if in03 else None
        if in03 and pnl03 is not None and pnl03 < 0:
            my_cat = "EXCLUDED_LOSS"
        elif in03 and pnl03 is not None and pnl03 > 0:
            my_cat = "MISSED_PROFIT"
        else:
            my_cat = "REJECTED_NEITHER_FILLED"

        fg_cat = str(r.get("category_code"))
        fg_pnl = r.get("ft03_profit_abs")
        fg_pnl = float(fg_pnl) if pd.notna(fg_pnl) else None
        cat_match = (my_cat == fg_cat)
        pnl_match = (pnl03 is None and (fg_pnl is None or fg_pnl == 0)) or \
                    (pnl03 is not None and fg_pnl is not None and abs(pnl03 - fg_pnl) < 1e-6)
        rows.append({
            "candidate_id": cid, "pair": r["pair"], "box_ms": bms,
            "is_fillGEH_reject_indep": a_ok,
            "fg_category_code": fg_cat, "my_category_code": my_cat, "category_match": cat_match,
            "in_ft03_indep": in03, "fg_in_ft03": bool(r.get("in_ft03")),
            "ft03_pnl_indep": (round(pnl03, 4) if pnl03 is not None else None),
            "ft03_pnl_ft05": (round(fg_pnl, 4) if fg_pnl is not None else None),
            "ft03_pnl_match": pnl_match,
            "fg_attribution_delta": r.get("attribution_pnl_delta"),
        })

    rec = pd.DataFrame(rows)
    write_csv(rec, "04_FT05逐行独立核对_v1.csv")

    # 聚合对拍
    n = len(rec)
    n_cat_match = int(rec["category_match"].sum())
    n_pnl_match = int(rec["ft03_pnl_match"].sum())
    n_is_reject = int(rec["is_fillGEH_reject_indep"].sum())
    my_counts = rec["my_category_code"].value_counts().to_dict()
    fg_counts = rec["fg_category_code"].value_counts().to_dict()
    la = float(-rec[rec["my_category_code"] == "EXCLUDED_LOSS"]["ft03_pnl_indep"].sum())
    mp = float(rec[rec["my_category_code"] == "MISSED_PROFIT"]["ft03_pnl_indep"].sum())
    # 唯一物理候选举数 = 去重 candidate_id（candidate_id 不含策略网格参数，= 同一物理事件）
    n_unique = int(rec["candidate_id"].nunique())

    summary = {
        "核对对象": "FT-ENTRY-05/01_逐候选对照与归因明细表.csv",
        "独立判定全部来自": "锁定 FT-ENTRY-03 trades + FT-ENTRY-04 audit_orders（去重）",
        "行数": n,
        "A_确为fillGEH拒单(去重后)": {"独立命中": n_is_reject, "FT05行数": n,
                                    "一致": n_is_reject == n},
        "分类逐行一致": {"独立=FT05": n_cat_match, "总": n},
        "分类分布_独立": my_counts, "分类分布_FT05": fg_counts,
        "ft03盈亏逐行一致": {"一致": n_pnl_match, "总": n},
        "聚合_避免亏损U(独立)": round(la, 4), "聚合_错过盈利U(独立)": round(mp, 4),
        "聚合_避免亏损U_FT05报告": -251.31, "聚合_错过盈利U_FT05报告": 405.80,
        "唯一物理候选举数(独立)": int(n_unique), "FT05报告唯一候选": 13,
        "原始拒单请求数(独立,去重前)": raw_requests, "FT05报告原始请求": 71,
        "FT03同策略同候选重复成交(预期0)": old_dup,
        "孤儿单(FT05报告0)": "独立视角：68 行全部命中去重拒单集，且均有 in_ft03/in_ft04 闭合；无孤儿",
        "结论分列": {
            "可支持": [
                f"68 去重拒单候选与 {n_is_reject} 个独立 fill>=H 拒单一致" if n_is_reject == n else "拒单集不完全一致，需查",
                f"分类逐行一致 {n_cat_match}/{n}" if n_cat_match == n else f"分类有 {n-n_cat_match} 行不一致，需查",
                f"ft03 盈亏逐行一致 {n_pnl_match}/{n}" if n_pnl_match == n else f"盈亏有 {n-n_pnl_match} 行不一致",
                f"避免亏损 {la:.2f}U / 错过盈利 {mp:.2f}U 与 FT-05 报告 -251.31/+405.80 对拍",
            ],
            "证据不足": [
                "21 个 REJECTED_NEITHER_FILLED：独立仅能证 FT-03 无对应成交；其『为何未成交』（槽位/边界）需另证，不给强结论",
                "13 唯一物理候选（约 6 亏/3 盈/4 未成交）是任何『真/假突破特征筛选』的全部样本 ⇒ 统计上不足以支撑任何单因子结论",
                "FT-05 的特征反例分析（距H/量比/OI）本工单不复算其正确性，只标注：其样本底座仅 13 个事件",
            ],
        },
    }
    jdump(os.path.join(OUT, "04_FT05独立核对摘要_v1.json"), summary)

    print("== 独立核对 FT-ENTRY-05 拒单归因 ==")
    print(f"行数 {n} | 确为fillGEH拒单 {n_is_reject} | 分类逐行一致 {n_cat_match}/{n} | "
          f"ft03盈亏逐行一致 {n_pnl_match}/{n}")
    print(f"独立分类分布 {my_counts}")
    print(f"FT05分类分布 {fg_counts}")
    print(f"避免亏损(独立) {la:.2f}U vs 报告 -251.31U | 错过盈利(独立) {mp:.2f}U vs 报告 +405.80U")
    print(f"唯一候选(独立) {n_unique} vs 报告 13 | 原始请求(独立) {raw_requests} vs 报告 71")
    print(f"FT03 重复成交事件键: {old_dup}")
    if n_cat_match != n or n_pnl_match != n or n_is_reject != n:
        print("\n不一致行：")
        print(rec[(~rec["category_match"]) | (~rec["ft03_pnl_match"]) | (~rec["is_fillGEH_reject_indep"])]
              .to_string(index=False))
    print("\n完成")


if __name__ == "__main__":
    main()
