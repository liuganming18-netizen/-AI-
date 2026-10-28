#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""04 · 本单**新增判据**的负向用例：证明它们真的会拦住，不是恒真装饰。

为什么必须单独做：新增的两条前视保护
  · `realtime_fill_without_available_time`（声称当场成交却拿不出可得时刻）
  · `data_available_after_read_no_lookahead`（可得时刻晚于读取时刻）
如果只跑正向用例，它们永远不触发，等于没写。**必须构造会失败的条件。**

构造方式：**合成行情库**（只复制生产库的 schema，不复制任何数据），
在里面插入一根 `realtime_klines` 行，`received_at` 按用例给值。
⇒ 这样能精确控制「数据可得时刻」，而生产行情库只能读不能写。
**明确标注：这些是合成行，不是真实行情**；真实行情的正向证据在 `03_boundary.json`。

用例：
  N1 received_at=NULL            ⇒ 期望 reject `realtime_fill_without_available_time`
  N2 received_at=now+3600s       ⇒ 期望 reject `data_available_after_read_no_lookahead`
  N3 实时行已是更晚一根           ⇒ 期望 reject `fill_point_passed_no_backfill`
  N4 实时表无该标的 + 无闭 bar    ⇒ 期望 `pending_future_fill`（等待，不伪造）
  N0 正向对照：received_at 正常   ⇒ 期望**成交**（证明同一套构造下判据不会误杀）
"""
from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

UTC = dt.timezone.utc
PROJ = Path("/Users/boos/Desktop/威科夫因子共振趋势")
EVID = PROJ / "evidence" / "MAINLINE-01-WORK"
ISO = EVID / "isolated"
OUT = EVID / "out"
SCRIPTS = EVID / "scripts"
BACKEND = PROJ / "框架" / "backend"
MARKET_DB = PROJ / "数据" / "市场数据" / "db" / "market_v2.sqlite3"
PROD_LEDGER = PROJ / "数据" / "paper"
PROD_LIVE = PROJ / "框架" / "backend" / "live" / "live_signals.sqlite3"
M15 = 900_000
SYMBOL = "ETHUSDT"


def iso(ms):
    return None if ms is None else dt.datetime.fromtimestamp(int(ms) / 1000, UTC).isoformat()


def now_ms():
    return int(dt.datetime.now(UTC).timestamp() * 1000)


def align15(ms: int) -> int:
    return ((int(ms) + M15 - 1) // M15) * M15


def build_synthetic_market(dst: Path) -> dict:
    """只复制生产库的 **schema**（表→视图→索引顺序），不复制任何行。"""
    if dst.exists():
        dst.unlink()
    src = sqlite3.connect(f"file:{MARKET_DB}?mode=ro", uri=True, timeout=15)
    rows = src.execute(
        "SELECT type,name,sql FROM sqlite_master WHERE sql IS NOT NULL "
        "AND name NOT LIKE 'sqlite_%'").fetchall()
    src.close()
    order = {"table": 0, "view": 1, "index": 2, "trigger": 3}
    rows.sort(key=lambda r: order.get(r[0], 9))
    con = sqlite3.connect(str(dst))
    made = {"table": 0, "view": 0, "index": 0, "trigger": 0, "failed": []}
    for typ, name, sql in rows:
        try:
            con.execute(sql)
            made[typ] = made.get(typ, 0) + 1
        except sqlite3.Error as exc:
            made["failed"].append({"type": typ, "name": name, "err": str(exc)[:160]})
    con.commit()
    con.close()
    made["db"] = str(dst)
    made["size"] = dst.stat().st_size
    return made


def insert_realtime_row(db: Path, *, open_time: int, open_price: float,
                        received_at: int | None, symbol: str = SYMBOL) -> None:
    con = sqlite3.connect(str(db))
    cols = {r[1] for r in con.execute("PRAGMA table_info(realtime_klines)")}
    rec = {
        "exchange": "binance", "market": "um", "symbol": symbol, "timeframe": "15m",
        "open_time": int(open_time), "close_time": int(open_time) + M15 - 1,
        "open": float(open_price), "high": float(open_price) * 1.001,
        "low": float(open_price) * 0.999, "close": float(open_price),
        "volume": 1.0, "quote_volume": 1e9, "is_closed": 0,
        "event_time": int(received_at) if received_at else None,
        "received_at": (int(received_at) if received_at is not None else None),
        "source": "synthetic_mainline01", "transport": "synthetic",
    }
    rec = {k: v for k, v in rec.items() if k in cols}
    # 补齐 NOT NULL 且无默认值的列（`updated_at` 这类），否则插入被约束拒绝
    info = {r[1]: r for r in con.execute("PRAGMA table_info(realtime_klines)")}
    for name, row in info.items():
        if name in rec:
            continue
        notnull, dflt, typ = row[3], row[4], (row[2] or "").upper()
        if notnull and dflt is None:
            rec[name] = int(open_time) if "INT" in typ else ""
    con.execute(f"INSERT OR REPLACE INTO realtime_klines({','.join(rec)}) "
                f"VALUES({','.join('?' * len(rec))})", tuple(rec.values()))
    con.commit()
    con.close()


def build_ws(tag: str) -> dict:
    root = ISO / tag
    if root.exists():
        shutil.rmtree(root)
    (root / "ledger").mkdir(parents=True)
    (root / "locks").mkdir(parents=True)
    for arm in ("3a", "3b", "3c", "3d"):
        src = PROD_LEDGER / f"paper_{arm}.sqlite3"
        if src.exists():
            shutil.copy2(src, root / "ledger" / f"paper_{arm}.sqlite3")
    live = root / "live_signals.sqlite3"
    if PROD_LIVE.exists():
        shutil.copy2(PROD_LIVE, live)
    gate = root / "locks" / "paper_new_entry_gate.json"
    gate.write_text(json.dumps({
        "allow_new_entries": True, "reason": "iso_mainline_01",
        "set_by": "mainline_01_work", "since_ms": 1_792_748_844_240,
        "scope": "server_side_new_entry_only"}, ensure_ascii=False), encoding="utf-8")
    return {"root": root, "ledger": root / "ledger", "live": live,
            "gate": gate, "locks": root / "locks"}


def run_case(ws: dict, cfg: dict, market_db: Path) -> dict:
    env = dict(os.environ)
    env.update({
        "LIVE_DB": str(ws["live"]),
        "PAPER_LEDGER_DIR": str(ws["ledger"]),
        "PAPER_LOCK_DIR": str(ws["locks"]),
        "PAPER_GATE_FILE": str(ws["gate"]),
        "PAPER_LEDGER_ALLOW_INIT": "1",
        "MARKET_DB": str(market_db),
        "PYTHONPATH": str(BACKEND),
        "NO_PROXY": "127.0.0.1,localhost,::1",
        "no_proxy": "127.0.0.1,localhost,::1",
    })
    # 为什么显式设 realtime_only 且 origin 用 "test"：
    #   origin="live" + 单段式调用会撞上 `earliest_fill_before_first_request_clock`
    #   （旧信号不得续期保护，PARALLEL-01 §9 第 12 条已记录）；而 origin="test" 默认回落
    #   legacy，测不到实时路径。两者组合 ⇒ 时钟可控 + 走实时判据，测的正是本单新增的两条校验。
    env["WX_REALITY_FILL_MODE"] = "realtime_only"
    env["ISO_CASE"] = json.dumps(cfg, ensure_ascii=False)
    p = subprocess.run([sys.executable, str(SCRIPTS / "_iso_case.py")],
                       capture_output=True, text=True, env=env, timeout=180)
    line = None
    for ln in (p.stdout or "").splitlines():
        if ln.startswith("ISO_RESULT "):
            line = ln[len("ISO_RESULT "):]
    if line is None:
        return {"case": cfg.get("name"), "ok": False, "subprocess_rc": p.returncode,
                "stdout": (p.stdout or "")[-2000:], "stderr": (p.stderr or "")[-3000:]}
    try:
        return json.loads(line)
    except Exception as exc:                                   # noqa: BLE001
        return {"case": cfg.get("name"), "parse_error": str(exc), "raw": line[:2000]}


def main() -> int:
    cases = []
    syn = ISO / "synth_market" / "market_synth.sqlite3"
    syn.parent.mkdir(parents=True, exist_ok=True)
    schema = build_synthetic_market(syn)

    t = now_ms()
    # 执行点：取「上一根已开始的 bar」—— 让 earliest_fill 落在它上面
    bar_open = (t // M15) * M15
    event_time = bar_open - M15            # 信号确认 = 上一根 bar 收盘
    earliest = bar_open                    # = event_time + 15m = 执行点
    # 真实挂钟已过执行点（bar 已开盘若干秒），所以 attempt(=now) >= earliest ✓
    base = {
        "arm": "3a", "symbol": SYMBOL, "direction": "long", "atr": 500.0,
        "stop_price": None, "event_time": event_time, "available_at": event_time,
        "origin": "test", "stoploss_s": 0.05, "trail_d": 0.05,
        "config_fingerprint": "82dec362c182", "model_version": "FT-LOW4H-EXP-V2.2",
        "attempt_ms": t,
    }
    meta = {"synthetic_market": schema, "exec_point_ms": earliest, "exec_point_utc": iso(earliest),
            "event_time_ms": event_time, "event_time_utc": iso(event_time),
            "attempt_ms": t, "attempt_utc": iso(t),
            "note": "合成行情库只复制 schema；realtime_klines 行是本脚本插入的**合成行**"}

    def one(name, *, received_at, row_open_time, expect, tag, price=2500.0, note=""):
        ws = build_ws(tag)
        db = syn
        # 每个用例独立一份合成库副本，避免互相污染
        db = ISO / tag / "market_synth.sqlite3"
        shutil.copy2(syn, db)
        if row_open_time is not None:
            insert_realtime_row(db, open_time=row_open_time, open_price=price,
                                received_at=received_at)
        cfg = dict(base)
        cfg.update({"name": name, "uid": f"mainline01_{tag}_{t}"})
        res = run_case(ws, cfg, db)
        r = (res.get("result") or {})
        actual = r.get("reason") if not r.get("ok") else "filled"
        ok = (actual == expect) if expect != "filled" else bool(r.get("ok"))
        cases.append({
            "case": name, "tag": tag, "expect": expect, "actual": actual,
            "pass": ok, "note": note,
            "synthetic_row": {"open_time": row_open_time, "open_time_utc": iso(row_open_time),
                              "received_at": received_at, "received_at_utc": iso(received_at),
                              "open": price},
            "result": r,
            "resolved": res.get("resolved"),
            "error": res.get("error"),
        })
        print(f"[{'PASS' if ok else 'FAIL'}] {name}: expect={expect} actual={actual}", flush=True)

    # N0 正向对照：可得时刻正常（略早于现在）⇒ 应成交
    one("N0_forward_control", received_at=t - 5000, row_open_time=earliest,
        expect="filled", tag="n0_forward",
        note="同一套合成构造下判据不误杀 —— 没有这条，N1/N2 的拒绝就无法排除「构造本身有问题」")
    # N1 received_at 缺失
    one("N1_no_available_time", received_at=None, row_open_time=earliest,
        expect="realtime_fill_without_available_time", tag="n1_no_avail")
    # N2 可得时刻在未来
    one("N2_available_in_future", received_at=t + 3_600_000, row_open_time=earliest,
        expect="data_available_after_read_no_lookahead", tag="n2_future_avail")
    # N3 实时行已是更晚一根
    one("N3_exec_point_passed", received_at=t - 1000, row_open_time=earliest + M15,
        expect="fill_point_passed_no_backfill", tag="n3_passed")
    # N4 实时表无该标的（不插行）+ 合成库无闭 bar ⇒ 等待，不伪造
    one("N4_no_realtime_row", received_at=None, row_open_time=None,
        expect="pending_future_fill", tag="n4_no_row")

    rec = {"generated_utc": dt.datetime.now(UTC).isoformat(), "meta": meta,
           "cases": cases,
           "verdict": "PASS" if all(c["pass"] for c in cases) else "FAIL",
           "pass_n": sum(1 for c in cases if c["pass"]), "total_n": len(cases)}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "04_negative.json").write_text(
        json.dumps(rec, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps({"verdict": rec["verdict"], "pass_n": rec["pass_n"],
                      "total_n": rec["total_n"]}, ensure_ascii=False))
    return 0 if rec["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
