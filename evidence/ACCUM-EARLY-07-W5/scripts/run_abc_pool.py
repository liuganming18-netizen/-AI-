# -*- coding: utf-8 -*-
"""ACCUM-EARLY-07-W5｜甲/乙/丙 全币池同口径回测跑分器（唯一新增脚本，不改任何策略/框架代码）

它做什么
--------
用**同一份行情、同一套冻结账户成本底座、同一窗口、同一币池**，把
甲（4a）/ 乙（4b）/ 丙（4c）三组 ACCUM 提前入场配置各跑一遍全币池回测，
再从三本隔离账本里算出同一组核心指标，输出一张可拍板的对比表。

为什么这样接
------------
1. **不自己写撮合与算术**：观察→候选→委托→成交→退出→账本全部走既有唯一入口
   `accum_early_entry.research_run()`；成本、滑点、手续费、参与率、资金费
   全部由 `paper_engine`（生效视图 `82dec362c182`）决定。本脚本只做编排与汇总。
2. **三组同时在一个进程里跑**：`research_run(schemes=("4a","4b","4c"))` 本身
   就按标的单次推进、三个观察者共用同一份 bar 缓存 ⇒ 「同口径」是结构保证，
   不是靠事后对齐参数得来的。三本账本、三个账户（paper43_4a/4b/4c）互相独立。
3. **物理隔离**：账本、观察库、实时库、闸门全部落在 `runs/<run_id>/` 之下；
   `PAPER_LEDGER_DIR` 指向生产目录时直接拒跑（`ledger_isolation()` 也会再拒一次）。
4. **身份可复核**：跑前跑后各算一次共享文件哈希；配置、引擎生效视图、
   池口径、窗口、代码版本全部写进 `out/运行身份.json`。跑后哈希与跑前不一致
   ⇒ 本次结果标 `INVALID_CODE_DRIFT`，不给结论。

必须说清的三件事（脚本自己会打印，报告里也会原样带上）
----------------------------------------------------
· 现在**没有**用户批准的配置。唯一字段齐全的是 06-W2 的 `fixture` 自测配置，
  13 项待决参数用的是占位值 ⇒ 跑出来的不是「甲乙丙策略绩效」，只能回答
  「这套阈值下链路会怎么走」。
· 逐笔盈亏 ≠ 组合绩效。本脚本给的是**账户账本口径**（含仓位上限与现金约束），
  但回撤是「已实现口径」——期末未平仓只计数、不估值、不进净额。
· 样本量为 0 时不许下任何「哪组更好」的结论。

用法
----
  # 只数候选（不撮合、不出成绩），用于确认这套阈值到底有没有样本
  python run_abc_pool.py --scan-only --max-boxes 12

  # 正式跑分（三组一起，出对比表）
  python run_abc_pool.py --allow-fixture --max-boxes 12

  # 指定别的配置（例如 W1~W4 批下来的 approved 配置）
  python run_abc_pool.py --config /path/approved_rules.json

  # 小样本自检
  python run_abc_pool.py --symbols BTCUSDT,ETHUSDT --limit 2 --allow-fixture
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve()
W5 = HERE.parents[1]                       # evidence/ACCUM-EARLY-07-W5
PROJECT = HERE.parents[3]
BACKEND = PROJECT / "框架" / "backend"
DEFAULT_CONFIG = PROJECT / "evidence" / "ACCUM-EARLY-06-W2" / "01" / "config" / "fixture_rules_06w2.json"
MARKET_DB = PROJECT / "数据" / "市场数据" / "db" / "market_v2.sqlite3"
ARCHIVE = PROJECT / "数据" / "市场数据" / "parquet"

#: 共享文件身份清单（跑前跑后对撞；漂移即判本次结果失效）
SHARED_FILES = [
    "框架/backend/accum_early_entry.py",
    "框架/backend/replay_calc.py",
    "框架/backend/paper_engine.py",
    "框架/backend/live_store.py",
    "框架/backend/research_15m.py",
    "框架/backend/accum_research.py",
    "框架/backend/observe_store.py",
    "框架/backend/pool_scope.py",
]

DAY = 86_400_000


# ══════════════════════════════════════════════════════════════════════
# 一、小工具
# ══════════════════════════════════════════════════════════════════════
def ms_of(s: str) -> int:
    import calendar
    return int(calendar.timegm(time.strptime(s, "%Y-%m-%dT%H:%M:%SZ")) * 1000)


def iso(ms) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(int(ms) / 1000))


def sha256_file(p: Path) -> dict:
    p = Path(p)
    if not p.exists():
        return {"path": str(p), "exists": False}
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(8 * 1024 * 1024), b""):
            h.update(chunk)
    return {"path": str(p), "exists": True, "bytes": p.stat().st_size,
            "sha256": h.hexdigest()}


def code_identity() -> dict:
    return {rel: sha256_file(PROJECT / rel) for rel in SHARED_FILES}


def pool_symbols() -> tuple[list, dict]:
    """币池 = 项目唯一事实源 `pool_scope`（crypto_only_v1，526 个，已排除指数）。"""
    sys.path.insert(0, str(BACKEND))
    import pool_scope as PS
    syms = PS.binance_scope_symbols()
    return list(syms), PS.scope_meta()


# ══════════════════════════════════════════════════════════════════════
# 二、子进程（唯一持有账本连接的地方）
# ══════════════════════════════════════════════════════════════════════
WORKER_SRC = r'''
# -*- coding: utf-8 -*-
"""跑分 worker：只做一次 research_run，把结果摘要写回 json。"""
import json, sys, time
sys.path.insert(0, r"%(project)s")
sys.path.insert(0, r"%(backend)s")
import accum_early_entry as A

job = json.load(open(sys.argv[1], encoding="utf-8"))

class LimitedFeed(A.DataFeed):
    """同一时刻只保留当前标的的 bar 缓存。

    为什么需要：DataFeed 永不淘汰缓存，526 个标的 × (4h+1h+15m) ≈ 800 万根 bar，
    全留在内存里会直接把进程打爆。只清缓存不改任何读数逻辑——每个标的的窗口
    仍是同一份 MergedReader 读出来的同一批 bar。
    """
    def load(self, tf, symbol, start_ms, end_ms):
        if not job.get("no_cache_limit"):
            for k in [k for k in self._cache if k[1] != symbol]:
                self._cache.pop(k, None); self._range.pop(k, None); self._stats.pop(k, None)
        return super().load(tf, symbol, start_ms, end_ms)

cfg = A.load_config(job["config"])
brief = {k: cfg.get(k) for k in ("present", "ready", "open_allowed", "open_scope",
                                 "kind", "approved", "blocked_by", "config_sha256",
                                 "approval_ref")}
brief["missing_n"] = len(cfg.get("missing") or [])
brief["missing_head"] = (cfg.get("missing") or [])[:8]
brief["unsupported"] = cfg.get("unsupported") or []
brief["fixed_rules_ok"] = (cfg.get("fixed_rules") or {}).get("ok")
brief["economic_mismatch"] = (cfg.get("economic_caliber") or {}).get("declared_mismatch")
brief["engine"] = (cfg.get("fixed_rules") or {}).get("engine")
brief["economic_engine"] = (cfg.get("economic_caliber") or {}).get("engine")

if not cfg.get("ready"):
    json.dump({"fatal": "config_not_ready", "config": brief},
              open(job["out"], "w", encoding="utf-8"), ensure_ascii=False)
    raise SystemExit(0)
if not cfg.get("open_allowed") and job["open"]:
    json.dump({"fatal": "open_not_allowed", "config": brief},
              open(job["out"], "w", encoding="utf-8"), ensure_ascii=False)
    raise SystemExit(0)

feed = LimitedFeed()
store = A.open_store(job["observe_db"])
t0 = time.time()
try:
    res = A.research_run(config=job["config"], symbols=job["symbols"],
                         start_ms=job["start_ms"], end_ms=job["end_ms"],
                         schemes=("4a", "4b", "4c"),
                         open_candidates=job["open"], drive_exits=job["exits"],
                         max_boxes=job["max_boxes"], walk_days=job["walk_days"],
                         observe_db=job["observe_db"], feed=feed, store=store)
finally:
    try:
        store.close()
    finally:
        feed.close()

el = time.time() - t0
# 只留摘要，避免把 526 个标的的全量事件数组塞进 json
per = []
for r in res["per_symbol"]:
    per.append({
        "symbol": r["symbol"],
        "boxes_formed": r["boxes_formed"], "boxes_used": r["boxes_used"],
        "n_candidates": len(r["candidates"]),
        "cand_by_scheme": {s: sum(1 for c in r["candidates"] if c["scheme"] == s)
                           for s in ("4a", "4b", "4c")},
        "actions": r["actions"],
        "data": {"4h": r["bars"]["4h"], "1h": r["bars"]["1h"],
                 "4h_gaps": r["bars"]["4h_gaps"], "1h_gaps": r["bars"]["1h_gaps"]},
        "candidates": [{"scheme": c["scheme"], "signal_uid": c["signal_uid"],
                        "confirm_close_ms": c["confirm_close_ms"],
                        "entry_bar_open_ms": c.get("entry_bar_open_ms"),
                        "resistance": c.get("resistance"),
                        "entry_ref_price": c.get("entry_ref_price"),
                        "open_result": {k: (c.get("open_result") or {}).get(k)
                                        for k in ("ok", "reason", "called_paper_engine")}}
                       for c in r["candidates"]],
    })
out = {
    "config": brief,
    "elapsed_s": round(el, 2),
    "window": res["window"],
    "n_symbols": len(res["symbols"]),
    "n_errors": len(res["errors"]), "errors": res["errors"][:20],
    "flags": res["flags"],
    "identity": {k: res["identity"][k] for k in
                 ("entry_version", "config_schema", "rule_version", "exit_rules",
                  "generated_utc", "isolation", "schemes")},
    "per_symbol": per,
    "n_exits": len(res["exits"]),
    "exits": [{k: e.get(k) for k in ("scheme", "position_id", "symbol", "exit_class",
                                     "net_pnl", "exit_ts", "gap", "trail_updates")}
              for e in res["exits"]],
    "ledgers": {s: {k: (len(v) if isinstance(v, list) else v)
                    for k, v in led.items()} for s, led in res["ledgers"].items()},
}
json.dump(out, open(job["out"], "w", encoding="utf-8"), ensure_ascii=False, default=str)
print(json.dumps({"written": job["out"], "elapsed_s": out["elapsed_s"]}, ensure_ascii=False))
'''


def isolated_env(run_dir: Path, config: Path, fixture_ok: bool) -> dict:
    (run_dir / "paper").mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    for k in ("ACCUM04_CONFIG", "ACCUM04_FIXTURE_OK"):
        env.pop(k, None)
    env.update({
        "PAPER_LEDGER_DIR": str(run_dir / "paper"),
        "PAPER_LEDGER_ALLOW_INIT": "1",
        "LIVE_DB": str(run_dir / "live_signals.sqlite3"),
        "PAPER_GATE_FILE": str(run_dir / "gate.json"),
        "MARKET_DB": str(MARKET_DB),
        "WX_ARCHIVE_DIR": str(ARCHIVE),
        "WX_MARKET_MERGE": "1",
        "ACCUM05_CONFIG": str(config),
    })
    if fixture_ok:
        env["ACCUM05_FIXTURE_OK"] = "1"
    else:
        env.pop("ACCUM05_FIXTURE_OK", None)
    return env


# ══════════════════════════════════════════════════════════════════════
# 三、从三本账本算指标
# ══════════════════════════════════════════════════════════════════════
SCHEME_LABEL = {"4a": "甲（4a 收上压制位即入）",
                "4b": "乙（4b 下一根守住再入）",
                "4c": "丙（4c 等回抽再确认）"}


def _q(con, sql, args=()):
    return [dict(r) for r in con.execute(sql, args)]


def ledger_metrics(paper_dir: Path, scheme: str, window_days: float) -> dict:
    acc = f"paper43_{scheme}"
    f = paper_dir / f"paper_{scheme}.sqlite3"
    out = {"scheme": scheme, "label": SCHEME_LABEL[scheme], "ledger": str(f),
           "ledger_exists": f.exists(), "account_id": acc}
    if not f.exists():
        out["error"] = "ledger_missing"
        return out
    con = sqlite3.connect(f"file:{f}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        tabs = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        out["tables"] = sorted(tabs)

        acct = _q(con, "SELECT * FROM paper_account WHERE account_id=?", (acc,))
        init_eq = float((acct[0].get("initial_equity") if acct else None)
                        or 0.0) or None
        st = _q(con, "SELECT cash,realized_pnl,fees_paid,funding_paid "
                     "FROM paper_state WHERE account_id=?", (acc,))
        state = st[0] if st else {}

        # 已实现：**分子分母都过滤 voided**（旧审计函数只过滤分母，会把作废行算进净额）
        real = _q(con, "SELECT position_id,net_pnl,exit_reason,entry_ts,exit_ts,"
                       "funding_paid FROM paper_realized WHERE account_id=? "
                       "AND COALESCE(voided,0)=0 ORDER BY exit_ts, position_id", (acc,))
        n = len(real)
        pnls = [float(r["net_pnl"] or 0.0) for r in real]
        wins = [x for x in pnls if x > 0]
        losses = [x for x in pnls if x <= 0]
        net = sum(pnls)
        gp = sum(wins)
        gl = abs(sum(losses))

        pos = _q(con, "SELECT position_id,symbol,status,voided,entry_ts,exit_ts,"
                      "entry_fee,exit_fee,funding_paid,exit_reason,qty,entry_price "
                      "FROM paper_position WHERE account_id=? "
                      "AND COALESCE(voided,0)=0", (acc,))
        n_open = sum(1 for p in pos if p["status"] == "open")
        fees = sum(float(p["entry_fee"] or 0) + float(p["exit_fee"] or 0) for p in pos)
        funding = sum(float(p["funding_paid"] or 0) for p in pos)

        # 已实现权益曲线（按退出时刻排序；期末未平仓不估值，不进曲线）
        eq = init_eq or 0.0
        curve, peak, mdd, mdd_at = [], eq, 0.0, None
        for r in real:
            eq += float(r["net_pnl"] or 0.0)
            peak = max(peak, eq)
            dd = peak - eq
            if dd > mdd:
                mdd, mdd_at = dd, r["exit_ts"]
            curve.append({"ts": r["exit_ts"], "equity": round(eq, 10)})

        reasons = {}
        for r in real:
            k = r["exit_reason"] or "unknown"
            reasons[k] = reasons.get(k, 0) + 1
        hold = [(int(r["exit_ts"]) - int(r["entry_ts"])) / 3_600_000.0
                for r in real if r["exit_ts"] and r["entry_ts"]]

        rng = (net / init_eq) if init_eq else None
        ann = None
        if rng is not None and window_days and window_days > 0:
            ann = (1.0 + rng) ** (365.0 / window_days) - 1.0 if rng > -1 else None

        out.update({
            "initial_equity": init_eq,
            "state": state,
            "n_trades": n,
            "n_wins": len(wins),
            "n_losses": len(losses),
            "win_rate": (len(wins) / n) if n else None,
            "net_pnl": round(net, 10),
            "gross_profit": round(gp, 10),
            "gross_loss": round(gl, 10),
            "profit_factor": (round(gp / gl, 6) if gl > 0 else None),
            "avg_win": (round(gp / len(wins), 10) if wins else None),
            "avg_loss": (round(-gl / len(losses), 10) if losses else None),
            "payoff_ratio": (round((gp / len(wins)) / (gl / len(losses)), 6)
                             if wins and losses and gl > 0 else None),
            "fees_total": round(fees, 10),
            "funding_total": round(funding, 10),
            "max_drawdown": round(mdd, 10),
            "max_drawdown_pct": (round(mdd / (init_eq or 1.0), 8) if init_eq else None),
            "max_drawdown_at": mdd_at,
            "range_return": (round(rng, 8) if rng is not None else None),
            "annualized_return": (round(ann, 8) if ann is not None else None),
            "n_open_at_end": n_open,
            "n_positions_rows": len(pos),
            "avg_holding_hours": (round(sum(hold) / len(hold), 3) if hold else None),
            "exit_reasons": reasons,
            "equity_curve": curve,
            "reconcile": {
                "state_realized": state.get("realized_pnl"),
                "sum_realized": round(net, 10),
                "state_fees": state.get("fees_paid"),
                "sum_fees": round(fees, 10),
                "state_funding": state.get("funding_paid"),
                "sum_funding": round(funding, 10),
            },
        })
        return out
    finally:
        con.close()


def chain_diagnosis(observe_db: Path) -> dict:
    """候选为什么是 0：从观察库直接数卡点（只读）。"""
    if not observe_db.exists():
        return {"exists": False}
    con = sqlite3.connect(f"file:{observe_db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        out = {"exists": True, "n_events": 0, "by_scheme": {}}
        out["n_events"] = con.execute("SELECT COUNT(*) FROM observe_event").fetchone()[0]
        out["n_candidates"] = con.execute(
            "SELECT COUNT(*) FROM accum05_candidate").fetchone()[0]
        for s in ("4a", "4b", "4c"):
            rows = _q(con, "SELECT to_state, reason, payload_json FROM observe_transition "
                           "WHERE event_id LIKE ?", (f"ev_{s}_%",))
            term = {}
            absorb_fail = absorb_pass = 0
            no_res = 0
            for r in rows:
                if r["to_state"] in ("EXPIRED", "INVALID", "CANDIDATE"):
                    k = f'{r["to_state"]}｜{(r["reason"] or "")[:48]}'
                    term[k] = term.get(k, 0) + 1
                if r["to_state"] == "NO_RESISTANCE":
                    no_res += 1
                p = r["payload_json"] or ""
                if '"absorption"' in p:
                    if '"ok": false' in p:
                        absorb_fail += 1
                    elif '"ok": true' in p:
                        absorb_pass += 1
            n_ev = con.execute("SELECT COUNT(*) FROM observe_event WHERE event_id LIKE ?",
                               (f"ev_{s}_%",)).fetchone()[0]
            out["by_scheme"][s] = {
                "n_events": n_ev,
                "n_candidates": con.execute(
                    "SELECT COUNT(*) FROM accum05_candidate WHERE scheme=?", (s,)
                ).fetchone()[0],
                "absorption_fail": absorb_fail,
                "absorption_pass": absorb_pass,
                "no_resistance": no_res,
                "terminal_top": dict(sorted(term.items(), key=lambda x: -x[1])[:6]),
            }
        return out
    finally:
        con.close()


# ══════════════════════════════════════════════════════════════════════
# 四、主流程
# ══════════════════════════════════════════════════════════════════════
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="甲/乙/丙 全币池同口径回测跑分器")
    ap.add_argument("--config", default=str(DEFAULT_CONFIG))
    ap.add_argument("--allow-fixture", action="store_true",
                    help="配置 kind=fixture 时必须显式给出（否则拒跑）")
    ap.add_argument("--symbols", default="", help="逗号分隔；缺省=全池 526")
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 个标的（自检用）")
    ap.add_argument("--start", default="2026-06-27T08:00:00Z")
    ap.add_argument("--end", default="2026-10-26T09:00:00Z")
    ap.add_argument("--warmup-days", type=int, default=60)
    ap.add_argument("--max-boxes", type=int, default=40,
                    help="每标的最多消费几个 4h 箱体；太小会截断窗口覆盖（12 会截断 2/3）")
    ap.add_argument("--walk-days", type=int, default=30)
    ap.add_argument("--scan-only", action="store_true", help="只数候选，不撮合不出成绩")
    ap.add_argument("--run-id", default="")
    ap.add_argument("--no-cache-limit", action="store_true")
    a = ap.parse_args(argv)

    cfg_path = Path(a.config).resolve()
    if not cfg_path.exists():
        print(json.dumps({"fatal": "config_missing", "path": str(cfg_path)},
                         ensure_ascii=False))
        return 2

    if a.symbols.strip():
        symbols = [s.strip() for s in a.symbols.split(",") if s.strip()]
        scope = {"pool_source": "explicit_symbols", "n": len(symbols)}
    else:
        symbols, scope = pool_symbols()
    if a.limit:
        symbols = symbols[: a.limit]
    if not symbols:
        print(json.dumps({"fatal": "empty_pool"}, ensure_ascii=False))
        return 2

    start_ms, end_ms = ms_of(a.start), ms_of(a.end)
    window_days = (end_ms - start_ms) / DAY
    run_id = a.run_id or time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    run_dir = W5 / "runs" / run_id
    if run_dir.exists():
        shutil.rmtree(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "gate.json").write_text(json.dumps(
        {"allow_new_entries": True, "set_by": "ACCUM-EARLY-07-W5"}), encoding="utf-8")

    worker = run_dir / "_worker.py"
    worker.write_text(WORKER_SRC % {"project": str(PROJECT), "backend": str(BACKEND)},
                      encoding="utf-8")

    code_before = code_identity()
    cfg_sha = sha256_file(cfg_path)

    job = {"config": str(cfg_path), "symbols": symbols,
           "start_ms": start_ms, "end_ms": end_ms,
           "max_boxes": a.max_boxes, "walk_days": a.walk_days,
           "open": not a.scan_only, "exits": not a.scan_only,
           "observe_db": str(run_dir / "observe.sqlite3"),
           "no_cache_limit": bool(a.no_cache_limit),
           "out": str(run_dir / "raw.json")}
    job_path = run_dir / "job.json"
    job_path.write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")

    print(json.dumps({"stage": "start", "run_id": run_id, "symbols": len(symbols),
                      "window": [a.start, a.end], "config": str(cfg_path),
                      "scan_only": bool(a.scan_only)}, ensure_ascii=False), flush=True)

    # 预检：fixture 必须显式放行（fixture 的阈值是占位值，不许悄悄当成批准配置跑）
    kind = json.loads(cfg_path.read_text(encoding="utf-8")).get("kind")
    if kind == "fixture" and not a.allow_fixture:
        print(json.dumps({"fatal": "fixture_requires_explicit_flag",
                          "hint": "加 --allow-fixture；并注意 fixture 阈值未经批准"},
                         ensure_ascii=False))
        return 3

    env = isolated_env(run_dir, cfg_path, fixture_ok=(kind == "fixture"))
    t0 = time.time()
    proc = subprocess.run([sys.executable, str(worker), str(job_path)],
                          capture_output=True, text=True, env=env,
                          cwd=str(PROJECT), timeout=8 * 3600)
    wall = time.time() - t0
    if proc.returncode != 0:
        print(json.dumps({"fatal": "worker_failed", "rc": proc.returncode,
                          "stdout": proc.stdout[-1500:], "stderr": proc.stderr[-4000:]},
                         ensure_ascii=False))
        return 4

    raw_path = run_dir / "raw.json"
    if not raw_path.exists():
        print(json.dumps({"fatal": "worker_no_output", "stderr": proc.stderr[-3000:]},
                         ensure_ascii=False))
        return 4
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    if raw.get("fatal"):
        print(json.dumps(raw, ensure_ascii=False, indent=1))
        return 5

    code_after = code_identity()
    drift = [rel for rel in SHARED_FILES
             if code_before[rel].get("sha256") != code_after[rel].get("sha256")]

    # 指标
    paper_dir = run_dir / "paper"
    metrics = {s: ledger_metrics(paper_dir, s, window_days) for s in ("4a", "4b", "4c")}
    diag = chain_diagnosis(run_dir / "observe.sqlite3")

    # 候选与成交（来自 raw）
    cand = {s: 0 for s in ("4a", "4b", "4c")}
    opened = {s: 0 for s in ("4a", "4b", "4c")}
    open_reasons = {s: {} for s in ("4a", "4b", "4c")}
    for r in raw["per_symbol"]:
        for c in r["candidates"]:
            s = c["scheme"]
            cand[s] += 1
            orr = c.get("open_result") or {}
            if orr.get("ok"):
                opened[s] += 1
            else:
                k = orr.get("reason") or "no_result"
                open_reasons[s][k] = open_reasons[s].get(k, 0) + 1

    cfg_brief = raw["config"]
    boxes_capped = sum(1 for p in raw["per_symbol"]
                       if int(p.get("boxes_used") or 0) >= a.max_boxes)
    status = "OK"
    if drift:
        status = "INVALID_CODE_DRIFT"
    elif cfg_brief.get("kind") != "approved" or not cfg_brief.get("approved"):
        status = "UNVERIFIED_CONFIG_UNAPPROVED"

    identity = {
        "run_id": run_id, "status": status,
        "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "window": {"start": a.start, "end": a.end, "days": round(window_days, 3)},
        "pool": scope, "n_symbols": len(symbols),
        "max_boxes": a.max_boxes, "boxes_capped": boxes_capped,
        "boxes_capped_ratio": round(boxes_capped / max(1, len(symbols)), 4),
        "config": {"path": str(cfg_path), "sha256": cfg_sha.get("sha256"),
                   "kind": cfg_brief.get("kind"), "approved": cfg_brief.get("approved"),
                   "approval_ref": cfg_brief.get("approval_ref"),
                   "ready": cfg_brief.get("ready"),
                   "open_allowed": cfg_brief.get("open_allowed"),
                   "open_scope": cfg_brief.get("open_scope"),
                   "missing_n": cfg_brief.get("missing_n")},
        "engine": cfg_brief.get("engine"),
        "economic_engine": cfg_brief.get("economic_engine"),
        "code_before": {k: v.get("sha256") for k, v in code_before.items()},
        "code_after": {k: v.get("sha256") for k, v in code_after.items()},
        "code_drift": drift,
        "entry_identity": raw.get("identity"),
        "worker_elapsed_s": raw.get("elapsed_s"), "wall_s": round(wall, 2),
        "n_errors": raw.get("n_errors"), "errors": raw.get("errors"),
        "scan_only": bool(a.scan_only),
        "candidates": cand, "opened": opened, "open_reject_reasons": open_reasons,
        "n_exits": raw.get("n_exits"),
        "ledger_counts": raw.get("ledgers"),
    }

    result = {"identity": identity, "metrics": metrics, "diagnosis": diag,
              "per_symbol": raw["per_symbol"]}
    out = W5 / "out"
    out.mkdir(parents=True, exist_ok=True)
    stem = "扫描_候选普查" if a.scan_only else "回测_甲乙丙对比"
    (out / f"{stem}.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=1, default=str), encoding="utf-8")

    write_table(out / f"{stem}.md", result, a)
    write_csv(out / f"{stem}.csv", result)
    (out / f"运行身份_{run_id}.json").write_text(
        json.dumps(identity, ensure_ascii=False, indent=1, default=str), encoding="utf-8")

    print(json.dumps({"stage": "done", "run_id": run_id, "status": status,
                      "candidates": cand, "opened": opened,
                      "n_exits": raw.get("n_exits"),
                      "trades": {s: metrics[s].get("n_trades") for s in metrics},
                      "code_drift": drift,
                      "out": str(out / f"{stem}.md")}, ensure_ascii=False, indent=1))
    return 0


# ══════════════════════════════════════════════════════════════════════
# 五、出表
# ══════════════════════════════════════════════════════════════════════
def _f(x, nd=2, suffix="", dash="—"):
    if x is None:
        return dash
    if isinstance(x, str):
        return x
    return f"{x:,.{nd}f}{suffix}"


def write_table(path: Path, result: dict, a) -> None:
    ident = result["identity"]
    m = result["metrics"]
    diag = result["diagnosis"]
    scan_only = bool(ident.get("scan_only"))
    L = []
    L.append("# 甲 vs 乙 vs 丙 · 全币池同口径" + ("候选普查（未撮合）" if scan_only
                                                 else "回测成绩"))
    L.append("")
    L.append(f"- 运行：`{ident['run_id']}`，{ident['generated_utc']}，"
             f"耗时 {ident['wall_s']}s（worker {ident['worker_elapsed_s']}s）")
    L.append(f"- 窗口：{ident['window']['start']} → {ident['window']['end']}"
             f"（{ident['window']['days']:.1f} 天）")
    L.append(f"- 币池：{ident['pool'].get('pool_source')}，{ident['n_symbols']} 个标的")
    L.append(f"- 每标的箱体上限：{ident.get('max_boxes')}，触顶标的 "
             f"{ident.get('boxes_capped')} 个（{ident.get('boxes_capped_ratio', 0) * 100:.1f}%）"
             "——触顶说明该上限可能截断了窗口覆盖")
    L.append(f"- 配置：`{ident['config']['path']}`")
    L.append(f"  - sha256 `{(ident['config']['sha256'] or '')[:16]}`，"
             f"kind=`{ident['config']['kind']}`，approved=`{ident['config']['approved']}`，"
             f"open_scope=`{ident['config']['open_allowed']}`")
    eng = ident.get("engine") or {}
    L.append(f"- 成本底座（引擎生效视图）：`{eng.get('view_sha256')}` "
             f"{eng.get('strategy_version')}，来源 `{eng.get('_source')}`")
    L.append(f"- 状态：**{ident['status']}**")
    if ident["code_drift"]:
        L.append(f"- ⚠ 代码漂移：{ident['code_drift']} ⇒ 本次结果判失效")
    if ident["status"] == "UNVERIFIED_CONFIG_UNAPPROVED":
        L.append("")
        L.append("> **这张表不是甲乙丙的策略绩效。** 当前配置 `kind=fixture`、"
                 "`approved=false`，13 项待决参数用的是占位值；它只能回答"
                 "「这套阈值下链路怎么走」，不能用来判断哪组更值得上线。")
    L.append("")
    L.append("## 一、成绩对比表")
    L.append("")
    head = ["指标", "甲 4a", "乙 4b", "丙 4c"]
    L.append("| " + " | ".join(head) + " |")
    L.append("|" + "---|" * len(head))
    rows = [
        ("扣费后净收益（USDT）", lambda d: _f(d.get("net_pnl"), 4)),
        ("区间收益率", lambda d: _f((d.get("range_return") or 0) * 100
                                    if d.get("range_return") is not None else None, 4, "%")),
        ("年化收益率（线性外推）", lambda d: _f((d.get("annualized_return") or 0) * 100
                                          if d.get("annualized_return") is not None else None, 2, "%")),
        ("总交易笔数（已平仓）", lambda d: _f(d.get("n_trades"), 0)),
        ("胜率", lambda d: _f((d.get("win_rate") or 0) * 100
                             if d.get("win_rate") is not None else None, 2, "%")),
        ("盈亏比（均盈/均亏）", lambda d: _f(d.get("payoff_ratio"), 3)),
        ("利润因子（总盈/总亏）", lambda d: _f(d.get("profit_factor"), 3)),
        ("最大回撤（已实现口径，USDT）", lambda d: _f(d.get("max_drawdown"), 4)),
        ("最大回撤占初始权益", lambda d: _f((d.get("max_drawdown_pct") or 0) * 100
                                     if d.get("max_drawdown_pct") is not None else None, 3, "%")),
        ("手续费总额（USDT）", lambda d: _f(d.get("fees_total"), 4)),
        ("资金费总额（USDT）", lambda d: _f(d.get("funding_total"), 4)),
        ("平均持仓（小时）", lambda d: _f(d.get("avg_holding_hours"), 2)),
        ("期末未平仓笔数", lambda d: _f(d.get("n_open_at_end"), 0)),
        ("初始权益（USDT）", lambda d: _f(d.get("initial_equity"), 2)),
        ("候选数 → 实际成交", lambda d: f"{ident['candidates'].get(d['scheme'], 0)} → "
                                  f"{ident['opened'].get(d['scheme'], 0)}"),
    ]
    for name, fn in rows:
        L.append("| " + name + " | " + " | ".join(fn(m[s]) for s in ("4a", "4b", "4c")) + " |")
    L.append("")
    L.append("## 二、退出原因分布")
    L.append("")
    L.append("| 退出原因 | 甲 4a | 乙 4b | 丙 4c |")
    L.append("|---|---|---|---|")
    allr = sorted({k for s in m for k in (m[s].get("exit_reasons") or {})})
    if not allr:
        L.append("| （无已平仓交易） | 0 | 0 | 0 |")
    for r in allr:
        L.append(f"| {r} | " + " | ".join(
            str((m[s].get("exit_reasons") or {}).get(r, 0)) for s in ("4a", "4b", "4c")) + " |")
    L.append("")
    L.append("## 三、账本对账（账本自证，不是结论）")
    L.append("")
    L.append("| 项 | 甲 4a | 乙 4b | 丙 4c |")
    L.append("|---|---|---|---|")
    for key, label in (("state_realized", "paper_state.realized_pnl"),
                       ("sum_realized", "逐笔净额合计"),
                       ("state_fees", "paper_state.fees_paid"),
                       ("sum_fees", "逐笔手续费合计"),
                       ("state_funding", "paper_state.funding_paid"),
                       ("sum_funding", "逐笔资金费合计")):
        L.append(f"| {label} | " + " | ".join(
            _f((m[s].get("reconcile") or {}).get(key), 6) for s in ("4a", "4b", "4c")) + " |")
    L.append("")
    L.append("## 四、为什么是这个结果（链路卡点，只读观察库）")
    L.append("")
    if diag.get("exists"):
        L.append(f"- 观察事件总数：{diag.get('n_events')}，候选总数：{diag.get('n_candidates')}")
        L.append("")
        L.append("| 组 | 事件数 | 候选数 | 承接通过 | 承接未通过 | 找不到合格压制位 | 主要终止原因 |")
        L.append("|---|---|---|---|---|---|---|")
        for s in ("4a", "4b", "4c"):
            d = diag["by_scheme"].get(s) or {}
            top = "；".join(f"{k}×{v}" for k, v in
                           list((d.get("terminal_top") or {}).items())[:2]) or "—"
            L.append(f"| {s} | {d.get('n_events', 0)} | {d.get('n_candidates', 0)} | "
                     f"{d.get('absorption_pass', 0)} | {d.get('absorption_fail', 0)} | "
                     f"{d.get('no_resistance', 0)} | {top} |")
    else:
        L.append("- 观察库不存在（未落盘）")
    L.append("")
    L.append("## 五、这次能不能拍板")
    L.append("")
    tot = sum((m[s].get("n_trades") or 0) for s in m)
    if scan_only:
        cc = ident.get("candidates") or {}
        L.append("**本次只数候选，没有撮合，所以没有成绩。** "
                 f"候选数 甲 {cc.get('4a', 0)} / 乙 {cc.get('4b', 0)} / 丙 {cc.get('4c', 0)}；"
                 "有候选不等于能成交——下单还会被参与率、单名上限、总敞口、现金、"
                 "最小可下单量逐笔拦一遍，那些只有真正跑一次才看得到。")
    elif tot == 0:
        L.append("**不能。三组已平仓交易合计 0 笔 ⇒ 没有任何可比样本，"
                 "「哪组更好」这个问题在这份配置下无解。**")
        L.append("")
        L.append("要让这张表有内容，先要有人给阈值（待决项，脚本不代选）："
                 "`sell_pressure_threshold`（承接/卖压倍数）、"
                 "`local_resistance_definition`（局部压制位取法）、"
                 "`observe_deadline_hours`、`pullback_deadline_hours`（只影响丙）。"
                 "阈值一到位，把 `--config` 换成批准配置重跑即可，本脚本不用改。")
    else:
        L.append(f"三组已平仓合计 {tot} 笔。样本量、是否同一批事件配对、"
                 "以及阈值是否已批准，三件事同时成立才能定稿；"
                 "否则只能当探索性对照。")
    L.append("")
    L.append("### 口径声明（不许省）")
    L.append("")
    L.append("1. 净收益 = 账本 `paper_realized.net_pnl` 合计（已扣手续费、滑点、资金费），"
             "`voided=1` 的行分子分母都不计。")
    L.append("2. 最大回撤 = **已实现权益曲线**的峰谷差，按退出时刻排序；"
             "期末未平仓只计数、不估值、不进曲线 ⇒ 不是逐 bar 市值回撤。")
    L.append("3. 年化是把区间收益线性外推到 365 天，样本不足一年时它只是换算，不是预测。")
    L.append("4. 三组共用同一份行情缓存、同一窗口、同一成本底座；"
             "差异只来自 4a/4b/4c 的入场确认规则。")
    L.append("5. 这是**账户账本口径**（含仓位/现金约束），不是逐笔累加；"
             "也不能据此宣称自然行情成交已验证。")
    path.write_text("\n".join(L) + "\n", encoding="utf-8")


def write_csv(path: Path, result: dict) -> None:
    import csv
    m = result["metrics"]
    keys = ["scheme", "label", "n_trades", "n_wins", "n_losses", "win_rate",
            "net_pnl", "gross_profit", "gross_loss", "profit_factor", "payoff_ratio",
            "avg_win", "avg_loss", "fees_total", "funding_total", "max_drawdown",
            "max_drawdown_pct", "range_return", "annualized_return",
            "n_open_at_end", "avg_holding_hours", "initial_equity"]
    with path.open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=keys, extrasaction="ignore")
        w.writeheader()
        for s in ("4a", "4b", "4c"):
            w.writerow({k: m[s].get(k) for k in keys})


if __name__ == "__main__":
    raise SystemExit(main())
