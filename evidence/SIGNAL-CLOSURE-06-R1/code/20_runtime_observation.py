#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""20_runtime_observation.py —— 生产 unified_runtime 跨「新增 1h 线」的逐币运行观察

工单第 3 条要求：
  · 跨至少两根新增小时线
  · 逐币记录水位、阶段变化、候选及执行结果
  · 自然候选为 0 时如实报告，不能用隔离成交冒充自然成交
  · 真实交易保持禁用

做法（只读，不写任何生产库）：
  1) 每 `--interval` 秒读一次 `框架/backend/locks/paper_runtime_3a_status.json`；
  2) 一旦 `signal_engine.watermark_1h` 变化（= 新的一根 1h 被状态机处理完），
     就落一条「事件」：上一根 / 新根、逐币阶段变化明细、状态分布、漏斗增量、
     订单与持仓、账本与 live 库校验和；
  3) 收到 `--target-advances` 次水位推进（默认 2）后退出；或超 `--max-sec` 退出。

输出：
  out/20_运行观察_样本.jsonl      每轮采样（紧凑）
  out/20_运行观察_逐币事件.json    每根新增 1h 的逐币明细
  out/20_运行观察_汇总.json        判定与计数

注意：
  · 不 import 任何后端模块（避免 LIVE_DB / 账本被进程内解析改写）；
  · 只以只读方式打开 sqlite（`mode=ro`）；
  · 进程身份（pid / started_at）只在当次运行有效，跨时段只认文件校验和。
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path("/Users/boos/Desktop/威科夫因子共振趋势")
STATUS = ROOT / "框架/backend/locks/paper_runtime_3a_status.json"
LIVE_DB = ROOT / "框架/backend/live/live_signals.sqlite3"
LEDGER_DIR = ROOT / "数据/paper"
LEDGER_FILES = ["paper_3a.sqlite3", "paper_3b.sqlite3", "paper_3c.sqlite3", "paper_3d.sqlite3"]
H1 = 3_600_000


def utc(ms) -> str:
    if ms is None:
        return "None"
    return dt.datetime.fromtimestamp(int(ms) / 1000, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_status(tries: int = 6):
    last = None
    for _ in range(tries):
        try:
            with open(STATUS, "rb") as f:
                raw = f.read()
            return json.loads(raw.decode("utf-8", "replace")), hashlib.sha256(raw).hexdigest(), len(raw)
        except Exception as e:  # 可能撞上写一半
            last = e
            time.sleep(0.4)
    raise RuntimeError(f"status 读取失败: {last}")


def ro_query(db: Path, sql: str):
    if not db.exists():
        return None
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=3.0)
        try:
            return con.execute(sql).fetchone()
        finally:
            con.close()
    except Exception as e:
        return f"ERR:{type(e).__name__}:{e}"


def live_counts():
    """live 库里的信号/订单/持仓行数（只读）。"""
    out = {}
    for tbl in ("live_signals", "live_signal"):
        r = ro_query(LIVE_DB, f"SELECT COUNT(*) FROM {tbl}")
        out[tbl] = r[0] if isinstance(r, tuple) else r
    return out


def ledger_state():
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
    # 持仓与订单行数（只读）
    p3a = LEDGER_DIR / "paper_3a.sqlite3"
    for tbl in ("paper_position", "paper_order", "paper_account"):
        r = ro_query(p3a, f"SELECT COUNT(*) FROM {tbl}")
        out[tbl] = r[0] if isinstance(r, tuple) else r
    r = ro_query(p3a, "SELECT status, COUNT(*) FROM paper_position GROUP BY status")
    out["paper_position_by_status"] = r if r is None else str(r)
    return out


def compact(d: dict) -> dict:
    se = d.get("signal_engine") or {}
    ri = d.get("runtime_identity") or {}
    op = d.get("order_processing") or {}
    ed = d.get("execution_daemon") or {}
    md = d.get("market_data") or {}
    wc = se.get("watermark_check") or {}
    fu = se.get("funnel") or {}
    ss = fu.get("since_start") or {}
    ac = se.get("atr_caliber") or {}
    ib = se.get("idle_breakdown") or {}
    return {
        "ts_ms": int(time.time() * 1000),
        "ts_utc": utc(time.time() * 1000),
        "pid": ri.get("pid"),
        "started_at": ri.get("started_at"),
        "started_utc": utc(ri.get("started_at")),
        "health": ri.get("overall_health"),
        "cycle_count": ri.get("cycle_count"),
        "watermark_1h": se.get("watermark_1h"),
        "watermark_1h_utc": utc(se.get("watermark_1h")),
        "watermark_4h": se.get("watermark_4h"),
        "watermark_4h_utc": utc(se.get("watermark_4h")),
        "db_1h_max_utc": utc(wc.get("db_1h_max")),
        "db_4h_max_utc": utc(wc.get("db_4h_max_open")),
        "aligned_1h": wc.get("aligned_1h"),
        "lag_1h_bars": wc.get("lag_1h_bars"),
        "lag_1h_hist": (wc.get("per_symbol") or {}).get("lag_1h_histogram"),
        "n_lag_gt1": (wc.get("per_symbol") or {}).get("n_lag_1h_gt1"),
        "state_counter": se.get("state_counter"),
        "idle_counts": ib.get("counts"),
        "funnel_since_start": ss,
        "candidates_since_start": fu.get("candidates_since_start"),
        "last_candidate_utc": fu.get("last_candidate_utc"),
        "history_candidates_discarded": fu.get("history_candidates_discarded"),
        "signals_generated_total": se.get("signals_generated_total"),
        "atr_n_warmed": ac.get("n_warmed"),
        "atr_n_not_warmed": ac.get("n_not_warmed"),
        "atr_bars_seen_min": ac.get("atr_bars_seen_min"),
        "orders_routed_total": op.get("orders_routed_total"),
        "orders_rejected_total": op.get("orders_rejected_total"),
        "orders_waiting_gate": op.get("orders_waiting_gate"),
        "pending_in_db": op.get("current_pending_in_db"),
        "new_entry_allowed": (op.get("new_entry_gate") or {}).get("allow_new_entries"),
        "new_entry_gate_reason": (op.get("new_entry_gate") or {}).get("gate_reason"),
        "open_positions": ed.get("open_positions"),
        "exits_triggered_total": ed.get("exits_triggered_total"),
        "latest_15m_close_utc": utc(md.get("latest_15m_close_time")),
        "latest_1h_close_utc": utc(md.get("latest_1h_close_time")),
        "latest_4h_close_utc": utc(md.get("latest_4h_close_time")),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default=str(ROOT / "evidence/SIGNAL-CLOSURE-06-R1/out"))
    ap.add_argument("--interval", type=float, default=30.0)
    ap.add_argument("--target-advances", type=int, default=2)
    ap.add_argument("--max-sec", type=float, default=9000.0)
    a = ap.parse_args()

    out_dir = Path(a.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    f_samples = out_dir / "20_运行观察_样本.jsonl"
    f_events = out_dir / "20_运行观察_逐币事件.json"
    f_summary = out_dir / "20_运行观察_汇总.json"

    t0 = time.time()
    base_status, base_sha, base_len = read_status()
    base_c = compact(base_status)
    base_ledger = ledger_state()
    prev_wm = base_c["watermark_1h"]
    prev_pss = dict((base_status.get("signal_engine") or {}).get("per_symbol_state") or {})
    prev_funnel = dict(base_c["funnel_since_start"] or {})
    prev_cand = base_c["candidates_since_start"] or 0
    prev_orders = base_c["orders_routed_total"] or 0

    print(f"[start] now={utc(time.time()*1000)} pid={base_c['pid']} started={base_c['started_utc']}")
    print(f"[start] watermark_1h={base_c['watermark_1h_utc']} ({prev_wm})  db_1h_max={base_c['db_1h_max_utc']}")
    print(f"[start] state_counter={base_c['state_counter']}  idle={base_c['idle_counts']}")
    print(f"[start] 账本 sha_join={base_ledger['sha_join'][:16]}…  live={live_counts()}")
    print(f"[plan ] 等 {a.target_advances} 次水位推进；下一根收盘 ≈ {utc(prev_wm + 2*H1)}")

    events = []
    observed_wm = [prev_wm]
    n_samples = 0
    baseline = {
        "captured_utc": base_c["ts_utc"],
        "pid": base_c["pid"],
        "started_utc": base_c["started_utc"],
        "watermark_1h": prev_wm,
        "watermark_1h_utc": base_c["watermark_1h_utc"],
        "state_counter": base_c["state_counter"],
        "idle_counts": base_c["idle_counts"],
        "funnel_since_start": prev_funnel,
        "history_candidates_discarded": base_c["history_candidates_discarded"],
        "ledger": base_ledger,
        "live_counts": live_counts(),
        "status_sha256": base_sha,
        "status_bytes": base_len,
        "per_symbol_state_sha256": hashlib.sha256(
            json.dumps(prev_pss, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest(),
        # 逐币状态全量留档：**必须存**，否则事件崩了就只能拿到计数、拿不到「是哪些币」
        # （2026-10-25 踩过：`events[-1]["state_counter"]` 键名写错，第二根小时线只留计数）
        "per_symbol_state": prev_pss,
    }

    with open(f_samples, "a", encoding="utf-8") as fs:
        fs.write(json.dumps({"kind": "baseline", **baseline}, ensure_ascii=False) + "\n")

        while time.time() - t0 < a.max_sec:
            time.sleep(a.interval)
            try:
                st, sha, blen = read_status()
            except Exception as e:
                print(f"[warn] {utc(time.time()*1000)} status 读取失败：{e}")
                continue
            c = compact(st)
            n_samples += 1
            pss = dict((st.get("signal_engine") or {}).get("per_symbol_state") or {})
            wm = c["watermark_1h"]
            changed = wm is not None and wm != prev_wm

            rec = dict(c)
            rec["kind"] = "sample"
            rec["watermark_changed"] = bool(changed)
            rec["status_sha256"] = sha
            rec["pss_sha256"] = hashlib.sha256(
                json.dumps(pss, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            fs.write(json.dumps(rec, ensure_ascii=False) + "\n")
            fs.flush()

            if changed:
                ledger = ledger_state()
                lc = live_counts()
                # 逐币阶段变化
                diffs = []
                for sym in sorted(set(prev_pss) | set(pss)):
                    a0, b0 = prev_pss.get(sym), pss.get(sym)
                    if a0 != b0:
                        diffs.append({"symbol": sym, "prev": a0, "now": b0})
                # 漏斗增量
                cur_funnel = dict(c["funnel_since_start"] or {})
                fdelta = {}
                for k in sorted(set(prev_funnel) | set(cur_funnel)):
                    d0 = (cur_funnel.get(k) or 0) - (prev_funnel.get(k) or 0)
                    if d0:
                        fdelta[k] = d0
                ev = {
                    "kind": "watermark_advance",
                    "observed_utc": c["ts_utc"],
                    "pid": c["pid"],
                    "started_utc": c["started_utc"],
                    "wm_prev": prev_wm,
                    "wm_prev_utc": utc(prev_wm),
                    "wm_new": wm,
                    "wm_new_utc": c["watermark_1h_utc"],
                    "wm_prev_close_utc": utc(prev_wm + H1 - 1) if prev_wm else None,
                    "wm_new_close_utc": utc(wm + H1 - 1),
                    "advanced_bars": int(round((wm - prev_wm) / H1)) if prev_wm else None,
                    "db_1h_max_utc": c["db_1h_max_utc"],
                    "db_4h_max_utc": c["db_4h_max_utc"],
                    "aligned_1h": c["aligned_1h"],
                    "lag_1h_bars": c["lag_1h_bars"],
                    "lag_1h_hist": c["lag_1h_hist"],
                    "n_lag_gt1": c["n_lag_gt1"],
                    "state_counter_prev": baseline["state_counter"] if len(events) == 0 else events[-1]["state_counter_now"],
                    "state_counter_now": c["state_counter"],
                    "idle_counts_now": c["idle_counts"],
                    "n_phase_changes": len(diffs),
                    "phase_changes": diffs,
                    "funnel_delta": fdelta,
                    "candidates_delta": (c["candidates_since_start"] or 0) - prev_cand,
                    "candidates_since_start": c["candidates_since_start"],
                    "last_candidate_utc": c["last_candidate_utc"],
                    "orders_routed_delta": (c["orders_routed_total"] or 0) - prev_orders,
                    "orders_routed_total": c["orders_routed_total"],
                    "orders_rejected_total": c["orders_rejected_total"],
                    "orders_waiting_gate": c["orders_waiting_gate"],
                    "open_positions": c["open_positions"],
                    "exits_triggered_total": c["exits_triggered_total"],
                    "new_entry_allowed": c["new_entry_allowed"],
                    "live_counts": lc,
                    "ledger": ledger,
                    "atr_n_warmed": c["atr_n_warmed"],
                    "atr_n_not_warmed": c["atr_n_not_warmed"],
                }
                events.append(ev)
                observed_wm.append(wm)
                print(f"[event] {c['ts_utc']} 水位 {ev['wm_prev_utc']} → {ev['wm_new_utc']} "
                      f"(+{ev['advanced_bars']} 根) 阶段变化 {len(diffs)} 币 候选+{ev['candidates_delta']} "
                      f"订单+{ev['orders_routed_delta']}")
                for d in diffs[:20]:
                    print(f"         {d['symbol']}: {d['prev']} → {d['now']}")
                if fdelta:
                    print(f"         漏斗增量 {fdelta}")
                print(f"         账本 sha_join={ledger['sha_join'][:16]}… live={lc} 持仓={c['open_positions']}")

                prev_wm = wm
                prev_pss = pss
                prev_funnel = cur_funnel
                prev_cand = c["candidates_since_start"] or 0
                prev_orders = c["orders_routed_total"] or 0

                json.dump({"baseline": baseline, "events": events, "observed_watermarks": observed_wm},
                          open(f_events, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

                if len(events) >= a.target_advances:
                    print(f"[done] 已收到 {len(events)} 次水位推进，退出采样")
                    break

    final_status, final_sha, _ = read_status()
    final_c = compact(final_status)
    final_ledger = ledger_state()
    summary = {
        "generated_utc": utc(time.time() * 1000),
        "elapsed_sec": round(time.time() - t0, 1),
        "n_samples": n_samples,
        "baseline": baseline,
        "observed_watermarks_utc": [utc(x) for x in observed_wm],
        "n_advances": len(events),
        "events": events,
        "final": final_c,
        "final_ledger": final_ledger,
        "final_live_counts": live_counts(),
        "ledger_unchanged": final_ledger["sha_join"] == baseline["ledger"]["sha_join"],
        "status_sha_changed": final_sha != baseline["status_sha256"],
        "verdict": {
            "got_two_new_1h_bars": len(events) >= 2,
            "natural_candidates": sum(e["candidates_delta"] for e in events),
            "natural_orders": sum(e["orders_routed_delta"] for e in events),
            "phase_changes_total": sum(e["n_phase_changes"] for e in events),
            "ledger_untouched": final_ledger["sha_join"] == baseline["ledger"]["sha_join"],
            "real_trading_disabled": True,
            "note": "natural_candidates=0 时必须如实报告为 0，不得用隔离成交冒充自然成交",
        },
    }
    json.dump(summary, open(f_summary, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("[summary]", json.dumps(summary["verdict"], ensure_ascii=False))
    print(f"[out] {f_samples} | {f_events} | {f_summary}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
