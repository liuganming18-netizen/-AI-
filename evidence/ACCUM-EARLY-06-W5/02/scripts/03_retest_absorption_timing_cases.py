# -*- coding: utf-8 -*-
"""ACCUM-EARLY-06-W5-02 | 脚本 03: 承接判定时序修正后行为独立端到端复验

【测试目标】
对窗口二修正后的 accum_early_entry.py 进行独立端到端承接反例复验：
1. 案例 A (扫低承接通过_突破根不通过):
   - 扫低根放量吸筹 (tbq=700 >= 500)，突破根缩量 (tbq=300 < 500)；
   - 验证：确认根读取扫低根达标结果，成功生成候选！(expect_candidate=1)
2. 案例 B (扫低不通过_突破根通过):
   - 扫低根卖压超标 (tbq=300 < 500)，突破根放量买入 (tbq=700 >= 500)；
   - 验证：确认根读取扫低根失败结果，成功拦截！(expect_candidate=0)
3. 案例 C (扫低根数据不足):
   - 扫低根 tbq=None，确认根放量；
   - 验证：确认根读取扫低根缺失，正规终结为 DATA_INSUFFICIENT！
4. 案例 D (门槛关闭对照 require=false):
   - 验证门槛关闭时安全跳过承接校验，顺利放行；
5. 输出至 out/03_absorption_timing_retest.json。
"""
import copy
import json
import os
import sys
import tempfile
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[4]
BACKEND_DIR = PROJECT_ROOT / "框架" / "backend"
W2_DIR = PROJECT_ROOT / "evidence" / "ACCUM-EARLY-06-W2" / "01" / "scripts"
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(BACKEND_DIR))
sys.path.insert(0, str(W2_DIR))

import _w2 as W
import accum_early_entry as AEE

OUT_DIR = PROJECT_ROOT / "evidence" / "ACCUM-EARLY-06-W5" / "02" / "out"
OUT_DIR.mkdir(parents=True, exist_ok=True)

def run_suite():
    cfg = W.load_fixture_config()
    results = {
        "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "tested_code_sha256": W.sha256_file(BACKEND_DIR / "accum_early_entry.py"),
        "cases": []
    }

    # ── 1. 独立重跑 W2 的三大基准时序用例 ──
    for name, spec in W.CASES.items():
        bars = W.series(spec["sweep_tbq"], spec["confirm_tbq"], confirm_i=spec["confirm_i"])
        r = W.run_observer(name, bars, cfg, tag="w5_02_" + name[:1])
        n_cands = r["n_candidates"]
        exp_cands = 1 if spec["expect_candidate"] else 0
        terminal = r["final_state"] if r["final_state"] in ("INVALIDATED", "EXPIRED", "CLOSED", "DATA_INSUFFICIENT") else None
        exp_terminal = spec["expect_terminal"]
        
        # 获取承接调用的 bar 下标
        ab_calls = [c for c in r["trace"] if c["fn"] == "absorption"]
        ab_indices = sorted({c["i"] for c in ab_calls})
        judged_i = ab_indices[0] if ab_indices else None

        passed = (n_cands == exp_cands and terminal == exp_terminal and judged_i == W.SWEEP_I)

        results["cases"].append({
            "case_name": name,
            "expected_candidates": exp_cands,
            "actual_candidates": n_cands,
            "expected_terminal": exp_terminal,
            "actual_terminal": terminal,
            "absorption_judged_bar_i": judged_i,
            "expected_judged_bar_i": W.SWEEP_I,
            "pass": passed,
            "verdict": "PASS_CORRECTLY_VERIFIED" if passed else "FAIL"
        })

    # ── 2. 门槛关闭对照 (require=false) ──
    cfg_disabled = copy.deepcopy(cfg)
    cfg_disabled["doc"]["shared"]["sell_pressure"]["require"] = False
    # 扫低 tbq=300 (原不通过), 突破 tbq=300
    bars_disabled = W.series(W.TBQ_FAIL, W.TBQ_FAIL)
    r_dis = W.run_observer("D_门槛关闭对照", bars_disabled, cfg_disabled, tag="w5_02_disabled")
    pass_dis = (r_dis["n_candidates"] == 1)
    results["cases"].append({
        "case_name": "D_门槛关闭对照 (require=false)",
        "expected_candidates": 1,
        "actual_candidates": r_dis["n_candidates"],
        "pass": pass_dis,
        "verdict": "PASS_SKIPPED_CLEAN" if pass_dis else "FAIL"
    })

    out_file = OUT_DIR / "03_absorption_timing_retest.json"
    out_file.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Absorption timing independent verification finished, saved to {out_file}")

if __name__ == "__main__":
    run_suite()
