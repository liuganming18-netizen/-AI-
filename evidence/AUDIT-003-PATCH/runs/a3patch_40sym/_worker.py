
# -*- coding: utf-8 -*-
"""跑分 worker：只做一次 research_run，把结果摘要写回 json。"""
import json, sys, time
sys.path.insert(0, r"/Users/boos/Desktop/威科夫因子共振趋势")
sys.path.insert(0, r"/Users/boos/Desktop/威科夫因子共振趋势/框架/backend")
import accum_early_entry as A

job = json.load(open(sys.argv[1], encoding="utf-8"))

class LimitedFeed(A.DataFeed):
    """同一时刻只保留当前标的的 bar 缓存。

    为什么需要：DataFeed 永不淘汰缓存，526 个标的 × (4h+1h+15m) ≈ 800 万根 bar，
    全留在内存里会直接把进程打爆。只清缓存不改任何读数逻辑——每个标的的窗口
    仍是同一份 MergedReader 读出来的同一批 bar。
    """
    def load(self, tf, symbol, start_ms, end_ms):
        if not job.get("no_cache_limit"):
            for k in [k for k in self._cache if k[1] != symbol]:
                self._cache.pop(k, None); self._range.pop(k, None); self._stats.pop(k, None)
        return super().load(tf, symbol, start_ms, end_ms)

cfg = A.load_config(job["config"])
brief = {k: cfg.get(k) for k in ("present", "ready", "open_allowed", "open_scope",
                                 "kind", "approved", "blocked_by", "config_sha256",
                                 "approval_ref")}
brief["missing_n"] = len(cfg.get("missing") or [])
brief["missing_head"] = (cfg.get("missing") or [])[:8]
brief["unsupported"] = cfg.get("unsupported") or []
brief["fixed_rules_ok"] = (cfg.get("fixed_rules") or {}).get("ok")
brief["economic_mismatch"] = (cfg.get("economic_caliber") or {}).get("declared_mismatch")
brief["engine"] = (cfg.get("fixed_rules") or {}).get("engine")
brief["economic_engine"] = (cfg.get("economic_caliber") or {}).get("engine")

if not cfg.get("ready"):
    json.dump({"fatal": "config_not_ready", "config": brief},
              open(job["out"], "w", encoding="utf-8"), ensure_ascii=False)
    raise SystemExit(0)
if not cfg.get("open_allowed") and job["open"]:
    json.dump({"fatal": "open_not_allowed", "config": brief},
              open(job["out"], "w", encoding="utf-8"), ensure_ascii=False)
    raise SystemExit(0)

feed = LimitedFeed()
store = A.open_store(job["observe_db"])
t0 = time.time()
try:
    res = A.research_run(config=job["config"], symbols=job["symbols"],
                         start_ms=job["start_ms"], end_ms=job["end_ms"],
                         schemes=("4a", "4b", "4c"),
                         open_candidates=job["open"], drive_exits=job["exits"],
                         max_boxes=job["max_boxes"], walk_days=job["walk_days"],
                         observe_db=job["observe_db"], feed=feed, store=store)
finally:
    try:
        store.close()
    finally:
        feed.close()

el = time.time() - t0
# 只留摘要，避免把 526 个标的的全量事件数组塞进 json
per = []
for r in res["per_symbol"]:
    per.append({
        "symbol": r["symbol"],
        "boxes_formed": r["boxes_formed"], "boxes_used": r["boxes_used"],
        "n_candidates": len(r["candidates"]),
        "cand_by_scheme": {s: sum(1 for c in r["candidates"] if c["scheme"] == s)
                           for s in ("4a", "4b", "4c")},
        "actions": r["actions"],
        "data": {"4h": r["bars"]["4h"], "1h": r["bars"]["1h"],
                 "4h_gaps": r["bars"]["4h_gaps"], "1h_gaps": r["bars"]["1h_gaps"]},
        "candidates": [{"scheme": c["scheme"], "signal_uid": c["signal_uid"],
                        "confirm_close_ms": c["confirm_close_ms"],
                        "entry_bar_open_ms": c.get("entry_bar_open_ms"),
                        "resistance": c.get("resistance"),
                        "entry_ref_price": c.get("entry_ref_price"),
                        "open_result": {
                            "ok": (c.get("open_result") or {}).get("ok"),
                            "reason": (c.get("open_result") or {}).get("reason"),
                            "called_paper_engine":
                                (c.get("open_result") or {}).get("called_paper_engine"),
                            "paper_reason": (((c.get("open_result") or {})
                                              .get("paper_result")) or {}).get("reason"),
                            "paper_detail": {k: v for k, v in
                                             ((((c.get("open_result") or {})
                                                .get("paper_result")) or {})
                                              .get("detail") or {}).items()
                                             if k in ("max_positions", "cap", "need",
                                                      "have", "limit", "notional",
                                                      "qty", "reason")}}}
                       for c in r["candidates"]],
    })
out = {
    "config": brief,
    "elapsed_s": round(el, 2),
    "window": res["window"],
    "n_symbols": len(res["symbols"]),
    "n_errors": len(res["errors"]), "errors": res["errors"][:20],
    # [AUDIT-003-PATCH] 退出推进异常 / 平仓被拒 ⇒ research_run 把状态打成 FAILED，
    # 这里必须原样透传，否则跑分器又会算出一份看起来正常的成绩单（BLK-01）。
    "run_status": res.get("run_status", "OK"),
    "invalid_reasons": res.get("invalid_reasons") or [],
    "run_problems": (res.get("run_problems") or [])[:20],
    "run_invalid_note": res.get("run_invalid_note"),
    "flags": res["flags"],
    "identity": {k: res["identity"][k] for k in
                 ("entry_version", "config_schema", "rule_version", "exit_rules",
                  "generated_utc", "isolation", "schemes")},
    "per_symbol": per,
    "n_exits": len(res["exits"]),
    "exits": [{k: e.get(k) for k in ("scheme", "position_id", "symbol", "exit_class",
                                     "net_pnl", "exit_ts", "gap", "trail_updates")}
              for e in res["exits"]],
    "ledgers": {s: {k: (len(v) if isinstance(v, list) else v)
                    for k, v in led.items()} for s, led in res["ledgers"].items()},
}
json.dump(out, open(job["out"], "w", encoding="utf-8"), ensure_ascii=False, default=str)
print(json.dumps({"written": job["out"], "elapsed_s": out["elapsed_s"]}, ensure_ascii=False))
