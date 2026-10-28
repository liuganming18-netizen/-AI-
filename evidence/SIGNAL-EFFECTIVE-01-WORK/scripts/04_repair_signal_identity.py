# -*- coding: utf-8 -*-
"""SIGNAL-EFFECTIVE-01-WORK｜把 289 条研究回测行的**身份/时间接线**恢复为可审计的诚实状态。

改什么（**只改这四列，其余一个字节不动**）
----------------------------------------
| 列/字段                     | 现值（被改写后）        | 恢复为                        | 依据 |
|-----------------------------|-------------------------|-------------------------------|------|
| `origin`                    | `recovered`             | `historical_research`         | 来源语义；`recovered` 是「进程重启后仍在时效内的真实候选」专用 |
| `executable_state`          | `enterable`             | `historical_not_executable`   | 研究行恒不可执行（08 单契约） |
| `available_at`              | `= confirmed_at`        | `NULL`                        | 本单第四节第 4 条：不得以确认时刻填补可得时刻 |
| `payload_json.role`         | `enterable_arm_entry`   | `research_only`               | 08 单三重隔离第三重 |
| `payload_json.identity_repair` | —                    | 追加审计子对象（前后值/原因/时刻） | 留痕 |

**不改**：`first_seen_at`、`event_time`、`confirmed_at`、`bar_open_time`、`bar_close_time`、
`symbol`、`strategy_key/version`、`config_hash`、`executable_reason`、`display_state`、
`admission`、`rank`、`payload_json` 的其余键。
**不删**：任何行。**不碰**：任何纸面账本、任何行情库。

幂等：第二次运行命中 0 条。
回滚：`--rollback` 读 `baseline/repair_before.jsonl` 逐行还原（同样只还原这四列）。

用法：
    python 04_repair_signal_identity.py                # 预演（不写）
    python 04_repair_signal_identity.py --apply        # 真写（目标库由 LIVE_DB 决定）
    python 04_repair_signal_identity.py --rollback --apply
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sqlite3
import time
from pathlib import Path

PROJ = Path("/Users/boos/Desktop/威科夫因子共振趋势")
EV = PROJ / "evidence" / "SIGNAL-EFFECTIVE-01-WORK"
BASE = EV / "baseline"
OUT = EV / "out"

TARGET_ORIGIN = "historical_research"
TARGET_EXEC = "historical_not_executable"
TARGET_ROLE = "research_only"
RESEARCH_SOURCE = "antigravity_08_backtest"
REPAIR_TAG = "SIGNAL-EFFECTIVE-01-WORK"


def iso(ms) -> str | None:
    if ms is None:
        return None
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(int(ms) / 1000))


def db_path(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit)
    if os.environ.get("LIVE_DB"):
        return Path(os.environ["LIVE_DB"])
    return PROJ / "框架/backend/live/live_signals.sqlite3"


def select_targets(con: sqlite3.Connection) -> list[dict]:
    """命中判据：研究来源 + 任一项不是诚实状态。"""
    q = """
    SELECT signal_uid, origin, executable_state, available_at, confirmed_at,
           payload_json, updated_at
      FROM live_signal
     WHERE source = ?
       AND (
            origin <> ?
         OR executable_state <> ?
         OR available_at IS NOT NULL
         OR COALESCE(json_extract(payload_json,'$.role'),'') <> ?
       )
     ORDER BY confirmed_at
    """
    rows = con.execute(q, (RESEARCH_SOURCE, TARGET_ORIGIN, TARGET_EXEC,
                           TARGET_ROLE)).fetchall()
    return [dict(r) for r in rows]


def build_new_payload(old_payload: str, row: dict, now_ms: int) -> str:
    p = json.loads(old_payload or "{}")
    before = {
        "origin": row["origin"],
        "executable_state": row["executable_state"],
        "available_at": row["available_at"],
        "role": p.get("role"),
    }
    p["role"] = TARGET_ROLE
    ts = p.get("timestamps") or {}
    # 时间戳子对象：如实回到「无证据」
    ts["available_at_ms"] = None
    ts["available_at_status"] = "unproven"
    ts["available_at_reason"] = (
        "反重力回测产物未提供 OI/交易所发布延迟证据；"
        "不得用 confirmed_at 代替可得时刻（SIGNAL-EFFECTIVE-01-WORK 复原）")
    p["timestamps"] = ts
    # 追加审计留痕（不覆盖既有键）
    hist = p.get("identity_repair")
    if not isinstance(hist, list):
        hist = []
    hist.append({
        "at_ms": now_ms,
        "at_utc": iso(now_ms),
        "order": REPAIR_TAG,
        "reason": ("研究回测行被改成 recovered/enterable 且 available_at=confirmed_at，"
                   "导致历史信号进入当前区；恢复为研究身份与未证明可得性"),
        "before": before,
        "after": {"origin": TARGET_ORIGIN, "executable_state": TARGET_EXEC,
                  "available_at": None, "role": TARGET_ROLE},
    })
    p["identity_repair"] = hist
    return json.dumps(p, ensure_ascii=False, default=str)


def run(dbp: Path, apply: bool, rollback: bool) -> dict:
    now = int(time.time() * 1000)
    con = sqlite3.connect(str(dbp), timeout=20.0)
    con.row_factory = sqlite3.Row
    report = {"target_db": str(dbp), "apply": apply, "rollback": rollback,
              "at_ms": now, "at_utc": iso(now), "rows": [], "counts": {}}
    try:
        if rollback:
            src = BASE / "repair_before.jsonl"
            if not src.exists():
                report["error"] = "缺回滚镜像 baseline/repair_before.jsonl"
                return report
            n = 0
            for line in src.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                b = json.loads(line)
                if apply:
                    con.execute(
                        "UPDATE live_signal SET origin=?, executable_state=?, "
                        "available_at=?, payload_json=?, updated_at=? WHERE signal_uid=?",
                        (b["origin"], b["executable_state"], b["available_at"],
                         b["payload_json"], b["updated_at"], b["signal_uid"]))
                n += 1
            if apply:
                con.commit()
            report["counts"] = {"rolled_back": n, "applied": apply}
            return report

        targets = select_targets(con)
        report["counts"]["matched"] = len(targets)
        mirror = []
        for r in targets:
            new_payload = build_new_payload(r["payload_json"], r, now)
            mirror.append({
                "signal_uid": r["signal_uid"],
                "origin": r["origin"],
                "executable_state": r["executable_state"],
                "available_at": r["available_at"],
                "payload_json": r["payload_json"],
                "updated_at": r["updated_at"],
            })
            report["rows"].append({
                "signal_uid": r["signal_uid"],
                "origin_before": r["origin"], "origin_after": TARGET_ORIGIN,
                "exec_before": r["executable_state"], "exec_after": TARGET_EXEC,
                "available_at_before": r["available_at"], "available_at_after": None,
                "role_before": json.loads(r["payload_json"] or "{}").get("role"),
                "role_after": TARGET_ROLE,
                "confirmed_at_utc": iso(r["confirmed_at"]),
            })
            if apply:
                con.execute(
                    "UPDATE live_signal SET origin=?, executable_state=?, "
                    "available_at=NULL, payload_json=?, updated_at=? WHERE signal_uid=?",
                    (TARGET_ORIGIN, TARGET_EXEC, new_payload, now, r["signal_uid"]))
        if apply:
            con.commit()
            (BASE / "repair_before.jsonl").write_text(
                "\n".join(json.dumps(x, ensure_ascii=False) for x in mirror) + "\n",
                encoding="utf-8")
            # 复核：改完立刻按同一判据再查一次
            report["counts"]["remaining_after"] = len(select_targets(con))
            report["counts"]["origin_recovered_after"] = con.execute(
                "SELECT COUNT(*) FROM live_signal WHERE origin='recovered'").fetchone()[0]
            report["counts"]["available_at_not_null_after"] = con.execute(
                "SELECT COUNT(*) FROM live_signal WHERE available_at IS NOT NULL").fetchone()[0]
            report["counts"]["enterable_after"] = con.execute(
                "SELECT COUNT(*) FROM live_signal WHERE executable_state='enterable'").fetchone()[0]
            report["counts"]["total_rows_after"] = con.execute(
                "SELECT COUNT(*) FROM live_signal").fetchone()[0]
        return report
    finally:
        con.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=None, help="目标信号库（缺省 LIVE_DB 或生产路径）")
    ap.add_argument("--apply", action="store_true", help="真写（缺省预演）")
    ap.add_argument("--rollback", action="store_true", help="按镜像回滚")
    a = ap.parse_args()
    dbp = db_path(a.db)
    rep = run(dbp, a.apply, a.rollback)
    OUT.mkdir(parents=True, exist_ok=True)
    tag = "rollback" if a.rollback else ("apply" if a.apply else "dryrun")
    p = OUT / f"repair_identity_{tag}.json"
    p.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    if rep["rows"]:
        with open(OUT / f"repair_identity_{tag}.csv", "w", encoding="utf-8-sig",
                  newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rep["rows"][0].keys()))
            w.writeheader()
            w.writerows(rep["rows"])
    print(json.dumps(rep["counts"], ensure_ascii=False, indent=1))
    print(f"产物：{p}")
    if rep.get("error"):
        print("错误：", rep["error"])
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
