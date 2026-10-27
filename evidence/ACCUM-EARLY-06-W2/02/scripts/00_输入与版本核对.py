# -*- coding: utf-8 -*-
"""ACCUM-EARLY-06-W2/02｜脚本 00：输入未换（7 事件 / 行情范围 / 测试配置）+ 版本身份

四组核对，全部回读原始文件，不靠记忆：
  I1 原 7 事件：从 `05-E/out/03_event_trace.json` 读回，与固化的清单逐条比对（含旧状态/旧确认时刻）
  I2 行情范围：从 `05-E-R1/scripts/02_retest_original_events.py` 源码里抽出时间区间、
     `max_boxes`、`warmup_days`，与本单的 `RUNS_SPEC` 比对（**改输入会当场红**）
  I3 测试配置：本窗副本与 05-B 原文件**逐字节相同**，且 sha = 05-E-R1 记录的 f322a940…
  I4 版本身份：修前 d96ad60e…（06-W2/01 逆向重建）、修后 3c570edc…、05-E-R1 当时 f7056980…
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _w2b as W                                    # noqa: E402

TRACE_05E = W.PROJECT / "evidence" / "ACCUM-EARLY-05-E" / "out" / "03_event_trace.json"
SCRIPT_05E_R1 = W.PROJECT / "evidence" / "ACCUM-EARLY-05-E-R1" / "scripts" / "02_retest_original_events.py"
CFG_05B = W.PROJECT / "evidence" / "ACCUM-EARLY-05-B" / "config" / "fixture_rules_05.json"


def check_events() -> dict:
    doc = json.loads(TRACE_05E.read_text(encoding="utf-8"))
    cases = doc["cases"]
    rows = []
    ok = (len(cases) == 7 and [c["event_id"] for c in cases] == W.EVENT_IDS)
    for c in cases:
        meta = W.parse_event_id(c["event_id"])
        rows.append({**meta, "old_status": c.get("status"),
                     "old_confirm_utc": c.get("confirm_time_utc"),
                     "in_my_list": c["event_id"] in W.EVENT_IDS})
    return {"source": str(TRACE_05E), "cases_count_in_source": doc.get("cases_count"),
            "n_cases": len(cases), "ids_identical_and_ordered": ok, "rows": rows}


def check_ranges() -> dict:
    src = SCRIPT_05E_R1.read_text(encoding="utf-8")
    times = re.findall(r'ms_of\("([0-9T:\-Z]+)"\)', src)
    max_boxes = re.findall(r"max_boxes=(\d+)", src)
    warmup = re.findall(r"warmup_days=(\d+)", src)
    # 源码里出现顺序：SOL start/end，AVAX start/end
    src_spec = []
    if len(times) >= 4:
        src_spec = [
            {"symbol": "SOLUSDT", "start": times[0], "end": times[1],
             "max_boxes": int(max_boxes[0]), "warmup_days": int(warmup[0])},
            {"symbol": "AVAXUSDT", "start": times[2], "end": times[3],
             "max_boxes": int(max_boxes[1]), "warmup_days": int(warmup[1])},
        ]
    mine = [{k: s[k] for k in ("symbol", "start", "end", "max_boxes", "warmup_days")}
            for s in W.RUNS_SPEC]
    return {"source": str(SCRIPT_05E_R1), "times_in_source": times,
            "max_boxes_in_source": max_boxes, "warmup_in_source": warmup,
            "source_spec": src_spec, "my_spec": mine,
            "identical": src_spec == mine}


def check_config() -> dict:
    a = W.sha256_file(W.CONFIG_PATH)
    b = W.sha256_file(CFG_05B)
    return {"my_copy": a, "original_05B": b,
            "byte_identical": a.get("sha256") == b.get("sha256"),
            "equals_05E_R1_recorded": a.get("sha256") == W.CONFIG_SHA_EXPECT,
            "05E_R1_recorded": W.CONFIG_SHA_EXPECT}


def check_versions() -> dict:
    pre = W.sha256_file(W.PRE_FIX_PATH)
    post = W.sha256_file(W.POST_FIX_PATH)
    pre_mod = W.load_pre_fix()
    post_mod = W.load_post_fix()
    return {
        "pre_fix": pre, "post_fix": post,
        "pre_fix_expected": W.PRE_FIX_SHA_EXPECT,
        "post_fix_expected": W.POST_FIX_SHA_EXPECT,
        "pre_fix_matches": pre["sha256"] == W.PRE_FIX_SHA_EXPECT,
        "post_fix_matches": post["sha256"] == W.POST_FIX_SHA_EXPECT,
        "pre_module_version": getattr(pre_mod, "VERSION", None),
        "post_module_version": getattr(post_mod, "VERSION", None),
        "same_declared_version_string": (getattr(pre_mod, "VERSION", None)
                                         == getattr(post_mod, "VERSION", None)),
        "05E_R1_tested_version": W.VERSION_05E_R1,
        "05E_R1_differs_from_both": W.VERSION_05E_R1 not in
                                    (pre["sha256"], post["sha256"]),
        "note": ("05-E-R1 的 7 事件结论产生在 f7056980 上；本单不重跑该版本（已被覆盖），"
                 "只在报告里作第三方对照列，并明确标注版本不同。"),
    }


def main() -> int:
    ev = check_events()
    rg = check_ranges()
    cf = check_config()
    vs = check_versions()
    out = {"generated_utc": W.iso(int(__import__("time").time() * 1000)),
           "I1_events": ev, "I2_ranges": rg, "I3_config": cf, "I4_versions": vs}
    out["summary"] = {
        "events_ok": ev["n_cases"] == 7 and ev["ids_identical_and_ordered"],
        "ranges_ok": rg["identical"],
        "config_ok": cf["byte_identical"] and cf["equals_05E_R1_recorded"],
        "versions_ok": vs["pre_fix_matches"] and vs["post_fix_matches"],
    }
    out["summary"]["verdict"] = ("输入未换（7 事件 / 范围 / 配置逐项对上）"
                                if all(out["summary"].values()) else "输入核对未通过")
    W.dump("00_inputs_and_versions.json", out)

    print("I1 events:", out["summary"]["events_ok"], "| n =", ev["n_cases"])
    print("I2 ranges:", out["summary"]["ranges_ok"], "|", json.dumps(rg["source_spec"], ensure_ascii=False))
    print("I3 config:", out["summary"]["config_ok"], "|", cf["my_copy"]["sha256"][:12])
    print("I4 versions:", out["summary"]["versions_ok"], "|",
          vs["pre_fix"]["sha256"][:12], "->", vs["post_fix"]["sha256"][:12],
          "| 05E-R1", vs["05E_R1_tested_version"][:12])
    print("summary:", json.dumps(out["summary"], ensure_ascii=False))
    return 0 if all(out["summary"][k] for k in
                    ("events_ok", "ranges_ok", "config_ok", "versions_ok")) else 2


if __name__ == "__main__":
    raise SystemExit(main())
