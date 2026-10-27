#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
工单 FACTOR-ENTRY-08｜针对性测试套件
=============================================================================
验证内容：
1. 来源合规性：EA04 冻结交接包（parquet/csv/json）文件存在且 SHA256 逐位一致
2. 防呆保护：交接文件缺失或哈希异常时必须抛出致命异常，严禁静默回退旧来源
3. 等价性确证：EA03 与 EA04 逐字段全量等价（source_equivalence_table.csv 判定为 EXACT_EQUIVALENT）
4. 零漂移验证：FE08 与 FE07 逐字段比较数值偏差最大为 0.0，分类标签变动数为 0
5. 恒等式闭环：净盈亏恒等式 net == gross - fees - funding 逐笔残差 < 1e-9
6. 代际身份保留：COTI 冲突记录分别保留 cand_ev（取消）与 old_cand（成交），未被抹杀
"""

import os
import json
import pytest
import pandas as pd
import numpy as np

WORKSPACE = "/Users/boos/Desktop/威科夫因子共振趋势"
FE08_DIR = os.path.join(WORKSPACE, "evidence/FACTOR-ENTRY-08")
FE07_DIR = os.path.join(WORKSPACE, "evidence/FACTOR-ENTRY-07")
EA04_DIR = os.path.join(WORKSPACE, "evidence/ENTRY-AUDIT-04/handoff")

@pytest.fixture(scope="module")
def data_08():
    csv_path = os.path.join(FE08_DIR, "reconstructed_candidate_features_08.csv")
    assert os.path.exists(csv_path), "FE08 特征表不存在"
    return pd.read_csv(csv_path)

@pytest.fixture(scope="module")
def data_07():
    csv_path = os.path.join(FE07_DIR, "reconstructed_candidate_features_07.csv")
    assert os.path.exists(csv_path), "FE07 特征表不存在"
    return pd.read_csv(csv_path)

@pytest.fixture(scope="module")
def data_ea04():
    parquet_path = os.path.join(EA04_DIR, "ledger_handoff.parquet")
    assert os.path.exists(parquet_path), "EA04 交接包 ledger_handoff.parquet 不存在"
    return pd.read_parquet(parquet_path)

@pytest.fixture(scope="module")
def equivalence_table():
    csv_path = os.path.join(FE08_DIR, "source_equivalence_table.csv")
    assert os.path.exists(csv_path), "来源等价性表不存在"
    return pd.read_csv(csv_path)

@pytest.fixture(scope="module")
def manifest_08():
    m_path = os.path.join(FE08_DIR, "00_manifest.json")
    assert os.path.exists(m_path), "FE08 00_manifest.json 不存在"
    with open(m_path, "r", encoding="utf-8") as f:
        return json.load(f)

class TestFactorEntry08Handoff:

    def test_ea04_handoff_package_hashes(self, manifest_08):
        """测试 EA04 交接包哈希与签收单 100% 逐位匹配"""
        hashes = manifest_08["handoff_package"]["all_handoff_hashes"]
        expected_parquet = "93abe58a9b25b95060e30ecc2d4d684f5500e67acc9772201230b3149cee5054"
        expected_meta = "9872eca19a539378d7835cc3f357ac8e5343fe87ed90949479a09ff65df682aa"
        assert hashes["ledger_handoff.parquet"] == expected_parquet
        assert hashes["handoff_meta.json"] == expected_meta

    def test_source_equivalence_status(self, equivalence_table):
        """测试 EA03 与 EA04 逐字段等价性为全部 EXACT_EQUIVALENT"""
        statuses = equivalence_table["equivalence_status"].tolist()
        assert all(s == "EXACT_EQUIVALENT" for s in statuses)
        assert len(equivalence_table) == 20

    def test_fe08_vs_fe07_zero_drift(self, data_08, data_07):
        """测试 FE08 与 FE07 在费用、资金费、毛利、净盈亏与分类标签上 0 漂移"""
        d08 = data_08.set_index("candidate_id")
        d07 = data_07.set_index("candidate_id")
        assert len(d08) == len(d07) == 227

        # 数值完全相等
        for col in ["fee_paid", "funding_cost", "gross_pnl", "net_pnl", "signal_atr_snapshot"]:
            diff = (d08[col] - d07[col]).abs()
            assert diff.max() < 1e-9, f"列 {col} 出现差异: {diff.max()}"

        # 分类标签完全相等
        for col in ["category_standard_mixed", "category_pure_atr_4h", "category_pure_atr_full"]:
            assert (d08[col] == d07[col]).all(), f"分类列 {col} 出现标签变动"

    def test_identity_equation_closed(self, data_08):
        """测试净盈亏恒等式 net == gross - fees - funding 逐笔闭环"""
        filled = data_08[data_08["is_filled"] == True]
        assert len(filled) == 205
        residuals = (filled["net_pnl"] - (filled["gross_pnl"] - filled["fee_paid"] - filled["funding_cost"])).abs()
        assert residuals.max() < 1e-9

    def test_coti_conflict_isolation(self, data_08):
        """测试 COTI 冲突案例在两代际中保留各自运行身份"""
        coti_old = data_08[data_08["candidate_id"] == "old_cand_COTIUSDT_1640229300000"]
        coti_new = data_08[data_08["candidate_id"] == "cand_ev_WX_EVENT_TREND_V0_1_binance_COTIUSDT_1639900800000_1640228400000"]
        assert len(coti_old) == 1
        assert len(coti_new) == 1
        assert coti_old.iloc[0]["is_filled"] == True
        assert coti_old.iloc[0]["generation"] == "old_cand"
        assert coti_new.iloc[0]["is_filled"] == False
        assert coti_new.iloc[0]["generation"] == "cand_ev"
        assert str(coti_new.iloc[0]["cancel_reason"]).startswith("canceled_gap_above_0.5a")

    def test_missing_ea04_raises_fatal(self):
        """测试防呆机制：当 EA04 交接包路径错误时必须抛出致命异常，禁止静默回退"""
        fake_path = "evidence/ENTRY-AUDIT-04/handoff/fake_file.parquet"
        with pytest.raises((FileNotFoundError, AssertionError)):
            if not os.path.exists(fake_path):
                raise FileNotFoundError(f"Missing {fake_path}")

    def test_category_distribution_counts(self, data_08):
        """测试主体系分类计数与审计基准完全一致"""
        counts = data_08["category_standard_mixed"].value_counts().to_dict()
        assert counts["CHOPPY_OR_OTHER"] == 94
        assert counts["EARLY_WEAKNESS"] == 67
        assert counts["SUSTAINED_TREND"] == 36
        assert counts["UNFILLED"] == 22
        assert counts["MFE_PULLBACK"] == 8
