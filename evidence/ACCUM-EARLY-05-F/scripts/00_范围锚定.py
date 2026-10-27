# -*- coding: utf-8 -*-
"""ACCUM-EARLY-05-F｜00 范围锚定（只读）。

做什么
------
把本单的「对象」先钉死，避免后面对着一个含糊的区间做统计：
  1. 05-C 的产物到底在不在盘上（不存在就如实记，不拿 04-A 顶替成 05-C）；
  2. 04-A 已落盘的两段共同观察范围（窗口 / 池 / 候选数）；
  3. 数据源与读取器的**当时**状态（文件大小、修改时刻、冷档水位）；
  4. 读取范围（含预热）—— 这是后面「窗口连续」核对的真实边界。

不写任何生产文件，产物只落本单 out/。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve()
ROOT = HERE.parents[3]
OUT = HERE.parents[1] / "out"
EVID = ROOT / "evidence"

HOT = ROOT / "数据" / "市场数据" / "db" / "market_v2.sqlite3"
COLD = ROOT / "数据" / "市场数据" / "parquet"
SPEC = (ROOT / "数据" / "研究输入" / "外部迁移_2026-10-16" /
        "本次离线决策_2026-10-12" / "07_526币保守执行近似规格.csv")


def iso(ms):
    if ms is None:
        return None
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(int(ms) / 1000))


def stat(p: Path) -> dict:
    if not p.exists():
        return {"path": str(p.relative_to(ROOT)), "exists": False}
    st = p.stat()
    return {
        "path": str(p.relative_to(ROOT)),
        "exists": True,
        "size_bytes": st.st_size,
        "size_gib": round(st.st_size / (1024 ** 3), 3),
        "mtime_utc": iso(st.st_mtime * 1000),
        "sha256": "SKIPPED_BY_DESIGN(12GiB级文件禁止整读哈希，改用大小+修改时刻+内容统计)",
    }


def read_json(p: Path):
    return json.loads(p.read_text(encoding="utf-8"))


def main():
    now_ms = int(time.time() * 1000)
    facts: dict = {
        "ticket": "ACCUM-EARLY-05-F",
        "generated_at_utc": iso(now_ms),
        "python": sys.version.split()[0],
        "readonly_declaration": [
            "本脚本只读：不写生产库、不写 parquet、不下单、不改共享实现",
            "产物只落 evidence/ACCUM-EARLY-05-F/out/",
        ],
    }

    # ── 1. 05 系列目录盘点：05-C 在不在 ───────────────────────────────
    dirs05 = sorted(p.name for p in EVID.glob("ACCUM-EARLY-05*") if p.is_dir())
    facts["sibling_05"] = {
        "found": dirs05,
        "has_05C": "ACCUM-EARLY-05-C" in dirs05,
        "note": ("05-C 产物存在" if "ACCUM-EARLY-05-C" in dirs05 else
                 "核对时刻盘上不存在 ACCUM-EARLY-05-C 目录；本单不得声称已按其冻结范围执行"),
    }
    if "ACCUM-EARLY-05-C" in dirs05:
        d = EVID / "ACCUM-EARLY-05-C"
        facts["sibling_05"]["files"] = [
            str(p.relative_to(ROOT)) for p in sorted(d.rglob("*")) if p.is_file()][:40]

    # ── 2. 04-A 已落盘的两段共同观察范围 ──────────────────────────────
    ranges = {}
    for tag in ("research_final", "holdout_final"):
        p = EVID / "ACCUM-EARLY-04-A" / "out" / f"01_共同观察范围_{tag}.json"
        if not p.exists():
            ranges[tag] = {"exists": False, "path": str(p.relative_to(ROOT))}
            continue
        d = read_json(p)
        agg = d["aggregate"]
        ranges[tag] = {
            "exists": True,
            "path": str(p.relative_to(ROOT)),
            "generated_at_utc": d.get("generated_at_utc"),
            "window": d["window"],
            "pool": d["pool"],
            "symbols_scanned": agg["symbols_scanned"],
            "symbols_with_data": agg["symbols_with_data"],
            "symbols_skipped": agg["symbols_skipped"],
            "box_candidates_total": agg["box_candidates_total"],
            "schemes": {k: {
                "events": v.get("events"),
                "confirmed": v.get("confirmed"),
                "trades": (v.get("trade_summary") or {}).get("成交"),
                "R_sum": (v.get("trade_summary") or {}).get("R合计"),
            } for k, v in agg["schemes"].items()},
            "discipline": d.get("discipline"),
        }
    facts["common_observation_range_04A"] = ranges

    # ── 3. 数据源当时状态 ─────────────────────────────────────────────
    facts["sources"] = {
        "hot_db": stat(HOT),
        "cold_root": {"path": str(COLD.relative_to(ROOT)), "exists": COLD.exists()},
        "cold_state": read_json(COLD / "_state.json") if (COLD / "_state.json").exists() else None,
        "spec_csv": stat(SPEC),
    }
    # 冷档水位（导出器断点）
    for name in ("_export_state.json", "_catalog.json"):
        p = COLD / name
        if p.exists():
            try:
                facts["sources"][f"cold_{name}"] = read_json(p)
            except Exception as exc:                        # noqa: BLE001
                facts["sources"][f"cold_{name}"] = {"read_error": repr(exc)}

    # ── 4. 读取范围（含预热），与 04-A 脚本逐项对齐 ────────────────────
    DAY = 86_400_000
    read_scope = {}
    for tag, r in ranges.items():
        if not r.get("exists"):
            continue
        w0, w1 = r["window"]["from_ms"], r["window"]["to_ms"]
        read_scope[tag] = {
            "4h": {"from_ms": w0 - 400 * DAY, "to_ms": w1 + 40 * DAY,
                   "from_utc": iso(w0 - 400 * DAY), "to_utc": iso(w1 + 40 * DAY),
                   "why": "Wilder ATR(14) 与 42 根箱体需要长预热"},
            "1h": {"from_ms": w0 - 30 * DAY, "to_ms": w1 + 40 * DAY,
                   "from_utc": iso(w0 - 30 * DAY), "to_utc": iso(w1 + 40 * DAY),
                   "why": "局部压制位需要前 48 根 1h + 余量"},
            "15m": {"from_ms": w0 - 2 * DAY, "to_ms": w1 + 45 * DAY,
                    "from_utc": iso(w0 - 2 * DAY), "to_utc": iso(w1 + 45 * DAY),
                    "why": "成交/止损按事件切片，前后各留缓冲"},
            "insufficient_history_gate": {"4h_bars_min": 260, "1h_bars_min": 400,
                                          "source": "01_共同观察范围.py:120"},
        }
    facts["read_scope_including_warmup"] = read_scope

    # ── 5. 本单核验对象：显式声明用哪个范围 ───────────────────────────
    facts["object_of_this_ticket"] = {
        "primary": "ACCUM-EARLY-04-A 已落盘的两段共同观察范围（research_final / holdout_final）",
        "why": ("05-C 目录核对时刻不存在；04-A 的共同观察范围是本工程唯一已落盘、"
                "带池与候选计数的范围定义，可被独立复核"),
        "limitation": ("若 05-C 后续冻结的范围与 04-A 不同（区间/池/门槛任一变化），"
                       "本单的覆盖统计必须按新范围重跑，不能直接搬运"),
    }

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "00_范围锚定.json").write_text(
        json.dumps(facts, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "generated_at_utc": facts["generated_at_utc"],
        "sibling_05": facts["sibling_05"],
        "ranges": {k: (v.get("window"), v.get("symbols_with_data"),
                       v.get("symbols_skipped"), v.get("box_candidates_total"))
                   for k, v in ranges.items()},
        "hot_gib": facts["sources"]["hot_db"].get("size_gib"),
        "cold_watermark": (facts["sources"].get("cold_state") or {}).get(
            "archive_verified_upto_ms"),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
