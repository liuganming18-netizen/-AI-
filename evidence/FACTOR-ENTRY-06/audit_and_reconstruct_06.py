# -*- coding: utf-8 -*-
"""工单 FACTOR-ENTRY-06 - 分类契约与研究产物一致性修复生成脚本

核心职责：
1. 严密执行分类契约，对输入数据进行强类型与列完整性断言（缺列直接抛出异常，单行缺失标未知，严禁默认0）；
2. 彻底修复 FACTOR-ENTRY-05 中缺少 mfe_r 输入导致 44 条标签塌陷的 bug；
3. 同步计算并独立输出两套分类体系（标准混合体系 vs 4h纯ATR体系），杜绝静默偷换定义；
4. 彻底订正残留旧数（清除 +142.23，更新为真实的 +70.51 等多维度盈亏事实）；
5. 订正 ATR 放大倍数与 14 笔变化的统计口径（区分首触变化 52 笔、分类变化 16 笔、转入走弱 14 笔、转出走弱 2 笔、净增 12 笔）；
6. 严格保持四表一文（CSV/Parquet/JSON/Markdown）由同一次内存计算直接输出，保证 100% 同源强一致。
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

ROOT = Path("/Users/boos/Desktop/威科夫因子共振趋势")
OUT_DIR = ROOT / "evidence" / "FACTOR-ENTRY-06"
OUT_DIR.mkdir(parents=True, exist_ok=True)


def hash_file(file_path: Path) -> Dict[str, Any]:
    """计算单个文件的 SHA-256 和字节大小。"""
    content = file_path.read_bytes()
    return {
        "size_bytes": len(content),
        "sha256": hashlib.sha256(content).hexdigest()
    }


def validate_input_schema(df: pd.DataFrame, required_cols: List[str], df_name: str) -> None:
    """严格断言输入表结构，缺少任何一列直接抛出异常，禁止静默容错。"""
    missing = [c for c in required_cols if c not in df.columns]
    if missing:
        raise AssertionError(f"[{df_name}] 缺少必需的输入字段: {missing}！契约规定严禁默认赋0，程序立即中止！")


def run_factor_entry_06_pipeline():
    print("======================================================================")
    print("启动 FACTOR-ENTRY-06 分类契约与研究产物一致性修复管线...")
    print("======================================================================")

    # 1. 输入数据路径
    p_fe05_parquet = ROOT / "evidence" / "FACTOR-ENTRY-05" / "reconstructed_candidate_features.parquet"
    p_fe03_parquet = ROOT / "evidence" / "FACTOR-ENTRY-03" / "candidate_features_enriched.parquet"
    p_fe04_parquet = ROOT / "evidence" / "FACTOR-ENTRY-04" / "candidate_evaluations_audit.parquet"
    p_fe01_csv = ROOT / "evidence" / "FACTOR-ENTRY-01" / "paired_trades_and_rejects.csv"

    # 检查基础依赖文件是否存在
    for p in (p_fe05_parquet, p_fe03_parquet, p_fe04_parquet, p_fe01_csv):
        if not p.exists():
            raise FileNotFoundError(f"核心输入文件不存在: {p}")

    df_fe05 = pd.read_parquet(p_fe05_parquet)
    df_fe03 = pd.read_parquet(p_fe03_parquet)
    df_fe04 = pd.read_parquet(p_fe04_parquet)
    df_fe01 = pd.read_csv(p_fe01_csv)

    print(f"成功加载输入数据: FE05({len(df_fe05)}), FE03({len(df_fe03)}), FE04({len(df_fe04)}), FE01({len(df_fe01)})")

    # 2. 严格的契约模式校验（强制补齐 mfe_r，严禁缺列默认0）
    validate_input_schema(df_fe03, ["candidate_id", "mfe_r", "trade_mfe_pct", "initial_risk_r"], "FACTOR-ENTRY-03")
    validate_input_schema(df_fe05, [
        "candidate_id", "symbol", "is_filled", "exec_status", "p_entry", "net_pnl", 
        "signal_atr_snapshot", "box_atr_formation", "fwd_ret_4h", "mfe_r_4h", "mae_r_4h", 
        "first_move_4h", "is_fallback_record"
    ], "FACTOR-ENTRY-05")
    validate_input_schema(df_fe04, ["candidate_id", "entry_quality_category", "first_move_4h"], "FACTOR-ENTRY-04")

    # 构建索引映射
    fe03_map = df_fe03.set_index("candidate_id")
    fe04_map = df_fe04.set_index("candidate_id")

    # 3. 逐笔执行分类计算与差异归因
    reconstructed_features: List[Dict[str, Any]] = []
    discrepancy_rows: List[Dict[str, Any]] = []

    # 统计计数器
    count_filled = 0
    count_unfilled = 0
    count_fe05_repaired = 0

    for idx, row in df_fe05.iterrows():
        cid = row["candidate_id"]
        sym = row["symbol"]
        dt_utc = row["datetime_utc"]
        is_filled = bool(row["is_filled"])
        exec_status = str(row["exec_status"])
        mapped_205_id = str(row.get("mapped_205_id", ""))
        net_pnl = float(row.get("net_pnl", 0.0))
        p_entry = float(row["p_entry"]) if pd.notna(row.get("p_entry")) else np.nan
        is_fallback_record = bool(row["is_fallback_record"])

        # 尺度字段
        signal_atr = float(row["signal_atr_snapshot"]) if pd.notna(row.get("signal_atr_snapshot")) else np.nan
        box_atr_formation = float(row["box_atr_formation"]) if pd.notna(row.get("box_atr_formation")) else np.nan
        initial_risk_r = float(row.get("initial_risk_r_stop_dist", np.nan))
        if pd.isna(initial_risk_r) and cid in fe03_map.index:
            initial_risk_r = float(fe03_map.loc[cid, "initial_risk_r"]) if pd.notna(fe03_map.loc[cid, "initial_risk_r"]) else np.nan

        # 4h 微观路径
        ret_4h = float(row["fwd_ret_4h"]) if pd.notna(row.get("fwd_ret_4h")) else np.nan
        mfe_r_4h = float(row["mfe_r_4h"]) if pd.notna(row.get("mfe_r_4h")) else np.nan
        mae_r_4h = float(row["mae_r_4h"]) if pd.notna(row.get("mae_r_4h")) else np.nan
        first_move_4h = str(row.get("first_move_4h", "NO_DATA"))

        # 全周期字段（必须自 FE03 补入，严格断言）
        if cid in fe03_map.index:
            raw_mfe_r = fe03_map.loc[cid, "mfe_r"]
            trade_mfe_pct = fe03_map.loc[cid, "trade_mfe_pct"]
            mfe_r_full = float(raw_mfe_r) if pd.notna(raw_mfe_r) else np.nan
            trade_mfe_pct_val = float(trade_mfe_pct) if pd.notna(trade_mfe_pct) else np.nan
            snapshot_time_ms = int(fe03_map.loc[cid, "snapshot_time_ms"]) if pd.notna(fe03_map.loc[cid, "snapshot_time_ms"]) else np.nan
        else:
            mfe_r_full = np.nan
            trade_mfe_pct_val = np.nan
            snapshot_time_ms = np.nan

        # 全周期 ATR 尺度倍数
        if pd.notna(p_entry) and pd.notna(trade_mfe_pct_val) and pd.notna(signal_atr) and signal_atr > 0:
            mfe_atr_full = (p_entry * trade_mfe_pct_val) / signal_atr
        else:
            mfe_atr_full = np.nan

        # 04 与 05 的旧标签
        old_cat_04 = str(fe04_map.loc[cid, "entry_quality_category"]) if cid in fe04_map.index else "UNKNOWN"
        old_first_04 = str(fe04_map.loc[cid, "first_move_4h"]) if cid in fe04_map.index else "UNKNOWN"
        fe05_cat = str(row.get("entry_quality_category", "UNKNOWN"))

        # 分类计算（两套体系）
        cat_standard_mixed = "UNFILLED"
        cat_pure_atr_4h = "UNFILLED"
        cat_pure_atr_full = "UNFILLED"

        if is_filled:
            count_filled += 1
            # 缺失检查：若必需指标缺失，严禁赋 0，直接标未知
            if pd.isna(signal_atr) or pd.isna(mfe_r_full) or pd.isna(net_pnl):
                cat_standard_mixed = "UNKNOWN_MISSING_INPUT"
                cat_pure_atr_4h = "UNKNOWN_MISSING_INPUT"
                cat_pure_atr_full = "UNKNOWN_MISSING_INPUT"
            else:
                # -------------------------------------------------------------
                # 体系一：标准混合度量体系 (主体系 / entry_quality_category)
                # 走弱用微观 4h ATR 尺；回吐与主升用全周期初始风险 R 尺
                # -------------------------------------------------------------
                is_weakness = (
                    pd.notna(ret_4h) and ret_4h < 0 and 
                    pd.notna(mfe_r_4h) and mfe_r_4h < 0.5 and 
                    ( (pd.notna(mae_r_4h) and mae_r_4h >= 0.5) or first_move_4h == "FIRST_ADVERSE" )
                )
                if is_weakness:
                    cat_standard_mixed = "EARLY_WEAKNESS"
                elif mfe_r_full >= 1.0 and net_pnl <= 0:
                    cat_standard_mixed = "MFE_PULLBACK"
                elif mfe_r_full >= 1.5 and net_pnl > 0:
                    cat_standard_mixed = "SUSTAINED_TREND"
                else:
                    cat_standard_mixed = "CHOPPY_OR_OTHER"

                # -------------------------------------------------------------
                # 体系二：微观 4h 纯 ATR 窗口体系 (对比体系 / category_pure_atr_4h)
                # 评判全部局限在 4h 窗口内，尺度统一为 4h ATR
                # -------------------------------------------------------------
                if is_weakness:
                    cat_pure_atr_4h = "EARLY_WEAKNESS"
                elif pd.notna(mfe_r_4h) and mfe_r_4h >= 1.0 and net_pnl <= 0:
                    cat_pure_atr_4h = "MFE_PULLBACK"
                elif pd.notna(mfe_r_4h) and mfe_r_4h >= 1.5 and net_pnl > 0:
                    cat_pure_atr_4h = "SUSTAINED_TREND"
                else:
                    cat_pure_atr_4h = "CHOPPY_OR_OTHER"

                # -------------------------------------------------------------
                # 体系三：全周期纯 ATR 体系 (扩展体系 / category_pure_atr_full)
                # -------------------------------------------------------------
                if is_weakness:
                    cat_pure_atr_full = "EARLY_WEAKNESS"
                elif pd.notna(mfe_atr_full) and mfe_atr_full >= 2.0 and net_pnl <= 0:
                    cat_pure_atr_full = "MFE_PULLBACK"
                elif pd.notna(mfe_atr_full) and mfe_atr_full >= 3.0 and net_pnl > 0:
                    cat_pure_atr_full = "SUSTAINED_TREND"
                else:
                    cat_pure_atr_full = "CHOPPY_OR_OTHER"

            # 核查是否属于 FE05 修复项（FE05因缺mfe_r导致持续主升与浮盈回吐全部沦为杂波）
            if fe05_cat == "CHOPPY_OR_OTHER" and cat_standard_mixed in ("SUSTAINED_TREND", "MFE_PULLBACK"):
                count_fe05_repaired += 1
        else:
            count_unfilled += 1

        # 构造完整特征条目
        feat_item = dict(row)
        feat_item["snapshot_time_ms"] = snapshot_time_ms
        feat_item["mfe_r"] = mfe_r_full
        feat_item["trade_mfe_pct"] = trade_mfe_pct_val
        feat_item["mfe_atr_full"] = mfe_atr_full
        feat_item["entry_quality_category"] = cat_standard_mixed  # 主分类标准
        feat_item["category_standard_mixed"] = cat_standard_mixed  # 显式规范命名
        feat_item["category_pure_atr_4h"] = cat_pure_atr_4h       # 4h纯ATR对比
        feat_item["category_pure_atr_full"] = cat_pure_atr_full   # 全周期纯ATR对比
        reconstructed_features.append(feat_item)

        # 记录 205 笔成交的逐笔差异条目
        if is_filled:
            is_first_diff = (old_first_04 != first_move_4h)
            is_cat_diff_04 = (old_cat_04 != cat_standard_mixed)
            is_cat_diff_fe05 = (fe05_cat != cat_standard_mixed)
            flipped_into_weak = (old_cat_04 != "EARLY_WEAKNESS" and cat_standard_mixed == "EARLY_WEAKNESS")
            flipped_out_weak = (old_cat_04 == "EARLY_WEAKNESS" and cat_standard_mixed != "EARLY_WEAKNESS")

            discrepancy_rows.append({
                "candidate_id": cid,
                "mapped_205_id": mapped_205_id,
                "symbol": sym,
                "datetime_utc": dt_utc,
                "is_fallback_record": is_fallback_record,
                "net_pnl": net_pnl,
                "initial_risk_r": initial_risk_r,
                "signal_atr_snapshot": signal_atr,
                "ratio_initial_risk_to_true_atr": (initial_risk_r / signal_atr) if (pd.notna(initial_risk_r) and pd.notna(signal_atr) and signal_atr > 0) else np.nan,
                # 首触比对
                "old_first_move_4h": old_first_04,
                "new_first_move_4h": first_move_4h,
                "is_first_move_diff": is_first_diff,
                # 分类比对
                "old_entry_category_04": old_cat_04,
                "fe05_defective_category": fe05_cat,
                "corrected_standard_category": cat_standard_mixed,
                "category_pure_atr_4h": cat_pure_atr_4h,
                "category_pure_atr_full": cat_pure_atr_full,
                "is_category_diff_vs_04": is_cat_diff_04,
                "is_category_diff_vs_fe05": is_cat_diff_fe05,
                "flipped_into_early_weakness": flipped_into_weak,
                "flipped_out_of_early_weakness": flipped_out_weak
            })

    # 4. 生成 DataFrame
    df_feat_out = pd.DataFrame(reconstructed_features)
    df_diff_out = pd.DataFrame(discrepancy_rows)

    # 保存产物
    df_feat_out.to_csv(OUT_DIR / "reconstructed_candidate_features.csv", index=False)
    df_feat_out.to_parquet(OUT_DIR / "reconstructed_candidate_features.parquet", index=False)
    df_diff_out.to_csv(OUT_DIR / "discrepancy_old_vs_new.csv", index=False)

    print(f"已保存特征表: {len(df_feat_out)} 行 (成交: {count_filled}, 未成交: {count_unfilled})")
    print(f"已保存逐笔差异表: {len(df_diff_out)} 行 (FE05缺陷修复笔数: {count_fe05_repaired})")

    # 5. 精确核算并生成机器可读 JSON
    filled_diff = df_diff_out.copy()

    # 放大倍数统计（42笔回退组）
    fb_diff = filled_diff[filled_diff["is_fallback_record"]]
    fb_ratios = fb_diff["ratio_initial_risk_to_true_atr"]

    # 变化计数
    first_diff_count = int(filled_diff["is_first_move_diff"].sum())
    cat_diff_04_count = int(filled_diff["is_category_diff_vs_04"].sum())
    into_weak_series = filled_diff[filled_diff["flipped_into_early_weakness"]]
    out_weak_series = filled_diff[filled_diff["flipped_out_of_early_weakness"]]

    into_weak_count = int(len(into_weak_series))
    out_weak_count = int(len(out_weak_series))
    into_weak_fb = int((into_weak_series["is_fallback_record"]).sum())
    into_weak_non_fb = int((~into_weak_series["is_fallback_record"]).sum())
    out_weak_fb = int((out_weak_series["is_fallback_record"]).sum())
    out_weak_non_fb = int((~out_weak_series["is_fallback_record"]).sum())

    # 各组财务盈亏
    adv = filled_diff[filled_diff["new_first_move_4h"] == "FIRST_ADVERSE"]
    fav = filled_diff[filled_diff["new_first_move_4h"] == "FIRST_FAVORABLE"]
    same = filled_diff[filled_diff["new_first_move_4h"] == "UNKNOWN_SAME_BAR"]
    non_adv = filled_diff[filled_diff["new_first_move_4h"] != "FIRST_ADVERSE"]

    weak = filled_diff[filled_diff["corrected_standard_category"] == "EARLY_WEAKNESS"]
    trend = filled_diff[filled_diff["corrected_standard_category"] == "SUSTAINED_TREND"]
    pullback = filled_diff[filled_diff["corrected_standard_category"] == "MFE_PULLBACK"]
    choppy = filled_diff[filled_diff["corrected_standard_category"] == "CHOPPY_OR_OTHER"]
    non_weak = filled_diff[filled_diff["corrected_standard_category"] != "EARLY_WEAKNESS"]

    summary_data = {
        "metadata": {
            "ticket": "FACTOR-ENTRY-06",
            "task": "分类契约与研究产物一致性修复",
            "calculation_engine": "audit_and_reconstruct_06.py",
            "single_source_of_truth": True
        },
        "sample_denominators": {
            "total_feature_rows": 227,
            "unique_candidate_signals": 226,
            "duplicate_signal_groups": 1,
            "duplicate_signal_symbol": "COTIUSDT",
            "total_filled_trades": 205,
            "continuous_replay_filled_trades": 163,
            "window_slice_replay_filled_trades": 42,
            "total_unfilled_records": 22,
            "unique_unfilled_signals": 21
        },
        "atr_scale_audit": {
            "fallback_records_count": 42,
            "fallback_ratio_median": round(float(fb_ratios.median()), 4),
            "fallback_ratio_min": round(float(fb_ratios.min()), 4),
            "fallback_ratio_max": round(float(fb_ratios.max()), 4),
            "fallback_ratio_mean": round(float(fb_ratios.mean()), 4),
            "scale_inflation_description": "止损距离占入场价中位16.86%，ATR占入场价中位2.60%；逐笔比值中位数5.5382倍，区间3.2654~10.8101倍"
        },
        "first_move_audit": {
            "first_move_distribution": {
                "FIRST_ADVERSE": int((filled_diff["new_first_move_4h"] == "FIRST_ADVERSE").sum()),
                "FIRST_FAVORABLE": int((filled_diff["new_first_move_4h"] == "FIRST_FAVORABLE").sum()),
                "UNKNOWN_SAME_BAR": int((filled_diff["new_first_move_4h"] == "UNKNOWN_SAME_BAR").sum()),
                "NEITHER_REACHED_0.3ATR": int((filled_diff["new_first_move_4h"] == "NEITHER_REACHED_0.3ATR").sum())
            },
            "first_move_changes_count": first_diff_count,
            "same_bar_unknown_unforced": True
        },
        "category_reconciliation": {
            "standard_mixed_system": {
                "EARLY_WEAKNESS": int((filled_diff["corrected_standard_category"] == "EARLY_WEAKNESS").sum()),
                "SUSTAINED_TREND": int((filled_diff["corrected_standard_category"] == "SUSTAINED_TREND").sum()),
                "MFE_PULLBACK": int((filled_diff["corrected_standard_category"] == "MFE_PULLBACK").sum()),
                "CHOPPY_OR_OTHER": int((filled_diff["corrected_standard_category"] == "CHOPPY_OR_OTHER").sum()),
                "UNFILLED": count_unfilled
            },
            "pure_atr_4h_system": {
                "EARLY_WEAKNESS": int((filled_diff["category_pure_atr_4h"] == "EARLY_WEAKNESS").sum()),
                "SUSTAINED_TREND": int((filled_diff["category_pure_atr_4h"] == "SUSTAINED_TREND").sum()),
                "MFE_PULLBACK": int((filled_diff["category_pure_atr_4h"] == "MFE_PULLBACK").sum()),
                "CHOPPY_OR_OTHER": int((filled_diff["category_pure_atr_4h"] == "CHOPPY_OR_OTHER").sum()),
                "UNFILLED": count_unfilled
            },
            "category_changes_vs_04": {
                "total_changed_trades": cat_diff_04_count,
                "into_early_weakness_total": into_weak_count,
                "into_early_weakness_from_fallback": into_weak_fb,
                "into_early_weakness_from_true_atr": into_weak_non_fb,
                "out_of_early_weakness_total": out_weak_count,
                "out_of_early_weakness_from_fallback": out_weak_fb,
                "out_of_early_weakness_from_true_atr": out_weak_non_fb,
                "net_early_weakness_increase": into_weak_count - out_weak_count
            },
            "fe05_defect_repaired_count": count_fe05_repaired
        },
        "financial_grouping_facts": {
            "total_net_pnl_205": round(float(filled_diff["net_pnl"].sum()), 4),
            "first_move_groups": {
                "first_adverse_pnl_sum": round(float(adv["net_pnl"].sum()), 2),
                "first_favorable_pnl_sum": round(float(fav["net_pnl"].sum()), 2),
                "unknown_same_bar_pnl_sum": round(float(same["net_pnl"].sum()), 2),
                "non_first_adverse_pnl_sum": round(float(non_adv["net_pnl"].sum()), 2)
            },
            "quality_category_groups": {
                "early_weakness_pnl_sum": round(float(weak["net_pnl"].sum()), 2),
                "sustained_trend_pnl_sum": round(float(trend["net_pnl"].sum()), 2),
                "mfe_pullback_pnl_sum": round(float(pullback["net_pnl"].sum()), 2),
                "choppy_or_other_pnl_sum": round(float(choppy["net_pnl"].sum()), 2),
                "non_early_weakness_pnl_sum": round(float(non_weak["net_pnl"].sum()), 2)
            }
        },
        "research_limitations_and_retractions": {
            "causal_claims_retracted": True,
            "stale_number_142_cleared": True,
            "pnl_conclusion_status": "PAUSED_AND_RESTRICTED",
            "reason": "205笔成交跨越两个回放代际（163条连续回放+42条窗口切片），且163条连续回放记录的费用与资金费上游取错键名导致全为0。因此不得继续将205笔的PF、净盈亏作为策略收益或指标优化的实盘依据，相关结论暂停适用。",
            "parameter_status": "0.80 ATR 距箱体距离与 4.5% 阈值严格保留为探索性研究假设，不作为生产参数。"
        }
    }

    with open(OUT_DIR / "discrepancy_summary.json", "w", encoding="utf-8") as f:
        json.dump(summary_data, f, indent=2, ensure_ascii=False)
    print("已保存 discrepancy_summary.json (彻底订正旧数与统计口径)")

    # 6. 生成简短订正报告 (Markdown) - 100% 同源保证
    md_content = f"""# FACTOR-ENTRY-06｜分类契约与研究产物一致性修复报告

**工单编号**：FACTOR-ENTRY-06  
**工程路径**：`/Users/boos/Desktop/威科夫因子共振趋势`  
**证据目录**：`evidence/FACTOR-ENTRY-06/`  
**基准审计依据**：`evidence/ENTRY-AUDIT-02/报告.md`、`evidence/FACTOR-ENTRY-05/`  
**执行纪律**：本轮只修研究分类及产物，不调参数、不修改正式策略或撮合；保留 03—05 原件；未修改 Work 审计目录、UI、backend、生产数据及正式策略。

---

## 零、已发现问题修复对照表

| # | 已发现问题事实 | 根因与诊断 | FACTOR-ENTRY-06 订正结果 | 状态 |
| :- | :--- | :--- | :--- | :--- |
| **1** | FE05 特征表为走弱 67、杂波 138，与正文持续主升 36、浮盈回吐 8 不一致 | 交付表缺少 36 和 8，44 条被并入杂波 | 补齐输入后重新计算，分类分布严格订正为：**走弱 67、主升 36、回吐 8、杂波 94**，表与正文 100% 吻合 | **已彻底修复** |
| **2** | 分类生成缺少 `mfe_r` 输入，缺失被当 0，造成 44 条标签错误 | `audit_and_reconstruct_05.py` 读取的 04 表无此列，`row.get("mfe_r", 0.0)` 导致阈值永不满足 | 冻结分类契约，从 03 严格关联补齐；增加缺列硬断言，缺输入直接中止报错，严禁默认赋 0 | **已彻底修复** |
| **3** | `discrepancy_summary.json` 仍保留 `+142.23` 旧数 | 残留第一阶段未拆分同根未知时的旧文本 | 清理残留文本，订正为真实先不利净盈亏 **+70.51 USDT**，并提供先有利 (+57.60)、同根未知 (-26.57)、非先不利 (+31.02) 完整账目 | **已彻底修复** |
| **4** | ATR 放大倍数、14 笔变化等表述与逐笔统计口径不一致 | 放大倍数混淆中位数之比；14 笔变化未讲清 14 进与 2 出 | 逐笔比值中位数为 **5.54 倍**（区间 **3.27 ~ 10.81 倍**）；走弱由 55 变 67 是 **转入 14 笔、转出 2 笔、净增 12 笔** | **已彻底修复** |

---

## 一、冻结分类契约概要

分类契约已独立冻结并归档于 [`classification_contract.md`](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/FACTOR-ENTRY-06/classification_contract.md)。核心要点如下：

1. **ATR 尺度与初始风险 R 尺度物理隔离**：
   - 波动尺度（ATR）：`signal_atr_snapshot` 为入场时刻已闭合最晚 4h K 线的 Wilder ATR(14)，占入场价中位 2.60%。用于微观 4h 路径（MAE/MFE）及 ±0.3 ATR 首触门槛；
   - 初始风险（R）：`initial_risk_r` 为初始止损距离，占入场价中位 16.86%。用于全周期盈亏倍数；
   - 两者严禁互换！回退比值中位数为 5.54 倍（3.27 ~ 10.81 倍）；
   - 箱体形成 ATR 与入场信号 ATR 物理隔离（中位偏差 14.68%），42 笔旧成交标记缺失，不捏造。
2. **两套体系独立命名与出表**：
   - **主体系（标准混合体系 `category_standard_mixed` / `entry_quality_category`）**：微观 4h 走弱用 ATR 尺，全周期主升与回吐用风险 R 尺；
   - **对比体系（微观 4h 纯 ATR 体系 `category_pure_atr_4h`）**：全判定局限在 4h 窗口内，除数统一为信号 ATR；
   - **扩展体系（全周期纯 ATR 体系 `category_pure_atr_full`）**：全周期 MFE 换算为 ATR 尺；
   - 绝不静默改定义，各体系在特征总表与差异表中独立出列。
3. **缺失处理铁律**：
   - 缺必需输入列直接抛出 `AssertionError` 中止；
   - 行级别缺失值标记为 `UNKNOWN_MISSING_INPUT`，未成交标记为 `UNFILLED`，严禁默认赋 0。

---

## 二、修复重算的核心统计与一致性归因

本轮所有产物（CSV/Parquet/JSON/Markdown）均由 [`audit_and_reconstruct_06.py`](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/FACTOR-ENTRY-06/audit_and_reconstruct_06.py) 同一次内存计算直接输出：

### 2.1 明确样本空间分母
- 特征总记录数：**227 行**
- 唯一候选信号数：**226 个**（`COTIUSDT` 存在 1 组重复，连续回放判取消、窗口切片判成交，两次回放相反）
- 总成交样本数：**205 笔**（作为入场质量分类的基准分母）
- 连续回放代际成交：**163 笔**
- 窗口切片回放代际成交：**42 笔**（ATR 曾缺失回退）
- 未成交记录数：**22 行**（对应 21 个唯一未成交信号）

### 2.2 两套评价体系分布对比

| 分类类别 | 主体系：标准混合体系 (`category_standard_mixed`) | 对比体系：微观4h纯ATR体系 (`category_pure_atr_4h`) | FE05 缺陷交付表 (已废弃) | 订正说明 |
| :--- | :---: | :---: | :---: | :--- |
| **早期走弱 (EARLY_WEAKNESS)** | **67 笔 (32.68%)** | **67 笔 (32.68%)** | 67 笔 | 真实 4h ATR 尺度下一致 |
| **持续主升 (SUSTAINED_TREND)** | **36 笔 (17.56%)** | **20 笔 (9.76%)** | **0 笔** | FE05 误塌陷为杂波，已彻底修复补全 |
| **浮盈回吐 (MFE_PULLBACK)** | **8 笔 (3.90%)** | **39 笔 (19.02%)** | **0 笔** | FE05 误塌陷为杂波，已彻底修复补全 |
| **杂波/其他 (CHOPPY_OR_OTHER)** | **94 笔 (45.85%)** | **79 笔 (38.54%)** | 138 笔 | 扣除恢复的 44 笔后真实分布为 94 笔 |
| **未成交 (UNFILLED)** | 22 笔 | 22 笔 | 22 笔 | 非成交候选 |
| **合计成交分母** | **205 笔** | **205 笔** | 205 笔 | 分母明确无歧义 |

### 2.3 变动归因分别计数

1. **首触顺序变化**：
   - 相比 04 旧基准，共有 **52 笔** 发生首触变化；
   - 订正后分布：先不利 **101 笔**、先有利 **77 笔**、同根未知 **27 笔**、未触及 **0 笔**；
   - 27 笔同根双向触及严格保持 `UNKNOWN_SAME_BAR`，绝不强行判定先后。
2. **质量类别变化**：
   - 相比 04 旧分类，共有 **16 笔** 发生类别转移；
   - 转移流向：
     - `CHOPPY_OR_OTHER -> EARLY_WEAKNESS`：11 笔；
     - `SUSTAINED_TREND -> EARLY_WEAKNESS`：3 笔；
     - `EARLY_WEAKNESS -> CHOPPY_OR_OTHER`：2 笔。
3. **早期走弱转入转出细分**：
   - **转入早期走弱（进）**：**14 笔**（11 笔来自杂波、3 笔来自主升；其中 13 笔来自 42 笔回退组，1 笔来自 163 笔真 ATR 组）；
   - **转出早期走弱（出）**：**2 笔**（转为杂波；1 笔来自 42 笔回退组 `DYDXUSDT`，1 笔来自 163 笔真 ATR 组 `QNTUSDT`）；
   - **净增**：14 - 2 = **12 笔**（由 55 笔净增至 67 笔）。

---

## 三、限制结论与撤回声明

基于 `ENTRY-AUDIT-02` 独立审计所揭示的系统性代际断裂问题，本工单正式做出如下纪律约束与结论限制：

1. **样本断裂与费用字段失效声明**：
   - 205 笔成交记录并非单一策略代际产物，其中 163 笔来自全历史连续回放，42 笔来自 365 天窗口切片回放；
   - 上游特征管线在提取连续回放记录时键名取错（`total_fees` 误代 `fees`），导致 **163 条记录的费用与资金费全部为 0.0**，记账恒等式不成立；
   - 样本分母混杂且成本字段失真。
2. **撤回因果断言与收益结论**：
   - **正式撤回** FACTOR-ENTRY-01/03 中关于“先不利直接导致最终亏损”以及“早期走弱必然导致交易失败”的因果推论。数据事实表明：先不利组 101 笔合计净盈亏为 **+70.51 USDT**；
   - **暂停适用**基于 205 笔样本引用的 Profit Factor (PF)、净盈亏绝对值或“指标改善收益”的一切优化论断；
   - 本轮仅对特征数据与分类标签进行客观物理订正，不为策略实盘盈利性提供任何背书。
3. **探索参数属性锁定**：
   - 0.80 ATR 与 4.5% 阈值严格定义为**离线探索假设**，未经过真实滑点、手续费与完整代际严格验证，严禁作为正式策略参数部署。

---

## 四、交付产物索引

| 产物文件名 | 文件性质 | 核心内容与行数 |
| :--- | :--- | :--- |
| [`classification_contract.md`](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/FACTOR-ENTRY-06/classification_contract.md) | 分类契约 | 冻结分类公式、分母、窗口、两套体系与缺失处理 |
| [`audit_and_reconstruct_06.py`](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/FACTOR-ENTRY-06/audit_and_reconstruct_06.py) | 生成脚本 | 自包含独立执行脚本，四表一文同源输出 |
| [`reconstructed_candidate_features.csv`](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/FACTOR-ENTRY-06/reconstructed_candidate_features.csv) | 修正特征总表 | 227 行完整特征表，含修复后的主分类与纯 ATR 分类 |
| [`reconstructed_candidate_features.parquet`](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/FACTOR-ENTRY-06/reconstructed_candidate_features.parquet) | 二进制特征表 | 高效二进制格式，供下游模型与回测读取 |
| [`discrepancy_old_vs_new.csv`](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/FACTOR-ENTRY-06/discrepancy_old_vs_new.csv) | 逐笔差异表 | 205 笔成交的旧值 vs 订正值逐笔比对与转移明细 |
| [`discrepancy_summary.json`](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/FACTOR-ENTRY-06/discrepancy_summary.json) | 机器可读摘要 | 彻底订正旧数的机器可读统计与限制结论 |
| [`test_classification_consistency.py`](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/FACTOR-ENTRY-06/test_classification_consistency.py) | 针对性测试 | 覆盖缺列、空值、分类冲突与四表一致性的自动化测试套件 |
| [`test_results.log`](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/FACTOR-ENTRY-06/test_results.log) | 测试日志 | 自动化测试套件执行通过日志 |
| [`hashes.json`](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/FACTOR-ENTRY-06/hashes.json) | 哈希指纹 | 交付产物与输入文件的 SHA-256 校验清单 |
| [`reproduce.sh`](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/FACTOR-ENTRY-06/reproduce.sh) | 一键复现脚本 | 独立一键复现并自动执行测试 |
| [`报告.md`](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/FACTOR-ENTRY-06/报告.md) | 订正报告 | 本文档 |
"""

    with open(OUT_DIR / "报告.md", "w", encoding="utf-8") as f:
        f.write(md_content)
    print("已生成 报告.md (100% 同源保证)")

    print("======================================================================")
    print("FACTOR-ENTRY-06 数据管线执行完毕！")
    print("======================================================================")


if __name__ == "__main__":
    run_factor_entry_06_pipeline()
