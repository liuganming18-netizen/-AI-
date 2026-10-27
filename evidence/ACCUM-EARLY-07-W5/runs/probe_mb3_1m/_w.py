
import json, sys, time
sys.path.insert(0, r"/Users/boos/Desktop/威科夫因子共振趋势")
sys.path.insert(0, r"/Users/boos/Desktop/威科夫因子共振趋势/框架/backend")
job = json.load(sys.stdin)
import accum_early_entry as A
t0 = time.time()
res = A.research_run(config=job["config"], symbols=job["symbols"],
                     start_ms=job["start_ms"], end_ms=job["end_ms"],
                     schemes=("4a","4b","4c"), open_candidates=job["open"],
                     drive_exits=False, max_boxes=job["max_boxes"],
                     walk_days=30, observe_db=job["observe_db"])
out = {"elapsed_s": round(time.time()-t0, 2), "errors": res["errors"][:3],
       "per": [], "cand": {"4a":0,"4b":0,"4c":0}, "blocked": {}, "states": {}}
for r in res["per_symbol"]:
    out["per"].append({"s": r["symbol"], "boxes": r["boxes_formed"],
                       "used": r["boxes_used"], "c": len(r["candidates"]),
                       "acts": r["actions"]})
    for c in r["candidates"]:
        out["cand"][c["scheme"]] += 1
    for e in r["events"]:
        if e.get("blocked_config"):
            out["blocked"][e["reason"]] = out["blocked"].get(e["reason"], 0) + 1
        else:
            k = e.get("terminal_reason") or e.get("reason") or e.get("state")
            out["states"][str(k)[:60]] = out["states"].get(str(k)[:60], 0) + 1
print(json.dumps(out, ensure_ascii=False, default=str))
