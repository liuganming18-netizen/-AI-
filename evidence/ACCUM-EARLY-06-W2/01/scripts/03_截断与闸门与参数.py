# -*- coding: utf-8 -*-
"""ACCUM-EARLY-06-W2/01｜脚本 03：未来数据截断反例 + 配置闸门 + 未改阈值/经济参数

四块，每块都配**正向对照**（否则「挡住了」可能只是因为压根没跑）：
  T 未来数据截断：前缀序列（只到扫低根）不得产生候选；截断与全量的前段动作必须逐条相同；
    被触碰的最大 bar 下标必须 ≤ 截断点。负向对照：承接若改在确认根求值，结论会翻转
    ⇒ 该测试不是空转。
  G 配置闸门：未批准配置 ⇒ 状态机停在 BOX、承接一次都不求值、无候选；
    fixture 档缺显式环境变量 ⇒ `open_allowed=False`；必填 49 项、待决映射仍闭合。
  P 参数未动：本窗测试配置与 05-B fixture 逐项 deep-diff 只允许 `approval_ref` 一处；
    `FIXED_RULES` 8 项逐项核对；`REQUIRED_PATHS` / `IMPLEMENTED` 计数与内容核对。
  R 同类缺陷（不修，只交证据）：`_resistance` 仍按**确认根**取 48 根窗口。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _w2 as W                                    # noqa: E402

FIXTURE_05 = W.PROJECT / "evidence" / "ACCUM-EARLY-05-B" / "config" / "fixture_rules_05.json"
BLANK_05 = W.PROJECT / "evidence" / "ACCUM-EARLY-05-B" / "config" / "批准配置_空白表单_05.json"


# ══════════════════════════════════════════════════════════════════════════
# T 未来数据截断
# ══════════════════════════════════════════════════════════════════════════
def truncation(cfg) -> dict:
    spec = W.CASES["A_扫低承接通过_突破根不通过"]
    full_bars = W.series(spec["sweep_tbq"], spec["confirm_tbq"], confirm_i=spec["confirm_i"])
    cut = W.SWEEP_I + 1                                  # 只给到扫低根（含）
    trunc_bars = full_bars[:cut]

    r_trunc = W.run_observer("T_截断到扫低根", trunc_bars, cfg, tag="03_trunc")
    r_full = W.run_observer("T_全量", full_bars, cfg, tag="03_full")
    r_long = W.run_observer("T_多给100根", W.series(spec["sweep_tbq"], spec["confirm_tbq"])
                            + [W.bar(W.T0 + i * W.H, **W.QUIET)
                               for i in range(len(full_bars), len(full_bars) + 100)],
                            cfg, tag="03_long")

    def acts_upto(r, max_i):
        return [{k: a[k] for k in ("bar_i", "kind", "state", "reason")}
                for a in r["timeline"] if a["bar_i"] <= max_i]

    same_prefix = acts_upto(r_trunc, W.SWEEP_I) == acts_upto(r_full, W.SWEEP_I)
    same_confirm_prefix = acts_upto(r_full, W.CONFIRM_I) == acts_upto(r_long, W.CONFIRM_I)
    touched = max([c["i"] for c in r_trunc["trace"]] or [-1])

    # 负向对照：同一根 bar 下标换成确认根，承接结论会不会变（证明测试能分辨）
    bars_ratio = {"sweep": W.explain_ratio(full_bars, W.SWEEP_I, cfg),
                  "confirm": W.explain_ratio(full_bars, spec["confirm_i"], cfg)}

    return {
        "cut_index": W.SWEEP_I, "n_bars_truncated": len(trunc_bars),
        "truncated_n_candidates": r_trunc["n_candidates"],
        "truncated_final_state": r_trunc["final_state"],
        "truncated_absorption_indices": sorted({c["i"] for c in r_trunc["trace"]
                                                if c["fn"] == "absorption"}),
        "max_bar_index_touched_in_truncated_run": touched,
        "no_candidate_before_confirm": r_trunc["n_candidates"] == 0,
        "touched_within_cut": touched <= W.SWEEP_I,
        "prefix_actions_identical": same_prefix,
        "prefix_actions_full_vs_long_identical": same_confirm_prefix,
        "actions_prefix": acts_upto(r_trunc, W.SWEEP_I),
        "negative_control_ratio_by_index": bars_ratio,
        "negative_control_distinguishes": (
            bars_ratio["sweep"]["buy_ratio"] != bars_ratio["confirm"]["buy_ratio"]),
        "verdict": ("PASS" if (r_trunc["n_candidates"] == 0 and touched <= W.SWEEP_I
                               and same_prefix and same_confirm_prefix
                               and bars_ratio["sweep"]["buy_ratio"]
                               != bars_ratio["confirm"]["buy_ratio"]) else "FAIL"),
    }


# ══════════════════════════════════════════════════════════════════════════
# G 配置闸门
# ══════════════════════════════════════════════════════════════════════════
def gates(cfg) -> dict:
    spec = W.CASES["A_扫低承接通过_突破根不通过"]
    bars = W.series(spec["sweep_tbq"], spec["confirm_tbq"], confirm_i=spec["confirm_i"])

    # ① 未批准配置（05-B 的空白表单）：ready=False
    import os
    old = {k: os.environ.get(k) for k in ("ACCUM05_CONFIG", "ACCUM05_FIXTURE_OK")}
    os.environ["ACCUM05_CONFIG"] = str(BLANK_05)
    os.environ.pop("ACCUM05_FIXTURE_OK", None)
    cfg_blank = W.AEE.load_config(BLANK_05)
    r_blank = W.run_observer("G_未批准配置", bars, cfg_blank, tag="03_blank")

    # ② fixture 档但没有显式环境变量
    os.environ["ACCUM05_CONFIG"] = str(W.CONFIG_DIR / "fixture_rules_06w2.json")
    cfg_noenv = W.AEE.load_config(W.CONFIG_DIR / "fixture_rules_06w2.json")
    os.environ["ACCUM05_FIXTURE_OK"] = "1"
    cfg_env = W.AEE.load_config(W.CONFIG_DIR / "fixture_rules_06w2.json")
    for k, v in old.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v

    # ③ 正向对照：批准的 fixture（带 env）必须真的能跑到 SWEEP 之后
    r_ok = W.run_observer("G_正向对照", bars, cfg, tag="03_pos")

    mc = W.AEE.mapping_check()
    return {
        "blank_config": {"ready": cfg_blank.get("ready"),
                         "open_allowed": cfg_blank.get("open_allowed"),
                         "blocked_by": cfg_blank.get("blocked_by"),
                         "n_missing": len(cfg_blank.get("missing") or []),
                         "n_candidates": r_blank["n_candidates"],
                         "absorption_calls": len([c for c in r_blank["trace"]
                                                  if c["fn"] == "absorption"]),
                         "final_state": r_blank["final_state"],
                         "blocked_actions": [a["reason"] for a in r_blank["timeline"]
                                             if a["kind"] == "blocked_config"]},
        "fixture_without_env": {"ready": cfg_noenv.get("ready"),
                                "open_allowed": cfg_noenv.get("open_allowed"),
                                "open_scope": cfg_noenv.get("open_scope"),
                                "blocked_by": cfg_noenv.get("blocked_by")},
        "fixture_with_env": {"ready": cfg_env.get("ready"),
                             "open_allowed": cfg_env.get("open_allowed"),
                             "open_scope": cfg_env.get("open_scope")},
        "positive_control": {"n_candidates": r_ok["n_candidates"],
                             "absorption_indices": sorted({c["i"] for c in r_ok["trace"]
                                                           if c["fn"] == "absorption"})},
        "required_paths": len(W.AEE.REQUIRED_PATHS),
        "pending_mapping": mc,
        "verdict": ("PASS" if (cfg_blank.get("ready") is False
                               and cfg_blank.get("open_allowed") is False
                               and r_blank["n_candidates"] == 0
                               and not [c for c in r_blank["trace"]
                                        if c["fn"] == "absorption"]
                               and cfg_noenv.get("open_allowed") is False
                               and cfg_env.get("open_allowed") is True
                               and r_ok["n_candidates"] == 1
                               and mc["ok"] and len(W.AEE.REQUIRED_PATHS) == 49)
                    else "FAIL"),
    }


# ══════════════════════════════════════════════════════════════════════════
# P 参数未动
# ══════════════════════════════════════════════════════════════════════════
def deep_diff(a, b, path=""):
    out = []
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            out += deep_diff(a.get(k), b.get(k), f"{path}.{k}" if path else str(k))
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            out.append({"path": path, "a": a, "b": b})
        else:
            for i, (x, y) in enumerate(zip(a, b)):
                out += deep_diff(x, y, f"{path}[{i}]")
    elif a != b:
        out.append({"path": path, "a": a, "b": b})
    return out


def params_untouched(cfg) -> dict:
    d05 = json.loads(FIXTURE_05.read_text(encoding="utf-8"))
    d06 = json.loads((W.CONFIG_DIR / "fixture_rules_06w2.json").read_text(encoding="utf-8"))
    diffs = deep_diff(d05, d06)
    fixed_expect = {"confirm_timeframe": "1h", "execution_timeframe": "15m",
                    "entry_delay_minutes": 15, "initial_stop_pct": 0.05,
                    "trailing_low_pct": 0.05, "trailing_trigger_r": 0.0,
                    "max_holding_days": None, "taker_fee_bps": 5.0}
    fixed_ok = all(W.AEE.FIXED_RULES.get(k) == v for k, v in fixed_expect.items())
    return {
        "config_deep_diff_vs_05B_fixture": diffs,
        "only_approval_ref_changed": (len(diffs) == 1 and diffs[0]["path"] == "approval_ref"),
        "fixed_rules": W.AEE.FIXED_RULES,
        "fixed_rules_match_expected": fixed_ok,
        "required_paths_count": len(W.AEE.REQUIRED_PATHS),
        "implemented_keys": sorted(W.AEE.IMPLEMENTED),
        "sell_pressure_in_config": cfg["doc"]["shared"]["sell_pressure"],
        "verdict": ("PASS" if (len(diffs) == 1 and diffs[0]["path"] == "approval_ref"
                               and fixed_ok and len(W.AEE.REQUIRED_PATHS) == 49)
                    else "FAIL"),
    }


# ══════════════════════════════════════════════════════════════════════════
# R 同类缺陷（不修）：`_resistance` 仍按确认根取窗口
# ══════════════════════════════════════════════════════════════════════════
def adjacent_resistance(cfg) -> dict:
    """扫低根之后插一根「高点尖峰但不收上压制位」的 bar。

    · 规则（04-A README:52）：压制位 = **扫低根之前** 48 根 1h 内最后一个摆动高点；
    · 现状：`_confirm_actions` 传的是确认根下标 ⇒ 窗口右移，尖峰被算进压制位，
      确认门槛被抬高（本该确认的那一根不再确认）。
    本单**不修**（工单只点名 `_absorption`），只交证据。
    """
    sweep_i = W.SWEEP_I
    spike_i = sweep_i + 1                 # 尖峰根：高点 105，但不收上压制位
    confirm_i = spike_i + 4               # 尖峰之后第 4 根才收上 103.9（此时它已是合法摆动高点）
    bars = []
    for i in range(confirm_i + 1):
        ms = W.T0 + i * W.H
        if i == sweep_i:
            b = dict(W.SWEEP, tbq=W.TBQ_PASS)
        elif i == spike_i:
            b = dict(o=103.7, h=105.0, l=103.6, c=103.75, qv=1000.0, tbq=600.0)
        elif spike_i < i < confirm_i:
            b = dict(o=103.7, h=104.0, l=103.7, c=103.75, qv=1000.0, tbq=600.0)
        elif i == confirm_i:
            b = dict(o=103.8, h=104.0, l=103.75, c=103.95, qv=1000.0, tbq=900.0)
        else:
            b = dict(W.QUIET)
        bars.append(W.bar(ms, **b))

    r = W.run_observer("R_压制位窗口", bars, cfg, tag="03_res")
    res_calls = [c for c in r["trace"] if c["fn"] == "resistance"]
    sweep_window = W.AEE._resistance(bars, sweep_i, W.BOX, cfg)      # 只读对照
    confirm_window = W.AEE._resistance(bars, confirm_i, W.BOX, cfg)
    return {
        "note": "本项**未修**：工单 06-03 只点名 `_absorption`；这里只交证据。",
        "sweep_relative_resistance": sweep_window[0],
        "confirm_relative_resistance": confirm_window[0],
        "resistance_call_indices": sorted({c["i"] for c in res_calls}),
        "differs": sweep_window[0] != confirm_window[0],
        "n_candidates": r["n_candidates"],
        "confirm_bar_i": confirm_i,
        "confirm_bar_close": bars[confirm_i]["close"],
        "would_confirm_under_sweep_relative": (bars[confirm_i]["close"]
                                               > (sweep_window[0] or 0)),
    }


def main() -> int:
    cfg = W.load_fixture_config()
    out = {"code": W.sha256_file(W.BACKEND / "accum_early_entry.py"),
           "T_truncation": truncation(cfg),
           "G_gates": gates(cfg),
           "P_params": params_untouched(cfg),
           "R_adjacent_defect": adjacent_resistance(cfg)}
    out["summary"] = {k: out[k]["verdict"] if isinstance(out[k], dict) and "verdict" in out[k]
                      else "EVIDENCE_ONLY"
                      for k in ("T_truncation", "G_gates", "P_params", "R_adjacent_defect")}
    W.dump("03_truncation_gates_params.json", out)

    for k in ("T_truncation", "G_gates", "P_params"):
        print(k, "->", out[k]["verdict"])
    print("R_adjacent_defect:", json.dumps(
        {k: out["R_adjacent_defect"][k] for k in
         ("sweep_relative_resistance", "confirm_relative_resistance",
          "resistance_call_indices", "differs", "n_candidates")}, ensure_ascii=False))
    print("truncation:", json.dumps({k: out["T_truncation"][k] for k in
                                     ("truncated_n_candidates", "max_bar_index_touched_in_truncated_run",
                                      "touched_within_cut", "prefix_actions_identical",
                                      "negative_control_distinguishes")}, ensure_ascii=False))
    print("gates:", json.dumps({"blank_ready": out["G_gates"]["blank_config"]["ready"],
                                "blank_open": out["G_gates"]["blank_config"]["open_allowed"],
                                "blank_absorption_calls": out["G_gates"]["blank_config"]["absorption_calls"],
                                "fixture_noenv_open": out["G_gates"]["fixture_without_env"]["open_allowed"],
                                "required_paths": out["G_gates"]["required_paths"]},
                               ensure_ascii=False))
    ok = all(out[k]["verdict"] == "PASS" for k in ("T_truncation", "G_gates", "P_params"))
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
