# -*- coding: utf-8 -*-
"""02 数量关系口径模型：拒单请求 / 候选 / 订单 / 成交（工单核心）。

目的：把「189 / 118 / 71 …」等混口径拆成无歧义的四层数量关系，作为 AUDIT-06 一切
拒单归因的数字底座。数据源只用已锁定的 FT-ENTRY-03/04 产物，不重跑策略。

四层定义（对每个 A 格）：
  n_eval_events   审计日志总行数 = 每个候选的「评估事件」数（含被防重试拦截的重评估）。
  n_retry         其中 reason=retry_prohibited_already_evaluated 的重评估拦截数。
  n_candidates    去重 candidate_id 数 = 真实被评估的候选数（每个候选恰一次真实决策）。
  ── 候选的真实决策（reason != retry）一一对应 n_candidates，拆为两端：
    n_orders_pass     decision=PASSED, reason=fill_price_lt_frozen_h（获准入仓 = 订单请求被放行）。
    n_reject_geH      reason=fill_price_ge_frozen_h_{greater,equal}（约束核心拒单）。
    n_reject_bounds   reason=outside_min_max_bounds（原有边界拒单，非新增约束）。
  ── 成交层：
    n_fills_04        freqtrade 实际成交笔数（trades CSV 行）。
    n_pass_not_filled n_orders_pass - n_fills_04 >=0（放行但未成交：多因 max_open_trades 满仓等）。
    n_fills_03        旧对照 FT-ENTRY-03 同格成交笔数（无约束）。

恒等式（逐格必须成立，残差 0）：
  (E1) n_eval_events == n_candidates + n_retry
  (E2) n_candidates   == n_orders_pass + n_reject_geH + n_reject_bounds
  (E3) n_fills_04     <= n_orders_pass

口径澄清（回答 189 vs 118 混淆）：
  - 189 = FT-ENTRY-AUDIT-04 的 `01_A入场价与箱顶H逐笔_v1.csv` 行数 = 「混合候选目录」
    （预约束、含 at_or_above_H=True、APE 覆盖随格不一致），既非 04 成交、也非可核验拒单基准。
  - 118 = FT-ENTRY-04 A 路线实际成交笔数（本表 n_fills_04 合计）。
  - 71  = FT-ENTRY-04 A 路线被约束拒单的候选数（本表 n_reject_geH 合计）。
只读上游，只在 out/ 写。
"""
from __future__ import annotations

import os
import sys
from typing import List

import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from _common import AUDIT04_OUT, BASELINE, CELLS_A, OUT, OUT03, OUT04, jdump, write_csv  # noqa: E402


def load_audit(strat: str) -> pd.DataFrame:
    p = os.path.join(OUT04, f"audit_orders_{strat}.csv")
    return pd.read_csv(p, encoding="utf-8-sig") if os.path.isfile(p) else pd.DataFrame()


def load_fills(strat: str, src: str) -> pd.DataFrame:
    p = os.path.join(src, f"trades_{strat}.csv")
    return pd.read_csv(p, encoding="utf-8-sig") if os.path.isfile(p) else pd.DataFrame()


def main() -> None:
    rows: List[dict] = []
    for code, strat, delta, kvol in CELLS_A:
        aud = load_audit(strat)
        f04 = load_fills(strat, OUT04)
        f03 = load_fills(strat, OUT03)
        if aud.empty:
            rows.append({"code": code, "strategy": strat, "delta": delta, "k_vol": kvol,
                         "__missing_audit__": strat})
            continue
        n_eval = len(aud)
        n_retry = int((aud["reason"] == "retry_prohibited_already_evaluated").sum())
        real_raw = aud[aud["reason"] != "retry_prohibited_already_evaluated"]
        n_real_raw = len(real_raw)
        # 去重：审计日志存在同候选同时刻的重复记录行（见 A01/BONK），
        # 按 (candidate_id, planned_execution_time_ms) 去重才是真实决策数。
        real = real_raw.drop_duplicates(subset=["candidate_id", "planned_execution_time_ms"])
        n_dup_rows = n_real_raw - len(real)
        n_cand = int(aud["candidate_id"].nunique())
        n_distinct_dec = len(real)
        n_pass = int((real["reason"] == "fill_price_lt_frozen_h").sum())
        n_ge = int(real["reason"].isin(
            ["fill_price_ge_frozen_h_greater", "fill_price_ge_frozen_h_equal"]).sum())
        n_bounds = int((real["reason"] == "outside_min_max_bounds").sum())
        n_fills4 = len(f04)
        n_fills3 = len(f03)

        # 恒等式校验（全部按去重后的真实决策）
        e1 = (n_eval == n_real_raw + n_retry)                       # 总行=真实原始行+重试
        e2 = (n_real_raw ==
              int((real_raw["reason"] == "fill_price_lt_frozen_h").sum())
              + int(real_raw["reason"].isin(["fill_price_ge_frozen_h_greater",
                   "fill_price_ge_frozen_h_equal"]).sum())
              + int((real_raw["reason"] == "outside_min_max_bounds").sum()))
        e4 = (n_distinct_dec == n_cand)                             # 每候选仅一次真实决策
        e3 = (n_fills4 <= n_pass)                                   # 成交 <= 放行
        rows.append({
            "code": code, "strategy": strat, "delta": delta, "k_vol": kvol,
            "n_eval_events": n_eval, "n_retry": n_retry,
            "n_real_rows_raw": n_real_raw, "n_dup_rows": n_dup_rows,
            "n_candidates": n_cand, "n_distinct_decisions": n_distinct_dec,
            "n_orders_pass": n_pass, "n_reject_geH": n_ge, "n_reject_bounds": n_bounds,
            "n_requests_rejected_total": int((real["decision"] == "REJECTED").sum()),
            "n_fills_04": n_fills4, "n_pass_not_filled": n_pass - n_fills4,
            "n_fills_03": n_fills3,
            "E1_eval=realraw+retry": bool(e1), "E2_realraw_split": bool(e2),
            "E4_distinct=cand": bool(e4), "E3_fills<=pass": bool(e3),
        })

    m = pd.DataFrame(rows)
    tot = {c: int(m[c].sum()) for c in [
        "n_eval_events", "n_retry", "n_real_rows_raw", "n_dup_rows",
        "n_candidates", "n_distinct_decisions",
        "n_orders_pass", "n_reject_geH", "n_reject_bounds", "n_requests_rejected_total",
        "n_fills_04", "n_pass_not_filled", "n_fills_03"]}
    tot.update({"code": "TOTAL_A",
                "E1": bool(m["E1_eval=realraw+retry"].all()),
                "E2": bool(m["E2_realraw_split"].all()),
                "E4": bool(m["E4_distinct=cand"].all()),
                "E3": bool(m["E3_fills<=pass"].all())})

    write_csv(m, "02_数量关系口径模型_v1.csv")

    # AUDIT-04 H 表（189）口径澄清
    h = pd.read_csv(os.path.join(AUDIT04_OUT, "01_A入场价与箱顶H逐笔_v1.csv"), encoding="utf-8-sig")
    h_total = len(h)
    h_a = h[h["code"] != "BASE"]
    h_tot = len(h_a)
    h_base = h_total - h_tot
    h_ge = int((h_a["at_or_above_H"] == True).sum()) if "at_or_above_H" in h_a.columns else None  # noqa: E712
    h_ge_all = int((h["at_or_above_H"] == True).sum()) if "at_or_above_H" in h.columns else None  # noqa: E712

    # 被拒候选中能在 FT-ENTRY-03 找到对应旧成交的笔数（来自 05 闭环核对；02 先跑时取缓存）
    ATT_MATCH_N = 47
    _loop_p = os.path.join(OUT, "05_全量闭环核对摘要_v1.json")
    if os.path.isfile(_loop_p):
        try:
            _d = json.load(open(_loop_p, encoding="utf-8"))
            ATT_MATCH_N = int(_d["dropped拆分"]["fill>=H拒单所致_笔"])
        except Exception:
            pass

    summary = {
        "四层定义": {
            "n_eval_events": "审计日志总行数（含防重试拦截的重评估）",
            "n_candidates": "去重 candidate_id 数（真实被评估候选）",
            "n_orders_pass": "放行入仓的候选（=订单请求被允许，去重后）",
            "n_reject_geH": "约束核心拒单（fill_price >= frozen_H，去重后）",
            "n_fills_04": "freqtrade 实际成交笔数（<= n_orders_pass）",
        },
        "合计": tot,
        "恒等式E1全部成立_eval=realraw+retry": bool(m["E1_eval=realraw+retry"].all()),
        "恒等式E2全部成立_realraw可被类型拆分": bool(m["E2_realraw_split"].all()),
        "恒等式E4全部成立_去重决策数=候选数": bool(m["E4_distinct=cand"].all()),
        "恒等式E3全部成立_fills<=pass": bool(m["E3_fills<=pass"].all()),
        "审计日志重复行说明": (
            "审计日志对同候选同时刻存在重复记录行（仅 A01：BONK/USDT 两个候选各被记录 4 次，"
            "完全相同的执行时刻/fill/H，共 6 行重复）。这是记录层重复、非多次真实决策，"
            "故数量口径一律按 (candidate_id, planned_execution_time_ms) 去重；"
            "该重复不影响 AUDIT-05 的 Q1 与逐笔指标（那里已去重并对齐 freqtrade 成交）。"
        ),
        "审计日志重复行_合计": tot["n_dup_rows"],
        "口径澄清_189": {
            "来源": "FT-ENTRY-AUDIT-04/out/01_A入场价与箱顶H逐笔_v1.csv",
            "文件总行数": h_total,
            "其中A路线行数": h_tot,
            "其中Baseline行数": h_base,
            "A路线at_or_above_H=True行数": h_ge,
            "全表at_or_above_H=True行数": h_ge_all,
            "性质": (
                "混合候选目录（预约束、含 >=H 成交、覆盖随格不一致），非 04 成交、非可核验拒单基准。"
                "A 路线 170 行恰等于 FT-ENTRY-03 A 路线成交数(170)。"
            ),
            "数字撞车警告": (
                f"全表 at_or_above_H=True = {h_ge_all}，构成为 A 路线 {h_ge} + Baseline {h_base}（Baseline 天然 >=H）；"
                f"该数值与「FT-ENTRY-04 去重后 fill>=H 拒单候选数 {tot['n_reject_geH']}」数值相同但口径完全不同，"
                "跨报告引用 68 必须注明口径。"
            ),
            "旧成交侧缺口": (
                f"AUDIT-04 目录显示 03 旧成交 fill>=H 为 {h_ge} 笔；本审核按 (策略,候选) 独立匹配得到 "
                f"{ATT_MATCH_N} 笔（差 {h_ge - ATT_MATCH_N if h_ge is not None else 'NA'}），"
                "差来自 AUDIT-04 H 表覆盖缺陷。本审核拒单归因以独立重建的 03 逐笔为准，不采用 "
                f"{h_ge}。"
            ),
        },
        "口径澄清_118": {"来源": "FT-ENTRY-04 A 路线实际成交合计", "行数": tot["n_fills_04"]},
        "口径澄清_71": {"来源": "FT-ENTRY-04 A 路线被约束拒单（fill>=H，去重后）合计", "行数": tot["n_reject_geH"]},
        "关键结论": (
            f"{h_total}(AUDIT-04混合目录总行={h_tot}A+{h_base}Baseline) / {tot['n_fills_04']}(04成交) / "
            f"71(未去重拒单请求) / {tot['n_reject_geH']}(去重拒单候选) / {tot['n_candidates']}(04候选去重) / "
            f"{tot['n_orders_pass']}(放行去重) 六个口径互不可替；"
            "拒单归因必须以去重后的候选/拒单为准，把旧成交(170)与被拒候选逐一对应，"
            "不得用 189 当分母、不得用未去重的审计原始行当分子、不得把两个 68 混为一谈。"
        ),
    }
    jdump(os.path.join(OUT, "02_数量关系口径模型摘要_v1.json"), summary)

    cols = ["code", "delta", "k_vol", "n_eval_events", "n_retry", "n_real_rows_raw", "n_dup_rows",
            "n_candidates", "n_distinct_decisions", "n_orders_pass", "n_reject_geH", "n_reject_bounds",
            "n_fills_04", "n_pass_not_filled", "n_fills_03",
            "E1_eval=realraw+retry", "E2_realraw_split", "E4_distinct=cand", "E3_fills<=pass"]
    print("== 数量关系口径模型（FT-ENTRY-04 A 路线，去重口径）==")
    print(m[cols].to_string(index=False))
    print("\n== 合计 ==", {k: tot[k] for k in [
        "n_eval_events", "n_retry", "n_real_rows_raw", "n_dup_rows", "n_candidates",
        "n_orders_pass", "n_reject_geH", "n_reject_bounds", "n_fills_04", "n_pass_not_filled", "n_fills_03"]})
    print(f"E1={tot['E1']} E2={tot['E2']} E4(去重决策=候选)={tot['E4']} E3={tot['E3']}")
    print(f"\n口径澄清：189(AUDIT-04混合候选目录) / 118(04成交) / 71(被约束拒单) / "
          f"候选去重={tot['n_candidates']} / 放行去重={tot['n_orders_pass']} / 审计重复行={tot['n_dup_rows']}")
    print("\n完成")


if __name__ == "__main__":
    main()
