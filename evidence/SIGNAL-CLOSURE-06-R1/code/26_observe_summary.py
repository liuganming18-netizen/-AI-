#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""26_observe_summary.py —— 重建 `out/20_运行观察_汇总.json`（20 号脚本因崩溃没写出来的那份）

为什么要单独补这一步：
  20 号在**第 2 根新增 1 小时线触发的那一瞬间**崩了（事件字典键名写错），
  所以它既没写出 `20_运行观察_汇总.json`，`20_运行观察_逐币事件.json` 里也只落了第 1 根。
  而 22 号（报告数字机械复算）第 8 节是按 `20_运行观察_汇总.json` 的字段取数的
  ⇒ 报告里「两根合计 28 次阶段变化 / 候选 0 / 订单 0 / 账本逐字节未变」这几项
  **一直没有被机械复算**。本脚本把这份汇总从**已有产物**里重算出来，不重跑仿真。

数据来源（全部只读，且都是本单已有产物）：
  · `out/20_运行观察_样本.jsonl`         —— 196 条采样（含两次 `watermark_changed=true`）
  · `out/20_运行观察_逐币事件.json`       —— 第 1 根的逐币明细 + 基线（含账本 sha_join）
  · `out/25_第二根小时线逐币重建.json`     —— 第 2 根的逐币名单（离线重建、两端校验 PASS）
  · `out/20b_生产状态_08点那根之后.json`   —— 第 2 根之后已收敛的生产状态（漏斗终值）
  · `数据/paper/*.sqlite3`、`框架/backend/live/live_signals.sqlite3` —— 现算校验和

纪律：
  · 第 2 根的「逐币名单」来自离线重建，**必须在产物里标注来源**，不能与第 1 根混为一谈；
  · 账本「未变」= 基线 sha_join == 第 1 根事件里记的 sha_join == 本脚本执行时刻现算的 sha_join；
  · 自然候选/自然订单**如实按 0 记**，不得用隔离环境成交顶替。

用法：
    python code/26_observe_summary.py
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import sqlite3
from pathlib import Path

ROOT = Path("/Users/boos/Desktop/威科夫因子共振趋势")
R1 = ROOT / "evidence/SIGNAL-CLOSURE-06-R1"
OUT = R1 / "out"
LIVE_DB = ROOT / "框架/backend/live/live_signals.sqlite3"
LEDGER_DIR = ROOT / "数据/paper"
LEDGER_FILES = ["paper_3a.sqlite3", "paper_3b.sqlite3", "paper_3c.sqlite3", "paper_3d.sqlite3"]
H1 = 3_600_000


def utc(ms) -> str | None:
    if ms is None:
        return None
    return dt.datetime.fromtimestamp(int(ms) / 1000, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def ledger_state() -> dict:
    out = {"files": {}, "sha_join": None}
    shas = []
    for name in LEDGER_FILES:
        p = LEDGER_DIR / name
        if not p.exists():
            out["files"][name] = {"exists": False}
            shas.append("MISSING")
            continue
        s = sha256(p)
        shas.append(s)
        out["files"][name] = {"exists": True, "sha256": s, "bytes": p.stat().st_size}
    out["sha_join"] = hashlib.sha256("|".join(shas).encode()).hexdigest()
    return out


def ro_count(db: Path, sql: str):
    if not db.exists():
        return None
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=3.0)
        try:
            r = con.execute(sql).fetchone()
            return r[0] if isinstance(r, tuple) else r
        finally:
            con.close()
    except Exception as e:  # noqa: BLE001
        return f"ERR:{type(e).__name__}"


def load(name: str):
    p = OUT / name
    return json.load(open(p, encoding="utf-8")) if p.exists() else None


# ── 1. 采样序列：从 196 条采样里找出「水位推进」 ─────────────────────────────
samples = [json.loads(l) for l in open(OUT / "20_运行观察_样本.jsonl", encoding="utf-8") if l.strip()]
assert samples and samples[0].get("kind") == "baseline", "样本首行必须是 baseline"
base_rec = samples[0]
advances = [r for r in samples if r.get("watermark_changed")]
observed_wm = [base_rec["watermark_1h"]] + [r["watermark_1h"] for r in advances]

# ── 2. 逐币事件：第 1 根有落盘，第 2 根只有离线重建 ─────────────────────────
ev_file = load("20_运行观察_逐币事件.json")
baseline = ev_file["baseline"]
captured = ev_file["events"]                      # 实际只有第 1 根
d25 = load("25_第二根小时线逐币重建.json")
d20b = load("20b_生产状态_08点那根之后.json")

# 第 2 根：逐币名单取 25 号（离线重建，两端校验 PASS）；候选/订单取采样终值
last = samples[-1]
pre_funnel = dict(baseline["funnel_since_start"] or {})
for e in captured:
    for k, v in (e["funnel_delta"] or {}).items():
        pre_funnel[k] = (pre_funnel.get(k) or 0) + v
final_funnel = dict((d20b["signal_engine"]["funnel"]["since_start"]) or {})
fdelta2 = {}
for k in sorted(set(pre_funnel) | set(final_funnel)):
    d0 = (final_funnel.get(k) or 0) - (pre_funnel.get(k) or 0)
    if d0:
        fdelta2[k] = d0

events = []
for e in captured:
    events.append({
        "bar": len(events) + 1,
        "wm_prev_utc": e["wm_prev_utc"],
        "wm_new_utc": e["wm_new_utc"],
        "observed_utc": e["observed_utc"],
        "n_phase_changes": e["n_phase_changes"],
        "phase_changes": [[d["symbol"], d["prev"], d["now"]] for d in e["phase_changes"]],
        "state_counter_after": e["state_counter_now"],
        "funnel_delta": e["funnel_delta"],
        "candidates_delta": e["candidates_delta"],
        "orders_routed_delta": e["orders_routed_delta"],
        "lag_1h_hist": e["lag_1h_hist"],
        "ledger_sha_join": e["ledger"]["sha_join"],
        "phase_list_source": "20_运行观察_逐币事件.json（采样脚本当场落盘）",
    })

# 第 2 根（脚本崩在触发瞬间，名单由 25 号离线重建补回）
ev2 = {
    "bar": 2,
    "wm_prev_utc": utc(advances[1]["watermark_1h"] - H1) if len(advances) > 1 else None,
    "wm_new_utc": advances[-1]["watermark_1h_utc"],
    "observed_utc": advances[-1]["ts_utc"],
    "n_phase_changes": d25["n_phase_changes"],
    "phase_changes": [[x["symbol"], x["prev"], x["now"]] for x in d25["phase_changes"]],
    "state_counter_after": last["state_counter"],
    "funnel_delta": fdelta2,
    "candidates_delta": int((last.get("candidates_since_start") or 0)
                            - (captured[-1].get("candidates_since_start") or 0)),
    "orders_routed_delta": int((last.get("orders_routed_total") or 0)
                               - (captured[-1].get("orders_routed_total") or 0)),
    "lag_1h_hist": last["lag_1h_hist"],
    "ledger_sha_join": None,   # 触发瞬间没落盘；账本一致性另按「基线 vs 现算」判
    "phase_list_source": "25_第二根小时线逐币重建.json（离线重建 + 两端校验 PASS）",
    "why_reconstructed": "20 号在第二根触发瞬间 KeyError（取 events[-1]['state_counter']，"
                         "实际键名 state_counter_now）⇒ 计数拿到、逐币名单没拿到",
}
events.append(ev2)

# ── 3. 账本一致性：基线 == 第 1 根事件 == 现算 ─────────────────────────────
led_now = ledger_state()
base_sha = baseline["ledger"]["sha_join"]
ev1_sha = captured[-1]["ledger"]["sha_join"]
ledger_untouched = (base_sha == ev1_sha == led_now["sha_join"])

summary = {
    "generated_utc": utc(dt.datetime.now(dt.timezone.utc).timestamp() * 1000),
    "rebuilt_by": "code/26_observe_summary.py",
    "why": "20 号在第二根新增 1 小时线触发瞬间崩溃，未写出本汇总；此处从已有产物重算，不重跑仿真",
    "n_samples": len(samples) - 1,
    "baseline": {
        "captured_utc": base_rec.get("captured_utc"),
        "pid": base_rec.get("pid"),
        "started_utc": base_rec.get("started_utc"),
        "watermark_1h": base_rec["watermark_1h"],
        "watermark_1h_utc": base_rec["watermark_1h_utc"],
        "state_counter": base_rec["state_counter"],
        "history_candidates_discarded": base_rec.get("history_candidates_discarded"),
        "ledger_sha_join": base_sha,
        "live_counts": baseline.get("live_counts"),
    },
    "observed_watermarks_utc": [utc(x) for x in observed_wm],
    "n_advances": len(advances),
    "events": events,
    "ledger_check": {
        "baseline_sha_join": base_sha,
        "event1_sha_join": ev1_sha,
        "now_sha_join": led_now["sha_join"],
        "checked_utc": utc(dt.datetime.now(dt.timezone.utc).timestamp() * 1000),
        "ledger_untouched": ledger_untouched,
        "files": led_now["files"],
    },
    "live_now": {
        "live_signal": ro_count(LIVE_DB, "SELECT COUNT(*) FROM live_signal"),
        "live_signals": ro_count(LIVE_DB, "SELECT COUNT(*) FROM live_signals"),
    },
    "verdict": {
        "got_two_new_1h_bars": len(advances) >= 2,
        "natural_candidates": sum(e["candidates_delta"] for e in events),
        "natural_orders": sum(e["orders_routed_delta"] for e in events),
        "phase_changes_total": sum(e["n_phase_changes"] for e in events),
        "ledger_untouched": ledger_untouched,
        "real_trading_disabled": True,
        "n_phase_changes_by_bar": [e["n_phase_changes"] for e in events],
        "note": "natural_candidates=0 必须如实报告为 0，不得用隔离成交冒充自然成交；"
                "第 2 根逐币名单来自 25 号离线重建，已在 events[].phase_list_source 标注",
    },
}
json.dump(summary, open(OUT / "20_运行观察_汇总.json", "w", encoding="utf-8"),
          ensure_ascii=False, indent=1)

print(json.dumps(summary["verdict"], ensure_ascii=False, indent=1))
print("水位序列 :", " → ".join(summary["observed_watermarks_utc"]))
print("逐币变化 :", [f"第{e['bar']}根 {e['n_phase_changes']} 币" for e in events])
print("账本三方 :", base_sha[:16], ev1_sha[:16], led_now["sha_join"][:16],
      "→ untouched =", ledger_untouched)
print(f"[out] {OUT/'20_运行观察_汇总.json'}")
