# -*- coding: utf-8 -*-
"""STEP1-A 只读盘点：把 回测日志/ 下所有批次、run、窗口、参数指纹汇总成机器可读表。

只读：不写 run 目录、不改任何生产文件；输出只落在 evidence/STEP1-A/runs/。

用法：
    python inventory_objects.py
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from typing import Any, Dict, List, Optional

ROOT = "/Users/boos/Desktop/威科夫因子共振趋势"
LOG = os.path.join(ROOT, "回测日志")
OUT = os.path.join(ROOT, "evidence", "STEP1-A", "runs")

# 待盘点的批次目录（父目录, 批次名）
BATCHES = [
    ("event_trend_v0_1", "runs"),
    ("event_trend_v0_1", "runs_aug100"),
    ("event_trend_v0_1", "runs_2025aug100"),
    ("event_trend_v0_1", "runs_sep50"),
    ("event_trend_v0_1", "runs_sep50_check"),
    ("event_trend_v0_1", "runs_sep50_full"),
    ("event_trend_v0_1", "runs_sep50_strict"),
    ("event_trend_v0_1", "runs_sep_all_latest"),
    ("event_trend_v0_1", "reconstructed"),
    ("oi_resonance_v1", "runs"),
]


def jload(p: str) -> Optional[dict]:
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def jl_count(p: str) -> Optional[int]:
    if not os.path.exists(p):
        return None
    n = 0
    with open(p, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            if line.strip():
                n += 1
    return n


def scan_run(run_dir: str) -> Dict[str, Any]:
    name = os.path.basename(run_dir.rstrip("/"))
    rec: Dict[str, Any] = {"run_dir_name": name, "path": run_dir, "has_inputs": False}
    inp = jload(os.path.join(run_dir, "inputs.json"))
    sm = jload(os.path.join(run_dir, "summary.json"))
    mf = jload(os.path.join(run_dir, "manifest.json"))
    rj = jload(os.path.join(run_dir, "run.json"))

    if rj:
        rec["run_json_status"] = rj.get("status")
        rec["failure_reason"] = (rj.get("failure_reason") or "")[:120]

    if inp:
        rec["has_inputs"] = True
        rec["symbol"] = inp.get("symbol")
        rec["variant"] = inp.get("variant")
        rec["strategy_version"] = inp.get("strategy_version")
        rec["spec_status"] = inp.get("spec_status")
        ds = inp.get("data_summary") or {}
        tr = ds.get("time_range_1h") or {}
        rec["win_start_utc"] = tr.get("start_utc")
        rec["win_end_utc"] = tr.get("end_utc")
        rec["win_start_ms"] = tr.get("start_ms")
        rec["win_end_ms"] = tr.get("end_ms")
        rec["bar_4h"] = ds.get("bar_4h_count")
        rec["bar_1h"] = ds.get("bar_1h_count")
        rec["bar_15m"] = ds.get("bar_15m_count")
        rec["oi_records"] = ds.get("oi_records_count")
        fp = inp.get("fingerprints") or {}
        code = fp.get("code") or {}
        rec["code_combined_sha256"] = code.get("combined_sha256")
        rec["code_missing"] = ",".join(code.get("missing") or [])
        cfg = fp.get("config") or {}
        rec["config_sha256"] = cfg.get("sha256")
        rules = fp.get("rules") or {}
        rec["rules_sha256"] = rules.get("sha256")
        params = cfg.get("params") or {}
        rec["cfg_initial_equity"] = params.get("initial_equity")
        rec["cfg_taker_fee_bps"] = params.get("taker_fee_bps")
        rec["cfg_slippage_bps"] = params.get("slippage_bps")
        rec["cfg_risk_budget"] = params.get("risk_budget_per_trade")
        rec["cfg_max_active"] = params.get("max_active_positions")
        rec["cfg_max_symbol"] = params.get("max_symbol_positions")

    if sm:
        ts = sm.get("trading_summary") or {}
        rec["variant_summary"] = sm.get("variant")
        rec["symbol_summary"] = sm.get("symbol")
        rec["initial_equity"] = ts.get("initial_equity")
        rec["final_equity"] = ts.get("final_equity")
        rec["net_pnl"] = ts.get("net_pnl")
        rec["trade_count"] = ts.get("trade_count")
        rec["fee_total"] = ts.get("fee_total")
        rec["funding_total"] = ts.get("funding_total")
        rec["formal_pass_allowed"] = sm.get("formal_pass_allowed")
        spec = sm.get("instrument_spec") or {}
        rec["spec_applicability"] = spec.get("applicability")
        rec["spec_blocking"] = spec.get("blocking")
        fn = sm.get("funnel") or {}
        rec["funnel_boxes"] = fn.get("boxes")
        rec["funnel_breakouts"] = fn.get("breakouts")
        rec["funnel_candidates"] = fn.get("candidates")
        rec["funnel_executed"] = fn.get("executed_trades")
        rec["has_variant_abc"] = all(k in sm for k in
                                     ("summary_variant_a", "summary_variant_b", "summary_variant_c"))
        idt = sm.get("identity") or {}
        rec["identity_chain_ok"] = idt.get("chain_ok")
        prec = sm.get("precision") or {}
        rec["run_valid"] = prec.get("run_valid")

    if mf:
        rec["manifest_files"] = mf.get("files_count")

    rec["trades_jsonl"] = jl_count(os.path.join(run_dir, "trades.jsonl"))
    rec["n_files"] = len([f for f in os.listdir(run_dir) if not f.startswith(".")])
    return rec


def main() -> int:
    os.makedirs(OUT, exist_ok=True)
    all_rows: List[Dict[str, Any]] = []
    batch_rows: List[Dict[str, Any]] = []

    for parent, batch in BATCHES:
        bdir = os.path.join(LOG, parent, batch)
        if not os.path.isdir(bdir):
            batch_rows.append({"parent": parent, "batch": batch, "exists": False})
            continue
        runs = sorted(d for d in os.listdir(bdir)
                      if os.path.isdir(os.path.join(bdir, d)))
        rows = []
        for r in runs:
            rec = scan_run(os.path.join(bdir, r))
            rec["parent"] = parent
            rec["batch"] = batch
            rows.append(rec)
        all_rows.extend(rows)

        with_inp = [r for r in rows if r.get("has_inputs")]
        symbols = sorted({r.get("symbol") for r in with_inp if r.get("symbol")})
        wins = sorted({(r.get("win_start_utc"), r.get("win_end_utc")) for r in with_inp
                       if r.get("win_start_utc")})
        code_hashes = sorted({r.get("code_combined_sha256") for r in with_inp
                              if r.get("code_combined_sha256")})
        cfg_hashes = sorted({r.get("config_sha256") for r in with_inp if r.get("config_sha256")})
        rules_hashes = sorted({r.get("rules_sha256") for r in with_inp if r.get("rules_sha256")})
        eqs = sorted({r.get("initial_equity") for r in with_inp if r.get("initial_equity")})
        variants = sorted({r.get("variant") for r in with_inp if r.get("variant")})
        trades = sum(int(r.get("trade_count") or 0) for r in with_inp)
        batches_ok = sum(1 for r in with_inp if r.get("formal_pass_allowed"))
        statuses = {}
        for r in rows:
            k = r.get("run_json_status") or ("ok" if r.get("has_inputs") else "empty")
            statuses[k] = statuses.get(k, 0) + 1

        batch_rows.append({
            "parent": parent, "batch": batch, "exists": True,
            "runs_total": len(rows),
            "runs_with_inputs": len(with_inp),
            "runs_without_inputs": len(rows) - len(with_inp),
            "symbols": len(symbols),
            "windows": len(wins),
            "win_first": wins[0][0] if wins else None,
            "win_last": wins[-1][1] if wins else None,
            "window_list": "; ".join(f"{a}~{b}" for a, b in wins[:4]) +
                           (" ..." if len(wins) > 4 else ""),
            "code_sha_count": len(code_hashes),
            "code_sha_first": code_hashes[0][:16] if code_hashes else None,
            "config_sha_count": len(cfg_hashes),
            "config_sha_first": cfg_hashes[0][:16] if cfg_hashes else None,
            "rules_sha_count": len(rules_hashes),
            "rules_sha": rules_hashes[0] if rules_hashes else None,
            "initial_equity_values": ",".join(str(x) for x in eqs),
            "variants": ",".join(variants),
            "trades_sum": trades,
            "formal_pass_true": batches_ok,
            "status_mix": json.dumps(statuses, ensure_ascii=False),
        })

    def dump(rows: List[Dict[str, Any]], name: str) -> None:
        import csv
        keys: List[str] = []
        for r in rows:
            for k in r:
                if k not in keys:
                    keys.append(k)
        p = os.path.join(OUT, name)
        with open(p, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=keys)
            w.writeheader()
            for r in rows:
                w.writerow(r)
        print(f"wrote {p} ({len(rows)} rows)")

    dump(all_rows, "step1a_runs.csv")
    dump(batch_rows, "step1a_batches.csv")

    print("\n=== 批次汇总 ===")
    for b in batch_rows:
        if not b.get("exists"):
            print(f"{b['parent']}/{b['batch']}: 不存在")
            continue
        print(f"{b['parent']}/{b['batch']}: runs={b['runs_total']} "
              f"有inputs={b['runs_with_inputs']} 标的={b['symbols']} "
              f"窗口数={b['windows']} codeSHA={b['code_sha_count']} "
              f"cfgSHA={b['config_sha_count']} 权益={b['initial_equity_values']} "
              f"成交={b['trades_sum']} pass={b['formal_pass_true']} {b['status_mix']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
