#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""12 · 账本级不变式扫描：所有新口径成交行都必须满足时刻链。

工单要求「任何 `entry_ts`/`fill_ts` 不得早于其使用价格的可得时刻」。
前面各脚本证明的是「这一笔」；本脚本把**所有账本、所有成交行**扫一遍，
逐行判三条不变式，把违反项全部列出来（有就报，没有也报「0 违反」）。

扫描范围（只读）：
  · 生产 `数据/paper/paper_3{a,b,c,d}.sqlite3`
  · 本单隔离副本 `evidence/MAINLINE-01-WORK/isolated/**/ledger/*.sqlite3`
  · 上一单隔离副本 `evidence/PARALLEL-STRATEGY-01-WORK/isolated/**/ledger/*.sqlite3`

三条不变式：
  I1 `fill_ts >= data_available_at`（成交记录时刻不早于行情可得时刻）
  I2 `data_available_at <= read_at_ms <= matched_at_ms`（数据 → 读取 → 撮合）
  I3 `fill_ts == matched_at_ms` 且 `paper_order.ts == matched_at_ms`
只对 `reality_fill=1` 且三列齐备的行判 I2/I3；历史行（NULL）单独统计，不判为违反。
输出 `out/12_ledger_invariant.json`。
"""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
from pathlib import Path

UTC = dt.timezone.utc
PROJ = Path("/Users/boos/Desktop/威科夫因子共振趋势")
EVID = PROJ / "evidence" / "MAINLINE-01-WORK"
OUT = EVID / "out"
PROD = PROJ / "数据" / "paper"


def iso(ms):
    return None if ms is None else dt.datetime.fromtimestamp(int(ms) / 1000, UTC).isoformat()


def ro(p: Path):
    con = sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=8)
    con.row_factory = sqlite3.Row
    return con


def scan(path: Path) -> dict:
    con = ro(path)
    fcols = {r[1] for r in con.execute("PRAGMA table_info(paper_fill)")}
    pcols = {r[1] for r in con.execute("PRAGMA table_info(paper_position)")}
    ocols = {r[1] for r in con.execute("PRAGMA table_info(paper_order)")}
    need_f = {"reality_fill", "data_available_at", "read_at_ms", "matched_at_ms"}
    if not need_f.issubset(fcols):
        con.close()
        return {"path": str(path), "has_columns": False,
                "note": "该库还没有三时刻列（未跑过新代码）"}

    def c(cols, n):
        return n if n in cols else f"NULL AS {n}"

    rows = [dict(r) for r in con.execute(
        "SELECT fill_id,order_id,position_id,signal_uid,symbol,action,bar_open_time,"
        f"fill_ts,{c(fcols,'reality_fill')},{c(fcols,'data_available_at')},"
        f"{c(fcols,'read_at_ms')},{c(fcols,'matched_at_ms')} "
        "FROM paper_fill ORDER BY fill_id")]
    orders = {r["order_id"]: dict(r) for r in con.execute(
        f"SELECT order_id,ts,status FROM paper_order")} if "ts" in ocols else {}
    poss = {r["position_id"]: dict(r) for r in con.execute(
        f"SELECT position_id,entry_ts,{c(pcols,'reality_fill')},"
        f"{c(pcols,'data_available_at')},{c(pcols,'read_at_ms')},"
        f"{c(pcols,'matched_at_ms')} FROM paper_position")} if "entry_ts" in pcols else {}
    con.close()

    viol = []
    new_rows = 0
    for r in rows:
        if r.get("reality_fill") != 1:
            continue
        new_rows += 1
        ft, dav = r.get("fill_ts"), r.get("data_available_at")
        rd, mt = r.get("read_at_ms"), r.get("matched_at_ms")
        if dav is not None and ft is not None and int(ft) < int(dav):
            viol.append({"rule": "I1_fill_ts_before_data_available", "fill_id": r["fill_id"],
                         "fill_ts": iso(ft), "data_available_at": iso(dav)})
        if None not in (dav, rd, mt) and not (int(dav) <= int(rd) <= int(mt)):
            viol.append({"rule": "I2_order_violated", "fill_id": r["fill_id"],
                         "data_available_at": iso(dav), "read_at": iso(rd),
                         "matched_at": iso(mt)})
        if mt is not None and ft is not None and int(ft) != int(mt):
            viol.append({"rule": "I3_fill_ts_ne_matched", "fill_id": r["fill_id"],
                         "fill_ts": iso(ft), "matched_at": iso(mt)})
        o = orders.get(r.get("order_id"))
        if o and o.get("ts") is not None and mt is not None and int(o["ts"]) != int(mt):
            viol.append({"rule": "I3_order_ts_ne_matched", "order_id": o["order_id"],
                         "order_ts": iso(o["ts"]), "matched_at": iso(mt)})
    return {"path": str(path), "has_columns": True,
            "fill_rows_total": len(rows), "new_scheme_rows": new_rows,
            "historical_rows_null": len(rows) - new_rows,
            "violations": viol, "violation_n": len(viol),
            "rows": [{k: (iso(v) if k.endswith(("_at", "_ts", "_time", "_ms")) and isinstance(v, int)
                          else v) for k, v in r.items()} for r in rows]}


def main() -> int:
    targets = []
    for arm in ("3a", "3b", "3c", "3d"):
        p = PROD / f"paper_{arm}.sqlite3"
        if p.exists():
            targets.append(p)
    for base in (EVID / "isolated",
                 PROJ / "evidence" / "PARALLEL-STRATEGY-01-WORK" / "isolated"):
        if base.exists():
            targets += sorted(base.glob("**/ledger/paper_*.sqlite3"))
    res = [scan(p) for p in targets]
    total_new = sum(r.get("new_scheme_rows", 0) for r in res)
    total_viol = sum(r.get("violation_n", 0) for r in res)
    rec = {"generated_utc": dt.datetime.now(UTC).isoformat(),
           "databases_n": len(res), "results": res,
           "new_scheme_rows_total": total_new,
           "violations_total": total_viol,
           "verdict": "PASS" if total_viol == 0 else "FAIL"}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "12_ledger_invariant.json").write_text(
        json.dumps(rec, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps({"databases_n": len(res), "new_scheme_rows_total": total_new,
                      "violations_total": total_viol, "verdict": rec["verdict"],
                      "detail": [{"path": r["path"].split("威科夫因子共振趋势/")[-1],
                                  "new_rows": r.get("new_scheme_rows"),
                                  "viol": r.get("violation_n")} for r in res]},
                     ensure_ascii=False, indent=2))
    return 0 if total_viol == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
