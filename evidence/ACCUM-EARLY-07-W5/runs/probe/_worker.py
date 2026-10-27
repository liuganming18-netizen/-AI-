
import json, sys, time
sys.path.insert(0, r"/Users/boos/Desktop/威科夫因子共振趋势")
sys.path.insert(0, r"/Users/boos/Desktop/威科夫因子共振趋势/框架/backend")
job = json.load(sys.stdin)
import accum_early_entry as A
cfg = A.load_config(job["config"])
brief = {k: cfg.get(k) for k in ("present","ready","open_allowed","open_scope","kind",
                                "approved","blocked_by","config_sha256","missing","unsupported")}
brief["missing_n"] = len(cfg.get("missing") or [])
brief["fixed_rules_ok"] = (cfg.get("fixed_rules") or {}).get("ok")
brief["economic"] = (cfg.get("economic_caliber") or {}).get("declared_mismatch")
if not cfg.get("ready"):
    print(json.dumps({"config": brief, "fatal": "config_not_ready"}, ensure_ascii=False))
    raise SystemExit(0)
t0 = time.time()
res = A.research_run(config=job["config"], symbols=job["symbols"],
                     start_ms=job["start_ms"], end_ms=job["end_ms"],
                     schemes=("4a","4b","4c"), open_candidates=True,
                     drive_exits=True, max_boxes=job["max_boxes"],
                     walk_days=job["walk_days"],
                     observe_db=job["observe_db"])
el = time.time() - t0
out = {"config": brief, "elapsed_s": round(el, 2),
       "errors": res["errors"][:5], "n_errors": len(res["errors"]),
       "identity_engine": res["identity"]["engine"],
       "per_symbol": [], "exits_n": len(res["exits"]),
       "exit_reasons": {},
       "ledgers": {s: {k: len(v) if isinstance(v, list) else v
                       for k, v in led.items()} for s, led in res["ledgers"].items()},
       "candidates": {s: 0 for s in ("4a","4b","4c")},
       "open_results": {s: {"opened": 0, "rejected": 0, "reasons": {}} for s in ("4a","4b","4c")},
       "symbols_zero_cand": 0}
for r in res["per_symbol"]:
    out["per_symbol"].append({"symbol": r["symbol"], "boxes": r["boxes_formed"],
                              "cands": len(r["candidates"]), "acts": r["actions"]})
    if not r["candidates"]:
        out["symbols_zero_cand"] += 1
    for c in r["candidates"]:
        out["candidates"][c["scheme"]] += 1
        orr = c.get("open_result") or {}
        if orr.get("ok"):
            out["open_results"][c["scheme"]]["opened"] += 1
        else:
            out["open_results"][c["scheme"]]["rejected"] += 1
            why = orr.get("reason") or "no_result"
            out["open_results"][c["scheme"]]["reasons"][why] = \
                out["open_results"][c["scheme"]]["reasons"].get(why, 0) + 1
for e in res["exits"]:
    k = e.get("exit_class") or e.get("reason") or "unknown"
    out["exit_reasons"][k] = out["exit_reasons"].get(k, 0) + 1
print(json.dumps(out, ensure_ascii=False, default=str))
