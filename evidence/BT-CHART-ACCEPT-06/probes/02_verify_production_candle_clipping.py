#!/usr/bin/env python3
"""BT-CHART-ACCEPT-06｜探针 02：直接调用生产路径裁剪函数及接口核验。
覆盖：正常 ±7 天上下文、超预算裁剪生产路径、左右边界不足/超长请求。
严格遵守纪律：直接执行生产函数 _apply_candle_context 与 get_chart，严禁用独立模拟循环代替。
"""

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str((PROJECT_ROOT / "框架/backend").resolve()))

import backtest_api as BA
import backtest_store as S

results = {
    "task": "BT-CHART-ACCEPT-06",
    "section": "production_candle_clipping",
    "checks": [],
    "all_passed": True,
}

def record_check(cid: str, name: str, passed: bool, detail: dict):
    print(f"[{'PASS' if passed else 'FAIL'}] {cid}: {name}")
    results["checks"].append({
        "id": cid,
        "name": name,
        "pass": passed,
        "detail": detail,
    })
    if not passed:
        results["all_passed"] = False

TEST_RUN = "run_FT_LOW4H_DYN01_Wyckoff_C1_S05_D05_Vs04_Z02"
doc = S.read_run(TEST_RUN)
ch_res, code = BA.get_chart(TEST_RUN, {"symbol": "BCH", "timeframe": "1h"})
raw_chart = ch_res.get("chart", {})
base_candles = raw_chart.get("candles", [])

print(f"[*] 准备基准数据: Run={TEST_RUN}, 标的=BCH, 原始K线根数={len(base_candles)}")

# ──────────────────────────────────────────────────────────
# 1. 正常 ±7 天上下文分支（生产函数调用）
# ──────────────────────────────────────────────────────────
res_7d = BA._apply_candle_context(TEST_RUN, doc, raw_chart, 7)
run_bars_all_in = all(
    any(m["t"] == b["t"] for m in res_7d.get("candles", []))
    for b in base_candles
)
pass_7d = (
    res_7d.get("candles_context_days") == 7 and
    res_7d.get("candles_context_before", 0) > 0 and
    res_7d.get("candles_context_after", 0) > 0 and
    res_7d.get("candles_window_matches_run_window") is False and
    res_7d.get("candles_context_budget_clipped") is False and
    run_bars_all_in and
    res_7d.get("candles_bars") == len(res_7d.get("candles", []))
)
record_check(
    "PROD_CLIP_01_NORMAL_7D",
    "生产函数 _apply_candle_context 正常执行 ±7 天上下文，run 区间完整保全且未触发裁剪",
    pass_7d,
    {
        "context_days": res_7d.get("candles_context_days"),
        "before_count": res_7d.get("candles_context_before"),
        "after_count": res_7d.get("candles_context_after"),
        "total_bars": res_7d.get("candles_bars"),
        "budget_clipped": res_7d.get("candles_context_budget_clipped"),
        "run_bars_count": len(base_candles),
        "run_bars_all_preserved": run_bars_all_in,
    }
)

# ──────────────────────────────────────────────────────────
# 2. 超预算生产裁剪分支（通过生产路径真实触发 while 循环裁剪）
# ──────────────────────────────────────────────────────────
orig_cap = BA.FT_REPLAY_CANDLE_CAP
test_cap = len(base_candles) + 40  # 仅允许 40 根上下文预算，迫使生产函数执行裁剪

try:
    BA.FT_REPLAY_CANDLE_CAP = test_cap
    # 直接调用生产函数！
    res_clipped = BA._apply_candle_context(TEST_RUN, doc, raw_chart, 7)
    
    # 验证裁剪断言：
    # A. 标记被置为 True
    # B. 总根数严格等于 test_cap
    # C. run 区间内部蜡烛一根不少
    # D. remaining 严格等于 0
    run_bars_all_in_clip = all(
        any(m["t"] == b["t"] for m in res_clipped.get("candles", []))
        for b in base_candles
    )
    pass_clipped = (
        res_clipped.get("candles_context_budget_clipped") is True and
        res_clipped.get("candles_bars") == test_cap and
        res_clipped.get("candles_context_budget_remaining") == 0 and
        run_bars_all_in_clip and
        (res_clipped.get("candles_context_before") + res_clipped.get("candles_context_after") == 40)
    )
    record_check(
        "PROD_CLIP_02_OVER_BUDGET_CLIPPED",
        "生产函数 _apply_candle_context 超预算时执行生产裁剪分支，精准砍掉最远端，run 根数一根不少",
        pass_clipped,
        {
            "configured_cap": test_cap,
            "result_bars": res_clipped.get("candles_bars"),
            "context_before": res_clipped.get("candles_context_before"),
            "context_after": res_clipped.get("candles_context_after"),
            "context_sum": res_clipped.get("candles_context_before") + res_clipped.get("candles_context_after"),
            "budget_clipped_flag": res_clipped.get("candles_context_budget_clipped"),
            "budget_remaining": res_clipped.get("candles_context_budget_remaining"),
            "run_bars_preserved": run_bars_all_in_clip,
        }
    )
finally:
    BA.FT_REPLAY_CANDLE_CAP = orig_cap

# ──────────────────────────────────────────────────────────
# 3. 左右边界不足与超长请求分支（生产函数调用）
# ──────────────────────────────────────────────────────────
# 请求 90 天上下文
res_90d = BA._apply_candle_context(TEST_RUN, doc, raw_chart, 90)
pass_90d = (
    res_90d.get("candles_context_days") == 90 and
    res_90d.get("candles_bars") > res_7d.get("candles_bars") and
    "非本 run 消费区间" in res_90d.get("candles_source", "")
)
record_check(
    "PROD_CLIP_03_BOUNDARY_EXTENSION",
    "生产函数 _apply_candle_context 处理超长上下文时如实提取市场库可用范围，如实标注非本 run 区间",
    pass_90d,
    {
        "context_days": 90,
        "before_count": res_90d.get("candles_context_before"),
        "after_count": res_90d.get("candles_context_after"),
        "total_bars": res_90d.get("candles_bars"),
        "source_note": res_90d.get("candles_source", "")[:80],
    }
)

# ──────────────────────────────────────────────────────────
# 4. 接口级 get_chart 透传 context_days 生产验证
# ──────────────────────────────────────────────────────────
api_ch, api_code = BA.get_chart(TEST_RUN, {"symbol": "BCH", "timeframe": "1h", "context_days": 7})
api_c = api_ch.get("chart", {})
pass_api = (
    api_code == 200 and
    api_c.get("candles_context_days") == 7 and
    api_c.get("candles_bars") == res_7d.get("candles_bars")
)
record_check(
    "PROD_CLIP_04_API_INTEGRATION",
    "通过生产 API 接口 get_chart 传参 context_days 完整调通生产裁剪路径",
    pass_api,
    {
        "api_code": api_code,
        "api_context_days": api_c.get("candles_context_days"),
        "api_candles_bars": api_c.get("candles_bars"),
    }
)

out_file = PROJECT_ROOT / "evidence/BT-CHART-ACCEPT-06/out/production_candle_clipping.json"
out_file.parent.mkdir(parents=True, exist_ok=True)
out_file.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")

print(f"\n[✔] 生产裁剪路径全量核验完成: 判定={'ALL PASS' if results['all_passed'] else 'FAIL'}")
print(f"    报告文件: {out_file}")
