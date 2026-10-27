#!/usr/bin/env python3
"""60_prod_reconcile.py —— 生产侧三方对账 + 历史候选可追溯性复算（SIGNAL-UI-LINK-07）

做三件事，全部**只读**：
  1. 直接读生产库（实时库 + 四臂账本），拿到「库里到底有什么」的原始计数；
  2. 直接读生产接口（scope=current / scope=all / paper 守护状态），拿到「接口说有什么」；
  3. 读浏览器探针 40 的产物，拿到「页面显示什么」。
然后**逐条复算**，不用任何一方自报的数字当结论：
  · 51 条历史候选：ID 唯一性、时间是否早于启用时点、拒单原因是否都在终态集合内、
    条数是否等于漏斗里的 CANDIDATE 数；
  · 526 个标的：状态分布之和、空转明细之和是否自洽；
  · 三方是否一致（页面 0 张卡 / 接口 0 条 / 库里 0 行）。

输出：out/10_prod_reconcile.json
"""
import json
import sqlite3
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
EVID = Path(__file__).resolve().parents[1]
OUT = EVID / "out"
BE = ROOT / "框架" / "backend"
LIVE_DB = BE / "live" / "live_signals.sqlite3"
STATUS = BE / "locks" / "paper_runtime_3a_status.json"
LEDGER_DIR = ROOT / "数据" / "paper"
API = "http://127.0.0.1:8787"

PAPER_EPOCH_MS = 1_791_939_600_000          # 2026-10-14T01:00:00Z（冻结常量）
TERMINAL = {"missed_fill_moment_no_backfill", "before_paper_epoch_no_backfill"}

res = {"probe": "60_prod_reconcile",
       "generated_real_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
       "steps": [], "ok": False}


def step(name, ok, detail):
    res["steps"].append({"name": name, "ok": bool(ok), "detail": detail})
    print(("[OK] " if ok else "[FAIL] ") + name + (" — " + str(detail) if detail else ""))


def iso(ms):
    if ms is None:
        return None
    return datetime.fromtimestamp(int(ms) / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def api_get(path):
    import urllib.request
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with op.open(API + path, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def ro(path):
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


# ── 1. 库（只读）─────────────────────────────────────────────────────────────
con = ro(LIVE_DB)
db = {}
try:
    db["live_signal_rows"] = con.execute("SELECT COUNT(*) FROM live_signal").fetchone()[0]
    db["live_signal_by_origin"] = dict(con.execute(
        "SELECT origin, COUNT(*) FROM live_signal GROUP BY origin").fetchall())
    db["live_run"] = {}
    for r in con.execute("SELECT key, value_json FROM live_run"):
        try:
            db["live_run"][r["key"]] = json.loads(r["value_json"])
        except Exception:
            db["live_run"][r["key"]] = r["value_json"]
finally:
    con.close()
res["db_live"] = db

ledgers = {}
for arm in ("3a", "3b", "3c", "3d"):
    p = LEDGER_DIR / f"paper_{arm}.sqlite3"
    if not p.exists():
        ledgers[arm] = {"exists": False}
        continue
    c = ro(p)
    try:
        tabs = [t[0] for t in c.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        ledgers[arm] = {"exists": True, "tables": tabs}
        for t in ("paper_position", "paper_order", "paper_fill"):
            if t in tabs:
                ledgers[arm][t] = c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    finally:
        c.close()
res["db_ledgers"] = ledgers

# ── 2. 接口 ─────────────────────────────────────────────────────────────────
cur = api_get("/api/v1/signals?scope=current&limit=500")
all_ = api_get("/api/v1/signals?scope=all&limit=500")
st = api_get("/api/v1/paper/execution_daemon/status?arm=3a")
se = (st.get("item") or {}).get("signal_engine") or {}
bs = se.get("bootstrap") or {}
funnel = se.get("funnel") or {}
res["api"] = {
    "current_total": cur.get("total"),
    "current_items": len(cur.get("items") or []),
    "current_as_of": cur.get("as_of"),
    "current_excluded_breakdown": (cur.get("quality") or {}).get("excluded_breakdown"),
    "all_total": all_.get("total"),
    "all_items": len(all_.get("items") or []),
    "live_start_ms": (cur.get("quality") or {}).get("live_start_ms"),
    "paper_epoch_ms": (cur.get("quality") or {}).get("paper_epoch_ms"),
    "effective_floor_ms": (cur.get("quality") or {}).get("effective_floor_ms"),
    "universe": se.get("universe"),
    "state_counter": se.get("state_counter"),
    "funnel": funnel,
    "idle_breakdown_counts": (se.get("idle_breakdown") or {}).get("counts"),
    "idle_total": (se.get("idle_breakdown") or {}).get("idle_total"),
    "bootstrap_hist_discarded": bs.get("history_candidates_discarded"),
    "bootstrap_detail_n": bs.get("discarded_detail_n"),
    "bootstrap_detail_consistent": bs.get("discarded_detail_consistent"),
    "bootstrap_recovered": bs.get("recovered_candidates"),
}

step("库里没有实时信号行", db["live_signal_rows"] == 0, f"{db['live_signal_rows']} 行")
step("接口 scope=current 为 0", cur.get("total") == 0 and len(cur.get("items") or []) == 0,
     f"total={cur.get('total')} items={len(cur.get('items') or [])}")
step("接口 scope=all 为 0", all_.get("total") == 0 and len(all_.get("items") or []) == 0,
     f"total={all_.get('total')} items={len(all_.get('items') or [])}")
step("空表 as_of 不再伪造时刻", cur.get("as_of") is None, f"as_of={cur.get('as_of')!r}")

# ── 3. 历史候选可追溯性（逐条复算，不采信自报汇总）────────────────────────────
detail = bs.get("discarded_detail") or []
ids = [d.get("candidate_id") for d in detail]
snaps = [d.get("snapshot_utc") for d in detail]
reasons = [set(d.get("discard_reasons") or []) for d in detail]
bad_reason = [r for r in reasons if not r or not r.issubset(TERMINAL)]
after_epoch = [s for s in snaps if s and s >= iso(PAPER_EPOCH_MS)]
hist_cand = (funnel.get("history") or {}).get("CANDIDATE")

res["discarded_recompute"] = {
    "n_detail": len(detail),
    "n_unique_ids": len(set(ids)),
    "n_after_paper_epoch": len(after_epoch),
    "n_bad_reason": len(bad_reason),
    "latest_snapshot_utc": max(snaps) if snaps else None,
    "earliest_snapshot_utc": min(snaps) if snaps else None,
    "funnel_history_CANDIDATE": hist_cand,
    "self_reported_history_candidates_discarded": bs.get("history_candidates_discarded"),
    "self_reported_detail_n": bs.get("discarded_detail_n"),
}

step("明细条数 == 自报条数", len(detail) == bs.get("discarded_detail_n") == bs.get(
    "history_candidates_discarded"), f"{len(detail)} / {bs.get('discarded_detail_n')} / "
    f"{bs.get('history_candidates_discarded')}")
step("明细条数 == 漏斗历史候选数", len(detail) == hist_cand, f"{len(detail)} vs {hist_cand}")
step("候选 ID 唯一", len(set(ids)) == len(ids), f"{len(set(ids))}/{len(ids)}")
step("全部早于启用时点", len(after_epoch) == 0,
     f"晚于启用时点的有 {len(after_epoch)} 条（期望 0）")
step("拒单原因均在终态集合内", len(bad_reason) == 0, f"异常 {len(bad_reason)} 条")

# 逐币状态自洽
pss = se.get("per_symbol_state") or {}
sc = se.get("state_counter") or {}
dist = Counter(pss.values()) if isinstance(pss, dict) else Counter()
res["per_symbol"] = {
    "n_per_symbol_state": len(pss),
    "state_dist_recomputed": dict(dist),
    "state_counter_reported": sc,
    "sum_state_counter": sum(v for v in sc.values() if isinstance(v, int)),
}
step("逐币状态数与 universe 一致", len(pss) == se.get("universe") == 526,
     f"{len(pss)} / {se.get('universe')}")
step("状态分布复算 == 自报", dict(dist) == {k: v for k, v in sc.items() if v},
     f"复算 {dict(dist)} / 自报 {sc}")
step("空转明细合计 == 空转数",
     (se.get("idle_breakdown") or {}).get("idle_total") == sc.get("IDLE"),
     f"idle_total={(se.get('idle_breakdown') or {}).get('idle_total')} / IDLE={sc.get('IDLE')}")
step("本进程漏斗全 0（与库里 0 行一致）",
     all(v == 0 for v in (funnel.get("since_start") or {}).values()),
     json.dumps(funnel.get("since_start"), ensure_ascii=False))

# ── 4. 页面（读 40 号探针产物）───────────────────────────────────────────────
page = json.loads((OUT / "08_browser_page.json").read_text(encoding="utf-8"))
cards = [c for c in (page.get("page_cards") or [])]
res["page"] = {
    "ok": page.get("ok"),
    "diagnostic_rows": [r["label"] for r in (page.get("diagnostic_rows") or [])],
    "key_values": page.get("key_values"),
    "discarded_rows_on_page": len(page.get("discarded_ids_on_page") or []),
    "cards": len(cards),
}
step("页面 0 张卡", len(cards) == 0, f"{len(cards)} 张")
step("页面历史候选明细条数 == 库/接口 51",
     len(page.get("discarded_ids_on_page") or []) == len(detail),
     f"页面 {len(page.get('discarded_ids_on_page') or [])} / 接口 {len(detail)}")

# ── 5. 三方一致 ─────────────────────────────────────────────────────────────
three = (db["live_signal_rows"] == 0 and cur.get("total") == 0 and len(cards) == 0)
res["three_way"] = {"db_rows": db["live_signal_rows"], "api_total": cur.get("total"),
                    "page_cards": len(cards), "consistent": three}
step("三方一致（库 0 行 / 接口 0 条 / 页面 0 张）", three,
     f"库 {db['live_signal_rows']} / 接口 {cur.get('total')} / 页面 {len(cards)}")

# 距最近候选多久
if snaps:
    latest_ms = int(datetime.strptime(max(snaps), "%Y-%m-%dT%H:%M:%SZ")
                    .replace(tzinfo=timezone.utc).timestamp() * 1000)
    res["gap_since_last_candidate_days"] = round((time.time() * 1000 - latest_ms) / 86400000, 2)
    step("如实记录：距最近候选已过多少天", True,
         f"{res['gap_since_last_candidate_days']} 天（最近 {max(snaps)}）")

res["ok"] = all(s["ok"] for s in res["steps"])
OUT.mkdir(parents=True, exist_ok=True)
(OUT / "10_prod_reconcile.json").write_text(
    json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
print("\n== 结果：" + ("全部通过" if res["ok"] else "存在失败项") + " ==")
print("写出：evidence/SIGNAL-UI-LINK-07/out/10_prod_reconcile.json")
