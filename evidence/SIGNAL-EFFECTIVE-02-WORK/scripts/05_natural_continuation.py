# -*- coding: utf-8 -*-
"""SIGNAL-EFFECTIVE-02-WORK｜步骤4补：重载后持续观测 + 跨 1h 边界水位推进（只读）。

为什么单独一支：
  步骤 4 的 20 分钟窗口在 16:22Z 收工，当时 `watermark_1h` 停在 15:00Z ——
  16:00Z 那根 1h bar 要到 17:00Z 才收盘，窗口内根本等不到推进。
  本脚本只补这一件事：在 17:00Z 之后采一次，看水位有没有真的走。

纪律（与步骤 4 相同）：
  · 只读采样；不写库、不注入信号、不建单、不真实交易；
  · 不重启任何服务、不碰采集器；
  · 结果追加进同一份 JSON，前面的样本不覆盖；
  · 拿不到就如实记 `observed=False`，不推断、不补写。

用法：
  python 05_natural_continuation.py --now                 # 立即采一次
  python 05_natural_continuation.py --wait-until 17:03    # 等到该 UTC 时刻再采（今天）
  python 05_natural_continuation.py --wait-sec 1500       # 或等固定秒数
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

PROJ = Path("/Users/boos/Desktop/威科夫因子共振趋势")
ROOT = PROJ / "evidence" / "SIGNAL-EFFECTIVE-02-WORK"
OUT = ROOT / "out"
RESULT = OUT / "08c_跨1h边界水位观测.json"
STATUS = PROJ / "框架/backend/locks/paper_runtime_3a_status.json"
LIVE_DB = PROJ / "框架/backend/live/live_signals.sqlite3"
LEDGER = PROJ / "数据/paper/paper_3a.sqlite3"


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
    return {
        "pid": ri.get("pid"),
        "cycle_count": ri.get("cycle_count"),
        "overall_health": ri.get("overall_health"),
        "started_at_utc": utc(ri.get("started_at")) if ri.get("started_at") else None,
        "universe": se.get("universe"),
        "universe_declared": se.get("universe_declared"),
        "watermark_1h": se.get("watermark_1h"),
        "watermark_1h_utc": utc(se.get("watermark_1h")) if se.get("watermark_1h") else None,
        "watermark_4h_utc": utc(se.get("watermark_4h")) if se.get("watermark_4h") else None,
        "last_cycle_utc": utc(se.get("last_cycle_ms")) if se.get("last_cycle_ms") else None,
        "funnel_since_start": (se.get("funnel") or {}).get("since_start"),
        "funnel_history": (se.get("funnel") or {}).get("history"),
        "scan_errors_total": se.get("scan_errors_total"),
        "signals_generated_total": se.get("signals_generated_total"),
        "execution_daemon": {k: ed.get(k) for k in
                             ("status", "open_positions", "pending_matched_total",
                              "last_cycle_utc", "last_error") if k in ed},
    }


def sample_ledger() -> dict:
    out = {}
    try:
        con = sqlite3.connect(f"file:{LEDGER}?mode=ro", uri=True, timeout=15)
        try:
            for t in ("paper_pending", "paper_position", "paper_order",
                      "paper_fill", "paper_processed"):
                out[t] = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
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
        "total": js.get("total"),
        "n_items": len(js.get("items") or []),
        "quality_status": q.get("status"),
        "excluded_n": q.get("excluded_n"),
        "by_reason": (q.get("excluded_breakdown") or {}).get("by_reason"),
        "effective_floor_ms": q.get("effective_floor_ms"),
        "live_start_ms": q.get("live_start_ms"),
        "paper_epoch_ms": q.get("paper_epoch_ms"),
    }


def load_result() -> dict:
    if RESULT.exists():
        try:
            d = json.loads(RESULT.read_text(encoding="utf-8"))
            d.setdefault("samples", [])
            return d
        except Exception:
            pass
    return {
        "ticket": "SIGNAL-EFFECTIVE-02-WORK",
        "step": "4补 重载后持续观测与跨 1h 边界水位推进",
        "discipline": "只读采样；不写库、不注入信号、不建单、不重启服务；拿不到就记未观测",
        "baseline_watermark_1h_utc": "2026-10-27T15:00:00Z",
        "samples": [],
    }


def take(note: str) -> dict:
    R = load_result()
    s = {
        "i": len(R["samples"]) + 1,
        "at_utc": utc(),
        "note": note,
        "status": sample_status(),
        "ledger": sample_ledger(),
        "live": sample_live(),
        "api": sample_api(),
    }
    R["samples"].append(s)
    # 判定：相对 15:00Z 基线是否推进
    wm = (s["status"] or {}).get("watermark_1h")
    base = 1793113200000  # 2026-10-27T15:00:00Z
    R["observed_advance"] = bool(wm and wm > base)
    R["latest"] = {
        "at_utc": s["at_utc"],
        "watermark_1h_utc": (s["status"] or {}).get("watermark_1h_utc"),
        "watermark_4h_utc": (s["status"] or {}).get("watermark_4h_utc"),
        "cycle_count": (s["status"] or {}).get("cycle_count"),
        "candidates_since_start": ((s["status"] or {}).get("funnel_since_start") or {}).get("CANDIDATE"),
        "oi_unavailable_since_start": ((s["status"] or {}).get("funnel_since_start") or {}).get("OI_UNAVAILABLE"),
        "overall_health": (s["status"] or {}).get("overall_health"),
        "pid": (s["status"] or {}).get("pid"),
        "ledger": s["ledger"],
        "levels": (s["live"] or {}).get("levels"),
    }
    OUT.mkdir(parents=True, exist_ok=True)
    RESULT.write_text(json.dumps(R, ensure_ascii=False, indent=2), encoding="utf-8")
    st = s["status"] or {}
    print(f"[{s['i']:02d}] {s['at_utc']} pid={st.get('pid')} cycles={st.get('cycle_count')} "
          f"health={st.get('overall_health')} wm1h={st.get('watermark_1h_utc')} "
          f"wm4h={st.get('watermark_4h_utc')} "
          f"CANDIDATE={((st.get('funnel_since_start') or {}).get('CANDIDATE'))} "
          f"OI_UNAVAILABLE={((st.get('funnel_since_start') or {}).get('OI_UNAVAILABLE'))}")
    print(f"     ledger={json.dumps(s['ledger'], ensure_ascii=False)}")
    print(f"     推进={R['observed_advance']}  产物={RESULT}")
    return s


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--now", action="store_true", help="立即采样一次")
    ap.add_argument("--wait-sec", type=int, default=0, help="先等 N 秒再采样")
    ap.add_argument("--wait-until", type=str, default=None,
                    help="等到今天该 UTC 时刻（HH:MM[:SS]）再采样")
    ap.add_argument("--note", type=str, default="")
    a = ap.parse_args()

    if a.wait_until:
        hh, mm, *ss = a.wait_until.split(":")
        target = time.time()
        now = time.gmtime(target)
        target = (target - (now.tm_hour * 3600 + now.tm_min * 60 + now.tm_sec)
                  + int(hh) * 3600 + int(mm) * 60 + (int(ss[0]) if ss else 0))
        wait = target - time.time()
        if wait > 0:
            print(f"等到 {a.wait_until}Z（约 {wait/60:.1f} 分钟后）再采样……", flush=True)
            time.sleep(wait)
    elif a.wait_sec > 0:
        print(f"先等 {a.wait_sec} 秒……", flush=True)
        time.sleep(a.wait_sec)

    take(a.note or ("立即采样" if a.now else f"等待后采样({a.wait_until or a.wait_sec})"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
