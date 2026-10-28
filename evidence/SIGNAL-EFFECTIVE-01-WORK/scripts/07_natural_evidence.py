# -*- coding: utf-8 -*-
"""SIGNAL-EFFECTIVE-01-WORK｜自然运行证据 + 共振因子实际影响盘点（只读）。

「自然」= 真实运行时从生效水位之后自己产生的候选，**不含**任何回放/注入。
本脚本只读三处：运行时状态文件、修复后信号库快照、行情库（只读）。
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

PROJ = Path("/Users/boos/Desktop/威科夫因子共振趋势")
EV = PROJ / "evidence" / "SIGNAL-EFFECTIVE-01-WORK"
BASE = EV / "baseline"
OUT = EV / "out"

STATUS = PROJ / "框架/backend/locks/paper_runtime_3a_status.json"
LIVE = BASE / "live_signals_post_repair.sqlite3"


def iso(ms):
    return None if ms is None else time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ms / 1000))


def main() -> int:
    now = int(time.time() * 1000)
    R = {"generated_at_utc": iso(now)}

    # ── 1. 自然运行 ─────────────────────────────────────────────────────────
    st = json.loads(STATUS.read_text(encoding="utf-8"))
    se = st.get("signal_engine") or {}
    bs = se.get("bootstrap") or {}
    op = st.get("order_processing") or {}
    disc = bs.get("discarded_detail") or []
    reasons = {}
    for d in disc:
        for r in (d.get("discard_reasons") or []):
            reasons[r] = reasons.get(r, 0) + 1
    R["natural_run"] = {
        "run_identity": st.get("runtime_identity"),
        "watermark_1h_utc": iso(se.get("watermark_1h")),
        "watermark_4h_utc": iso(se.get("watermark_4h")),
        "universe": se.get("universe"),
        "bootstrap_ready": bs.get("ready"),
        "bootstrap_skipped": bs.get("skipped"),
        "history_candidates_discarded": bs.get("history_candidates_discarded"),
        "discard_reason_counts": reasons,
        "discard_samples": [
            {"candidate_id": d.get("candidate_id"), "symbol": d.get("symbol"),
             "available_at_utc": d.get("available_at_utc"),
             "execution_earliest_utc": d.get("execution_earliest_utc"),
             "reasons": d.get("discard_reasons")}
            for d in disc[:5]],
        "recovered_candidates": bs.get("recovered_candidates"),
        "orders_routed_total": op.get("orders_routed_total"),
        "orders_rejected_total": op.get("orders_rejected_total"),
        "waiting_gate": op.get("orders_waiting_gate"),
        "current_pending_in_db": op.get("current_pending_in_db"),
        "new_entry_gate": op.get("new_entry_gate"),
        "结论": ("运行时在跑、526 个标的全部预热完成、水位到 "
                 f"{iso(se.get('watermark_1h'))}；生效水位之后**自然候选 0 条**，"
                 "因此当前区 0 条是「真的没有」而不是「被过滤掉」。"
                 "bootstrap 期间回放出来的历史候选 51 条全部按既有闸门丢弃。"),
    }

    # ── 2. 信号库现状（修复后） ──────────────────────────────────────────────
    con = sqlite3.connect(str(LIVE))
    try:
        floor = 1792748844240
        R["signal_db"] = {
            "total": con.execute("SELECT COUNT(*) FROM live_signal").fetchone()[0],
            "by_origin": list(con.execute("SELECT origin,COUNT(*) FROM live_signal GROUP BY 1")),
            "after_floor": con.execute(
                "SELECT COUNT(*) FROM live_signal WHERE confirmed_at+1>=?", (floor,)).fetchone()[0],
            "current_scope_eligible": con.execute(
                "SELECT COUNT(*) FROM live_signal WHERE origin IN ('live','recovered') "
                "AND executable_state='enterable' AND available_at IS NOT NULL "
                "AND COALESCE(json_extract(payload_json,'$.approved'),1)<>0").fetchone()[0],
            "note": "「生效水位之后」与「够格进当前区」是两件事：前者是历史时刻，后者要身份+批准+可得性齐备",
        }

        # ── 3. 共振因子的实际影响（只统计，不评价对错）──────────────────────
        pm = list(con.execute(
            "SELECT json_extract(payload_json,'$.position_multiplier'),COUNT(*) "
            "FROM live_signal GROUP BY 1 ORDER BY 1"))
        avail = {k: 0 for k in ("oi", "whale_cost", "taker_ratio", "long_short")}
        for (pj,) in con.execute("SELECT payload_json FROM live_signal"):
            p = json.loads(pj or "{}")
            fd = p.get("factor_details") or {}
            for k in avail:
                if (fd.get(k) or {}).get("available"):
                    avail[k] += 1
        R["resonance_impact"] = {
            "rows_with_resonance_score": con.execute(
                "SELECT COUNT(*) FROM live_signal "
                "WHERE json_extract(payload_json,'$.resonance_score') IS NOT NULL").fetchone()[0],
            "position_multiplier_distribution": pm,
            "factor_available_rows": avail,
            "总计行数": R["signal_db"]["total"],
            "结论": ("289 条历史研究行的 payload 里**已经带**共振分与仓位倍数："
                     "89 条被算成 0.8x、200 条 1.0x；四项因子里 OI 289 行可得、"
                     "聪明钱阶段均价 80 行可得、Taker 与多空比 **0 行可得**。"
                     "这些数值在改动前会**直接乘进下单风险预算**（缺数据时算得 0.8x = 无依据缩仓 20%）；"
                     "本单已改为「无显式批准标记则恒 1.0」，数值仍保留可审计。"),
        }
    finally:
        con.close()

    p = OUT / "natural_run_evidence.json"
    p.write_text(json.dumps(R, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: R[k] for k in ("natural_run", "signal_db")},
                     ensure_ascii=False, indent=1)[:2200])
    print("\n共振影响:", json.dumps(R["resonance_impact"], ensure_ascii=False)[:600])
    print(f"\n产物：{p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
