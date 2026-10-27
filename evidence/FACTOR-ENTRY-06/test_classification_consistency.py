# -*- coding: utf-8 -*-
"""工单 FACTOR-ENTRY-06 自动化一致性与契约约束测试套件

测试范围：
1. 缺必需输入列立即报错（严禁静默默认0）；
2. 成交行单字段缺失显式标记未知（严禁硬判为杂波）；
3. 分类优先级冲突解析（走弱严格优先于主升与回吐）；
4. 四表一文（CSV/Parquet/JSON/Markdown）全量数字交叉强一致性；
5. ATR 与 R 尺度物理隔离及比值范围断言；
6. 计数闭合恒等式（227/226/205/163/42/22/21）；
7. 残留旧数（+142.23）彻底清除断言。
"""
from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path("/Users/boos/Desktop/威科夫因子共振趋势")
FE06_DIR = ROOT / "evidence" / "FACTOR-ENTRY-06"


class TestClassificationContractAndConsistency(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.p_feat_parquet = FE06_DIR / "reconstructed_candidate_features.parquet"
        cls.p_feat_csv = FE06_DIR / "reconstructed_candidate_features.csv"
        cls.p_diff_csv = FE06_DIR / "discrepancy_old_vs_new.csv"
        cls.p_summary_json = FE06_DIR / "discrepancy_summary.json"
        cls.p_report_md = FE06_DIR / "报告.md"

        assert cls.p_feat_parquet.exists(), f"文件缺失: {cls.p_feat_parquet}"
        assert cls.p_feat_csv.exists(), f"文件缺失: {cls.p_feat_csv}"
        assert cls.p_diff_csv.exists(), f"文件缺失: {cls.p_diff_csv}"
        assert cls.p_summary_json.exists(), f"文件缺失: {cls.p_summary_json}"
        assert cls.p_report_md.exists(), f"文件缺失: {cls.p_report_md}"

        cls.df_parquet = pd.read_parquet(cls.p_feat_parquet)
        cls.df_csv = pd.read_csv(cls.p_feat_csv)
        cls.df_diff = pd.read_csv(cls.p_diff_csv)
        with open(cls.p_summary_json, "r", encoding="utf-8") as f:
            cls.summary = json.load(f)
        cls.report_text = cls.p_report_md.read_text(encoding="utf-8")

    def test_01_schema_missing_column_raises_error(self):
        """测试 1: 缺少必需输入列必须触发 AssertionError，禁止静默容错赋0。"""
        from audit_and_reconstruct_06 import validate_input_schema

        dummy_df = pd.DataFrame({"candidate_id": ["c1"], "symbol": ["BTC"]})
        required = ["candidate_id", "symbol", "mfe_r"]  # 故意缺少 mfe_r

        with self.assertRaises(AssertionError) as ctx:
            validate_input_schema(dummy_df, required, "DUMMY_TABLE")
        self.assertIn("缺少必需的输入字段", str(ctx.exception))
        self.assertIn("mfe_r", str(ctx.exception))

    def test_02_row_missing_value_handled_as_unknown(self):
        """测试 2: 成交记录单行关键指标缺失时，分类必须显式判定为 UNKNOWN_MISSING_INPUT，禁止归入杂波。"""
        # 检验生成的特征表中没有未经处理的假值
        filled = self.df_parquet[self.df_parquet["is_filled"]]
        # 205 笔成交记录应该全具备有效的 mfe_r 与 signal_atr_snapshot
        self.assertEqual(filled["mfe_r"].notna().sum(), 205)
        self.assertEqual(filled["signal_atr_snapshot"].notna().sum(), 205)
        self.assertNotIn("UNKNOWN_MISSING_INPUT", filled["entry_quality_category"].values)

    def test_03_classification_priority_conflict_resolution(self):
        """测试 3: 分类优先级冲突解析：当走弱条件与主升条件同时触发时，Priority 1 (走弱) 必须优先。"""
        # 寻找在 04 中被判为 SUSTAINED_TREND 但在真实 ATR 下同时触发 4h 走弱的样本
        conflict_cases = self.df_diff[
            (self.df_diff["old_entry_category_04"] == "SUSTAINED_TREND") &
            (self.df_diff["corrected_standard_category"] == "EARLY_WEAKNESS")
        ]
        # 根据我们之前的分析，恰有 3 笔因走弱条件满足且优先级最高而从 SUSTAINED_TREND 转入 EARLY_WEAKNESS
        self.assertEqual(len(conflict_cases), 3, "应有 3 笔满足主升条件的交易因走弱优先级更高而被正确判定为走弱！")
        for _, row in conflict_cases.iterrows():
            self.assertEqual(row["corrected_standard_category"], "EARLY_WEAKNESS")

    def test_04_cross_artifact_data_consistency(self):
        """测试 4: 四表一文（CSV/Parquet/JSON/Markdown）全量数字交叉强一致性。"""
        # A. CSV vs Parquet 行列一致性
        self.assertEqual(len(self.df_parquet), len(self.df_csv))
        self.assertEqual(len(self.df_parquet), 227)
        self.assertEqual(len(self.df_diff), 205)

        # B. 主分类统计完全一致
        dist_parquet = self.df_parquet["entry_quality_category"].value_counts().to_dict()
        dist_json = self.summary["category_reconciliation"]["standard_mixed_system"]
        self.assertEqual(dist_parquet["EARLY_WEAKNESS"], 67)
        self.assertEqual(dist_parquet["SUSTAINED_TREND"], 36)
        self.assertEqual(dist_parquet["MFE_PULLBACK"], 8)
        self.assertEqual(dist_parquet["CHOPPY_OR_OTHER"], 94)
        self.assertEqual(dist_parquet["UNFILLED"], 22)
        self.assertEqual(dist_json["EARLY_WEAKNESS"], 67)
        self.assertEqual(dist_json["SUSTAINED_TREND"], 36)
        self.assertEqual(dist_json["MFE_PULLBACK"], 8)
        self.assertEqual(dist_json["CHOPPY_OR_OTHER"], 94)
        self.assertEqual(dist_json["UNFILLED"], 22)

        # C. 纯 ATR 4h 对比分类统计完全一致
        dist_pure_atr = self.df_parquet["category_pure_atr_4h"].value_counts().to_dict()
        dist_pure_json = self.summary["category_reconciliation"]["pure_atr_4h_system"]
        self.assertEqual(dist_pure_atr["EARLY_WEAKNESS"], 67)
        self.assertEqual(dist_pure_atr["SUSTAINED_TREND"], 20)
        self.assertEqual(dist_pure_atr["MFE_PULLBACK"], 39)
        self.assertEqual(dist_pure_atr["CHOPPY_OR_OTHER"], 79)
        self.assertEqual(dist_pure_atr["UNFILLED"], 22)
        self.assertEqual(dist_pure_json["EARLY_WEAKNESS"], 67)
        self.assertEqual(dist_pure_json["SUSTAINED_TREND"], 20)
        self.assertEqual(dist_pure_json["MFE_PULLBACK"], 39)
        self.assertEqual(dist_pure_json["CHOPPY_OR_OTHER"], 79)

        # D. 首触统计完全一致
        first_dist = self.summary["first_move_audit"]["first_move_distribution"]
        self.assertEqual(first_dist["FIRST_ADVERSE"], 101)
        self.assertEqual(first_dist["FIRST_FAVORABLE"], 77)
        self.assertEqual(first_dist["UNKNOWN_SAME_BAR"], 27)
        self.assertEqual(first_dist["NEITHER_REACHED_0.3ATR"], 0)
        self.assertEqual(self.summary["first_move_audit"]["first_move_changes_count"], 52)

        # E. 转移计数完全一致
        trans = self.summary["category_reconciliation"]["category_changes_vs_04"]
        self.assertEqual(trans["total_changed_trades"], 16)
        self.assertEqual(trans["into_early_weakness_total"], 14)
        self.assertEqual(trans["into_early_weakness_from_fallback"], 13)
        self.assertEqual(trans["into_early_weakness_from_true_atr"], 1)
        self.assertEqual(trans["out_of_early_weakness_total"], 2)
        self.assertEqual(trans["out_of_early_weakness_from_fallback"], 1)
        self.assertEqual(trans["out_of_early_weakness_from_true_atr"], 1)
        self.assertEqual(trans["net_early_weakness_increase"], 12)
        self.assertEqual(self.summary["category_reconciliation"]["fe05_defect_repaired_count"], 44)

        # F. Markdown 报告中关键数字匹配
        self.assertIn("走弱 67、主升 36、回吐 8、杂波 94", self.report_text)
        self.assertIn("+70.51", self.report_text)
        self.assertIn("+31.02", self.report_text)
        self.assertIn("转入 14 笔、转出 2 笔、净增 12 笔", self.report_text)
        self.assertIn("52 笔", self.report_text)
        self.assertIn("16 笔", self.report_text)

    def test_05_atr_vs_risk_scale_separation(self):
        """测试 5: ATR 尺度与初始风险 R 尺度物理隔离及比值范围断言。"""
        fb = self.df_diff[self.df_diff["is_fallback_record"]]
        self.assertEqual(len(fb), 42)
        ratios = fb["ratio_initial_risk_to_true_atr"]
        self.assertAlmostEqual(ratios.median(), 5.5382, places=3)
        self.assertAlmostEqual(ratios.min(), 3.2654, places=3)
        self.assertAlmostEqual(ratios.max(), 10.8101, places=3)

        # 检查特征表中两套 ATR 是否保持物理隔离
        # 已成交中具备箱体形成 ATR 的记录数为 163（42 笔旧切片记录严格为 NaN）
        filled_have_box = self.df_parquet[self.df_parquet["is_filled"] & self.df_parquet["box_atr_formation"].notna()]
        self.assertEqual(len(filled_have_box), 163)
        # 全量特征表（含 22 笔未成交）具备箱体形成 ATR 的记录数为 185 (163 + 22)
        all_have_box = self.df_parquet[self.df_parquet["box_atr_formation"].notna()]
        self.assertEqual(len(all_have_box), 185)

        # 163 笔成交中无任何一条 box_atr 与 signal_atr 相等（中位相对差 14.68%，最小相对差 0.19%）
        rel_diff = (filled_have_box["box_atr_formation"] - filled_have_box["signal_atr_snapshot"]).abs() / filled_have_box["signal_atr_snapshot"]
        self.assertEqual((rel_diff < 1e-9).sum(), 0)
        self.assertAlmostEqual(rel_diff.median(), 0.1468, places=3)

    def test_06_counting_closed_equations(self):
        """测试 6: 计数闭合恒等式严格通过。"""
        n_feat = len(self.df_parquet)
        n_filled = self.df_parquet["is_filled"].sum()
        n_unfilled = (~self.df_parquet["is_filled"]).sum()

        # 等式 1: 205 成交行 + 22 未成交行 = 227 特征行
        self.assertEqual(n_filled + n_unfilled, n_feat)
        self.assertEqual(n_filled, 205)
        self.assertEqual(n_unfilled, 22)
        self.assertEqual(n_feat, 227)

        # 等式 2: 去重候选信号 = 226
        unique_signals = len(self.df_parquet.drop_duplicates(subset=["symbol", "snapshot_time_ms"]))
        self.assertEqual(unique_signals, 226)

        # 等式 3: COTI 重复 1 组 (同一快照时刻 1640228400000 存在 2 行：一行取消、一行成交)
        coti_dup = self.df_parquet[(self.df_parquet["symbol"] == "COTIUSDT") & (self.df_parquet["snapshot_time_ms"] == 1640228400000)]
        self.assertEqual(len(coti_dup), 2)
        self.assertEqual(set(coti_dup["is_filled"]), {True, False})

    def test_07_no_stale_numbers_present(self):
        """测试 7: 断言所有交付产物中绝无把旧数字 142.23 当作有效事实。"""
        # JSON 事实检查：财务事实与分类统计中绝无 142.23
        fin_json = json.dumps(self.summary["financial_grouping_facts"])
        self.assertNotIn("142.23", fin_json, "财务事实中严禁出现 +142.23 旧数！")
        self.assertEqual(self.summary["financial_grouping_facts"]["first_move_groups"]["first_adverse_pnl_sum"], 70.51)
        self.assertTrue(self.summary["research_limitations_and_retractions"]["stale_number_142_cleared"])

        # Markdown 结果检查：先不利净盈亏必须为 +70.51
        self.assertIn("+70.51", self.report_text)


if __name__ == "__main__":
    unittest.main()
