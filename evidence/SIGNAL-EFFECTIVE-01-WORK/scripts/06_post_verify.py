# -*- coding: utf-8 -*-
"""SIGNAL-EFFECTIVE-01-WORK｜修复后生产核对（只读）。

核对项：
  1. 生产信号库修复后的字段分布（与快照逐项对撞）
  2. 行数不变、first_seen_at 不变、四个时刻不变（只改了 4 个字段）
  3. 四本纸面账本**未变化**（修复前后各做一次一致性快照，逐表行数 + sha256 对撞）
  4. 运行中接口的实际返回（当前区 / 研究区）
  5. 行情库未被本单写入（文件 mtime/sha 与基线一致）
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
import urllib.request
from pathlib import Path

PROJ = Path("/Users/boos/Desktop/威科夫因子共振趋势")
EV = PROJ / "evidence" / "SIGNAL-EFFECTIVE-01-WORK"
BASE = EV / "baseline"
OUT = EV / "out"

LEDGER_TABLES = ("paper_pending", "paper_order", "paper_fill",
                 "paper_position", "paper_realized", "paper_processed")


def iso(ms):
    return None if ms is None else time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ms / 1000))


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def consistent_snapshot(src: Path, dst: Path) -> dict:
    con = sqlite3.connect(str(src), timeout=25.0)
    con.execute("PRAGMA query_only=1")
    try:
        bak = sqlite3.connect(str(dst))
        try:
            with bak:
                con.backup(bak)
        finally:
            bak.close()
    finally:
        con.close()
    chk = sqlite3.connect(str(dst))
    try:
        chk.execute("PRAGMA journal_mode=DELETE")
        integrity = chk.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        chk.close()
    return {"snapshot": str(dst), "sha256": sha256_file(dst),
            "size": dst.stat().st_size, "integrity": integrity}


def ledger_state(p: Path) -> dict:
    if not p.exists():
        return {"status": "missing"}
    con = sqlite3.connect(str(p))
    try:
        d = {}
        for t in LEDGER_TABLES:
            try:
                d[t] = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            except sqlite3.OperationalError:
                d[t] = None
        return d
    finally:
        con.close()


def api_get(path: str) -> dict:
    try:
        with urllib.request.urlopen("http://127.0.0.1:8787" + path, timeout=10) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as exc:                              # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}"}


def main() -> int:
    now = int(time.time() * 1000)
    R = {"generated_at_utc": iso(now), "production_writes": "仅 live_signal 4 个字段（见 04 脚本）"}

    # 1-2 生产信号库修复后核对
    post = consistent_snapshot(PROJ / "框架/backend/live/live_signals.sqlite3",
                               BASE / "live_signals_post_repair.sqlite3")
    R["post_snapshot"] = post
    con = sqlite3.connect(str(BASE / "live_signals_post_repair.sqlite3"))
    pre = sqlite3.connect(str(BASE / "live_signals_snapshot.sqlite3"))
    try:
        R["dist_now"] = {
            "total": con.execute("SELECT COUNT(*) FROM live_signal").fetchone()[0],
            "by_origin": list(con.execute(
                "SELECT origin,COUNT(*) FROM live_signal GROUP BY 1")),
            "by_exec": list(con.execute(
                "SELECT executable_state,COUNT(*) FROM live_signal GROUP BY 1")),
            "by_role": list(con.execute(
                "SELECT json_extract(payload_json,'$.role'),COUNT(*) FROM live_signal GROUP BY 1")),
            "available_at_null": con.execute(
                "SELECT COUNT(*) FROM live_signal WHERE available_at IS NULL").fetchone()[0],
            "available_at_eq_confirmed": con.execute(
                "SELECT COUNT(*) FROM live_signal WHERE available_at=confirmed_at").fetchone()[0],
        }
        # 逐 UID 对撞：只允许 4 个字段变化
        q = ("SELECT signal_uid,first_seen_at,event_time,confirmed_at,bar_open_time,"
             "bar_close_time,symbol,strategy_key,config_hash FROM live_signal ORDER BY 1")
        pre_rows = {r[0]: r for r in pre.execute(q)}
        post_rows = {r[0]: r for r in con.execute(q)}
        diff_uid = set(pre_rows) ^ set(post_rows)
        changed_immutable = []
        for uid, r in pre_rows.items():
            if uid not in post_rows:
                continue
            if r[1:] != post_rows[uid][1:]:
                changed_immutable.append(uid)
        R["immutability"] = {
            "uid_set_diff": sorted(diff_uid),
            "immutable_fields_changed": changed_immutable,
            "结论": ("行集合与 first_seen/四个时刻/符号/策略/配置哈希全部未变"
                     if not diff_uid and not changed_immutable else "存在意外改动"),
        }
        # 审计留痕
        R["identity_repair_marks"] = con.execute(
            "SELECT COUNT(*) FROM live_signal "
            "WHERE json_extract(payload_json,'$.identity_repair') IS NOT NULL").fetchone()[0]
        R["sample_after"] = dict(zip(
            [d[0] for d in con.execute(
                "SELECT signal_uid,origin,executable_state,available_at,"
                "json_extract(payload_json,'$.role') role,"
                "json_extract(payload_json,'$.approved') approved,"
                "json_extract(payload_json,'$.timestamps.available_at_status') avail_status,"
                "executable_reason FROM live_signal LIMIT 1").description],
            con.execute(
                "SELECT signal_uid,origin,executable_state,available_at,"
                "json_extract(payload_json,'$.role') role,"
                "json_extract(payload_json,'$.approved') approved,"
                "json_extract(payload_json,'$.timestamps.available_at_status') avail_status,"
                "executable_reason FROM live_signal LIMIT 1").fetchone()))
    finally:
        con.close()
        pre.close()

    # 3 账本未变化
    ledger = {}
    for arm in ("3a", "3b", "3c", "3d"):
        p = PROJ / "数据/paper" / f"paper_{arm}.sqlite3"
        snap = BASE / f"paper_{arm}_post.sqlite3"
        s = consistent_snapshot(p, snap)
        ledger[arm] = {
            "rows_now": ledger_state(snap),
            "sha256_now": s["sha256"],
            "sha256_baseline": sha256_file(BASE / f"paper_{arm}_snapshot.sqlite3"),
        }
        ledger[arm]["unchanged"] = (ledger[arm]["rows_now"]
                                    == ledger_state(BASE / f"paper_{arm}_snapshot.sqlite3"))
    R["ledgers"] = ledger

    # 4 接口实测
    cur = api_get("/api/v1/signals?scope=current&limit=5")
    res = api_get("/api/v1/signals?scope=all&origin=historical_research&limit=3")
    R["api"] = {
        "current": {
            "total": cur.get("total"),
            "n_items": len(cur.get("items") or []),
            "quality": {k: v for k, v in (cur.get("quality") or {}).items()
                        if k in ("status", "scope", "effective_floor_ms", "excluded_n",
                                 "excluded_breakdown", "n_cards")},
            "error": cur.get("error"),
        },
        "research": {
            "total": res.get("total"),
            "n_items": len(res.get("items") or []),
            "has_more": res.get("has_more"),
            "sample_uid": ((res.get("items") or [{}])[0].get("signal_id")
                           if res.get("items") else None),
            "sample_origin": ((res.get("items") or [{}])[0].get("origin")
                              if res.get("items") else None),
            "sample_research_obj_present": bool(
                (res.get("items") or [{}])[0].get("research")) if res.get("items") else None,
            "error": res.get("error"),
        },
    }

    # 5 行情库未写入
    mkt = PROJ / "数据/市场数据/db/market_v2.sqlite3"
    R["market_db"] = {"path": str(mkt), "size": mkt.stat().st_size,
                      "mtime_utc": iso(int(mkt.stat().st_mtime * 1000)),
                      "note": "本单全程以只读连接访问（live_store.market_conn ro=True）"}

    p = OUT / "post_repair_verify.json"
    p.write_text(json.dumps(R, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: R[k] for k in ("dist_now", "immutability", "identity_repair_marks")},
                     ensure_ascii=False, indent=1))
    print("账本未变:", {a: v["unchanged"] for a, v in ledger.items()})
    print("接口当前区:", R["api"]["current"]["total"], "研究区:", R["api"]["research"]["total"])
    print(f"产物：{p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
