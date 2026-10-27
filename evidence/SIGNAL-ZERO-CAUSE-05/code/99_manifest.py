# -*- coding: utf-8 -*-
"""SIGNAL-ZERO-CAUSE-05｜99 证据清单（MANIFEST.json）。

只登记本单 `code/` 与 `out/` 下的文件指纹 + 关键结论数字 + 复算命令，
让下游（Luna 只读核查 / Codex 独立验收）不必相信任何自报文字。
"""
from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
cm = importlib.import_module("00_common")

HERE = Path(__file__).resolve().parents[1]


def sha(p: Path) -> str:
    return cm.sha256_file(p)


def collect(sub: str):
    d = HERE / sub
    out = {}
    for p in sorted(d.rglob("*")):
        if p.is_file() and p.suffix in (".py", ".mjs", ".js", ".json", ".csv",
                                        ".md", ".log", ".png", ".txt"):
            out[str(p.relative_to(HERE))] = {
                "size": p.stat().st_size, "sha256": sha(p)}
    return out


def read_json(name):
    p = HERE / "out" / name
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:                                        # noqa: BLE001
        return None


def main() -> int:
    s02 = read_json("02_汇总.json") or {}
    s04 = read_json("04_闸门对照.json") or {}
    s04b = read_json("04_闸门对照_修复前.json") or {}
    s05 = read_json("05_修复后parity.json") or {}
    s06 = read_json("06_生产核验_对比.json") or {}
    s07 = read_json("07_当前卡点汇总.json") or {}
    s03 = read_json("03_三场景对照.json") or {}
    s03b = read_json("03_三场景对照_修复前.json") or {}
    s11 = read_json("11_回溯抽样.json") or {}
    s10 = read_json("10_报告数字自检.json") or {}
    s06w = read_json("06_新增K线观察.json") or {}
    w_samples = s06w.get("samples") or []
    w_first = w_samples[0] if w_samples else {}
    w_last = w_samples[-1] if w_samples else {}
    new_bar = {
        "observed": bool(w_first.get("watermark_1h") and w_last.get("watermark_1h")
                         and w_last["watermark_1h"] > w_first["watermark_1h"]),
        "watermark_from_utc": w_first.get("watermark_1h_utc"),
        "watermark_to_utc": w_last.get("watermark_1h_utc"),
        "n_samples": s06w.get("n_samples"),
        "lag_1h_max": w_last.get("lag_1h_max"),
        "n_lag_1h_gt1": w_last.get("n_lag_1h_gt1"),
        "aligned_1h": w_last.get("aligned_1h"),
        "funnel_since_start": w_last.get("funnel_since_start"),
        "orders_routed_total": w_last.get("orders_routed_total"),
        "open_positions": w_last.get("open_positions"),
    }

    man = {
        "work_order": "SIGNAL-ZERO-CAUSE-05",
        "title": "零信号逐币归因与漏信号排查",
        "generated_at_utc": cm.utc(cm.now_ms()),
        "project": str(cm.BASE),
        "frozen_rule": {
            "path": "evidence/WX_EVENT_TREND_V0_1_策略与回测冻结规则.md",
            "sha256": cm.sha256_file(
                cm.BASE / "evidence" / "WX_EVENT_TREND_V0_1_策略与回测冻结规则.md"),
            "changed_by_this_work_order": False,
        },
        "frozen_params_changed": False,
        "real_trading_enabled": False,
        "code_changed": [
            {"path": "框架/backend/unified_runtime.py",
             "sha256_before": "d58f95fe9608335db88b811c65fe4b96a2318af2b27840c67b0bfa04df4f9e55",
             "sha256_after": cm.sha256_file(cm.BACKEND / "unified_runtime.py"),
             "changes": [
                 "RealSignalEngine/MultiSymbolSignalEngine 新增 box_gate_mode（默认 atr_warmup）",
                 "process_market：建档闸门改用 atr_bars_seen（规范口径）",
                 "process_market：4h 取数下界前移 (box_bars_4h-1) 根，42 根可跨窗口起点",
                 "bootstrap：上下文 bar 一并交给 _MemWatcher",
                 "atr_caliber：暴露 box_gate_mode / gate_metric；box_gate_bars 降级为诊断量",
                 "idle_breakdown：ATR 改用 eng.last_atr（与决策同一个数）",
             ]},
            {"path": "策略/WyckoffMaster/event_trend_v0_1/tests/test_ft_low4h_parity_closure.py",
             "sha256_after": cm.sha256_file(
                 cm.BASE / "策略/WyckoffMaster/event_trend_v0_1/tests"
                 / "test_ft_low4h_parity_closure.py"),
             "changes": ["补足 220 根 4h 预热数据（断言未改）；该用例修复前即失败"]},
        ],
        "key_numbers": {
            "effective_event_window_days_before": 15.0,
            "effective_event_window_days_after": 41.33,
            "lost_days": 26.33,
            "worst_case_event_chain_days": 24.0,
            "boxes_before": (s04.get("summary") or {}).get("total_boxes_current"),
            "boxes_spec": (s04.get("summary") or {}).get("total_boxes_spec"),
            "candidates_before": (s04.get("summary") or {}).get("total_candidates_current"),
            "candidates_spec": (s04.get("summary") or {}).get("total_candidates_spec"),
            "state_diff_before_60h": (s03b.get("summary") or {}).get(
                "n_diff_vs_S1", {}).get("S2_初始化后接60h新行情"),
            "state_diff_after_60h": (s03.get("summary") or {}).get(
                "n_diff_vs_S1", {}).get("S2_初始化后接60h新行情"),
            "state_diff_after_same_length_60h": (s03.get("summary") or {}).get(
                "n_diff_vs_S1", {}).get("S5_同长度_60h处重建"),
            "parity_ok": (s05.get("summary") or {}).get("n_parity_ok"),
            "parity_mismatch": (s05.get("summary") or {}).get("n_mismatch"),
            "prod_checks_pass": (s06 or {}).get("n_pass"),
            "prod_checks_total": (s06 or {}).get("n_total"),
            "prod_history_boxes_after": 4262,
            "prod_history_candidates_after": 18,
            "prod_history_candidates_discarded": 18,
            "current_state_distribution": (s07 or {}).get("state_distribution"),
            "current_blocked_by": (s07 or {}).get("blocked_by_distribution"),
            "retest_gap_median_pct": ((s07 or {}).get("retest_gap_to_breakout_pct") or {}).get("median"),
            "candidates_all_checks_pass": (s07 or {}).get("n_candidates_all_checks_pass"),
            "negative_cases": (s07 or {}).get("n_negative_cases"),
            "negative_violations": (s07 or {}).get("n_negative_violations"),
            "traceback_n_picked": (s11 or {}).get("n_picked"),
            "traceback_n_comparisons": (s11 or {}).get("n_comparisons"),
            "traceback_n_missing": (s11 or {}).get("n_missing"),
            "traceback_n_failed": (s11 or {}).get("n_failed"),
            "traceback_all_match": (s11 or {}).get("all_match"),
            "traceback_upto_utc": (s11 or {}).get("upto_utc"),
            "selfcheck_n_pass": (s10 or {}).get("n_pass"),
            "selfcheck_n_checks": (s10 or {}).get("n_checks"),
            "selfcheck_all_pass": (s10 or {}).get("all_pass"),
            "new_bar_after_fix": new_bar,
        },
        "recompute": {
            "01": "python code/01_spec_vs_impl.py",
            "02": "python code/02_per_symbol_zero_signal.py",
            "03": "python code/03_leak_three_scenarios.py",
            "04": "python code/04_gate_isolated_ab.py",
            "05": "python code/05_verify_fix_parity.py",
            "06": "python code/06_prod_verify.py --phase pre | --restart | --wait-ready | "
                  "--phase post | --compare",
            "07": "python code/07_two_way_cases.py",
            "11": "python code/11_traceback_sample.py（锚定 02 的 upto_utc，不取当前时刻）",
            "14": "python code/14_gate_open_time.py（旧口径真跑，不手算开闸时刻）",
            "10": "python code/10_selfcheck.py",
            "06-watch": "python code/06_prod_verify.py --watch-min 65",
        },
        "files": {"code": collect("code"), "out": collect("out"),
                  # MANIFEST.json 不登记自己：写盘前算的哈希必然是上一版，自指无意义
                  "root": {p.name: {"size": p.stat().st_size, "sha256": sha(p)}
                           for p in HERE.iterdir()
                           if p.is_file() and p.suffix in (".md", ".json")
                           and p.name != "MANIFEST.json"}},
    }
    p = HERE / "MANIFEST.json"
    p.write_text(json.dumps(man, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[out] {p} ({p.stat().st_size:,} B)")
    print(f"  code 文件 {len(man['files']['code'])} 个 / out 文件 {len(man['files']['out'])} 个")
    return 0


if __name__ == "__main__":
    sys.exit(main())
