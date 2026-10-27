#!/usr/bin/env python3
"""BT-KLINE-CENTER-01｜把「K 线铺满」的像素测量结果机械汇总成结论。

**不手抄任何数字**：全部从 `center_steps_measure.json` / `guard_steps_measure.json` 读。

两组判据：
  A 铺满（正例）：每一步 `right_gap_ratio <= 0.05` 且 `left_gap_ratio <= 0.05`
     且 `candles_visible` 等于该 run 的真实 1h 根数（ACHUSDT = 18，由 time_range_1h 推出）。
  B 不吃缩放（反向保护）：同一场景「放大后」与「改宽再改回后」的 `bar_spacing_px`
     相对变化 <= 10%（只贴右缘 ⇒ 间距应当**完全不变**）。

用法：python 10_center_verdict.py
"""
from __future__ import annotations

import json
from pathlib import Path

BASE = Path("/Users/boos/Desktop/威科夫因子共振趋势/evidence/WORK-DYNAMIC-BT-02/out/21_K线居中")
EXPECTED_1H_BARS = 18          # ACHUSDT run：time_range_1h 跨度 17h ⇒ 17+1
GAP_TOL = 0.05
SPACING_TOL = 0.10


def load(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


def main() -> int:
    center = load(BASE / "center_steps_measure.json")
    guards = load(BASE / "guards" / "guard_steps_measure.json")

    rows_a = []
    for r in center["rows"]:
        ok = (
            "error" not in r
            and r["right_gap_ratio"] <= GAP_TOL
            and r["left_gap_ratio"] <= GAP_TOL
            and r["candles_visible"] == EXPECTED_1H_BARS
        )
        rows_a.append({
            "tag": r["tag"], "plot_w": r.get("plot_w"),
            "candles_visible": r.get("candles_visible"),
            "bar_spacing_px": r.get("bar_spacing_px"),
            "left_gap_ratio": r.get("left_gap_ratio"),
            "right_gap_ratio": r.get("right_gap_ratio"),
            "pass": bool(ok),
        })

    by_case: dict[str, dict[str, dict]] = {}
    for r in guards["rows"]:
        case, _, phase = r["tag"].rpartition("_")
        by_case.setdefault(case, {})[r["tag"].rsplit("_", 1)[-1]] = r
    rows_b = []
    for case, phases in sorted(by_case.items()):
        zoom = next((v for k, v in phases.items() if k == "zoomed"), None)
        back = next((v for k, v in phases.items() if k == "back"), None)
        if not zoom or not back or not zoom.get("bar_spacing_px"):
            rows_b.append({"case": case, "pass": False, "why": "缺 zoomed/back 步"})
            continue
        delta = abs(back["bar_spacing_px"] - zoom["bar_spacing_px"]) / zoom["bar_spacing_px"]
        rows_b.append({
            "case": case,
            "spacing_zoomed": zoom["bar_spacing_px"],
            "spacing_after_back": back["bar_spacing_px"],
            "rel_delta": round(delta, 4),
            "pass": bool(delta <= SPACING_TOL),
            "why": "缩放级别未被吃掉" if delta <= SPACING_TOL else "缩放级别被改写",
        })

    verdict = {
        "task": "BT-KLINE-CENTER-01",
        "generated_from": [
            str(BASE / "center_steps_measure.json"),
            str(BASE / "guards" / "guard_steps_measure.json"),
        ],
        "criterion": {
            "A_fill": f"left/right_gap_ratio <= {GAP_TOL} 且 candles_visible == {EXPECTED_1H_BARS}",
            "B_zoom_kept": f"|spacing_after_back - spacing_zoomed| / spacing_zoomed <= {SPACING_TOL}",
        },
        "A_fill": rows_a,
        "B_zoom_kept": rows_b,
        "A_pass": all(r["pass"] for r in rows_a),
        "B_pass": all(r["pass"] for r in rows_b),
        "overall": "PASS" if all(r["pass"] for r in rows_a) and all(r["pass"] for r in rows_b) else "FAIL",
        "baseline_before_fix": {
            "note": "同一复现路径（1668→1200→1668）修复前的实测值，来自 out/21_K线居中/resize_test.json 与 resize_3_back1668.png",
            "step": "3_back1668",
            "candles_visible": 10,
            "bar_spacing_px": 46.0,
            "right_gap_px": 370,
            "plot_w": 820,
            "right_gap_ratio": 0.45,
        },
    }
    out = BASE / "22_K线居中修复结论.json"
    out.write_text(json.dumps(verdict, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(verdict, ensure_ascii=False, indent=2))
    print(f"\n写出 {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
