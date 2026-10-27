# -*- coding: utf-8 -*-
"""
FLOW-OI-01 自动化一致性与契约测试套件
"""
import os
import json
import hashlib
import pandas as pd
import pytest

WORKSPACE = "/Users/boos/Desktop/威科夫因子共振趋势"
OUT_DIR = os.path.join(WORKSPACE, "evidence/FLOW-OI-01")
FE09_PARQUET = os.path.join(WORKSPACE, "evidence/FACTOR-ENTRY-09/reconstructed_candidate_features_08.parquet")

def sha256_of_file(filepath: str) -> str:
    hasher = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()

def test_01_candidate_baseline_alignment():
    """测试 1: 候选样本基准严格对齐 FE09 (227行, 205笔已成交, 22笔未成交)"""
    feat_path = os.path.join(OUT_DIR, "candidate_flow_oi_features.parquet")
    assert os.path.exists(feat_path), "特征底表缺失"
    df_feat = pd.read_parquet(feat_path)
    df_fe09 = pd.read_parquet(FE09_PARQUET)

    assert len(df_feat) == 227
    assert len(df_feat) == len(df_fe09)
    assert df_feat["is_filled"].sum() == 205
    assert (len(df_feat) - df_feat["is_filled"].sum()) == 22
    assert list(df_feat["candidate_id"]) == list(df_fe09["candidate_id"])

def test_02_data_coverage_completeness():
    """测试 2: 数据覆盖率 100% 对齐，无样本筛选排除"""
    cov_path = os.path.join(OUT_DIR, "03_coverage_and_exclusions.csv")
    assert os.path.exists(cov_path), "覆盖表缺失"
    df_cov = pd.read_csv(cov_path)
    assert len(df_cov) == 227
    assert df_cov["has_c1h"].all(), "1h K线存在未对齐样本"
    assert df_cov["has_metrics"].all(), "Metrics 存在未对齐样本"
    assert df_cov["has_oi"].all(), "OI 存在未对齐样本"
    assert (df_cov["coverage_status"] == "COMPLETE").all()

def test_03_field_contract_and_bounds():
    """测试 3: 严格对齐 FLOW-DATA-01 字段契约与数值边界"""
    feat_path = os.path.join(OUT_DIR, "candidate_flow_oi_features.csv")
    df = pd.read_csv(feat_path)

    # 主动买入占比在 [0, 1] 区间
    valid_tbr = df["bar_taker_buy_ratio"].dropna()
    assert (valid_tbr >= 0.0).all() and (valid_tbr <= 1.0).all()

    # 归一化 CVD 在 [-1, 1] 区间
    valid_ncvd = df["norm_cvd_24h"].dropna()
    assert (valid_ncvd >= -1.0).all() and (valid_ncvd <= 1.0).all()

    # 主动买卖额恒等式
    valid_q = df[df["bar_quote_volume"].notna()]
    for idx, r in valid_q.iterrows():
        b_q = r["bar_quote_volume"]
        tb_q = r["bar_taker_buy_quote"]
        ts_q = r["bar_taker_sell_quote"]
        assert pytest.approx(tb_q + ts_q, 1e-4) == b_q

def test_04_four_groups_nesting_logic():
    """测试 4: 四组对照嵌套逻辑递进 (G1 >= G2 >= G3 >= G4)"""
    comp_path = os.path.join(OUT_DIR, "four_groups_comparison.csv")
    df = pd.read_csv(comp_path)
    df_f = df[df["is_filled"]]

    assert df_f["pass_g1"].sum() == 205
    assert df_f["pass_g2"].sum() <= df_f["pass_g1"].sum()
    assert df_f["pass_g3"].sum() <= df_f["pass_g2"].sum()
    assert df_f["pass_g4"].sum() <= df_f["pass_g3"].sum()

    # 确保后一组通过的，前一组必然通过
    assert (df_f[df_f["pass_g4"]]["pass_g3"]).all()
    assert (df_f[df_f["pass_g3"]]["pass_g2"]).all()
    assert (df_f[df_f["pass_g2"]]["pass_g1"]).all()

def test_05_business_metrics_and_generations():
    """测试 5: 基线收益守恒与两代际分层表现"""
    json_path = os.path.join(OUT_DIR, "four_groups_comparison.json")
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    g1 = data["overall"][0]
    assert pytest.approx(g1["net_pnl"], 1e-2) == 101.53
    assert g1["trades"] == 205
    assert g1["early_weakness_count"] == 67

    # 两代际分层守恒
    cand_ev_g1 = data["splits_by_generation"]["cand_ev"]["g1"]
    old_cand_g1 = data["splits_by_generation"]["old_cand"]["g1"]
    assert pytest.approx(cand_ev_g1["net_pnl"], 1e-2) == 144.11
    assert pytest.approx(old_cand_g1["net_pnl"], 1e-2) == -42.57

def test_06_case_studies_coverage():
    """测试 6: 8 个预定规则代表案例完整性与深入归因"""
    case_path = os.path.join(OUT_DIR, "05_case_studies.md")
    assert os.path.exists(case_path), "05_case_studies.md 不存在"
    with open(case_path, "r", encoding="utf-8") as f:
        content = f.read()

    for i in range(1, 9):
        assert f"## Case {i}:" in content, f"Case {i} 缺失"
    assert "SAFEUSDT" in content, "轧空反例 SAFEUSDT 缺失"
    assert "SOONUSDT" in content, "超级赢家误伤案例 SOONUSDT 缺失"

def test_07_manifest_and_hashes():
    """测试 7: Manifest 与 hashes 完整性与无循环引用"""
    manifest_path = os.path.join(OUT_DIR, "00_manifest.json")
    hashes_path = os.path.join(OUT_DIR, "hashes.json")
    assert os.path.exists(manifest_path)
    assert os.path.exists(hashes_path)

    with open(manifest_path, "r", encoding="utf-8") as f:
        m_data = json.load(f)

    # manifest 不自引用
    for item in m_data["deliverables"]:
        assert "00_manifest.json" not in item["name"]
        fpath = os.path.join(WORKSPACE, item["path"])
        assert os.path.exists(fpath)
        assert os.path.getsize(fpath) == item["bytes"]
        assert sha256_of_file(fpath) == item["sha256"]
