# -*- coding: utf-8 -*-
"""ACCUM-EARLY-04-B｜02 配置闸门：未批准就阻塞开仓，但数据/记录/接口/隔离照做。

四组对照（都在同一份代码路径上跑，只换配置）：
  A 无配置文件           → 36 项必填全缺
  B 缺一个字段           → 精确指出缺哪项
  C fixture 完整但没开环境开关 → 仍然阻塞（防止「配置一改就自动跑」）
  D 接口自检             → 三组身份/待决/隔离/HTTP 可见性
并验证：被阻塞时**没有**创建任何 paper_4x 账本、**没有**调 paper_engine。
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common as C                                    # noqa: E402

import accum_early_entry as AE                         # noqa: E402

RUN = C.RUNS / "02_gate"


def _fake_candidate(scheme="4a", symbol="GATEUSDT") -> dict:
    """只用于闸门测试的候选（不会走到引擎）。"""
    confirm = C.ms_of("2026-08-02T02:59:59Z")
    return {"candidate_id": "cd_gate_test", "event_id": "ev_gate_test",
            "scheme": scheme, "symbol": symbol,
            "confirm_close_ms": confirm, "earliest_entry_ms": confirm + C.M15,
            "entry_bar_open_ms": confirm + C.M15, "entry_ref_price": 1.0,
            "resistance": 0.99, "status": "WAIT_ENTRY", "status_reason": "gate test",
            "conditions": [], "gap": None,
            "signal_uid": f"accum04_{scheme}_{symbol}_{confirm}"}


def _count_rows(db: Path, table: str) -> int:
    import sqlite3
    if not db.exists():
        return -1
    con = sqlite3.connect(str(db))
    try:
        return int(con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    except Exception:                                   # noqa: BLE001
        return -1
    finally:
        con.close()


def main() -> int:
    if RUN.exists():
        shutil.rmtree(RUN)
    RUN.mkdir(parents=True, exist_ok=True)

    import os
    os.environ["PAPER_LEDGER_DIR"] = str(RUN / "paper")     # 隔离目录（空）
    os.environ["ACCUM04_CONFIG"] = str(RUN / "does_not_exist.json")
    os.environ.pop("ACCUM04_FIXTURE_OK", None)

    out = {"generated_utc": C.iso(int(__import__("time").time() * 1000)),
           "runs": str(RUN)}

    # ── A 无配置 ────────────────────────────────────────────────────────
    cfg_a = AE.load_config()
    store_a = AE.open_store(RUN / "observe_A.sqlite3")
    feed = AE.DataFeed()
    start = C.ms_of("2026-08-01T00:00:00Z")
    end = C.ms_of("2026-08-20T00:00:00Z")
    scans = []
    for sym in ("BTCUSDT", "ETHUSDT", "SOLUSDT"):
        scans.append(AE.scan_symbol(sym, cfg_a, feed, store_a,
                                    start_ms=start, end_ms=end, max_boxes=1))
    feed.close()

    blocked_events = [e for s in scans for e in s["events"] if e.get("blocked_config")]
    sample_event = None
    if blocked_events:
        sample_event = store_a.dump_event(blocked_events[0]["event_id"])
    store_a.close()

    res_a = {
        "config_present": cfg_a["present"],
        "missing_count": len(cfg_a["missing"]),
        "missing_fields": cfg_a["missing"],
        "open_allowed": cfg_a["open_allowed"],
        "blocked_by": cfg_a["blocked_by"],
        "events_created": sum(len(s["events"]) for s in scans),
        "events_blocked_at_config": len(blocked_events),
        "candidates_generated": sum(len(s["candidates"]) for s in scans),
        "candidate_rows_in_db": _count_rows(RUN / "observe_A.sqlite3", "accum04_candidate"),
        "sample_blocked_event": (
            {"event": {k: sample_event["event"][k] for k in
                       ("event_id", "scheme", "symbol", "state", "created_ms")},
             "transitions": [{"from": t["from_state"], "to": t["to_state"],
                              "at_utc": t["at_utc"], "reason": t["reason"],
                              "conditions_count": len(
                                  (json.loads(t["payload_json"] or "{}")
                                   .get("conditions") or []))}
                             for t in sample_event["transitions"]],
             "condition_sample": (json.loads(
                 sample_event["transitions"][-1]["payload_json"] or "{}")
                 .get("conditions") or [])[:4]}
            if sample_event else None),
        "paper_ledger_created": sorted(p.name for p in (RUN / "paper").glob("*.sqlite3")),
    }

    # ── B 缺一个字段 ────────────────────────────────────────────────────
    fixture = json.loads(C.FIXTURE.read_text(encoding="utf-8"))
    broken = json.loads(json.dumps(fixture))
    broken["shared"]["local_resistance"].pop("mode")
    bp = RUN / "broken_rules.json"
    bp.write_text(json.dumps(broken, ensure_ascii=False, indent=1), encoding="utf-8")
    cfg_b = AE.load_config(bp)
    store_b = AE.open_store(RUN / "observe_B.sqlite3")
    open_b = AE.try_open("4a", "GATEUSDT", _fake_candidate(), cfg_b, store_b)
    store_b.close()
    res_b = {"missing": cfg_b["missing"], "open_allowed": cfg_b["open_allowed"],
             "try_open": open_b,
             "paper_ledger_created": sorted(p.name for p in (RUN / "paper").glob("*.sqlite3"))}

    # ── C fixture 完整但未开环境开关 ────────────────────────────────────
    cfg_c = AE.load_config(C.FIXTURE)                # 此刻 ACCUM04_FIXTURE_OK 未设
    store_c = AE.open_store(RUN / "observe_C.sqlite3")
    open_c = AE.try_open("4a", "GATEUSDT", _fake_candidate(), cfg_c, store_c)
    store_c.close()
    res_c = {"ready": cfg_c["ready"], "open_allowed": cfg_c["open_allowed"],
             "blocked_by": cfg_c["blocked_by"], "try_open": open_c,
             "paper_ledger_created": sorted(p.name for p in (RUN / "paper").glob("*.sqlite3"))}

    # ── D 接口自检 ──────────────────────────────────────────────────────
    diag = AE.diagnose(C.FIXTURE, probe_symbol="BTCUSDT")

    out.update({"A_no_config": res_a, "B_one_field_missing": res_b,
                "C_fixture_without_env": res_c, "D_diagnose": diag})
    out["verdict"] = {
        "开仓被阻塞": (not res_a["open_allowed"] and not res_b["open_allowed"]
                  and not res_c["open_allowed"]),
        "阻塞时未创建任何研究账本": (res_a["paper_ledger_created"] == []
                            and res_b["paper_ledger_created"] == []
                            and res_c["paper_ledger_created"] == []),
        "阻塞时未调用 paper_engine": all(
            not (x.get("try_open") or {}).get("called_paper_engine", False)
            for x in (res_b, res_c)),
        "数据读取仍然完成": sum(s["bars"]["1h"] for s in scans) > 0,
        "观察与记录仍然完成": res_a["events_created"] > 0,
        "隔离边界仍可核对": diag["isolation"]["isolated"] is True,
    }
    C.dump("02_配置闸门.json", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
