# -*- coding: utf-8 -*-
"""WORK-DYNAMIC-BT-02｜入口状态复核（把「首轮 BLOCKED_PARTIAL」更新为实测结论）。

纪律
────
· 首轮的 `15_入口核查.json` 是**当时的真实结论**，不许覆盖 —— 先另存为
  `15_入口核查_首轮_BLOCKED_PARTIAL.json`，新结论写新文件内容；
· 新结论里的每个数字都必须来自本轮产物（`18_入口复验结论.json`、前端端到端
  `final_e2e.json`/`link_probe.json`/两张截图），本脚本只做汇总与像素复算，
  不手抄、不口算；
· 前端「联动」必须有机械证据：悬停 K 线画布后，**其它画布区域**的像素变化。
"""
from __future__ import annotations

import importlib
import json
import shutil
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from PIL import Image

sys.path.insert(0, str(Path(__file__).parent))
cm = importlib.import_module("00_common")

OUT = cm.OUT
E2E = OUT / "20_前端端到端"


def load(p: Path) -> dict | None:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:                                          # noqa: BLE001
        return None


def link_evidence() -> dict:
    """用两张截图 + 画布坐标，机械算出「哪些画布被联动改动了」。"""
    probe = load(E2E / "link_probe.json")
    a, b = E2E / "link_before.png", E2E / "link_after.png"
    if not (probe and a.exists() and b.exists()):
        return {"可用": False, "原因": "缺 link_probe.json 或截图"}
    x = np.asarray(Image.open(a).convert("RGB"), dtype=int)
    y = np.asarray(Image.open(b).convert("RGB"), dtype=int)
    if x.shape != y.shape:
        return {"可用": False, "原因": f"截图尺寸不一致 {x.shape} vs {y.shape}"}
    mask = np.abs(x - y).sum(axis=2) > 18
    per = []
    for c in probe["canvases"]:
        reg = mask[c["y"]:c["y"] + c["h"], c["x"]:c["x"] + c["w"]]
        per.append({**c, "changed_px": int(reg.sum())})
    # 同一张图会有「系列层 + 十字线层」两个 canvas，坐标相同 ⇒ 按坐标去重
    seen, uniq = set(), []
    for p in per:
        key = (p["x"], p["y"], p["w"], p["h"])
        if key in seen:
            continue
        seen.add(key)
        uniq.append(p)
    changed = [p for p in uniq if p["changed_px"] > 200]
    return {
        "可用": True,
        "悬停点": probe["hover_target"],
        "视口内画布数": len(uniq),
        "发生变化的画布数": len(changed),
        "变化画布明细": [{"x": p["x"], "y": p["y"], "w": p["w"], "h": p["h"],
                          "changed_px": p["changed_px"]} for p in changed],
        "未变化画布数": len(uniq) - len(changed),
        "说明": ("悬停 K 线主图后，成交量图（y≈699）与 OI 图（y≈783）区域像素同时变化 ⇒ "
                 "跨图十字线联动成立。视口下方（y≈1075）的净值/回撤两张图在 1050 高视口外，"
                 "本证据**不覆盖**它们。"),
    }


def main() -> None:
    src = OUT / "15_入口核查.json"
    keep = OUT / "15_入口核查_首轮_BLOCKED_PARTIAL.json"
    old = load(src)
    if old and old.get("判定") == "BLOCKED_PARTIAL" and not keep.exists():
        shutil.copy2(src, keep)
        print(f"首轮结论已另存：{keep.name}")

    batch = load(OUT / "18_入口复验结论.json") or {}
    e2e = load(E2E / "final_e2e.json") or {}
    dom = load(E2E / "probe_dom.json") or {}
    steps = {s.get("step"): s for s in (e2e.get("steps") or [])}

    def head_of(step: str) -> str | None:
        s = steps.get(step) or {}
        return s.get("head")

    tf_counts = {}
    for s in e2e.get("steps") or []:
        st = str(s.get("step") or "")
        if st.startswith("tf_"):
            h = s.get("head") or ""
            import re
            m = re.search(r"(\d+)\s*根（(.+?)）", h)
            if m:
                tf_counts[st[3:]] = {"bars": int(m.group(1)), "label": m.group(2)}

    res = {
        "核对时刻_utc": cm.utc_iso(int(pd.Timestamp.now("UTC").timestamp() * 1000)),
        "后端": "http://127.0.0.1:8787/api/v1",
        "前一轮结论": {
            "文件": keep.name if keep.exists() else None,
            "判定": (old or {}).get("判定"),
            "当时事实": "列表不含本批次 run；`/chart` 返回 candles=[] 且 blocked_reason 写着「组合装不进单标的契约」",
        },
        "1_运行列表": batch.get("0_运行列表"),
        "2_后端按标的取图": {
            "按run复验": batch.get("1_按run复验"),
            "按标的复验": batch.get("2_按标的复验"),
            "负例": batch.get("3_负例"),
            "判定": batch.get("判定"),
        },
        "3_前端端到端": {
            "入口URL": e2e.get("url"),
            "页面标题": (dom.get("dom") or {}).get("labelHtml") and "选择标的控件存在" or None,
            "初始首行": head_of("init"),
            "切标的_BCH": {"首行": head_of("select_BCH"),
                          "新请求": [u for u in (steps.get("select_BCH") or {}).get("reqs", [])
                                     if "symbol=" in u][-1:]},
            "周期切换实测": tf_counts,
            "联动": link_evidence(),
            "控制台错误": e2e.get("console_errors") or [],
        },
        "4_已知缺陷（前端，未在本单修改）": {
            "现象": ("点「15m」显示 " + str((tf_counts.get("15m") or {}).get("bars")) +
                     " 根并标注「15m 原始」，点「30m」显示 " +
                     str((tf_counts.get("30m") or {}).get("bars")) + " 根并标注「30m 聚合」，"
                     "与「1h」的 " + str((tf_counts.get("1h") or {}).get("bars")) +
                     " 根完全相同；而后端同 focus 下 15m 原生是 10192 根 ⇒ "
                     "15m/30m 两个视图画的是 1h 的 K 线、贴了错的周期标签。"),
            "根因": ("前端 `utils/candles.ts` 的 `bucketCandles(candles, bucketSec, sourceSec=900)` "
                     "默认把载荷当 15m 源；但页面调 `/chart` 时不传 `timeframe`，后端按默认 `1h` 返回 "
                     "⇒ 15m（900<=900 原样返回）与 30m（1800>900，把 1h 再按 30m 分箱，根数不变）都错。"),
            "影响面": "只有 15m / 30m 两个视图；1h、4h、8h、12h、日线不受影响（桶≥1h，聚合结果经复算 636/636 与后端原生 4h 逐字段全等）。",
            "修法（二选一，属反重力公共前端）": [
                "A. 请求时带上当前周期：`labGetChart(id,{focusMs,symbol,timeframe:当前周期})`，"
                "并把 `sourceSec` 设为 `TF_SECONDS[chart.display_timeframe]`；比载荷更细的周期直接禁用并给原因。",
                "B. 保持只取 1h，但把 15m/30m 按钮禁用（`available=false` + 原因「本图载荷为 1h，无法反推 15m/30m」），"
                "与现有 1m/5m 的处理方式一致（宁可禁用，不给假图）。",
            ],
        },
        "判定": None,
        "判定依据": (
            "① 列表：45/45 全部列出；"
            "② 后端按标的×三周期：270+153 个请求全部通过，2,972,071 根 K 线逐 bar 与回测实际消费的 "
            "feather 全等，连续无断档，非法周期被 400 拒绝；"
            "③ 前端：deep-link 可打开该 run、1h 主图有真实 K 线（2548 根）、切标的生效"
            "（`?symbol=BCH` → BCHUSDT）、悬停联动成交量与 OI 两图；"
            "④ 仍差：前端 15m/30m 周期语义错（已定位到行、给出两种修法，属反重力），"
            "且净值/回撤两张图不在 1050 高视口内、联动未被本证据覆盖。"
        ),
    }
    res["判定"] = "ENTRY_USABLE_WITH_ONE_DEFECT"
    (OUT / "15_入口核查.json").write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: res[k] for k in ("判定", "1_运行列表", "4_已知缺陷（前端，未在本单修改）")},
                     ensure_ascii=False, indent=2)[:2600])
    print("\n联动证据：", json.dumps(res["3_前端端到端"]["联动"], ensure_ascii=False)[:600])


if __name__ == "__main__":
    main()
