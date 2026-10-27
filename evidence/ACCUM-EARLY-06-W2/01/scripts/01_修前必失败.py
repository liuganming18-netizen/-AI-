# -*- coding: utf-8 -*-
"""ACCUM-EARLY-06-W2/01｜脚本 01：修前必失败（三类用例）

判据与修后完全同一套（`_w2.CASES` 的 expect_*），只换被测代码版本：
  · 本脚本在**修前代码**上跑，要求三类用例**每一类都至少有一项不符**；
  · 脚本 02 在**修后代码**上跑同一套判据，要求三类全部相符。

三类用例（缺一类就不算交齐）：
  A 扫低承接通过、突破根不通过 ⇒ 应出候选（修前不出）
  B 扫低不通过、突破根通过   ⇒ 不应出候选（修前出了）
  C 扫低根数据不足           ⇒ 不应出候选且终结为 DATA_INSUFFICIENT（修前出了候选）

「状态机已接上」的证据：全部走 `SchemeObserver.on_bar` + `observe_store` 迁移表，
不直接调 `_absorption`；`_absorption` 的下标只从轨迹里读，不从注释里抄。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _w2 as W                                    # noqa: E402


def expected_of(name: str, spec: dict) -> dict:
    return {
        "n_candidates": 1 if spec["expect_candidate"] else 0,
        "terminal_state": spec["expect_terminal"],
        "absorption_bar_i": W.SWEEP_I,
        "absorption_bar_close_utc": W.iso(W.T0 + W.SWEEP_I * W.H + W.H - 1),
    }


def actual_of(r: dict, spec: dict) -> dict:
    ab = [c for c in r["trace"] if c["fn"] == "absorption"]
    idx = sorted({c["i"] for c in ab})
    # 候选条件表（有候选时从候选 note 读，没有时读最后一组条件）
    conds = {}
    if r["candidates"]:
        note = json.loads(r["candidates"][-1]["note"])
        conds = {c["name"]: c["result"] for c in note.get("conditions") or []}
        status = note.get("status")
    else:
        conds = W.conds_of(r)
        status = None
    terminal = None
    if r["final_state"] in ("INVALIDATED", "EXPIRED", "CLOSED", "DATA_INSUFFICIENT"):
        terminal = r["final_state"]
    return {
        "n_candidates": r["n_candidates"],
        "terminal_state": terminal,
        "final_state": r["final_state"],
        "candidate_status": status,
        "absorption_call_indices": idx,
        "absorption_bar_i": (idx[0] if len(idx) == 1 else (idx[-1] if idx else None)),
        "absorption_n_calls": len(ab),
        "absorption_bar_close_utc": (ab[-1]["bar_close_utc"] if ab else None),
        "conditions": conds,
        "transitions": [{"to": t["to_state"], "at": W.iso(t["at_ms"]),
                         "reason": t["reason"]} for t in r["transitions"]],
        "closed_reason": r["closed_reason"],
    }


def diff(exp: dict, act: dict) -> list:
    out = []
    if act["n_candidates"] != exp["n_candidates"]:
        out.append({"item": "n_candidates", "expected": exp["n_candidates"],
                    "actual": act["n_candidates"]})
    if act["terminal_state"] != exp["terminal_state"]:
        out.append({"item": "terminal_state", "expected": exp["terminal_state"],
                    "actual": act["terminal_state"]})
    if act["absorption_bar_i"] != exp["absorption_bar_i"]:
        out.append({"item": "absorption_bar_i(承接判断读的 bar 下标)",
                    "expected": exp["absorption_bar_i"],
                    "actual": act["absorption_bar_i"]})
    return out


def main() -> int:
    # 防篡改：本脚本只在**修前代码**上有意义。若产物已存在且记录的代码哈希与当前不符，
    # 说明代码已改 ⇒ 拒绝覆盖（否则「修前必失败」的证据会被修后结果悄悄替换）。
    prev = W.load_out("01_pre_fix_fail.json")
    cur = W.sha256_file(W.BACKEND / "accum_early_entry.py")
    if prev and prev.get("code_under_test", {}).get("sha256") not in (None, cur["sha256"]):
        print(json.dumps({
            "skipped": True,
            "why": "被测代码已变更，保留既有修前证据未覆盖（修后请跑 02）",
            "recorded_sha256": prev["code_under_test"]["sha256"],
            "current_sha256": cur["sha256"]}, ensure_ascii=False))
        return 0

    cfg = W.load_fixture_config()
    out = {"code_under_test": cur,
           "config_sha256": cfg.get("config_sha256"),
           "config_ready": cfg.get("ready"),
           "expectation_source": ("04-A README:53（承接判断时点=扫低根 1h 收盘）+ "
                                  "02_规则接口.md:134（基准=扫低根之前 20 根）"),
           "cases": {}, "summary": {}}

    all_failed = True
    for name, spec in W.CASES.items():
        bars = W.series(spec["sweep_tbq"], spec["confirm_tbq"], confirm_i=spec["confirm_i"])
        r = W.run_observer(name, bars, cfg, tag="01_" + name[:1])
        exp = expected_of(name, spec)
        act = actual_of(r, spec)
        d = diff(exp, act)
        all_failed = all_failed and bool(d)
        out["cases"][name] = {
            "sweep_bar": {"i": W.SWEEP_I, "close_utc": W.iso(bars[W.SWEEP_I]["close_time"]),
                          "quote_volume": bars[W.SWEEP_I]["quote_volume"],
                          "taker_buy_quote_volume": bars[W.SWEEP_I]["taker_buy_quote_volume"]},
            "confirm_bar": {"i": spec["confirm_i"],
                            "close_utc": W.iso(bars[spec["confirm_i"]]["close_time"]),
                            "quote_volume": bars[spec["confirm_i"]]["quote_volume"],
                            "taker_buy_quote_volume": bars[spec["confirm_i"]]["taker_buy_quote_volume"]},
            "expected": exp, "actual": act, "mismatches": d,
            "verdict": "FAIL_AS_EXPECTED" if d else "UNEXPECTED_PASS",
        }

    out["summary"] = {
        "all_cases_fail_before_fix": all_failed,
        "n_cases": len(out["cases"]),
        "verdict": ("修前必失败：三类全部不符预期" if all_failed
                    else "异常：存在修前就通过（或未触发差异）的用例，说明用例没打到缺陷"),
    }
    W.dump("01_pre_fix_fail.json", out)

    for n, c in out["cases"].items():
        print(f"{n}: {c['verdict']}  mismatches=" +
              json.dumps(c["mismatches"], ensure_ascii=False))
    print("summary:", json.dumps(out["summary"], ensure_ascii=False))
    return 0 if all_failed else 2


if __name__ == "__main__":
    raise SystemExit(main())
