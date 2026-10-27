# -*- coding: utf-8 -*-
"""ACCUM-EARLY-03-B｜03 接线前后对照（同一事件、同一代码、只切换数据来源）。

目的只有一个：**证明这次接线解决了那条缺陷，而且没有改变已经能跑的那条路**。

每个真实历史事件跑两臂（各自独立账本、独立进程）：
  · `hot`  = 不设 `WX_MARKET_MERGE`（= 改前的行为，只读热库）
  · `merge`= 设 `WX_MARKET_MERGE=1`（冷+热合并）
判定口径：
  · 冷窗事件（E1/E2）：`hot` 必须**跑不通**（取不到成交 bar），`merge` 必须跑通到底；
  · 热窗事件（E3）：两臂的成交价、退出价、净额必须**逐位相同**（零漂移）。

输出：out/03_接线前后对照.json
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve()
PROJECT = HERE.parents[3]
OUT = PROJECT / "evidence" / "ACCUM-EARLY-03-B" / "out"
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(PROJECT / "框架" / "backend"))
sys.path.insert(0, str(PROJECT))

import _harness as H                                          # noqa: E402


def iso(ms):
    if ms is None:
        return None
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(int(ms) / 1000))


def summarize(w: dict) -> dict:
    lg = w.get("ledger") or {}
    pos = (lg.get("positions") or [None])[0]
    real = (lg.get("realized") or [None])[0]
    steps = w.get("steps") or []
    opened = next((s for s in steps if s["step"] == "open_position"), {}).get("result", {})
    exit_step = next((s for s in steps if s["step"] == "exit_15m"), None)
    trail_exit = next((s for s in steps
                       if s["step"] == "trailing" and s.get("exit_triggered")), None)
    return {
        "verdict": w.get("verdict"),
        "reject_reason": w.get("reject_reason"),
        "entry_bar_utc": iso(opened.get("bar_open_time")),
        "entry_price": opened.get("entry_price"),
        "entry_raw": opened.get("raw_price"),
        "market_source": opened.get("market_source"),
        "bar_provenance": opened.get("bar_provenance"),
        "status": (pos or {}).get("status"),
        "exit_price": (pos or {}).get("exit_price"),
        "exit_reason": (pos or {}).get("exit_reason"),
        "exit_bar_utc": (exit_step or {}).get("bar_open_utc") or (trail_exit or {}).get("bar4h_open_utc"),
        "net_pnl": (real or {}).get("net_pnl"),
        "gross_pnl": (real or {}).get("gross_pnl"),
        "funding_paid": (real or {}).get("funding_paid"),
        "counts": lg.get("counts"),
        "stop_history": [(s.get("old_stop"), s.get("new_stop")) for s in (lg.get("stop_history") or [])],
        "walk_bars_checked": w.get("walk_bars_checked"),
        "errors": w.get("errors"),
        "market_mode": w.get("market_mode"),
        "run": w.get("_run"),
    }


def main() -> int:
    rep = {"probe": "03_接线前后对照", "generated_at_utc": iso(time.time() * 1000),
           "env_flag": "WX_MARKET_MERGE", "events": {}, "checks": {}, "errors": []}
    rep["生产账本指纹_跑前"] = H.production_ledger_fingerprint()

    for ev in H.EVENTS:
        rows = {}
        for mode in ("hot", "merge"):
            name = f"03_{ev['key']}_{mode}"
            try:
                w = H.run_job(H.event_job(ev, mode=mode), name, mode=mode)
                rows[mode] = summarize(w)
                rows[mode]["ledger_identity"] = H.ledger_identity(name)
            except Exception as exc:                         # noqa: BLE001
                rep["errors"].append({"event": ev["key"], "mode": mode,
                                      "error": f"{type(exc).__name__}: {exc}"})
                rows[mode] = {"error": str(exc)}
        rep["events"][ev["key"]] = {
            "symbol": ev["symbol"], "signal_uid": ev["signal_uid"],
            "snapshot_utc": ev["snapshot_utc"], "expect_layer": ev["expect_layer"],
            "why": ev["why"], "hot": rows.get("hot"), "merge": rows.get("merge"),
        }

    # ── 判据 ─────────────────────────────────────────────────────────────
    for key, v in rep["events"].items():
        hot, mg = v.get("hot") or {}, v.get("merge") or {}
        if v["expect_layer"] == "cold":
            ok = (hot.get("verdict") == "rejected_at_order"
                  and mg.get("verdict") == "closed"
                  and (mg.get("bar_provenance") or {}).get("layer") == "cold")
            rep["checks"][key] = {
                "判据": "冷窗事件：只读热库跑不通；合并后跑通且成交 bar 来自冷档",
                "hot_verdict": hot.get("verdict"), "hot_reject": hot.get("reject_reason"),
                "merge_verdict": mg.get("verdict"),
                "merge_bar_layer": (mg.get("bar_provenance") or {}).get("layer"),
                "merge_net_pnl": mg.get("net_pnl"),
                "结果": "通过" if ok else "不通过",
                "说明": ("改前：热库第一根 15m 是 2026-09-12，晚于本事件应成交时刻 ⇒ "
                         "按「不许用未来 bar 成交」被拒（fail-closed，没有回填）；"
                         "改后：冷档补齐该时刻的 bar，成交价=该根开盘价。"
                         if ok else "与预期不符，见明细"),
            }
        else:
            same = (hot.get("entry_price") == mg.get("entry_price")
                    and hot.get("exit_price") == mg.get("exit_price")
                    and hot.get("net_pnl") == mg.get("net_pnl")
                    and hot.get("stop_history") == mg.get("stop_history")
                    and hot.get("verdict") == mg.get("verdict"))
            rep["checks"][key] = {
                "判据": "热窗事件：两臂成交价/退出价/净额/止损轨迹必须逐位相同（零漂移）",
                "hot": {k: hot.get(k) for k in ("verdict", "entry_price", "exit_price",
                                                "net_pnl", "stop_history")},
                "merge": {k: mg.get(k) for k in ("verdict", "entry_price", "exit_price",
                                                 "net_pnl", "stop_history")},
                "结果": "通过" if same else "不通过",
                "说明": ("合并开启后结果逐位不变 ⇒ 接线没有改变原本能跑通的那条路。"
                         if same else "出现漂移，必须查清后再交付"),
            }

    rep["生产账本指纹_跑后"] = H.production_ledger_fingerprint()
    changed = [a for a in rep["生产账本指纹_跑前"]
               if (rep["生产账本指纹_跑前"][a].get("sha256")
                   != rep["生产账本指纹_跑后"][a].get("sha256"))]
    rep["生产账本是否被改动"] = {"changed_arms": changed,
                          "verdict": "未被改动" if not changed else "被改动（违规）"}

    ok_all = all(c["结果"] == "通过" for c in rep["checks"].values())
    rep["总判定"] = ("通过" if ok_all and not changed else "不通过")

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "03_接线前后对照.json").write_text(
        json.dumps(rep, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(json.dumps({"checks": {k: {"结果": v["结果"],
                                     "hot": v.get("hot_verdict") or v.get("hot", {}).get("verdict"),
                                     "merge": v.get("merge_verdict") or v.get("merge", {}).get("verdict")}
                                for k, v in rep["checks"].items()},
                      "生产账本": rep["生产账本是否被改动"]["verdict"],
                      "总判定": rep["总判定"],
                      "errors": rep["errors"]},
                     ensure_ascii=False, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
