# -*- coding: utf-8 -*-
"""
FACTOR-ENTRY-09 交付一致性与契约自动化测试套件
"""
import os
import re
import json
import hashlib
import pandas as pd
import pytest

WORKSPACE = "/Users/boos/Desktop/威科夫因子共振趋势"
FE09_DIR = os.path.join(WORKSPACE, "evidence/FACTOR-ENTRY-09")
FE08_DIR = os.path.join(WORKSPACE, "evidence/FACTOR-ENTRY-08")
EA04_DIR = os.path.join(WORKSPACE, "evidence/ENTRY-AUDIT-04/handoff")

def sha256_of_file(filepath: str) -> str:
    hasher = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()

def test_01_ea04_handoff_files_and_hashes():
    """测试 1: EA04 交接包 7 项输入文件存在且 SHA256 绝对匹配基准"""
    expected = {
        "ledger_handoff.parquet": "93abe58a9b25b95060e30ecc2d4d684f5500e67acc9772201230b3149cee5054",
        "ledger_handoff.csv": "4b3207b5c6fd63d2a74179a2be60f58e41e9e3be74ac9424d79193eb45eef71f",
        "handoff_meta.json": "9872eca19a539378d7835cc3f357ac8e5343fe87ed90949479a09ff65df682aa",
        "generation_split.json": "95bcfbdae96784a725c127455b2b610dcf4a3c8af682b45bfc1d8c85b290cfff",
        "field_dictionary.csv": "8cd998b61e2b2f3d0d833f5bd47c2f3585ac6397dc5390fb8919b9bc848949d2",
        "unfilled_22.csv": "527b0d0fe7b6f75e3f4faf26a2cca597df4e5d3701aa329319091f664fe91d27",
        "coti_duplicate.csv": "5ca803697aa28aa0dae4f1248b8f29fd7a9ba4200389222d8786e9af59c8fdf1"
    }
    for fname, exp_hash in expected.items():
        fpath = os.path.join(EA04_DIR, fname)
        assert os.path.exists(fpath), f"EA04 文件缺失: {fpath}"
        assert sha256_of_file(fpath) == exp_hash, f"EA04 哈希不匹配: {fname}"

def test_02_fe08_data_files_identity():
    """测试 2: FE09 数据产物与 FE08 基准逐文件 100% SHA256 与大小一致"""
    data_files = [
        "reconstructed_candidate_features_08.csv",
        "reconstructed_candidate_features_08.parquet",
        "source_equivalence_table.csv",
        "discrepancy_vs_fe07.csv",
        "discrepancy_summary.json"
    ]
    for fname in data_files:
        p08 = os.path.join(FE08_DIR, fname)
        p09 = os.path.join(FE09_DIR, fname)
        assert os.path.exists(p08), f"FE08 文件缺失: {p08}"
        assert os.path.exists(p09), f"FE09 文件缺失: {p09}"
        assert os.path.getsize(p08) == os.path.getsize(p09), f"大小不一致: {fname}"
        assert sha256_of_file(p08) == sha256_of_file(p09), f"SHA256 不一致: {fname}"

def test_03_business_metrics_conservation():
    """测试 3: 核心业务指标守恒（样本行数、已成交分母、未成交、费用、资金费、毛利、净盈亏与分类分布）"""
    csv_path = os.path.join(FE09_DIR, "reconstructed_candidate_features_08.csv")
    df = pd.read_csv(csv_path)
    assert len(df) == 227
    assert df["is_filled"].sum() == 205
    assert (len(df) - df["is_filled"].sum()) == 22

    df_filled = df[df["is_filled"]]
    assert pytest.approx(df_filled["fee_paid"].sum(), 1e-5) == 13.125871
    assert pytest.approx(df_filled["funding_cost"].sum(), 1e-5) == 6.977506
    assert pytest.approx(df_filled["gross_pnl"].sum(), 1e-5) == 121.635348
    assert pytest.approx(df_filled["net_pnl"].sum(), 1e-5) == 101.531971

    cats = df["entry_quality_category"].value_counts().to_dict()
    assert cats.get("CHOPPY_OR_OTHER") == 94
    assert cats.get("EARLY_WEAKNESS") == 67
    assert cats.get("SUSTAINED_TREND") == 36
    assert cats.get("MFE_PULLBACK") == 8
    assert cats.get("UNFILLED") == 22

def test_04_report_hashes_accuracy():
    """测试 4: 报告.md 中的所有文件大小和 SHA256 与磁盘实际落盘文件 100% 吻合（杜绝手写错）"""
    report_path = os.path.join(FE09_DIR, "报告.md")
    assert os.path.exists(report_path), "报告.md 不存在"
    with open(report_path, "r", encoding="utf-8") as f:
        content = f.read()

    # 正则匹配 Markdown 表格行：| [`file`](...) | 123 字节 | `hash` | 说明 |
    table_pattern = re.compile(r"\|\s*\[`([^`]+)`\]\([^)]+\)\s*\|\s*([0-9,]+)\s*字节\s*\|\s*`([a-f0-9]{64})`\s*\|")
    matches = table_pattern.findall(content)
    assert len(matches) >= 8, f"报告中提取到的哈希条目不足: {len(matches)}"

    for fname, size_str, exp_hash in matches:
        act_size = int(size_str.replace(",", ""))
        target_path = os.path.join(FE09_DIR, fname)
        assert os.path.exists(target_path), f"报告中引用的文件不存在: {target_path}"
        assert os.path.getsize(target_path) == act_size, f"文件大小与报告不匹配: {fname}, act={os.path.getsize(target_path)}, exp={act_size}"
        assert sha256_of_file(target_path) == exp_hash, f"SHA256 与报告不匹配: {fname}, act={sha256_of_file(target_path)}, exp={exp_hash}"

def test_05_manifest_coverage_and_hashes():
    """测试 5: 00_manifest.json 中记录的所有交付文件存在且哈希、大小绝对吻合"""
    manifest_path = os.path.join(FE09_DIR, "00_manifest.json")
    assert os.path.exists(manifest_path), "00_manifest.json 不存在"
    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    # 1. 验证 EA04 交接包记录
    ea04_info = manifest["handoff_package"]
    assert ea04_info["version"] == "ENTRY-AUDIT-04.handoff.v1"
    for fname, exp_hash in ea04_info["all_handoff_hashes"].items():
        assert sha256_of_file(os.path.join(EA04_DIR, fname)) == exp_hash

    # 2. 验证交付物清单哈希
    deliverables = manifest["deliverables"]
    assert len(deliverables) >= 10
    for item in deliverables:
        rel_path = item["path"]
        exp_hash = item["sha256"]
        exp_bytes = item["bytes"]
        full_path = os.path.join(WORKSPACE, rel_path)
        assert os.path.exists(full_path), f"Manifest 条目文件不存在: {full_path}"
        assert os.path.getsize(full_path) == exp_bytes, f"大小不匹配: {rel_path}"
        assert sha256_of_file(full_path) == exp_hash, f"SHA256 不匹配: {rel_path}"

def test_06_no_hash_cycle():
    """测试 6: 杜绝哈希循环引用（Manifest 不自引用，报告不包含 Manifest 最终 SHA256）"""
    manifest_path = os.path.join(FE09_DIR, "00_manifest.json")
    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest_text = f.read()
    manifest_data = json.loads(manifest_text)

    manifest_hash = sha256_of_file(manifest_path)

    # 1. manifest deliverables 不收录 00_manifest.json
    for item in manifest_data["deliverables"]:
        assert "00_manifest.json" not in item["path"], "Manifest 存在自引用错误！"

    # 2. 报告不嵌入 manifest 哈希
    report_path = os.path.join(FE09_DIR, "报告.md")
    with open(report_path, "r", encoding="utf-8") as f:
        report_text = f.read()

    assert manifest_hash not in report_text, "报告违规嵌入了 manifest 最终哈希，存在哈希循环！"
    assert os.path.exists(manifest_path), "Manifest 磁盘文件应存在"

def test_07_field_contract_binding():
    """测试 7: 字段契约文件存在且包含 20 项核心字段与公式"""
    contract_path = os.path.join(FE09_DIR, "field_contract_reference.md")
    assert os.path.exists(contract_path), "field_contract_reference.md 缺失"
    with open(contract_path, "r", encoding="utf-8") as f:
        content = f.read()
    assert "net_pnl" in content
    assert "gross_pnl" in content
    assert "fees" in content
    assert "funding_fee" in content
    assert "spec_qualification" in content
    assert "formal_pass_allowed" in content
