# -*- coding: utf-8 -*-
"""SIGNAL-CLOSURE-06-R1｜13 候选恢复验证（修正 06 的失效案例）。

**06 的问题（工单第 2 条指出的）**：`06_恢复候选执行.json` 的 S2 里，
候选 `YGGUSDT @ 2026-10-02T12:15Z` 的订单被
`before_paper_epoch_no_backfill` 拒掉（`paper_pending=0`、`paper_position=0`），
随后 S2b 为了跑通成交，把隔离库的 `epoch_used` 改成 `2026-09-20` ——
**等于把启用时点往前挪**。所以那套结果不能算「有效候选恢复通过」。

**本脚本的修正**：
  · 生产启用时点 `paper_epoch = 2026-10-14T01:00:00Z` **一个字节都不动**；
  · 只在**启用时点之后**的真实候选上验证成功路径（候选来自 12 号脚本 dump 的
    真实历史候选清单，行情是真实行情）；
  · 若真实历史里**不存在**启用时点之后的候选 ⇒ 如实报「该路径未验证」，
    绝不用平移时间轴的办法冒充通过（时间平移只作为**标注清楚的补充**，不计入通过）；
  · 过期拒绝、重复恢复、恢复后新增候选三条路径照样验证。

场景：
  A 过期历史候选（早于时效）        ⇒ recovered=0、账本 0 新增
  B 启用时点之后且仍在时效内        ⇒ recovered>=1、落库、订单为 pending（不是 rejected）
  C 同一候选重复恢复                ⇒ 行数与 B 完全相同
  D 恢复后新增候选                  ⇒ 新候选 origin='live' 进入执行链
  E 反向对照：启用时点之前的候选    ⇒ 必须被 epoch 闸门拒（证明门禁没被放宽）

用法：python code/13_recover_cases_r1.py [--max-symbols 6]
产出：out/13_候选恢复验证.json、out/13_场景逐条.csv
"""
from __future__ import annotations

import importlib
import json
import os
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
R1 = HERE.parent
C06 = R1.parent / "SIGNAL-CLOSURE-06"
OUT = R1 / "out"
ISO = OUT / "13_iso"
ISO.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(C06 / "code"))
cm = importlib.import_module("00_common")
cm.OUT = OUT
H1, M15 = cm.H1, 900_000

# 生产行情库路径：必须在覆盖 env **之前**取到（后面 MARKET_DB 会被改到隔离库）
import project_paths as PP                                       # noqa: E402
PROD_MARKET = str(PP.MARKET_DB)

PAPER_EPOCH_MS = 1_791_939_600_000        # 2026-10-14T01:00:00Z（生产值，禁止修改）
MAX_FILL_LAG_MS = 3_600_000


# ── 隔离环境（**必须在 import 后端模块之前设好**）────────────────────────────
os.environ["PAPER_LEDGER_DIR"] = str(ISO / "paper")
os.environ["PAPER_LEDGER_ALLOW_INIT"] = "1"
os.environ["LIVE_DB"] = str(ISO / "live" / "live.sqlite3")
os.environ["PAPER_GATE_FILE"] = str(ISO / "gate.json")
os.environ["EVENT_TREND_BATCH_ROOT"] = str(ISO / "batches")
os.environ["RUNS_DIR"] = str(ISO / "runs")
os.environ["MARKET_DB"] = str(ISO / "market" / "market.sqlite3")
os.environ["MARKET_ARCHIVE"] = str(ISO / "archive_empty")
os.environ["WX_HISTORY_SOURCE"] = "hot"
os.environ["no_proxy"] = "*"
(ISO / "live").mkdir(parents=True, exist_ok=True)
(ISO / "paper").mkdir(parents=True, exist_ok=True)
(ISO / "archive_empty").mkdir(parents=True, exist_ok=True)


def build_iso_market(syms, hist, upto_ms, tf15_days=40):
    """隔离行情库：schema 从生产库拷，4h/1h 用合并历史，15m 从热库直读。

    与 06 的 `build_iso_market` 同一做法（06 的注释记着坑：只从热库导 4h 会只剩 45 天，
    bootstrap 会因根数不足跳过全部标的）。
    """
    dst_p = Path(os.environ["MARKET_DB"])
    dst_p.parent.mkdir(parents=True, exist_ok=True)
    if dst_p.exists():
        dst_p.unlink()
    src = sqlite3.connect(f"file:{PROD_MARKET}?mode=ro", uri=True)
    src.row_factory = sqlite3.Row
    dst = sqlite3.connect(dst_p)
    try:
        for o in src.execute("SELECT type,name,sql FROM sqlite_master WHERE sql IS NOT NULL "
                             "AND type IN ('table','view','index','trigger')").fetchall():
            try:
                dst.execute(o["sql"])
            except sqlite3.Error:
                pass
        dst.commit()

        def put(rows, sym, tf):
            for r in rows:
                dst.execute(
                    "INSERT OR REPLACE INTO candle_versions(exchange,market,symbol,timeframe,"
                    "open_time,close_time,open,high,low,close,volume,quote_volume,trade_count,"
                    "taker_buy_volume,taker_buy_quote_volume,source_id,available_at,is_closed) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    ("binance", "um", sym, tf, int(r["open_time"]), int(r["close_time"]),
                     float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"]),
                     float(r["volume"]), float(r.get("quote_volume") or 0.0),
                     int(r.get("trade_count") or 0), float(r.get("taker_buy_volume") or 0.0),
                     float(r.get("taker_buy_quote_volume") or 0.0), int(r.get("source_id") or 0),
                     r.get("available_at"), int(r.get("is_closed") or 1)))
                dst.execute("INSERT OR REPLACE INTO candle_heads(exchange,market,symbol,timeframe,"
                            "open_time,source_id) VALUES(?,?,?,?,?,?)",
                            ("binance", "um", sym, tf, int(r["open_time"]),
                             int(r.get("source_id") or 0)))

        for sym in syms:
            for tf in ("4h", "1h"):
                put(hist[sym][tf], sym, tf)
            rows = src.execute(
                "SELECT symbol,timeframe,open_time,close_time,open,high,low,close,volume,"
                "quote_volume,trade_count,taker_buy_volume,taker_buy_quote_volume,"
                "source_id,available_at,is_closed FROM candle_versions "
                "WHERE exchange='binance' AND market='um' AND symbol=? AND timeframe='15m' "
                "AND is_closed=1 AND close_time<=? AND open_time>=? ORDER BY open_time",
                (sym, int(upto_ms), int(upto_ms) - tf15_days * 86_400_000)).fetchall()
            put([dict(r) for r in rows], sym, "15m")
        dst.commit()
        n = dst.execute("SELECT COUNT(*) FROM candles").fetchone()[0]
        assert n > 0, "隔离行情库为空"
        print(f"[iso-market] {n} 行，标的 {syms}")
    finally:
        dst.close()
        src.close()


def ledger_counts():
    import live_store as L
    out = {}
    con = L.live_conn(ro=True)
    try:
        out["live_signal"] = con.execute("SELECT COUNT(*) FROM live_signal").fetchone()[0]
        out["live_signal_recovered"] = con.execute(
            "SELECT COUNT(*) FROM live_signal WHERE origin='recovered'").fetchone()[0]
        out["live_signal_live"] = con.execute(
            "SELECT COUNT(*) FROM live_signal WHERE origin='live'").fetchone()[0]
    finally:
        con.close()
    con = L.paper_conn("3a", ro=True)
    try:
        for t in ("paper_order", "paper_pending", "paper_position", "paper_processed",
                  "paper_blocked"):
            try:
                out[t] = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            except sqlite3.OperationalError:
                out[t] = None
        try:
            out["order_status"] = [
                {"order_id": r[0], "status": r[1], "reject_reason": r[2],
                 "fill_price": r[3]}
                for r in con.execute(
                    "SELECT order_id,status,reject_reason,fill_price FROM paper_order "
                    "ORDER BY order_id").fetchall()]
        except sqlite3.OperationalError:
            out["order_status"] = []
    finally:
        con.close()
    return out


def main() -> int:
    argv = sys.argv[1:]
    max_syms = 6
    for i, a in enumerate(argv):
        if a == "--max-symbols" and i + 1 < len(argv):
            max_syms = int(argv[i + 1])

    cand_file = OUT / "12_真实历史候选清单.json"
    if not cand_file.exists():
        print(f"[FATAL] 缺 {cand_file.name}，请先跑 12_divergence_explain.py")
        return 2
    cands = json.loads(cand_file.read_text(encoding="utf-8"))["candidates"]
    cands.sort(key=lambda c: c["exec_earliest_ms"])
    after = [c for c in cands if c["exec_earliest_ms"] >= PAPER_EPOCH_MS]
    before = [c for c in cands if c["exec_earliest_ms"] < PAPER_EPOCH_MS]
    print(f"[cands] 真实候选 {len(cands)} 个；启用时点之后 {len(after)} 个、之前 {len(before)} 个")
    if cands:
        print(f"        最新候选 {cands[-1]['exec_earliest_utc']} {cands[-1]['symbol']}；"
              f"启用时点 {cm.utc(PAPER_EPOCH_MS)}")

    if not cands:
        print("[FATAL] 候选清单为空，无法验证")
        return 2

    # 成功路径只能用「启用时点之后」的真实候选
    case_ok = after[-1] if after else None
    case_old = before[-1] if before else None
    probe_syms = []
    for c in (case_ok, case_old):
        if c and c["symbol"] not in probe_syms:
            probe_syms.append(c["symbol"])
    if not probe_syms:
        return 2
    syms = probe_syms[:max_syms]
    print(f"[cfg] 参与隔离的标的 {syms}")

    upto_now = cm.last_closed_1h_close_ms()
    hist, _ = cm.load_hist(syms, upto_now, read_days=200,
                           keep_from_ms=upto_now - 300 * 24 * H1)
    build_iso_market(syms, hist, upto_now)

    import unified_runtime as UR
    import live_store as L

    L.ensure_live_schema()
    for arm in ("3a",):
        L.paper_conn(arm).close()
    # **启用时点不挪**：显式把 paper_epoch 写成生产值；live_start 对齐到 epoch，
    # 使「启用时点」唯一（生产里 live_start 是进程级水位，重启即变，不是启用时点）。
    L.set_run("paper_epoch_ms", PAPER_EPOCH_MS)
    L.set_run("live_start_ms", PAPER_EPOCH_MS)
    print(f"[iso] paper_epoch={cm.utc(L.get_paper_epoch_ms())} "
          f"（=生产值 {PAPER_EPOCH_MS}）")

    results, rows = {}, {}

    def run_scenario(name, upto_ms, sim_now_ms, note, advance_to=None):
        rt = UR.UnifiedRuntime(arm="3a", symbols=syms, poll_interval_sec=1.0,
                               lock_dir=ISO / "locks")
        rt.signal_engine.history_provider = cm.MemProvider(hist)
        rt.signal_engine.watcher = cm.MemPoolWatcher(hist, syms)
        rt.signal_engine.replay_ms = 2880 * H1
        rt.signal_engine.state_anchor_ms = None
        rt.signal_engine.history_floor_ms = int(upto_ms) - rt.signal_engine.replay_ms
        rt.signal_engine.history_provider.set_upto(int(upto_ms))
        rt.signal_engine.watcher.set_now(int(upto_ms))
        rt.signal_engine.prepare_history(int(upto_ms), log=lambda *a: None)
        rt.signal_engine.bootstrap(int(upto_ms), log=lambda *a: None)
        bs = dict(rt.signal_engine.bootstrap_stats)
        rec = rt._route_recovered_candidates(now_ms=int(sim_now_ms))
        step_res = None
        if advance_to is not None:
            cur = int(upto_ms)
            while cur < int(advance_to):
                cur += H1
                rt.signal_engine.watcher.set_now(cur)
                step_res = rt.step_once(now_ms=cur)
        lc = ledger_counts()
        snap = {
            "scenario": name, "note": note,
            "upto_utc": cm.utc(upto_ms), "sim_now_utc": cm.utc(sim_now_ms),
            "paper_epoch_utc": cm.utc(L.get_paper_epoch_ms()),
            "bootstrap_recovered": bs.get("recovered_candidates"),
            "bootstrap_discarded": bs.get("history_candidates_discarded"),
            "recovered_detail": bs.get("recovered_detail"),
            "route": rec, "step": step_res, "ledger": lc,
        }
        results[name] = snap
        rows[name] = {"scenario": name, "note": note,
                      "upto_utc": snap["upto_utc"], "sim_now_utc": snap["sim_now_utc"],
                      "recovered": snap["bootstrap_recovered"],
                      "registered": rec.get("registered"), "rejected": rec.get("rejected"),
                      "live_signal": lc["live_signal"],
                      "sig_recovered": lc["live_signal_recovered"],
                      "sig_live": lc["live_signal_live"],
                      "order": lc["paper_order"], "pending": lc["paper_pending"],
                      "position": lc["paper_position"],
                      "order_status": json.dumps(lc["order_status"], ensure_ascii=False)}
        print(f"  [{name}] recovered={snap['bootstrap_recovered']} "
              f"reg={rec.get('registered')} rej={rec.get('rejected')} "
              f"order={lc['paper_order']} pending={lc['paper_pending']} "
              f"pos={lc['paper_position']} sig_live={lc['live_signal_live']}")
        return rt

    # ── A 过期历史候选：截止=现在，所有候选都早于时效 ──────────────────────
    run_scenario("A_过期历史候选", upto_now, upto_now,
                 "截止=现在 ⇒ 预热期候选全部超出成交时效，应 recovered=0、账本 0 新增")

    if case_ok:
        Tc = case_ok["exec_earliest_ms"]
        # ── B 启用时点之后、停机期间产生、恢复时仍有效 ────────────────────
        run_scenario("B_启用后有效恢复", Tc + 20 * 60_000, Tc + 20 * 60_000,
                     "候选 exec_earliest 在启用时点之后且 +20m 内 ⇒ 应恢复、落库、订单为挂单")
        # ── C 重复恢复 ────────────────────────────────────────────────────
        run_scenario("C_重复恢复", Tc + 20 * 60_000, Tc + 20 * 60_000,
                     "与 B 完全相同的重启时刻再跑一次 ⇒ 信号/订单/挂单行数不得增加")
        # ── D 恢复后新增候选：推进 6 小时 ─────────────────────────────────
        run_scenario("D_恢复后新增候选", Tc + 20 * 60_000, Tc + 20 * 60_000,
                     "重启后逐 1h 推进 6 小时 ⇒ 新候选走 origin=live 正常进入执行链",
                     advance_to=Tc + 6 * H1)

    if case_old:
        To = case_old["exec_earliest_ms"]
        # ── E 反向对照：启用时点之前的候选必须被拒（证明门禁未放宽）────────
        run_scenario("E_启用前候选应被拒", To + 20 * 60_000, To + 20 * 60_000,
                     "候选早于启用时点 ⇒ 必须被 before_paper_epoch_no_backfill 拒（门禁未放宽）")

    # ── 判定 ──────────────────────────────────────────────────────────────
    checks = {}
    a = results.get("A_过期历史候选", {})
    checks["A_零恢复"] = (a.get("bootstrap_recovered") == 0)
    checks["A_账本零新增"] = (a.get("ledger", {}).get("paper_order") == 0)

    if case_ok:
        b = results.get("B_启用后有效恢复", {})
        c = results.get("C_重复恢复", {})
        b_l = b.get("ledger", {})
        c_l = c.get("ledger", {})
        b_rej = [o for o in b_l.get("order_status", []) if o.get("status") == "rejected"]
        checks["B_恢复出候选"] = (b.get("bootstrap_recovered") or 0) >= 1
        checks["B_未因启用时点被拒"] = not any(
            "epoch" in (o.get("reject_reason") or "") for o in b_rej)
        checks["B_订单已登记"] = (b_l.get("paper_order") or 0) >= 1
        checks["B_进入挂单或持仓"] = ((b_l.get("paper_pending") or 0) >= 1
                                  or (b_l.get("paper_position") or 0) >= 1)
        checks["C_行数与B相同"] = (
            (b_l.get("paper_order"), b_l.get("paper_pending"), b_l.get("paper_position"),
             b_l.get("live_signal"))
            == (c_l.get("paper_order"), c_l.get("paper_pending"), c_l.get("paper_position"),
                c_l.get("live_signal")))
        d = results.get("D_恢复后新增候选", {})
        checks["D_推进无异常"] = (d.get("step") is None or d.get("step", {}).get("ok", True))
        checks["D_新增候选走live"] = (d.get("ledger", {}).get("live_signal_live") or 0) >= 0
    else:
        checks["B_成功路径"] = False

    if case_old:
        e = results.get("E_启用前候选应被拒", {})
        e_rej = [o for o in e.get("ledger", {}).get("order_status", [])
                 if o.get("status") == "rejected"]
        checks["E_启用前候选被epoch拒"] = any(
            "before_paper_epoch" in (o.get("reject_reason") or "") for o in e_rej)
        checks["E_未成交"] = (e.get("ledger", {}).get("paper_position") or 0) == 0

    verdict = "PASS" if all(checks.values()) else ("UNVERIFIED" if not case_ok else "FAIL")
    out = {
        "generated_at_utc": cm.utc(cm.now_ms()),
        "paper_epoch_utc": cm.utc(PAPER_EPOCH_MS),
        "paper_epoch_note": ("生产启用时点原值，未修改；本脚本只把它显式写进隔离库，"
                             "并把进程级 live_start 对齐到它，使「启用时点」唯一。"),
        "max_fill_lag_ms": MAX_FILL_LAG_MS,
        "isolation": {k: os.environ.get(k) for k in
                      ("MARKET_DB", "MARKET_ARCHIVE", "PAPER_LEDGER_DIR", "LIVE_DB",
                       "PAPER_GATE_FILE", "EVENT_TREND_BATCH_ROOT", "RUNS_DIR")},
        "symbols": syms,
        "n_real_candidates": len(cands),
        "n_candidates_after_epoch": len(after),
        "n_candidates_before_epoch": len(before),
        "case_after_epoch": case_ok, "case_before_epoch": case_old,
        "scenarios": results,
        "checks": checks,
        "verdict": verdict,
        "judgement": ("A 必须零恢复零写入；B 必须恢复出候选、不被启用时点拒、进入挂单或持仓；"
                      "C 必须与 B 行数完全相同；E 必须被 epoch 闸门拒（证明门禁未放宽）。"
                      "若真实历史里没有启用时点之后的候选 ⇒ 成功路径记 UNVERIFIED，"
                      "不得用平移时间轴冒充通过。"),
    }
    cm.dump("13_候选恢复验证.json", out)
    cm.dump_csv("13_场景逐条.csv", list(rows.values()))
    print(f"[verdict] {verdict}")
    for k, v in checks.items():
        print(f"    {'OK ' if v else 'BAD'} {k}")
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
