# -*- coding: utf-8 -*-
"""SIGNAL-NOW-01-WORK｜步骤8：自然运行观测（只观察，不注入、不建单）。

纪律：
- **不注入任何信号**、不写任何账本表、不触发下单接口。只读状态文件 + 只读 SQL + 只读 HTTP。
- 自然候选为 0 就记 0，**不承诺成交**；隔离环境的正例不得称为自然行为。
- 分因按冻结规则的事件链逐层记录：BOX → SWEEP(跌破下沿) → RETEST → CANDIDATE，
  以及候选层的资格/阻断原因（volume / OI / 风险 / 批准 / 配置）。

产物：out/08_自然扫描分因.json
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
import time
from pathlib import Path

PROJ = Path("/Users/boos/Desktop/威科夫因子共振趋势")
ROOT = PROJ / "evidence" / "SIGNAL-NOW-01-WORK"
OUT = ROOT / "out"
BACKEND = PROJ / "框架/backend"
STATUS = BACKEND / "locks" / "paper_runtime_3a_status.json"
LIVE_DB = BACKEND / "live" / "live_signals.sqlite3"
PAPER_DB = PROJ / "数据/paper/paper_3a.sqlite3"
API = "http://127.0.0.1:8787"

SAMPLES = 20          # 采样次数
INTERVAL = 60         # 秒


def utc(ts=None):
    t = time.time() if ts is None else float(ts)
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


def http_get(url, timeout=8):
    cp = subprocess.run(["curl", "-s", "-m", str(timeout), "-w", "\n__HTTP__%{http_code}", url],
                        capture_output=True, text=True, timeout=timeout + 6)
    body, code = cp.stdout, ""
    if "__HTTP__" in body:
        body, code = body.rsplit("__HTTP__", 1)
    try:
        js = json.loads(body)
    except Exception:
        js = None
    return {"http": code.strip(), "json": js}


def _ms2utc(ms):
    if not ms:
        return None
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(int(ms) / 1000))


def status_snapshot():
    try:
        d = json.loads(STATUS.read_text(encoding="utf-8"))
    except Exception as e:
        return {"error": str(e)}
    ri = d.get("runtime_identity") or {}
    se = d.get("signal_engine") or {}
    od = d.get("order_processing") or {}
    ed = d.get("execution_daemon") or {}
    fn = se.get("funnel") or {}
    wc = se.get("watermark_check") or {}
    w1h, w4h = se.get("watermark_1h"), se.get("watermark_4h")
    return {
        "at_utc": utc(),
        "pid": ri.get("pid"),
        "cycle_count": ri.get("cycle_count"),
        "universe": se.get("universe"),
        "universe_declared": se.get("universe_declared"),
        "watermark_1h": w1h,
        "watermark_4h": w4h,
        # 注意：状态文件里**没有** watermark_1h_utc 字段（07 脚本曾误读），
        # 这里由毫秒值自行换算，口径为 bar 的 open_time（区间起点）。
        "watermark_1h_utc": _ms2utc(w1h),
        "watermark_4h_utc": _ms2utc(w4h),
        "db_1h_max_open": wc.get("db_1h_max_open"),
        "db_4h_max_open": wc.get("db_4h_max_open"),
        "aligned_1h": wc.get("aligned_1h"),
        "aligned_4h": wc.get("aligned_4h"),
        "lag_1h_bars": wc.get("lag_1h_bars"),
        "lag_4h_bars": wc.get("lag_4h_bars"),
        "funnel_since_start": fn.get("since_start"),
        "funnel_history": fn.get("history"),
        "candidates_since_start": fn.get("candidates_since_start"),
        "last_candidate_utc": fn.get("last_candidate_utc"),
        "observe_summary": (se.get("observe_summary") or {}).get("counts"),
        "observing_total": (se.get("observe_summary") or {}).get("observing_total"),
        "data_not_ready": (se.get("observe_summary") or {}).get("data_not_ready"),
        "idle_breakdown": se.get("idle_breakdown"),
        "data_quality_blocked_n": (se.get("data_quality") or {}).get("blocked_n"),
        "scan_errors_total": se.get("scan_errors_total"),
        "skipped_reasons": se.get("skipped_reasons"),
        "state_counter": se.get("state_counter"),
        "signals_generated_total": se.get("signals_generated_total"),
        "current_pending_in_db": od.get("current_pending_in_db"),
        "orders_routed_total": od.get("orders_routed_total"),
        "orders_rejected_total": od.get("orders_rejected_total"),
        "orders_waiting_gate": od.get("orders_waiting_gate"),
        "open_positions": ed.get("open_positions"),
        "pending_matched_total": ed.get("pending_matched_total"),
        "exits_triggered_total": ed.get("exits_triggered_total"),
        "last_error": ed.get("last_error"),
        "bootstrap": se.get("bootstrap"),
    }


def ledger_state():
    """账本只读计数（不做任何写）。"""
    if not PAPER_DB.exists():
        return {"error": "paper db missing", "path": str(PAPER_DB)}
    con = sqlite3.connect(f"file:{PAPER_DB}?mode=ro", uri=True, timeout=20)
    try:
        q = lambda s: con.execute(s).fetchone()[0]
        return {
            "pending_total": q("SELECT COUNT(*) FROM paper_pending"),
            "pending_by_status": {r[0]: r[1] for r in con.execute(
                "SELECT status, COUNT(*) FROM paper_pending GROUP BY status")},
            "pending_max_id": q("SELECT COALESCE(MAX(pending_id),0) FROM paper_pending"),
            "order_total": q("SELECT COUNT(*) FROM paper_order"),
            "order_max_id": q("SELECT COALESCE(MAX(order_id),0) FROM paper_order"),
            "fill_total": q("SELECT COUNT(*) FROM paper_fill"),
            "position_total": q("SELECT COUNT(*) FROM paper_position"),
            "position_open": q("SELECT COUNT(*) FROM paper_position WHERE status='open'"),
            "processed_total": q("SELECT COUNT(*) FROM paper_processed"),
            "anchor_total": q("SELECT COUNT(*) FROM paper_request_anchor"),
            "open_uids": [r[0] for r in con.execute(
                "SELECT signal_uid FROM paper_position WHERE status='open'")],
        }
    finally:
        con.close()


def api_probe(full: bool = True):
    cur = http_get(f"{API}/api/v1/signals?scope=current&limit=5")
    js = cur.get("json") or {}
    q = js.get("quality") or {}
    eb = q.get("excluded_breakdown") or {}
    out = {
        "current": {"http": cur["http"], "total": js.get("total"),
                    "excluded_n": q.get("excluded_n"), "by_reason": eb.get("by_reason"),
                    "effective_floor_ms": q.get("effective_floor_ms"),
                    "live_start_ms": q.get("live_start_ms"),
                    "paper_epoch_ms": q.get("paper_epoch_ms"),
                    "note": js.get("note")},
    }
    if not full:
        # 中间采样只抓当前区计数，避免每次拉 289 条大 JSON 干扰运行
        return out
    allr = http_get(f"{API}/api/v1/signals?scope=all&origin=historical_research&limit=3")
    ajs = allr.get("json") or {}
    aitems = ajs.get("items") or ajs.get("signals") or []
    big = http_get(f"{API}/api/v1/signals?scope=all&origin=historical_research&limit=500", timeout=30)
    bjs = big.get("json") or {}
    bitems = bjs.get("items") or bjs.get("signals") or []
    dist = {}
    for it in bitems:
        k = (it.get("executable_state"), (it.get("research") or {}).get("approved"),
             it.get("role"))
        dist[str(k)] = dist.get(str(k), 0) + 1
    avail_null = sum(1 for it in bitems if it.get("available_at") is None)
    out["research_scope"] = {"http": allr["http"], "total": ajs.get("total"),
                             "has_more": ajs.get("has_more"),
                             "first_uids": [x.get("signal_id") for x in aitems[:3]]}
    out["research_dist_500"] = {"n": len(bitems), "total_field": bjs.get("total"),
                                "has_more": bjs.get("has_more"),
                                "exec_state_approved_role": dist,
                                "available_at_null": avail_null}
    return out


def main() -> int:
    R = {"ticket": "SIGNAL-NOW-01-WORK", "step": "8 自然运行观测（只读）",
         "started_at_utc": utc(), "samples_planned": SAMPLES,
         "interval_seconds": INTERVAL, "samples": [], "errors": []}
    R["baseline_ledger"] = ledger_state()
    R["baseline_api"] = api_probe()
    print("[基线] 账本:", {k: v for k, v in R["baseline_ledger"].items()
                          if k in ("pending_total", "order_total", "fill_total",
                                   "position_open", "processed_total")})
    print("[基线] API current total=", R["baseline_api"]["current"]["total"],
          "excluded_n=", R["baseline_api"]["current"]["excluded_n"])

    for i in range(SAMPLES):
        s = status_snapshot()
        s["ledger"] = ledger_state()
        s["api"] = api_probe(full=(i == 0 or i == SAMPLES - 1))
        R["samples"].append(s)
        fn = s.get("funnel_since_start") or {}
        print(f"[{i:02d}] {s['at_utc']} pid={s.get('pid')} cycles={s.get('cycle_count')} "
              f"uni={s.get('universe')} w1h={s.get('watermark_1h_utc')} "
              f"w4h={s.get('watermark_4h_utc')} "
              f"BOX={fn.get('BOX')} SWEEP={fn.get('SWEEP')} RETEST={fn.get('RETEST')} "
              f"CAND={fn.get('CANDIDATE')} OI_UN={fn.get('OI_UNAVAILABLE')} "
              f"pend={s.get('current_pending_in_db')}")
        if i < SAMPLES - 1:
            time.sleep(INTERVAL)

    R["finished_at_utc"] = utc()

    # ── 派生：水位是否推进、候选是否出现、账本是否变化 ──
    ss = R["samples"]

    def first_last(key):
        """取**原始首尾值**（不跳过 None）。

        为什么要保留 None：新进程预热期水位是 None，预热完成后变成具体值。
        若跳过 None 再比较，会把「None → 17:00Z」误判成「未推进」。
        """
        if not ss:
            return (None, None)
        return (ss[0].get(key), ss[-1].get(key))

    w1h_f, w1h_l = first_last("watermark_1h")
    w4h_f, w4h_l = first_last("watermark_4h")
    cyc_f, cyc_l = first_last("cycle_count")
    fn_last = (ss[-1].get("funnel_since_start") or {}) if ss else {}
    led_last = ss[-1].get("ledger") if ss else {}

    R["derived"] = {
        "samples_actual": len(ss),
        "pid_stable": len({x.get("pid") for x in ss}) == 1,
        "cycle_count": {"first": cyc_f, "last": cyc_l,
                        "advanced": (cyc_f is not None and cyc_l is not None and cyc_l > cyc_f)},
        "watermark_1h": {"first": w1h_f, "last": w1h_l,
                         "first_utc": _ms2utc(w1h_f), "last_utc": _ms2utc(w1h_l),
                         "advanced": w1h_l != w1h_f,
                         "note": "首值为 None = 本进程尚在预热；None→具体值 也算推进（预热完成）"},
        "watermark_4h": {"first": w4h_f, "last": w4h_l,
                         "first_utc": _ms2utc(w4h_f), "last_utc": _ms2utc(w4h_l),
                         "advanced": w4h_l != w4h_f},
        "funnel_since_start_last": fn_last,
        "candidates_since_start_last": (ss[-1].get("candidates_since_start") if ss else None),
        "ledger_changed": {
            "pending_total": [R["baseline_ledger"].get("pending_total"),
                              led_last.get("pending_total")],
            "order_total": [R["baseline_ledger"].get("order_total"),
                            led_last.get("order_total")],
            "fill_total": [R["baseline_ledger"].get("fill_total"),
                           led_last.get("fill_total")],
            "position_open": [R["baseline_ledger"].get("position_open"),
                              led_last.get("position_open")],
        },
        "natural_candidates_this_window": fn_last.get("CANDIDATE", 0),
        "natural_fills_this_window": (
            (led_last.get("fill_total") or 0) - (R["baseline_ledger"].get("fill_total") or 0)),
    }

    # ── 逐层分因（照冻结事件链；数字全部来自 since_start 实测，不推算） ──
    box, sweep = fn_last.get("BOX", 0), fn_last.get("SWEEP", 0)
    retest, cand = fn_last.get("RETEST", 0), fn_last.get("CANDIDATE", 0)
    layers = [
        {"layer": "1 箱体建立 BOX", "n": box,
         "note": "4h 结构形成箱体；单边行情里会大量存在但永不跌破下沿"},
        {"layer": "2 向下扫破 SWEEP", "n": sweep,
         "note": "必须跌破箱体下沿 L；FT-LOW4H 是反转策略，不跌破就永远到不了下一层"},
        {"layer": "3 回踩确认 RETEST", "n": retest,
         "note": "扫破后回踩不破；02 单已定位「RETEST>0 而 CANDIDATE=0」是卡点层"},
        {"layer": "4 突破上沿成候选 CANDIDATE", "n": cand,
         "note": "本层是自然候选的计数口径；为 0 即本窗口无自然机会"},
    ]
    R["layer_attribution"] = {
        "definition": "只读诊断：各层计数取自 runtime_identity 状态文件 funnel.since_start，"
                      "为**本进程启动后**的状态机迁移数，不含预热回放（回放数在 funnel.history）",
        "layers": layers,
        "candidate_block_reasons_observed": {
            "VOLUME_REJECTED": fn_last.get("VOLUME_REJECTED", 0),
            "OI_UNAVAILABLE": fn_last.get("OI_UNAVAILABLE", 0),
            "OI_REJECTED": fn_last.get("OI_REJECTED", 0),
            "INVALIDATED": fn_last.get("INVALIDATED", 0),
            "EXPIRED": fn_last.get("EXPIRED", 0),
            "MISSED_WITHOUT_RETEST": fn_last.get("MISSED_WITHOUT_RETEST", 0),
        },
        "history_layer_counts": (ss[-1].get("funnel_history") if ss else None),
        "bottleneck_layer": ("4 突破上沿成候选 CANDIDATE" if cand == 0
                             else ("3 回踩确认 RETEST" if retest == 0 else None)),
        "honesty": "本窗口候选为 0 时，**不得**表述为「策略已能按现实时间入场」，"
                   "也不得把隔离断言的正例当作自然行为。",
    }

    OUT.mkdir(parents=True, exist_ok=True)
    p = OUT / "08_自然扫描分因.json"
    p.write_text(json.dumps(R, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print("\n[派生]")
    for k, v in R["derived"].items():
        print(f"  {k}: {v}")
    print(f"\n产物：{p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
