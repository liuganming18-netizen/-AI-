# -*- coding: utf-8 -*-
"""ACCUM-EARLY-06-W2/02｜脚本 01：同输入双版本回放（修前 d96ad60e… / 修后 3c570edc…）

输入一字不改（脚本 00 已逐项核过）：
  · 7 个原事件的 (方案, 标的, 箱体 formation_ms)
  · 两段行情范围 + `max_boxes=2` + `warmup_days=60`
  · 测试配置 `config/fixture_rules_05.json`（sha f322a940…，与 05-B 逐字节相同）

两个版本各跑一遍 `scan_symbol`（单次时间推进，与生产同一入口），
把事件 / 迁移 / 候选 / 缺口 / 扫描汇总全量落 JSON，供脚本 02 逐事件对撞。

**不宣称**：这里只产出「同一输入下两个版本各自走了什么轨迹」，
`open_candidates=False` ⇒ 不涉及开仓、不涉及成交、不涉及绩效。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _w2b as W                                    # noqa: E402


def totals(run: dict) -> dict:
    """把一个版本的运行结果压成「可一眼对撞」的汇总（不做任何解释）。"""
    per = {}
    for sym, sc in run["scans"].items():
        per[sym] = {
            "boxes_formed": sc["boxes_formed"], "boxes_used": sc["boxes_used"],
            "actions": sc["actions"], "n_candidates": sc["n_candidates"],
            "n_events_in_store": sum(1 for e in run["events"].values()
                                     if e["symbol"] == sym),
            "n_candidates_in_store": sum(len(v) for k, v in run["candidates"].items()
                                         if any(e["event_id"] == k and e["symbol"] == sym
                                                for e in run["events"].values())),
            "bars_4h": sc["bars"].get("4h"), "bars_1h": sc["bars"].get("1h"),
            "cooldown_ms": sc["cooldown_ms"],
            "skipped": sc["skipped"],
        }
    return {"per_symbol": per,
            "n_events_total": len(run["events"]),
            "n_transitions_total": sum(len(v) for v in run["transitions"].values()),
            "n_candidates_total": sum(len(v) for v in run["candidates"].values()),
            "n_gap_rows_total": sum(len(v) for v in run["gaps"].values()),
            "events_by_state": _count(e["state"] for e in run["events"].values()),
            "code_sha256": run["code"].get("sha256"),
            "code_bytes": run["code"].get("bytes"),
            "config": run["config"]}


def _count(it) -> dict:
    out = {}
    for x in it:
        out[x] = out.get(x, 0) + 1
    return dict(sorted(out.items()))


def main() -> int:
    pre_mod = W.load_pre_fix()
    post_mod = W.load_post_fix()

    print("[1/2] 修前 d96ad60e… 回放 …", flush=True)
    pre = W.run_version(pre_mod, "pre")
    W.dump("01_pre_run.json", pre)

    print("[2/2] 修后 3c570edc… 回放 …", flush=True)
    post = W.run_version(post_mod, "post")
    W.dump("01_post_run.json", post)

    out = {
        "inputs": {
            "event_ids": W.EVENT_IDS,
            "runs_spec": W.RUNS_SPEC,
            "config": W.sha256_file(W.CONFIG_PATH),
            "config_sha_expect": W.CONFIG_SHA_EXPECT,
            "open_candidates": False,
            "note": "open_candidates=False：不建仓、不记账本，本脚本只产出观察轨迹",
        },
        "pre": totals(pre), "post": totals(post),
    }
    out["summary"] = {
        "pre_code_sha": pre["code"].get("sha256"),
        "post_code_sha": post["code"].get("sha256"),
        "same_config": (pre["config"].get("config_sha256")
                        == post["config"].get("config_sha256")
                        == W.CONFIG_SHA_EXPECT),
        "same_ready": pre["config"].get("ready") == post["config"].get("ready"),
        "same_open_scope": pre["config"].get("open_scope") == post["config"].get("open_scope"),
        "candidates_pre": out["pre"]["n_candidates_total"],
        "candidates_post": out["post"]["n_candidates_total"],
        "events_pre": out["pre"]["n_events_total"],
        "events_post": out["post"]["n_events_total"],
    }
    W.dump("01_replay_summary.json", out)

    print(json.dumps(out["summary"], ensure_ascii=False, indent=1))
    print("pre :", json.dumps(out["pre"]["per_symbol"], ensure_ascii=False))
    print("post:", json.dumps(out["post"]["per_symbol"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
