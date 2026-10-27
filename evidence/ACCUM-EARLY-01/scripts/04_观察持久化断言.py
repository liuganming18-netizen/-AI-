# -*- coding: utf-8 -*-
"""ACCUM-EARLY-01｜04 观察事件与候选持久化断言。

六条断言：锚点冻结、候选不覆盖、期限不重置、数据不足≠失效、禁止删除、
研究库与生产库隔离。
输出：evidence/ACCUM-EARLY-01/out/04_观察持久化断言.json
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve()
PROJECT = HERE.parents[3]
BACKEND = PROJECT / "框架" / "backend"
sys.path.insert(0, str(BACKEND))

DB = PROJECT / "数据" / "research" / "ACCUM-EARLY-01" / "observe_assert.sqlite3"


def iso(ms):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(int(ms) / 1000))


def main() -> int:
    import observe_store as OS
    if DB.exists():
        DB.unlink()
    st = OS.ObserveStore(DB)
    rep = {"probe": "04_观察持久化断言", "generated_at_utc": iso(time.time() * 1000),
           "db": str(DB), "assertions": {}, "errors": []}

    rep["隔离"] = st.assert_isolated()

    e1 = st.create_event(
        scheme="4a", symbol="BTCUSDT", venue="binance",
        strategy_version="ACCUM-RESEARCH-v0", param_version="PENDING",
        box_version_id="bx_demo_1", structure_seen_ms=1790000000000,
        confirmed_ms=1790003600000, expires_at_ms=1790608400000,
        old_low=100.0, sweep_low=98.0, vwap_anchor_ms=1789990000000,
        vwap_anchor_source="quote_volume/volume 推导",
        data_quality="ok")
    # 重复扫描：期限不得重置
    e2 = st.create_event(
        scheme="4a", symbol="BTCUSDT", venue="binance",
        strategy_version="ACCUM-RESEARCH-v0", param_version="PENDING",
        box_version_id="bx_demo_1", structure_seen_ms=1790000000000,
        confirmed_ms=1790007200000, expires_at_ms=1792000000000)
    rep["assertions"]["重复扫描不改期限"] = {
        "created_again": e2["created"],
        "expires_same": e2["expires_at_ms"] == e1["expires_at_ms"],
        "expires_at_utc": iso(e2["expires_at_ms"]),
    }

    # 锚点冻结
    for col, val in (("expires_at_ms", 1), ("old_low", 1.0), ("sweep_low", 1.0),
                     ("structure_seen_ms", 1), ("box_version_id", "hack")):
        try:
            st.con.execute(f"UPDATE observe_event SET {col}=? WHERE event_id=?",
                           (val, e1["event_id"]))
            rep["assertions"][f"冻结_{col}"] = False
        except Exception as exc:                               # noqa: BLE001
            rep["assertions"][f"冻结_{col}"] = "ABORT" in str(exc).upper() or "frozen" in str(exc)

    # 候选不覆盖
    c1 = st.add_candidate(e1["event_id"], scheme="4a", created_ms=1790010000000,
                          local_support_price=99.5)
    c2 = st.add_candidate(e1["event_id"], scheme="4a", created_ms=1790020000000,
                          local_support_price=99.9)
    st.cancel_candidate(c1["candidate_id"], "回踩跌回压制位下方", 1790025000000)
    c3 = st.add_candidate(e1["event_id"], scheme="4a", created_ms=1790030000000,
                          local_support_price=100.4)
    d = st.dump_event(e1["event_id"])
    rep["assertions"]["候选不覆盖"] = {
        "seq": [c["seq_in_event"] for c in d["candidates"]],
        "count": len(d["candidates"]),
        "第一条仍保留": any(c["candidate_id"] == c1["candidate_id"]
                          and c["cancel_reason"] for c in d["candidates"]),
    }

    # 禁止删除
    for tbl, key, val in (("observe_event", "event_id", e1["event_id"]),
                          ("observe_candidate", "candidate_id", c1["candidate_id"])):
        try:
            st.con.execute(f"DELETE FROM {tbl} WHERE {key}=?", (val,))
            rep["assertions"][f"禁止删除_{tbl}"] = False
        except Exception as exc:                               # noqa: BLE001
            rep["assertions"][f"禁止删除_{tbl}"] = "no_delete" in str(exc)

    # 数据不足 ≠ 形态失败
    e3 = st.create_event(
        scheme="4b", symbol="ETHUSDT", venue="binance",
        strategy_version="ACCUM-RESEARCH-v0", param_version="PENDING",
        box_version_id="bx_demo_2", structure_seen_ms=1790100000000,
        confirmed_ms=1790103600000, expires_at_ms=1790708400000,
        old_low=2000.0, sweep_low=1950.0, data_quality="missing")
    st.add_data_gap(e3["event_id"], "15m", "冷档 15m 在该段缺失", 1790100000000,
                    1790200000000)
    st.close_event(e3["event_id"], "DATA_INSUFFICIENT", "15m 缺口，无法判定回踩", 1790300000000)
    d3 = st.dump_event(e3["event_id"])
    rep["assertions"]["数据不足独立表达"] = {
        "state": d3["event"]["state"],
        "closed_reason": d3["event"]["closed_reason"],
        "gaps": len(d3["data_gaps"]),
        "不等于 INVALIDATED": d3["event"]["state"] != "INVALIDATED",
        "映射说明": "DATA_INSUFFICIENT 是研究侧新增状态，生产状态机无对应项；"
                    "合流需生产侧新增同名状态（改冻结包，未批准前只在本库存在）",
    }

    # 三方案分表不分库：同一观察事件可分别进入三组
    rep["三组可分账"] = {}
    for s in ("4a", "4b", "4c"):
        ev = st.create_event(
            scheme=s, symbol="SOLUSDT", venue="binance",
            strategy_version="ACCUM-RESEARCH-v0", param_version="PENDING",
            box_version_id="bx_shared_1", structure_seen_ms=1790200000000,
            confirmed_ms=1790203600000, expires_at_ms=1790808400000)
        rep["三组可分账"][s] = ev["event_id"]

    rep["summary"] = {
        "all_pass": all(v is True or (isinstance(v, dict) and v.get("expires_same"))
                        for k, v in rep["assertions"].items()
                        if k.startswith(("冻结_", "禁止删除_", "重复扫描")))
                    and rep["assertions"]["候选不覆盖"]["count"] == 3
                    and rep["assertions"]["数据不足独立表达"]["不等于 INVALIDATED"],
        "event_count": len(st.con.execute(
            "SELECT event_id FROM observe_event").fetchall()),
        "candidate_count": len(st.con.execute(
            "SELECT candidate_id FROM observe_candidate").fetchall()),
        "transition_count": len(st.con.execute(
            "SELECT id FROM observe_transition").fetchall()),
    }
    st.close()

    out = PROJECT / "evidence" / "ACCUM-EARLY-01" / "out"
    out.mkdir(parents=True, exist_ok=True)
    (out / "04_观察持久化断言.json").write_text(
        json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(rep["assertions"], ensure_ascii=False, indent=1))
    print("summary", json.dumps(rep["summary"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
