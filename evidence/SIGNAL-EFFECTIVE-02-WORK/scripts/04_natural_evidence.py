# -*- coding: utf-8 -*-
"""SIGNAL-EFFECTIVE-02-WORK｜自然运行观测（只读，不注入、不建单、不交易）。

只做三件事：
  1. 定期采样 Runtime 状态文件：水位推进、漏斗计数、候选数、执行守护计数；
  2. 采样生产账本：挂单/持仓/订单/成交 的行数与指纹（证明没被重启破坏、也没凭空开单）；
  3. 采样 API：当前区条数 + 逐原因排除计数。

纪律：
  · 不写库、不下单、不注入信号；候选为 0 就如实记 0，不称「成交闭环」；
  · 不无限等待：默认最多 20 分钟；跨过 15m/1h 边界仍未推进 ⇒ 如实记「未观测到」。
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
import time
from pathlib import Path

PROJ = Path("/Users/boos/Desktop/威科夫因子共振趋势")
ROOT = PROJ / "evidence" / "SIGNAL-EFFECTIVE-02-WORK"
OUT = ROOT / "out"
STATUS = PROJ / "框架/backend/locks/paper_runtime_3a_status.json"
OBSERVE = PROJ / "框架/backend/locks/paper_runtime_3a_observe.json"
LIVE_DB = PROJ / "框架/backend/live/live_signals.sqlite3"
LEDGER = PROJ / "数据/paper/paper_3a.sqlite3"

MAX_MINUTES = 20
SAMPLE_SEC = 60


def utc(ms=None) -> str:
    t = time.time() if ms is None else float(ms) / 1000.0
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


def read_json(p: Path) -> dict:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        return {"__error__": f"{type(e).__name__}: {e}"}


def sample_status() -> dict:
    d = read_json(STATUS)
    if "__error__" in d:
        return d
    ri = d.get("runtime_identity") or {}
    se = d.get("signal_engine") or {}
    ed = d.get("execution_daemon") or {}
    op = d.get("order_processing") or {}
    return {
        "pid": ri.get("pid"),
        "cycle_count": ri.get("cycle_count"),
        "overall_health": ri.get("overall_health"),
        "started_at": ri.get("started_at"),
        "started_at_utc": utc(ri.get("started_at")) if ri.get("started_at") else None,
        "universe": se.get("universe"),
        "universe_declared": se.get("universe_declared"),
        "watermark_1h": se.get("watermark_1h"),
        "watermark_1h_utc": utc(se.get("watermark_1h")) if se.get("watermark_1h") else None,
        "watermark_4h_utc": utc(se.get("watermark_4h")) if se.get("watermark_4h") else None,
        "last_cycle_utc": utc(se.get("last_cycle_ms")) if se.get("last_cycle_ms") else None,
        "status": se.get("status"),
        "bootstrap": se.get("bootstrap"),
        "funnel_since_start": (se.get("funnel") or {}).get("since_start"),
        "funnel_history": (se.get("funnel") or {}).get("history"),
        "idle_breakdown": se.get("idle_breakdown"),
        "data_quality_blocked": (se.get("data_quality") or {}).get("blocked_n"),
        "skipped_reasons": se.get("skipped_reasons"),
        "scan_errors_total": se.get("scan_errors_total"),
        "signals_generated_total": se.get("signals_generated_total"),
        "execution_daemon": ed,
        "order_processing": {k: op.get(k) for k in list(op)[:12]},
    }


def sample_ledger() -> dict:
    out = {}
    try:
        con = sqlite3.connect(f"file:{LEDGER}?mode=ro", uri=True, timeout=15)
        try:
            for t in ("paper_pending", "paper_position", "paper_order", "paper_fill",
                      "paper_realized", "paper_processed"):
                out[t] = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            out["pending_rows"] = [
                {"pending_id": r[0], "symbol": r[1], "signal_uid": r[2], "status": r[3],
                 "origin": r[4], "event_time": r[5]}
                for r in con.execute(
                    "SELECT pending_id,symbol,signal_uid,status,origin,event_time "
                    "FROM paper_pending ORDER BY pending_id")]
            out["position_rows"] = [
                {"position_id": r[0], "symbol": r[1], "signal_uid": r[2], "status": r[3],
                 "qty": r[4], "stop_price": r[5], "entry_ts": r[6]}
                for r in con.execute(
                    "SELECT position_id,symbol,signal_uid,status,qty,stop_price,entry_ts "
                    "FROM paper_position ORDER BY position_id")]
            out["open_positions"] = con.execute(
                "SELECT COUNT(*) FROM paper_position WHERE status='open' "
                "AND COALESCE(voided,0)=0").fetchone()[0]
        finally:
            con.close()
    except Exception as e:
        out["__error__"] = f"{type(e).__name__}: {e}"
    return out


def sample_live() -> dict:
    try:
        con = sqlite3.connect(f"file:{LIVE_DB}?mode=ro", uri=True, timeout=15)
        try:
            return {
                "total": con.execute("SELECT COUNT(*) FROM live_signal").fetchone()[0],
                "by_origin": [list(r) for r in con.execute(
                    "SELECT origin, executable_state, COUNT(*) FROM live_signal "
                    "GROUP BY 1,2 ORDER BY 3 DESC")],
                "approved0": con.execute(
                    "SELECT COUNT(*) FROM live_signal "
                    "WHERE COALESCE(json_extract(payload_json,'$.approved'),1)=0").fetchone()[0],
                "levels": {r[0]: int(r[1]) for r in
                           con.execute("SELECT key,value_json FROM live_run")},
            }
        finally:
            con.close()
    except Exception as e:
        return {"__error__": f"{type(e).__name__}: {e}"}


def sample_api() -> dict:
    try:
        cp = subprocess.run(
            ["curl", "-s", "-m", "8",
             "http://127.0.0.1:8787/api/v1/signals?scope=current&limit=5"],
            capture_output=True, text=True, timeout=15)
        js = json.loads(cp.stdout or "{}")
    except Exception as e:
        return {"__error__": f"{type(e).__name__}: {e}"}
    q = js.get("quality") or {}
    return {
        "total": js.get("total"), "has_more": js.get("has_more"),
        "n_items": len(js.get("items") or []),
        "missing_reason": js.get("missing_reason"),
        "quality_status": q.get("status"),
        "excluded_n": q.get("excluded_n"),
        "excluded_breakdown": q.get("excluded_breakdown"),
        "n_cards": q.get("n_cards"),
        "effective_floor_ms": q.get("effective_floor_ms"),
        "live_start_ms": q.get("live_start_ms"),
        "paper_epoch_ms": q.get("paper_epoch_ms"),
    }


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    R = {"ticket": "SIGNAL-EFFECTIVE-02-WORK", "step": "4 自然运行观测",
         "started_at_utc": utc(), "samples": [], "writes": 0,
         "discipline": "只读采样；不注入信号、不建单、不真实交易；候选 0 就记 0"}
    first = None
    advanced = False
    t_end = time.time() + MAX_MINUTES * 60
    i = 0
    while time.time() < t_end:
        i += 1
        s = {"i": i, "at_utc": utc(), "status": sample_status()}
        if i == 1:
            first = s["status"]
        # 判定水位推进：与首样本相比 watermark_1h 变大，或 cycle_count > 0 且 funnel 有变化
        st = s["status"]
        if first and st.get("watermark_1h") and first.get("watermark_1h"):
            if st["watermark_1h"] > first["watermark_1h"]:
                advanced = True
        if first and (st.get("cycle_count") or 0) > (first.get("cycle_count") or 0):
            advanced = True
        s["ledger"] = sample_ledger()
        s["live"] = sample_live()
        if i % 3 == 1:
            s["api"] = sample_api()
        R["samples"].append(s)
        w1 = st.get("watermark_1h_utc")
        print(f"[{i:02d}] {s['at_utc']}  cycles={st.get('cycle_count')} "
              f"universe={st.get('universe')}/{st.get('universe_declared')} "
              f"wm1h={w1} funnel={json.dumps(st.get('funnel_since_start') or {}, ensure_ascii=False)}")
        if advanced and (st.get("cycle_count") or 0) >= 1:
            print("    → 已观测到水位/周期推进，再采一次后收工")
            break
        time.sleep(SAMPLE_SEC)

    last = R["samples"][-1] if R["samples"] else {}
    lst = last.get("status") or {}
    R["final"] = {
        "n_samples": len(R["samples"]),
        "observed_advance": advanced,
        "cycle_count": lst.get("cycle_count"),
        "universe": lst.get("universe"),
        "watermark_1h_utc": lst.get("watermark_1h_utc"),
        "watermark_4h_utc": lst.get("watermark_4h_utc"),
        "funnel_since_start": lst.get("funnel_since_start"),
        "funnel_history": lst.get("funnel_history"),
        "candidates_since_start": (lst.get("funnel_since_start") or {}).get("CANDIDATE"),
        "execution_daemon": lst.get("execution_daemon"),
        "ledger": last.get("ledger"),
        "api": last.get("api"),
        "live": last.get("live"),
    }
    p = OUT / "08_自然扫描分因.json"
    p.write_text(json.dumps(R, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n采样 {len(R['samples'])} 次；观测到推进={advanced}")
    print(f"产物：{p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
