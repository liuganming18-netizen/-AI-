# -*- coding: utf-8 -*-
"""ENTRY-AUDIT-04 / 01 · 第二阶段基准：按 FE06 冻结契约独立实现分类

工单第二阶段第 4、5 项要求：
  4. 按 FE06 冻结分类定义，独立计算 FE07 标签；特别核查 net_pnl<=0 参与的浮盈回吐分类。
  5. 验证 ATR 和首触顺序等不依赖费用的字段没有被意外改变。

FE07 尚未交付，因此本轮先把「基准」冻结下来：
  - 输入 A：交接包的净盈亏（引擎账本口径，已核 205/205 与 FE06 一致）
  - 输入 B：FE06 冻结特征表里不依赖费用的字段（ATR、首触、MFE/MAE、4h 收益）
  - 分类：**本脚本按契约原文重新实现，不调用 FE06 的代码、不采信任何既有标签列**
  - 输出：三套标签的独立计算结果 + 与 FE06 自报标签的逐条比对

FE07 一到，只需用 02_fe07_compare.py 把它的标签与本基准逐条比即可。

写入边界：仅 evidence/ENTRY-AUDIT-04/。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path("/Users/boos/Desktop/威科夫因子共振趋势")
OUT = ROOT / "evidence" / "ENTRY-AUDIT-04"
HANDOFF = OUT / "handoff"
FE06 = ROOT / "evidence" / "FACTOR-ENTRY-06"

# FE06 契约要求的必需输入列：缺一列立即中止，严禁静默 fallback 或默认赋 0
REQUIRED = ["mfe_r", "signal_atr_snapshot", "fwd_ret_4h", "mae_r_4h",
            "mfe_r_4h", "first_move_4h", "net_pnl", "is_filled"]

# 契约 §2.2 的首触枚举
FIRST_MOVE_ALLOWED = {"FIRST_ADVERSE", "FIRST_FAVORABLE", "UNKNOWN_SAME_BAR", "NEITHER_REACHED_0.3ATR"}


def classify_standard_mixed(df: pd.DataFrame) -> pd.Series:
    """主体系：标准混合度量（EARLY_WEAKNESS 用 ATR，主升/回吐用初始风险 R）。

    契约 §3.1 优先级：EARLY_WEAKNESS(1) → MFE_PULLBACK(2) → SUSTAINED_TREND(3) → CHOPPY(4)
    """
    out = pd.Series("CHOPPY_OR_OTHER", index=df.index, dtype=object)

    filled = df["is_filled"] == True
    # 未成交：统一 UNFILLED（契约 §3.1 第 5 条）
    out[~filled] = "UNFILLED"

    # 已成交但关键输入缺失：UNKNOWN_MISSING_INPUT，严禁归入 CHOPPY（契约 §4.2）
    key = df[["fwd_ret_4h", "mfe_r_4h", "mae_r_4h", "first_move_4h", "mfe_r", "net_pnl"]]
    miss = filled & key.isna().any(axis=1)
    out[miss] = "UNKNOWN_MISSING_INPUT"

    ok = filled & ~miss

    # Priority 3：持续主升（先算后面两条，最后被前面的覆盖）
    p3 = ok & (df["mfe_r"] >= 1.5) & (df["net_pnl"] > 0)
    out[p3] = "SUSTAINED_TREND"

    # Priority 2：浮盈回吐（net_pnl <= 0 参与判定）
    p2 = ok & (df["mfe_r"] >= 1.0) & (df["net_pnl"] <= 0)
    out[p2] = "MFE_PULLBACK"

    # Priority 1：早期走弱
    p1 = ok & (df["fwd_ret_4h"] < 0) & (df["mfe_r_4h"] < 0.5) & (
        (df["mae_r_4h"] >= 0.5) | (df["first_move_4h"] == "FIRST_ADVERSE"))
    out[p1] = "EARLY_WEAKNESS"
    return out


def classify_pure_atr_4h(df: pd.DataFrame) -> pd.Series:
    """对比体系：纯 ATR 4h 窗口（除数统一用 signal_atr_snapshot）。契约 §3.2"""
    out = pd.Series("CHOPPY_OR_OTHER", index=df.index, dtype=object)
    filled = df["is_filled"] == True
    out[~filled] = "UNFILLED"
    key = df[["fwd_ret_4h", "mfe_r_4h", "mae_r_4h", "first_move_4h", "net_pnl"]]
    miss = filled & key.isna().any(axis=1)
    out[miss] = "UNKNOWN_MISSING_INPUT"
    ok = filled & ~miss

    out[ok & (df["mfe_r_4h"] >= 1.5) & (df["net_pnl"] > 0)] = "SUSTAINED_TREND"
    out[ok & (df["mfe_r_4h"] >= 1.0) & (df["net_pnl"] <= 0)] = "MFE_PULLBACK"
    out[ok & (df["fwd_ret_4h"] < 0) & (df["mfe_r_4h"] < 0.5) & (
        (df["mae_r_4h"] >= 0.5) | (df["first_move_4h"] == "FIRST_ADVERSE"))] = "EARLY_WEAKNESS"
    return out


def classify_pure_atr_full(df: pd.DataFrame) -> pd.Series:
    """扩展体系：全周期纯 ATR（回吐 ≥2.0、主升 ≥3.0）。契约 §3.3"""
    out = pd.Series("CHOPPY_OR_OTHER", index=df.index, dtype=object)
    filled = df["is_filled"] == True
    out[~filled] = "UNFILLED"
    key = df[["fwd_ret_4h", "mfe_r_4h", "mae_r_4h", "first_move_4h", "mfe_atr_full", "net_pnl"]]
    miss = filled & key.isna().any(axis=1)
    out[miss] = "UNKNOWN_MISSING_INPUT"
    ok = filled & ~miss

    out[ok & (df["mfe_atr_full"] >= 3.0) & (df["net_pnl"] > 0)] = "SUSTAINED_TREND"
    out[ok & (df["mfe_atr_full"] >= 2.0) & (df["net_pnl"] <= 0)] = "MFE_PULLBACK"
    out[ok & (df["fwd_ret_4h"] < 0) & (df["mfe_r_4h"] < 0.5) & (
        (df["mae_r_4h"] >= 0.5) | (df["first_move_4h"] == "FIRST_ADVERSE"))] = "EARLY_WEAKNESS"
    return out


def dist(s: pd.Series) -> dict:
    vc = s.value_counts().to_dict()
    return {k: int(v) for k, v in sorted(vc.items(), key=lambda x: -x[1])}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)

    f6 = pd.read_csv(FE06 / "reconstructed_candidate_features.csv")
    ho = pd.read_parquet(HANDOFF / "ledger_handoff.parquet")

    # ── 契约 §4.1：缺必需列立即中止 ──
    missing = [c for c in REQUIRED if c not in f6.columns]
    if missing:
        raise AssertionError(f"FE06 特征表缺必需列：{missing}")

    if len(f6) != 227:
        raise AssertionError(f"FE06 特征表应为 227 行，实际 {len(f6)}")

    # ── 身份对齐：candidate_id 必须一一对应 ──
    m = f6.merge(ho[["candidate_id", "signal_key", "run_id", "generation", "generation_label",
                     "executed", "net_pnl", "fees", "funding_fee", "gross_pnl",
                     "spec_qualification", "formal_pass_allowed", "research_usability"]],
                 on="candidate_id", how="outer", suffixes=("_fe06", "_ho"), indicator=True)
    if (m["_merge"] != "both").any():
        raise AssertionError(f"身份未对齐：{m['_merge'].value_counts().to_dict()}")

    # net_pnl 必须与交接包（引擎账本口径）逐笔相同；不同就说明取到了费用为 0 的那份
    filled = m["executed"] == True
    dnet = (m.loc[filled, "net_pnl_fe06"] - m.loc[filled, "net_pnl_ho"]).abs()
    if (dnet > 1e-9).any():
        raise AssertionError(f"net_pnl 与交接包不一致 {int((dnet > 1e-9).sum())} 条，拒绝用错口径分类")

    # ── 独立分类（用交接包口径的 net_pnl 覆盖，其余特征取 FE06 冻结列）──
    work = m.copy()
    work["net_pnl"] = work["net_pnl_ho"]
    work["is_filled"] = work["executed"]
    work["cat_standard_mixed_indep"] = classify_standard_mixed(work)
    work["cat_pure_atr_4h_indep"] = classify_pure_atr_4h(work)
    work["cat_pure_atr_full_indep"] = classify_pure_atr_full(work)

    # ── 与 FE06 自报标签逐条比对（只作对照，不改 FE06 文件）──
    cmp_rows = []
    for a, b, name in [("category_standard_mixed", "cat_standard_mixed_indep", "标准混合"),
                       ("category_pure_atr_4h", "cat_pure_atr_4h_indep", "纯ATR-4h"),
                       ("category_pure_atr_full", "cat_pure_atr_full_indep", "纯ATR-全周期")]:
        if a not in work.columns:
            cmp_rows.append({"体系": name, "FE06列": a, "状态": "FE06 无此列"})
            continue
        diff = work[work[a].astype(str) != work[b].astype(str)]
        cmp_rows.append({
            "体系": name, "FE06列": a, "独立列": b,
            "总行数": len(work),
            "一致": int(len(work) - len(diff)),
            "不一致": int(len(diff)),
            "不一致明细": diff[["candidate_id", a, b, "net_pnl", "mfe_r", "mfe_r_4h"]].to_dict("records")[:20],
        })

    # ── net_pnl<=0 边界敏感性：这是工单点名的核查项 ──
    # 浮盈回吐用 net_pnl<=0 判定，所以 net_pnl 落在 0 附近的行最容易被口径差异翻转
    filled_rows = work[work["is_filled"] == True].copy()
    filled_rows["abs_net"] = filled_rows["net_pnl"].abs()
    boundary = filled_rows.sort_values("abs_net").head(25)
    # 三档阈值下的浮盈回吐计数：<=0 / <0 / <=1e-9
    pullback_sens = {}
    for name, cond in [("net_pnl <= 0（契约口径）", filled_rows["net_pnl"] <= 0),
                       ("net_pnl < 0（严格）", filled_rows["net_pnl"] < 0),
                       ("|net_pnl| <= 1e-9 视为 0 并入", filled_rows["net_pnl"] <= 1e-9)]:
        pullback_sens[name] = int(((filled_rows["mfe_r"] >= 1.0) & cond).sum())

    # 若把费用当 0（即误用上游 total_fees 口径），浮盈回吐会少算多少
    gross_based = int(((filled_rows["mfe_r"] >= 1.0) & (filled_rows["gross_pnl"] <= 0)).sum())

    baseline = {
        "audit": "ENTRY-AUDIT-04",
        "stage": "第二阶段基准（FE07 未交付，先冻结基准）",
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        "contract_source": "evidence/FACTOR-ENTRY-06/classification_contract.md",
        "implementation_note": "本脚本按契约原文重新实现三套分类，未调用 FE06 代码、未采信任何既有标签列",
        "inputs": {
            "net_pnl": "交接包（引擎账本口径，含手续费与资金费）",
            "atr_and_first_move": "FE06 冻结特征表 reconstructed_candidate_features.csv",
            "identity": "candidate_id 227/227 一一对应",
        },
        "sample_space": {
            "rows": 227,
            "unique_signal_key": int(work["signal_key"].nunique()),
            "filled": int((work["is_filled"] == True).sum()),
            "unfilled": int((work["is_filled"] != True).sum()),
            "filled_by_generation": {k: int(v) for k, v in
                                     work.loc[work["is_filled"] == True, "generation"].value_counts().items()},
        },
        "net_pnl_consistency_with_fe06": {
            "filled_compared": int(filled.sum()),
            "identical": int((dnet <= 1e-9).sum()),
            "different": int((dnet > 1e-9).sum()),
            "sum_fe06": round(float(m.loc[filled, "net_pnl_fe06"].sum()), 6),
            "sum_handoff": round(float(m.loc[filled, "net_pnl_ho"].sum()), 6),
            "conclusion": "FE06 的 net_pnl 与交接包逐笔相同 ⇒ FE06 没有踩「费用为 0」那个坑",
        },
        "independent_classification": {
            "category_standard_mixed": dist(work["cat_standard_mixed_indep"]),
            "category_pure_atr_4h": dist(work["cat_pure_atr_4h_indep"]),
            "category_pure_atr_full": dist(work["cat_pure_atr_full_indep"]),
        },
        "compare_with_fe06": cmp_rows,
        "net_pnl_boundary_check": {
            "why": "浮盈回吐的判定里有 net_pnl<=0；若净盈亏口径不同（例如把费用当 0），这一类的计数会变",
            "pullback_count_under_thresholds": pullback_sens,
            "pullback_if_using_gross_pnl": gross_based,
            "delta_vs_contract": gross_based - pullback_sens["net_pnl <= 0（契约口径）"],
            "boundary_rows_top25": boundary[[
                "candidate_id", "symbol", "generation", "net_pnl", "gross_pnl", "fees",
                "funding_fee", "mfe_r", "cat_standard_mixed_indep"]].to_dict("records"),
            "conclusion": ("用毛盈亏代替净盈亏会把浮盈回吐少数 "
                           f"{pullback_sens['net_pnl <= 0（契约口径）'] - gross_based} 条"
                           if gross_based != pullback_sens["net_pnl <= 0（契约口径）"] else
                           "用毛盈亏与用净盈亏得到相同的浮盈回吐条数"),
        },
        "fee_independent_fields_frozen": {
            "why": "工单第 5 项：ATR 与首触顺序等不依赖费用的字段必须没被意外改变",
            "fields": ["signal_atr_snapshot", "box_atr_formation", "fwd_ret_4h", "mfe_r_4h",
                       "mae_r_4h", "first_move_4h", "mfe_r", "mfe_atr_full",
                       "t_fav_ms", "t_adv_ms"],
            "how_to_check_when_fe07_arrives": "逐 candidate_id 逐字段比，容差 1e-12；NaN 必须对 NaN",
            "first_move_enum_check": {
                "allowed_by_contract": sorted(FIRST_MOVE_ALLOWED),
                "observed": sorted([str(x) for x in work["first_move_4h"].dropna().unique()]),
                "out_of_contract": sorted([str(x) for x in work["first_move_4h"].dropna().unique()
                                           if str(x) not in FIRST_MOVE_ALLOWED]),
                "contract_gap": ("契约 §2.2 只规定了已成交样本的首触枚举，没有规定未成交怎么标；"
                                 "FE06 对未成交标 NO_DATA。这不是错误，是契约没覆盖的空档"),
                "no_data_count": int((work["first_move_4h"].astype(str) == "NO_DATA").sum()),
                "unfilled_count": int((work["is_filled"] != True).sum()),
                "no_data_equals_unfilled": bool(
                    int((work["first_move_4h"].astype(str) == "NO_DATA").sum())
                    == int((work["is_filled"] != True).sum())),
                "judgement_rule_for_fe07": ("FE07 出现 NO_DATA 不得直接判 FAIL，"
                                            "但必须核对 NO_DATA 条数 == 未成交条数（22）"),
            },
            "atr_missing_is_known": {
                "signal_atr_snapshot_missing": int(work["signal_atr_snapshot"].isna().sum()),
                "box_atr_formation_missing": int(work["box_atr_formation"].isna().sum()),
                "box_atr_missing_by_generation": {
                    k: int(v) for k, v in work.groupby("generation")["box_atr_formation"]
                    .apply(lambda s: int(s.isna().sum())).items()},
                "note": ("box_atr_formation 的 42 条空值全部落在窗口回放代际，"
                         "与 ENTRY-AUDIT-01 的结论一致（该代际箱体几何是坏的，"
                         "box_high/box_low 实为入场价与止损价）。"
                         "这些空值**是有据的缺失，不是 0**；FE07 若填成 0 或别的数，"
                         "即判「把缺值当 0」或「改动不依赖费用字段」"),
            },
        },
    }
    (OUT / "01_baseline_classification.json").write_text(
        json.dumps(baseline, ensure_ascii=False, indent=2), encoding="utf-8")

    # ── 基准特征表（供 FE07 到达后逐列比对）──
    cols = ["candidate_id", "signal_key", "run_id", "generation", "generation_label", "symbol",
            "is_filled", "net_pnl", "gross_pnl", "fees", "funding_fee",
            "signal_atr_snapshot", "box_atr_formation", "fwd_ret_4h",
            "mfe_r_4h", "mae_r_4h", "mfe_r", "mfe_atr_full", "first_move_4h",
            "t_fav_ms", "t_adv_ms", "entry_time",
            "cat_standard_mixed_indep", "cat_pure_atr_4h_indep", "cat_pure_atr_full_indep",
            "spec_qualification", "formal_pass_allowed", "research_usability"]
    cols = [c for c in cols if c in work.columns]
    work[cols].to_csv(OUT / "01_baseline_features.csv", index=False, encoding="utf-8-sig")
    work[cols].to_parquet(OUT / "01_baseline_features.parquet", index=False)

    print(f"基准已生成 → {OUT}")
    print(f"  独立分类（标准混合）：{baseline['independent_classification']['category_standard_mixed']}")
    print(f"  独立分类（纯ATR-4h）：{baseline['independent_classification']['category_pure_atr_4h']}")
    print(f"  与 FE06 比对：{[ (r['体系'], r.get('不一致')) for r in cmp_rows ]}")
    print(f"  浮盈回吐边界：{pullback_sens} / 若用毛盈亏 {gross_based}")


if __name__ == "__main__":
    main()
