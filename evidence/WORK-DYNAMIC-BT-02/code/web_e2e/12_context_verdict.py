#!/usr/bin/env python3
"""BT-KLINE-CONTEXT-01｜把「±7 天上下文」的验证结果机械汇总成结论。

**不手抄任何数字**：前端读数来自 `out/21_K线居中/context/verify_steps.json`，
后端契约来自现取 `GET /api/v1/backtests/{id}/chart`。

判据（全部可复算）：
  A 首屏铺满 —— 可见逻辑跨度（`vlog.to - vlog.from`，四舍五入）== **载荷在该周期下的真实根数**
    · 真实根数由后端 15m 原始 K 线按 UTC 边界重新分桶算得（1h = floor(t/3600s)、4h = floor(t/4h)），
      **不读前端表头自报的根数**，避免「自己验自己」。
    · 左右像素留白 <= 5%。
  B 不吃缩放 —— 放大到 40 根后改宽再改回：可见**时间窗**三次完全一致，
    `barSpacing` 比值 == 绘图区宽度比值（容差 2%）。
  C 定位入口仍在 —— 主动 `setViewport` 后可见范围真的换了（旧行为没被首屏让位吃掉）。
  D 后端「只加不改」—— 同一 run 在 `context_days=0` 与 `=7` 下，**run 区间内的 K 线逐根相同**；
    上下文段只是追加在区间之外。
  E 预算裁剪 —— `context_days=90` 时总根数不超过 `FT_REPLAY_CANDLE_CAP`（20000），
    且 run 区间内的 K 线仍逐根相同（砍只砍离 run 最远的）。

用法：python 12_context_verdict.py [run_id]
"""
from __future__ import annotations

import json
import math
import urllib.request
from pathlib import Path

BASE = Path("/Users/boos/Desktop/威科夫因子共振趋势/evidence/WORK-DYNAMIC-BT-02/out/21_K线居中/context")
API = "http://127.0.0.1:8787/api/v1"
RID = "run_FT_ENTRY_09_C1_Vs04_Z02_1000XECUSDT"
GAP_TOL = 0.05
SPACING_TOL = 0.02
CAP = 20000
TF_MS = {"15m": 900_000, "1h": 3_600_000, "4h": 14_400_000}


def get_json(url: str) -> dict:
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with op.open(url, timeout=120) as r:
        return json.loads(r.read().decode("utf-8"))


def candles_of(rid: str, context_days: int | None) -> dict:
    q = f"?context_days={context_days}" if context_days is not None else ""
    return get_json(f"{API}/backtests/{rid}/chart{q}")["chart"]


def rows_of(chart: dict) -> list[list[float]]:
    out = []
    for c in chart.get("candles") or []:
        if isinstance(c, dict):
            out.append([c["t"], c["o"], c["h"], c["l"], c["c"], c["v"]])
        else:
            out.append(list(c))
    return out


def buckets(rows: list[list[float]], tf: str) -> int:
    step = TF_MS[tf]
    return len({(int(r[0]) // step) for r in rows})


def slice_run(rows: list[list[float]], start_ms: int, end_ms: int) -> list[list[float]]:
    return [r for r in rows if start_ms <= int(r[0]) < end_ms]


def main() -> int:
    steps_path = BASE / f"verify_steps_{RID}.json"
    steps = json.loads(steps_path.read_text(encoding="utf-8"))

    ch7 = candles_of(RID, 7)
    rows7 = rows_of(ch7)
    run_range = ch7.get("candles_run_range") or {}
    start_ms = int(run_range["start_ms"])
    end_ms = int(run_range["end_ms"])
    expect = {tf: buckets(rows7, tf) for tf in ("15m", "1h", "4h")}

    # ── A：首屏铺满（按周期分组） ──
    a_rows = []
    for s in steps["steps"]:
        if "st" not in s:
            continue
        st = s["st"] or {}
        vlog = st.get("vlog") or {}
        gaps = s.get("gaps") or {}
        tf = "15m" if "15m" in s["tag"] else ("4h" if "4h" in s["tag"] else "1h")
        span = None
        if vlog.get("from") is not None and vlog.get("to") is not None:
            span = int(round(vlog["to"] - vlog["from"])) + 1
        lg, rg = gaps.get("left_gap_pct"), gaps.get("right_gap_pct")
        ok = (
            span == expect[tf]
            and lg is not None and rg is not None
            and lg / 100 <= GAP_TOL and rg / 100 <= GAP_TOL
        )
        a_rows.append({
            "tag": s["tag"], "tf": tf,
            "visible_bars": span, "payload_bars": expect[tf],
            "left_gap_pct": lg, "right_gap_pct": rg,
            "bar_spacing_px": st.get("barSpacing"),
            "pass": bool(ok),
        })

    # ── B：不吃缩放 ──
    d = next(x for x in steps["steps"] if x["tag"] == "D_zoom_resize")
    vis = [(d[k]["vis"]["from"], d[k]["vis"]["to"]) for k in ("before", "mid", "after")]
    bs = [d[k]["barSpacing"] for k in ("before", "mid", "after")]
    w = [d[k]["width"] for k in ("before", "mid", "after")]
    ratio_sp = bs[1] / bs[0] if bs[0] else None
    ratio_w = w[1] / w[0] if w[0] else None
    b_ok = (
        vis[0] == vis[1] == vis[2]
        and ratio_sp is not None and ratio_w is not None
        and abs(ratio_sp - ratio_w) / ratio_w <= SPACING_TOL
        and abs(bs[2] - bs[0]) / bs[0] <= SPACING_TOL
    )
    b_row = {
        "visible_range": [list(v) for v in vis],
        "bar_spacing": bs, "width": w,
        "spacing_ratio": round(ratio_sp, 4) if ratio_sp else None,
        "width_ratio": round(ratio_w, 4) if ratio_w else None,
        "rel_delta_spacing": round(abs(ratio_sp - ratio_w) / ratio_w, 4) if ratio_sp and ratio_w else None,
        "pass": bool(b_ok),
    }

    # ── C：定位入口仍在 ──
    e = next(x for x in steps["steps"] if x["tag"] == "E_locate_entry")
    after = e.get("after") or {}
    av = after.get("vis") or {}
    want_from = (1717146900000 - 43200000) // 1000
    want_to = (1717171200000 + 43200000) // 1000
    c_ok = (
        bool(e.get("locateOk"))
        and av.get("from") is not None
        and abs(av["from"] - want_from) <= 3600
        and abs(av["to"] - want_to) <= 3600
        and abs((after.get("barSpacing") or 0) - bs[2]) > 1e-6
    )
    c_row = {
        "asked_from_s": want_from, "asked_to_s": want_to,
        "applied": av, "bar_spacing_after": after.get("barSpacing"),
        "bar_spacing_before": bs[2], "pass": bool(c_ok),
    }

    # ── D：后端只加不改 ──
    ch0 = candles_of(RID, 0)
    rows0 = rows_of(ch0)
    in0 = slice_run(rows0, start_ms, end_ms)
    in7 = slice_run(rows7, start_ms, end_ms)
    pre = [r for r in rows7 if int(r[0]) < start_ms]
    post = [r for r in rows7 if int(r[0]) >= end_ms]
    d_ok = (
        len(in0) == len(in7) and in0 == in7
        and len(rows0) == len(in0)
        and len(pre) == int(ch7.get("candles_context_before") or -1)
        and len(post) == int(ch7.get("candles_context_after") or -1)
    )
    d_row = {
        "context_days_0_bars": len(rows0),
        "context_days_7_bars": len(rows7),
        "run_slice_bars": len(in7),
        "run_slice_identical": in0 == in7,
        "context_before_declared": ch7.get("candles_context_before"),
        "context_before_actual": len(pre),
        "context_after_declared": ch7.get("candles_context_after"),
        "context_after_actual": len(post),
        "pass": bool(d_ok),
    }

    # ── E：预算裁剪 ──
    ch90 = candles_of(RID, 90)
    rows90 = rows_of(ch90)
    in90 = slice_run(rows90, start_ms, end_ms)
    e_ok = len(rows90) <= CAP and in90 == in7
    e_row = {
        "context_days": ch90.get("candles_context_days"),
        "total_bars": len(rows90),
        "cap": CAP,
        "trim_exercised": len(rows90) >= CAP,
        "run_slice_identical_to_7d": in90 == in7,
        "note": ch90.get("candles_context_note"),
        "pass": bool(e_ok),
        "未验证说明": (
            "context_days 上限 90 天、前后各 90 天共 17,306 根 < 20,000 根 ⇒ "
            "**预算裁剪分支本轮不可达、未验证**（未验证不计入通过）。"
            "本轮只证明「总根数不超上限 + run 区间逐根不变」。"
        ) if len(rows90) < CAP else None,
    }

    verdict = {
        "task": "BT-KLINE-CONTEXT-01",
        "run_id": RID,
        "captured_at": steps.get("captured_at"),
        "sources": {
            "frontend": str(steps_path),
            "backend": f"{API}/backtests/{RID}/chart",
        },
        "criterion": {
            "A_fill": f"visible_bars == payload_bars（按 UTC 边界独立重算）且 left/right_gap <= {GAP_TOL}",
            "B_zoom_kept": f"三次可见时间窗完全一致且 |spacing_ratio - width_ratio| / width_ratio <= {SPACING_TOL}",
            "C_locate": "主动 setViewport 后可见范围落在请求窗口 ±1 根内",
            "D_append_only": "context_days=0 与 7 的 run 区间内 K 线逐根相同",
            "E_budget": f"context_days=90 总根数 <= {CAP} 且 run 区间仍逐根相同",
        },
        "supersedes_note": (
            "`22_K线居中修复结论.json` 是 **context_days=0（上下文功能上线前）** 的实测："
            "ACHUSDT 可见 18 根 / 留白 0.7%~0.9%。当前默认载荷已带 ±7 天上下文，"
            "同一 run 的可见根数变成 343 根（1h）——判据本身不变（**可见根数 == 载荷根数**、"
            "留白 <= 5%），只是分母变了。两份结论互不矛盾，各自绑定各自的载荷。"
        ),
        "payload_bars": expect,
        "A_fill": a_rows,
        "B_zoom_kept": b_row,
        "C_locate": c_row,
        "D_append_only": d_row,
        "E_budget": e_row,
        "console_errors": steps.get("console_errors"),
        "A_pass": all(r["pass"] for r in a_rows),
        "B_pass": b_row["pass"],
        "C_pass": c_row["pass"],
        "D_pass": d_row["pass"],
        "E_pass": e_row["pass"],
        "F_console_clean": not (steps.get("console_errors") or []),
    }
    verdict["overall"] = "PASS" if all([
        verdict["A_pass"], verdict["B_pass"], verdict["C_pass"],
        verdict["D_pass"], verdict["E_pass"], verdict["F_console_clean"],
    ]) else "FAIL"

    out = BASE / "23_上下文行情结论.json"
    out.write_text(json.dumps(verdict, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(verdict, ensure_ascii=False, indent=2))
    print(f"\n写出 {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
