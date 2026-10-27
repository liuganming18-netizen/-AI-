#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
工单 FACTOR-ENTRY-07｜针对性一致性与断言测试套件
=============================================================================
验证内容：
1. 缺列立即抛出 AssertionError / KeyError
2. 单行关键指标缺失显式标为 UNKNOWN_MISSING_INPUT
3. 逐笔净盈亏恒等式严格闭环（net == gross - fee - funding，残差 < 1e-9）
4. 不依赖费用的 ATR、早期走弱、首触顺序完全一致
5. COTI 冲突记录代际身份严格保留，未被强行撮合
6. 205 笔已成交 + 22 笔未成交总分母严格自洽
"""

import os
import json
import pytest
import pandas as pd
import numpy as np

WORKSPACE = "/Users/boos/Desktop/威科夫因子共振趋势"
FE07_DIR = os.path.join(WORKSPACE, "evidence/FACTOR-ENTRY-07")
FE06_DIR = os.path.join(WORKSPACE, "evidence/FACTOR-ENTRY-06")

@pytest.fixture(scope="module")
def data_07():
    csv_path = os.path.join(FE07_DIR, "reconstructed_candidate_features_07.csv")
    assert os.path.exists(csv_path), "FE07 特征表不存在"
    return pd.read_csv(csv_path)

@pytest.fixture(scope="module")
def data_06():
    csv_path = os.path.join(FE06_DIR, "reconstructed_candidate_features.csv")
    assert os.path.exists(csv_path), "FE06 特征表不存在"
    return pd.read_csv(csv_path)

@pytest.fixture(scope="module")
def summary_07():
    json_path = os.path.join(FE07_DIR, "discrepancy_summary.json")
    assert os.path.exists(json_path), "FE07 汇总 json 不存在"
    with open(json_path, "r", encoding="utf-8") as f:
        return json.load(f)

class TestFactorEntry07Integrity:

    def test_total_samples_and_split(self, data_07):
        """测试样本总数与已成交/未成交严格符合 227 = 205 + 22"""
        assert len(data_07) == 227
        filled = data_07[data_07["is_filled"] == True]
        unfilled = data_07[data_07["is_filled"] == False]
        assert len(filled) == 205
        assert len(unfilled) == 22

    def test_generations_split(self, data_07):
        """测试代际拆分: 连续 185 (163成交+22未成交), 窗口 42 (全部成交)"""
        gen_a = data_07[data_07["generation"] == "cand_ev"]
        gen_b = data_07[data_07["generation"] == "old_cand"]
        assert len(gen_a) == 185
        assert (gen_a["is_filled"] == True).sum() == 163
        assert (gen_a["is_filled"] == False).sum() == 22
        assert len(gen_b) == 42
        assert (gen_b["is_filled"] == True).sum() == 42
        assert (gen_b["is_filled"] == False).sum() == 0

    def test_cost_and_net_identity_equation(self, data_07):
        """测试逐笔净盈亏恒等式: net_pnl == gross_pnl - fee_paid - funding_cost"""
        filled = data_07[data_07["is_filled"] == True]
        residuals = filled["net_pnl"] - (filled["gross_pnl"] - filled["fee_paid"] - filled["funding_cost"])
        max_res = residuals.abs().max()
        assert max_res < 1e-9, f"净盈亏恒等式最大残差超标: {max_res}"

    def test_fee_nonzero_for_163_continuous(self, data_07):
        """测试连续代际 163 笔成交的真实费用全部非零"""
        gen_a_filled = data_07[(data_07["generation"] == "cand_ev") & (data_07["is_filled"] == True)]
        assert len(gen_a_filled) == 163
        # 真实手续费全部 > 0
        assert (gen_a_filled["fee_paid"] > 0).all()
        # 真实资金费全部非零
        assert (gen_a_filled["funding_cost"] != 0).all()
        # 手续费合计精确匹配 10.308546
        assert abs(gen_a_filled["fee_paid"].sum() - 10.308545635009995) < 1e-5

    def test_micro_features_invariance_vs_fe06(self, data_07, data_06):
        """测试不依赖费用的微观指标与 FE06 严格一致 (ATR, 4h收益, 4h MFE/MAE, 首触等)"""
        d07 = data_07.set_index("candidate_id")
        d06 = data_06.set_index("candidate_id")

        # 检查关键不依赖费用的字段
        cols_to_check = [
            "signal_atr_snapshot", "fwd_ret_4h", "mfe_r_4h", "mae_r_4h",
            "first_move_4h", "mfe_r", "trade_mfe_pct", "mfe_atr_full"
        ]
        for col in cols_to_check:
            diff = (d07[col].fillna(0) - d06[col].fillna(0)).abs() if d07[col].dtype.kind in 'biufc' else (d07[col] != d06[col])
            if d07[col].dtype.kind in 'biufc':
                assert diff.max() < 1e-6, f"字段 {col} 出现非预期数值变动: {diff.max()}"
            else:
                assert diff.sum() == 0, f"字段 {col} 出现非预期枚举变动"

    def test_coti_conflict_isolation(self, data_07):
        """测试 COTI 冲突案例在两代际中保留各自运行身份，且结论自洽"""
        coti_old = data_07[data_07["candidate_id"] == "old_cand_COTIUSDT_1640229300000"]
        coti_new = data_07[data_07["candidate_id"] == "cand_ev_WX_EVENT_TREND_V0_1_binance_COTIUSDT_1639900800000_1640228400000"]
        assert len(coti_old) == 1
        assert len(coti_new) == 1
        # 窗口回放判成交
        assert coti_old.iloc[0]["is_filled"] == True
        assert coti_old.iloc[0]["generation"] == "old_cand"
        # 连续回放判未成交 (跳空越线)
        assert coti_new.iloc[0]["is_filled"] == False
        assert coti_new.iloc[0]["generation"] == "cand_ev"
        assert str(coti_new.iloc[0]["cancel_reason"]).startswith("canceled_gap_above_0.5a")

    def test_missing_input_guard(self):
        """测试分类函数防呆契约：缺少必需列抛出异常，单行空值标为 UNKNOWN_MISSING_INPUT"""
        from generate_factor_entry_07 import classify_standard_mixed
        # 模拟缺少 net_pnl
        row_missing_col = {"is_filled": True, "fwd_ret_4h": -0.01, "mfe_r_4h": 0.2, "mae_r_4h": 0.8, "first_move_4h": "FIRST_ADVERSE", "mfe_r": 0.5}
        with pytest.raises(KeyError):
            classify_standard_mixed(row_missing_col)
        
        # 模拟 net_pnl 为 NaN
        row_nan_val = {"is_filled": True, "fwd_ret_4h": -0.01, "mfe_r_4h": 0.2, "mae_r_4h": 0.8, "first_move_4h": "FIRST_ADVERSE", "mfe_r": 0.5, "net_pnl": np.nan}
        assert classify_standard_mixed(row_nan_val) == "UNKNOWN_MISSING_INPUT"

    def test_category_counts_stability(self, summary_07):
        """测试分类统计结果与报告强一致"""
        std = summary_07["classification_results"]["standard_mixed_category"]
        assert std["EARLY_WEAKNESS"] == 67
        assert std["SUSTAINED_TREND"] == 36
        assert std["MFE_PULLBACK"] == 8
        assert std["CHOPPY_OR_OTHER"] == 94
        assert std["UNFILLED"] == 22
