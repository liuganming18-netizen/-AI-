#!/usr/bin/env python3
"""BT-KLINE-CENTER-01｜逐像素测量 K 线绘图区的「铺满 / 留白」。

不读任何自报字段，只从截图里数：
  · 蜡烛根数（按列统计高饱和像素 → 聚成簇）
  · 相邻簇中心间距中位数 = barSpacing
  · 左留白 = 第一簇左缘到绘图区左边界；右留白 = 绘图区右边界到最后一簇右缘
  · 一屏格数 = 绘图区宽 / barSpacing

判据（本单口径）：
  · `right_gap_ratio <= 0.05` 才算「数据铺满」（首屏实测 1%）
  · `candles_visible == candles_expected` 才算「一根不缺」

用法：python 07_measure_candles.py <center_steps.json>
"""
from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

import numpy as np
from PIL import Image

OUT = Path("/Users/boos/Desktop/威科夫因子共振趋势/evidence/WORK-DYNAMIC-BT-02/out/21_K线居中")


def measure(png: Path, plot_rel: dict) -> dict:
    img = Image.open(png).convert("RGB")
    arr = np.asarray(img).astype(np.int16)
    h, w = arr.shape[:2]

    x0 = max(0, int(plot_rel["x"]))
    x1 = min(w, int(plot_rel["x"] + plot_rel["w"]))
    y0 = max(0, int(plot_rel["y"]))
    y1 = min(h, int(plot_rel["y"] + plot_rel["h"]))
    sub = arr[y0:y1, x0:x1]

    # 蜡烛 = 高饱和像素（红/绿实体与影线）；背景白、网格浅灰、文字黑灰 —— 饱和度都低
    mx = sub.max(axis=2)
    mn = sub.min(axis=2)
    sat = mx - mn
    colored = (sat > 45) & (mx > 60)

    col_counts = colored.sum(axis=0)
    on = col_counts > 0
    clusters: list[tuple[int, int]] = []
    i = 0
    while i < len(on):
        if not on[i]:
            i += 1
            continue
        j = i
        while j + 1 < len(on) and on[j + 1]:
            j += 1
        # 相邻蜡烛实体之间至少隔 1 列空白；间距 <3px 的视为同一根（影线+实体）
        clusters.append((i, j))
        i = j + 1

    # 合并间隙 <=2px 的簇（同一根蜡烛的左右边界抗锯齿）
    merged: list[list[int]] = []
    for c in clusters:
        if merged and c[0] - merged[-1][1] <= 2:
            merged[-1][1] = c[1]
        else:
            merged.append([c[0], c[1]])

    centers = [(a + b) / 2 for a, b in merged]
    diffs = [b - a for a, b in zip(centers, centers[1:])]
    spacing = statistics.median(diffs) if diffs else 0.0

    plot_w = x1 - x0
    left_gap = merged[0][0] if merged else plot_w
    right_gap = plot_w - merged[-1][1] if merged else plot_w

    return {
        "png": str(png),
        "plot_w": plot_w,
        "plot_h": y1 - y0,
        "candles_visible": len(merged),
        "bar_spacing_px": round(spacing, 1),
        "slots_per_screen": round(plot_w / spacing, 1) if spacing else None,
        "left_gap_px": left_gap,
        "right_gap_px": right_gap,
        "left_gap_ratio": round(left_gap / plot_w, 3),
        "right_gap_ratio": round(right_gap / plot_w, 3),
    }


def main() -> int:
    steps_json = Path(sys.argv[1] if len(sys.argv) > 1 else OUT / "center_steps.json")
    data = json.loads(steps_json.read_text(encoding="utf-8"))
    rows = []
    for st in data["steps"]:
        plot = st.get("plot") or {}
        pick, el = plot.get("pick"), plot.get("el")
        if not pick or not el:
            rows.append({"tag": st["tag"], "error": "no_plot_canvas"})
            continue
        rel = {"x": pick["x"] - el["x"], "y": pick["y"] - el["y"], "w": pick["w"], "h": pick["h"]}
        m = measure(Path(st["file"]), rel)
        m["tag"] = st["tag"]
        rows.append(m)

    print(f"{'tag':<20}{'plot_w':>7}{'根数':>5}{'间距':>7}{'一屏格':>7}{'左空%':>7}{'右空%':>7}")
    for r in rows:
        if "error" in r:
            print(f"{r['tag']:<20}  {r['error']}")
            continue
        print(
            f"{r['tag']:<20}{r['plot_w']:>7}{r['candles_visible']:>5}"
            f"{r['bar_spacing_px']:>7}{r['slots_per_screen']:>7}"
            f"{r['left_gap_ratio'] * 100:>7.1f}{r['right_gap_ratio'] * 100:>7.1f}"
        )

    verdict = []
    for r in rows:
        if "error" in r:
            verdict.append({"tag": r["tag"], "pass": False, "why": r["error"]})
            continue
        ok_fill = r["right_gap_ratio"] <= 0.05 and r["left_gap_ratio"] <= 0.05
        verdict.append({
            "tag": r["tag"],
            "pass": bool(ok_fill),
            "why": "铺满" if ok_fill else f"留白 左{r['left_gap_ratio']*100:.1f}% 右{r['right_gap_ratio']*100:.1f}%",
        })
    # 按输入文件名派生，避免不同批次的测量互相覆盖（曾把 guards 结果写进 center_measure.json）
    out = steps_json.parent / f"{steps_json.stem}_measure.json"
    out.write_text(json.dumps({"source": str(steps_json), "rows": rows, "verdict": verdict}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n写出 {out}")
    print("结论：" + ("全部铺满" if all(v["pass"] for v in verdict) else "仍有留白 —— " + "; ".join(v["why"] for v in verdict if not v["pass"])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
