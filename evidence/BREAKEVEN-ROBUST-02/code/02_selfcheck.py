#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""校验 ROBUST-02 报告、CSV、复算结果及输入指纹的一致性。"""
from __future__ import annotations

import csv
import hashlib
import json
from decimal import Decimal
from pathlib import Path

ROOT = Path("/Users/boos/Desktop/威科夫因子共振趋势")
BASE = ROOT / "evidence/BREAKEVEN-ROBUST-02"
OUT = BASE / "out"


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def main() -> int:
    result = json.loads((OUT / "independent_results.json").read_text(encoding="utf-8"))
    fingerprints = json.loads((OUT / "输入版本指纹.json").read_text(encoding="utf-8"))
    report = (BASE / "验收报告.md").read_text(encoding="utf-8")
    trades = read_csv(OUT / "逐笔贡献与变化.csv")
    jackknife = read_csv(OUT / "逐笔移除变号复核.csv")
    source_diff = read_csv(OUT / "来源事件差异.csv")
    c_gain = read_csv(OUT / "C唯一增益交易.csv")
    compare = read_csv(OUT / "52.4299_vs_52.5423对照.csv")

    assert len(trades) == 201
    assert sum((Decimal(row["A_net_U"]) for row in trades), Decimal(0)) == Decimal(result["A_net_U"])
    assert sum((Decimal(row["B_1R_net_U"]) for row in trades), Decimal(0)) == Decimal(result["B_1R_net_U"])
    assert sum((Decimal(row["C_2R_net_U"]) for row in trades), Decimal(0)) == Decimal(result["C_2R_net_U"])
    assert sum(row["B变化"] == "改善" for row in trades) == result["B_improved_n"] == 12
    assert sum(row["B变化"] == "恶化" for row in trades) == result["B_worsened_n"] == 16
    assert len(jackknife) == 28
    assert sum(row["相对基线变号"] == "true" for row in jackknife) == result["B_jackknife_sign_flip_n"] == 1
    assert len(source_diff) == 1 and source_diff[0]["symbol"] == "OPUSDT"
    assert Decimal(source_diff[0]["B_delta_BE02_minus_BE01_U"]) == Decimal("-0.112375")
    assert len(c_gain) == 1 and c_gain[0]["symbol"] == "1000LUNCUSDT"
    assert Decimal(c_gain[0]["移除后C_minus_A_U"]) == Decimal(0)
    assert len(compare) == 3
    assert Decimal(compare[0]["B_net_U"]).quantize(Decimal("0.0001")) == Decimal("52.5423")
    assert Decimal(compare[1]["B_net_U"]).quantize(Decimal("0.0001")) == Decimal("52.4299")
    assert Decimal(compare[2]["B_net_U"]).quantize(Decimal("0.0001")) == Decimal("52.4299")
    assert fingerprints["all_upstream_hashes_stable"] is True
    assert all(item["stable"] for item in fingerprints["upstream_files"].values())
    assert fingerprints["script"]["stable"] is True
    script_path = ROOT / fingerprints["script"]["path"]
    live_sha = hashlib.sha256(script_path.read_bytes()).hexdigest()
    assert live_sha == fingerprints["script"]["end"]["sha256"]

    required_report_text = [
        "待 Codex 审核", "105.439863", "52.542323", "108.487892", "−52.897540",
        "85.437922", "−138.335462", "−63.433581", "3.753152", "1/28",
        "1000LUNCUSDT", "3.048029", "52.4299", "0.112375", "不证明样本外有效",
        "pos_low_vol_exit", "pos_exit_*",
    ]
    assert all(text in report for text in required_report_text)
    print("自检通过：201行净额/贡献、来源差异、jackknife、C唯一增益、21份上游哈希及报告关键结论一致。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
