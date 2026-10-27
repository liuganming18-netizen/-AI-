# -*- coding: utf-8 -*-
"""SIGNAL-HISTORY-FIX-01｜05 历史与实时衔接三路对比（工单「四」）。

同数据 / 同配置 / 同截止时间，对比三条路径：
  **P1 连续历史回放**：在 `T_boot` 预热一次，然后**一直 step 到 T1**，中间不重启。
  **P2 历史初始化后逐根追加**：在 `T0` 预热一次（历史全部可得），然后逐根 step 到 T1。
  **P3 中途重启后继续**：同 P2，但在 `Tm` **销毁引擎、重建并重新预热**，再 step 到 T1。

三者都走**同一个** `MultiSymbolSignalEngine.step()`（生产同一个函数），
差别只在「预热发生在什么时刻、中间有没有重启」。因此：
  · P1 == P2 ⇒ 历史初始化没有改变状态；
  · P2 == P3 ⇒ 中途重启没有丢失/重复状态；
  · 任何不等 ⇒ 就是「状态恢复错误 / 实时推进错误」，逐项定位。

隔离：本脚本自建一个**独立行情库**（只含所选标的的 1h/4h），通过 `MARKET_DB`
环境变量注入，绝不碰生产库；也不调用 `step_once`（不写任何账本）。

产出：
  out/05_三路衔接对比.json
  out/05_候选逐项对照.csv
  out/05_状态逐币对照.csv
"""
from __future__ import annotations

import importlib
import json
import os
import shutil
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
cm = importlib.import_module("00_common")

# ── 0. 独立行情库（必须在 import unified_runtime 之前注入 env）──────────────
ISO = cm.OUT / "00_isolated_market"
ISO_DB = ISO / "linkage_market.sqlite3"


def build_isolated(symbols: list[str], floor_ms: int, ceil_ms: int) -> Path:
    """从生产库**只读**导出所选标的的 1h/4h 到一个独立库（结构同源）。"""
    ISO.mkdir(parents=True, exist_ok=True)
    if ISO_DB.exists():
        ISO_DB.unlink()
    src = cm.market_conn()
    dst = sqlite3.connect(ISO_DB)
    try:
        for name in ("candle_versions", "candle_heads"):
            sql = src.execute("SELECT sql FROM sqlite_master WHERE name=?", (name,)).fetchone()[0]
            dst.execute(sql)
        view = src.execute("SELECT sql FROM sqlite_master WHERE name='candles'").fetchone()[0]
        dst.execute(view)
        dst.execute("CREATE INDEX ix_cv ON candle_versions(exchange,market,symbol,timeframe,open_time)")
        dst.execute("CREATE INDEX ix_cv_ct ON candle_versions(exchange,market,symbol,timeframe,close_time)")
        n = 0
        for sym in symbols:
            for tf, step in (("1h", cm.H1), ("4h", cm.H4)):
                rows = src.execute(
                    "SELECT exchange,market,symbol,timeframe,open_time,close_time,open,high,low,"
                    "close,volume,quote_volume,trade_count,taker_buy_volume,taker_buy_quote_volume,"
                    "source_id,available_at,is_closed FROM candle_versions WHERE exchange=? AND market=? "
                    "AND symbol=? AND timeframe=? AND is_closed=1 AND open_time>=? AND close_time<=? "
                    "ORDER BY open_time",
                    (cm.EXCHANGE, cm.MARKET, sym, tf, int(floor_ms), int(ceil_ms))).fetchall()
                for r in rows:
                    dst.execute(
                        "INSERT OR REPLACE INTO candle_versions(exchange,market,symbol,timeframe,"
                        "open_time,close_time,open,high,low,close,volume,quote_volume,trade_count,"
                        "taker_buy_volume,taker_buy_quote_volume,source_id,available_at,is_closed) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", tuple(r))
                    dst.execute(
                        "INSERT OR REPLACE INTO candle_heads(exchange,market,symbol,timeframe,"
                        "open_time,source_id) VALUES(?,?,?,?,?,?)",
                        (r["exchange"], r["market"], r["symbol"], r["timeframe"],
                         r["open_time"], r["source_id"]))
                    n += 1
        dst.commit()
        print(f"[iso] {len(symbols)} symbols, {n} bars -> {ISO_DB.name}")
        # 自检：candles 视图必须真的能看到数据（否则 bootstrap 会静默跳过所有标的）。
        # 注意 `candles` 是 candle_versions JOIN candle_heads USING(...,source_id)，
        # 同一 open_time 可能有多个 source 版本，但只有 head 指向的那一版可见 ⇒
        # 视图行数应等于「head 条数」，不是「versions 写入条数」。
        n_head = dst.execute("SELECT COUNT(*) FROM candle_heads").fetchone()[0]
        chk = dst.execute("SELECT COUNT(*) FROM candles").fetchone()[0]
        assert chk == n_head and chk > 0, (
            f"candles 视图 {chk} 行 != candle_heads {n_head} 条（写入 versions {n} 条）")
    finally:
        dst.close()
        src.close()
    return ISO_DB


def main() -> None:
    # 1) 选标的：按全池回放结果分层，覆盖「有候选 / 错过回踩 / 超时 / 失效 / 当前回踩 / 仅箱体」
    full = json.loads((cm.OUT / "03_漏斗汇总.json").read_text(encoding="utf-8"))
    per = full["per_symbol"]
    cand = sorted([s for s, v in per.items() if v.get("n_candidates")])
    missed = sorted([s for s, v in per.items() if v.get("tr_MISSED_WITHOUT_RETEST")])
    expired = sorted([s for s, v in per.items() if v.get("tr_EXPIRED")])[:10]
    invalid = sorted([s for s, v in per.items() if v.get("tr_INVALIDATED")])[:10]
    in_retest = sorted([s for s, v in per.items() if v.get("stage") == "RETEST"])[:10]
    box_only = sorted([s for s, v in per.items()
                       if v.get("stage") == "IDLE_WAIT_BOX"])[:5]
    picked, seen = [], set()
    for grp in (cand, missed, expired, invalid, in_retest, box_only):
        for s in grp:
            if s not in seen:
                seen.add(s)
                picked.append(s)

    # 2) 时间窗
    con = cm.market_conn()
    try:
        r = con.execute("SELECT MAX(open_time) FROM candle_heads WHERE exchange=? AND market=? "
                        "AND timeframe='1h'", (cm.EXCHANGE, cm.MARKET)).fetchone()
        ref_t = int(r[0]) + cm.H1 - 1
        r = con.execute("SELECT MIN(open_time) FROM candle_heads WHERE exchange=? AND market=? "
                        "AND timeframe='4h'", (cm.EXCHANGE, cm.MARKET)).fetchone()
        data_start_4h = int(r[0])
        r = con.execute("SELECT MIN(open_time) FROM candle_heads WHERE exchange=? AND market=? "
                        "AND timeframe='1h'", (cm.EXCHANGE, cm.MARKET)).fetchone()
        data_start_1h = int(r[0])
        data_start = min(data_start_4h, data_start_1h)
    finally:
        con.close()

    T1 = ref_t                                   # 截止 = 最后一根已闭合 1h 的收盘
    T0 = int((cm.dt.datetime(2026, 10, 1, tzinfo=cm.UTC)).timestamp() * 1000)
    Tm = int((cm.dt.datetime(2026, 10, 12, 6, tzinfo=cm.UTC)).timestamp() * 1000)
    Tm_aligned = int((cm.dt.datetime(2026, 10, 12, tzinfo=cm.UTC)).timestamp() * 1000)
    # T_boot 固定，但**必须筛掉在该时刻预热不够的标的**（上市晚的币），
    # 否则四条路径的标的集合不同，比较就不成立。被筛掉的标的如实记录。
    T_boot = int((cm.dt.datetime(2026, 9, 20, tzinfo=cm.UTC)).timestamp() * 1000)
    assert T_boot < T0, f"T_boot {cm.iso(T_boot)} 不早于 T0 {cm.iso(T0)}"

    build_isolated(picked, floor_ms=data_start, ceil_ms=T1 + cm.H1)

    # ── 剔除「在 T0 时点达不到 bootstrap 两道门槛」的标的 ─────────────────────
    # 门槛是 `bootstrap` 里写死的：4h >= box_bars_4h+8 = 50 根、1h >= 200 根。
    # 达不到就**如实剔除并记录原因**，不偷偷改门槛、也不静默留下空引擎。
    _con = sqlite3.connect(ISO_DB)
    dropped = {}
    kept = []
    for sym in picked:
        n4 = _con.execute("SELECT COUNT(*) FROM candle_versions WHERE symbol=? AND timeframe='4h' "
                          "AND close_time<=?", (sym, T0)).fetchone()[0]
        n1 = _con.execute("SELECT COUNT(*) FROM candle_versions WHERE symbol=? AND timeframe='1h' "
                          "AND close_time<=?", (sym, T0)).fetchone()[0]
        n4b = _con.execute("SELECT COUNT(*) FROM candle_versions WHERE symbol=? AND timeframe='4h' "
                           "AND close_time<=?", (sym, T_boot)).fetchone()[0]
        n1b = _con.execute("SELECT COUNT(*) FROM candle_versions WHERE symbol=? AND timeframe='1h' "
                           "AND close_time<=?", (sym, T_boot)).fetchone()[0]
        if n4 < 50:
            dropped[sym] = f"4h_bars_insufficient_at_T0({n4}<50)"
        elif n1 < 200:
            dropped[sym] = f"1h_bars_insufficient_at_T0({n1}<200)"
        elif n4b < 50 or n1b < 200:
            dropped[sym] = f"cannot_bootstrap_at_T_boot(4h={n4b},1h={n1b})"
        else:
            kept.append(sym)
    _con.close()
    picked = kept
    print(f"[pick] kept={len(picked)} dropped={len(dropped)} {dropped}")

    # 注入隔离环境（必须在 import unified_runtime 前）
    os.environ["MARKET_DB"] = str(ISO_DB)
    os.environ["LIVE_DIR"] = str(ISO / "live")
    os.environ["PAPER_LEDGER_DIR"] = str(ISO / "paper")
    os.environ["PAPER_LOCK_DIR"] = str(ISO / "locks")
    (ISO / "live").mkdir(parents=True, exist_ok=True)
    for m in ("live_store", "unified_runtime"):
        sys.modules.pop(m, None)
    import unified_runtime as UR
    assert str(UR.L.MARKET_DB) == str(ISO_DB), f"MARKET_DB 未注入成功: {UR.L.MARKET_DB}"

    # ── 渐进激活器（等价于 sync_dataset_bars_upto 的多标的口径）──────────────
    class Activator:
        """把独立库里的 bar 按 close_time 逐根「激活」进 candles 视图。"""

        def __init__(self, db: Path, symbols: list[str], floor_ms: int, ceil_ms: int):
            self.db = db
            self.rows = []
            src = sqlite3.connect(db)
            src.row_factory = sqlite3.Row
            try:
                for sym in symbols:
                    for tf in ("1h", "4h"):
                        for r in src.execute(
                                "SELECT exchange,market,symbol,timeframe,open_time,close_time,open,"
                                "high,low,close,volume,quote_volume,trade_count,taker_buy_volume,"
                                "taker_buy_quote_volume,source_id,available_at,is_closed "
                                "FROM candle_versions WHERE symbol=? AND timeframe=? "
                                "ORDER BY close_time", (sym, tf)):
                            self.rows.append((int(r["close_time"]), tuple(r),
                                              r["source_id"]))
            finally:
                src.close()
            self.rows.sort(key=lambda x: x[0])
            # 清空可见数据，只保留 stage（全部内容先删掉，再按需插回）
            con = sqlite3.connect(db)
            con.execute("DELETE FROM candle_heads")
            con.execute("DELETE FROM candle_versions")
            con.commit()
            con.close()
            self.i = 0
            self.total = len(self.rows)

        def activate_upto(self, cur_ms: int) -> int:
            batch, sids = [], []
            while self.i < self.total and self.rows[self.i][0] <= cur_ms:
                batch.append(self.rows[self.i][1])
                sids.append(self.rows[self.i][2])
                self.i += 1
            if not batch:
                return 0
            con = sqlite3.connect(self.db)
            try:
                con.executemany(
                    "INSERT OR REPLACE INTO candle_versions(exchange,market,symbol,timeframe,"
                    "open_time,close_time,open,high,low,close,volume,quote_volume,trade_count,"
                    "taker_buy_volume,taker_buy_quote_volume,source_id,available_at,is_closed) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", batch)
                con.executemany(
                    "INSERT OR REPLACE INTO candle_heads(exchange,market,symbol,timeframe,"
                    "open_time,source_id) VALUES(?,?,?,?,?,?)",
                    [(b[0], b[1], b[2], b[3], b[4], s)
                     for b, s in zip(batch, sids)])
                con.commit()
            finally:
                con.close()
            return len(batch)

    # ── 跑一条路径 ──────────────────────────────────────────────────────────
    def run_path(name: str, boots: list[int], steps: list[int]) -> dict:
        act = Activator(ISO_DB, picked, data_start, T1)
        eng = UR.MultiSymbolSignalEngine(picked)
        eng.history_floor_ms = data_start
        cands, step_log = [], []
        bi = 0
        # 第一个预热点
        act.activate_upto(boots[0])
        eng.bootstrap(boots[0], log=lambda *a: None)
        assert eng.engines, (f"{name}: bootstrap 后 0 个引擎就绪 "
                             f"(skipped={eng.skipped_reasons})")
        for t in steps:
            if bi + 1 < len(boots) and t >= boots[bi + 1]:
                # 重启：销毁引擎、重建、重新预热
                eng = UR.MultiSymbolSignalEngine(picked)
                eng.history_floor_ms = data_start
                eng.bootstrap(t, log=lambda *a: None)
                assert eng.engines, (f"{name}: 重启 bootstrap 后 0 个引擎就绪 "
                                     f"(skipped={eng.skipped_reasons})")
                bi += 1
            act.activate_upto(t)
            got = eng.step(t)
            for c in got:
                cands.append({"symbol": c.symbol, "available_at_ms": int(c.available_at_ms),
                              "candidate_id": c.candidate_id,
                              "direction": getattr(c, "direction", None),
                              "breakout_close": c.breakout_close,
                              "initial_stop_price": c.initial_stop_price,
                              "box_high_h": c.box_high_h, "box_low_l": c.box_low_l,
                              "box_atr_a": c.box_atr_a})
            step_log.append({"t": t, "new": len(got)})
        states = {}
        for sym, e in eng.engines.items():
            ev = e.sm.active_event
            states[sym] = {
                "current_state": ev.current_state.value if ev else "IDLE",
                "event_id": ev.event_id if ev else None,
                "created_at_ms": ev.created_at_ms if ev else None,
                "sweep_time_ms": getattr(ev, "sweep_time_ms", None) if ev else None,
                "retest_time_ms": getattr(ev, "retest_time_ms", None) if ev else None,
                "sweep_low_s": getattr(ev, "sweep_low_s", None) if ev else None,
                "retest_low_lr": getattr(ev, "retest_low_lr", None) if ev else None,
                "n_transitions": len(e.sm.transitions),
                "n_candidates": len(e.sm.candidates),
                "last_processed_1h": (max(e.sm.processed_1h_bar_opens)
                                      if e.sm.processed_1h_bar_opens else None),
                "engine_last_1h_open": eng.last_1h_open.get(sym),
                "cooling_until_ms": e.sm.cooling_until_ms,
                "n_completed": len(e.sm.completed_events),
            }
        return {"name": name, "candidates": cands, "states": states,
                "step_log": step_log,
                "boots": boots, "steps_range": [steps[0], steps[-1], len(steps)]}

    def hourly(a: int, b: int) -> list[int]:
        out, t = [], a
        while t <= b:
            out.append(t)
            t += cm.H1
        return out

    # P1 连续：从 T_boot 一路 step 到 T1
    p1 = run_path("P1_continuous", [T_boot], hourly(T_boot, T1))
    # P2 初始化后逐根追加：T0 预热，然后 step 到 T1
    p2 = run_path("P2_init_then_append", [T0], hourly(T0, T1))
    # P3 中途重启：T0 预热 → step 到 Tm → 重启预热 → step 到 T1
    p3 = run_path("P3_restart_midway", [T0, Tm], hourly(T0, T1))
    # 对照：在 1h/4h 收盘**对齐**的时刻重启（T0 本身即对齐点，Tm_aligned 也是）
    p3b = run_path("P3b_restart_aligned", [T0, Tm_aligned], hourly(T0, T1))

    # ── 比较（统一截到 [T0, T1] 才有可比性）────────────────────────────────
    def in_window(cl, a=T0, b=T1):
        return [c for c in cl if a <= c["available_at_ms"] <= b]

    def key(c):
        return (c["symbol"], c["available_at_ms"])

    res = {"ticket": "SIGNAL-HISTORY-FIX-01", "generated_at_utc": cm.utc_now(),
           "config_identity": cm.config_identity(),
           "isolated_market_db": str(ISO_DB),
           "symbols_picked": picked, "symbols_picked_n": len(picked),
           "symbol_selection": {
               "candidate": cand, "missed_without_retest": missed,
               "expired_sample": expired, "invalidated_sample": invalid,
               "in_retest_now_sample": in_retest, "box_only_sample": box_only},
           "windows": {"T_boot_utc": cm.iso(T_boot), "T0_utc": cm.iso(T0),
                       "Tm_restart_utc": cm.iso(Tm),
                       "Tm_restart_aligned_utc": cm.iso(Tm_aligned), "T1_utc": cm.iso(T1),
                       "compare_window": [cm.iso(T0), cm.iso(T1)]},
           "paths": {}}
    for p in (p1, p2, p3, p3b):
        res["paths"][p["name"]] = {
            "boots_utc": [cm.iso(x) for x in p["boots"]],
            "steps": p["steps_range"],
            "candidates_total_all_window": len(p["candidates"]),
            "candidates_in_compare_window": len(in_window(p["candidates"])),
            "candidates": [{**c, "available_at_utc": cm.iso(c["available_at_ms"])}
                           for c in p["candidates"]],
        }

    def cmp_cands(a, b, na, nb):
        ka = {key(c): c for c in in_window(a)}
        kb = {key(c): c for c in in_window(b)}
        only_a = sorted(set(ka) - set(kb))
        only_b = sorted(set(kb) - set(ka))
        diff_fields = []
        for k in sorted(set(ka) & set(kb)):
            for f in ("direction", "breakout_close", "initial_stop_price",
                      "box_high_h", "box_low_l", "box_atr_a"):
                if ka[k][f] != kb[k][f]:
                    diff_fields.append({"key": [k[0], cm.iso(k[1])], "field": f,
                                        "a": ka[k][f], "b": kb[k][f]})
        return {"a": na, "b": nb, "a_n": len(ka), "b_n": len(kb),
                "only_in_a": [[k[0], cm.iso(k[1])] for k in only_a],
                "only_in_b": [[k[0], cm.iso(k[1])] for k in only_b],
                "field_diffs": diff_fields,
                "identical": not only_a and not only_b and not diff_fields}

    res["candidate_comparison"] = {
        "P1_vs_P2": cmp_cands(p1["candidates"], p2["candidates"], "P1", "P2"),
        "P2_vs_P3": cmp_cands(p2["candidates"], p3["candidates"], "P2", "P3"),
        "P1_vs_P3": cmp_cands(p1["candidates"], p3["candidates"], "P1", "P3"),
        "P2_vs_P3b_aligned_control": cmp_cands(p2["candidates"], p3b["candidates"], "P2", "P3b"),
    }

    def cmp_states(a, b, na, nb):
        diffs = []
        for sym in picked:
            sa, sb = a.get(sym), b.get(sym)
            if sa is None or sb is None:
                diffs.append({"symbol": sym, "field": "missing", "a": sa, "b": sb})
                continue
            for f in ("current_state", "event_id", "created_at_ms", "sweep_time_ms",
                      "retest_time_ms", "sweep_low_s", "retest_low_lr",
                      "n_transitions", "n_candidates", "last_processed_1h",
                      "engine_last_1h_open", "cooling_until_ms", "n_completed"):
                if sa[f] != sb[f]:
                    diffs.append({"symbol": sym, "field": f, "a": sa[f], "b": sb[f]})
        return {"a": na, "b": nb, "n_diff": len(diffs), "diffs": diffs,
                "identical": not diffs}

    res["state_comparison"] = {
        "P1_vs_P2": cmp_states(p1["states"], p2["states"], "P1", "P2"),
        "P2_vs_P3": cmp_states(p2["states"], p3["states"], "P2", "P3"),
        "P1_vs_P3": cmp_states(p1["states"], p3["states"], "P1", "P3"),
        "P2_vs_P3b_aligned_control": cmp_states(p2["states"], p3b["states"], "P2", "P3b"),
    }
    # 重复处理是否重复发信号：同一步连跑两次
    act = Activator(ISO_DB, picked, data_start, T1)
    eng = UR.MultiSymbolSignalEngine(picked)
    eng.history_floor_ms = data_start
    act.activate_upto(T0)
    eng.bootstrap(T0, log=lambda *a: None)
    t = T0 + 30 * cm.H1
    act.activate_upto(t)
    g1 = eng.step(t)
    g2 = eng.step(t)          # 同一时刻再跑一次
    act.activate_upto(t)
    g3 = eng.step(t)          # 再激活一次再跑
    res["duplicate_processing"] = {
        "t_utc": cm.iso(t), "first_run_new": len(g1), "second_run_same_t": len(g2),
        "third_run_after_reactivate": len(g3),
        "no_duplicate_signals": (len(g2) == 0 and len(g3) == 0),
        "note": "同一 upto_ms 重复 step 必须 0 新候选（状态机 processed_1h_bar_opens 去重）",
    }

    verdict = {
        "P1_vs_P2_candidates": res["candidate_comparison"]["P1_vs_P2"]["identical"],
        "P2_vs_P3_candidates": res["candidate_comparison"]["P2_vs_P3"]["identical"],
        "P2_vs_P3b_candidates": res["candidate_comparison"]["P2_vs_P3b_aligned_control"]["identical"],
        "P1_vs_P2_states": res["state_comparison"]["P1_vs_P2"]["identical"],
        "P2_vs_P3_states": res["state_comparison"]["P2_vs_P3"]["identical"],
        "P2_vs_P3b_states": res["state_comparison"]["P2_vs_P3b_aligned_control"]["identical"],
        "no_duplicate_signals": res["duplicate_processing"]["no_duplicate_signals"],
    }
    res["verdict"] = verdict
    res["verdict_all_pass"] = all(verdict.values())
    cm.write_json("05_三路衔接对比.json", res)

    # 逐项 CSV
    import csv
    with open(cm.OUT / "05_候选逐项对照.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["path", "symbol", "available_at_utc", "direction", "breakout_close",
                    "initial_stop_price", "box_high_h", "box_low_l", "box_atr_a"])
        for p in (p1, p2, p3, p3b):
            for c in p["candidates"]:
                w.writerow([p["name"], c["symbol"], cm.iso(c["available_at_ms"]),
                            c["direction"], c["breakout_close"], c["initial_stop_price"],
                            c["box_high_h"], c["box_low_l"], c["box_atr_a"]])
    with open(cm.OUT / "05_状态逐币对照.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["symbol", "path", "current_state", "event_id", "created_at_ms",
                    "sweep_time_ms", "retest_time_ms", "n_transitions", "n_candidates",
                    "last_processed_1h", "engine_last_1h_open", "cooling_until_ms",
                    "n_completed"])
        for p in (p1, p2, p3, p3b):
            for sym in picked:
                s = p["states"].get(sym) or {}
                w.writerow([sym, p["name"], s.get("current_state"), s.get("event_id"),
                            s.get("created_at_ms"), s.get("sweep_time_ms"),
                            s.get("retest_time_ms"), s.get("n_transitions"),
                            s.get("n_candidates"), s.get("last_processed_1h"),
                            s.get("engine_last_1h_open"), s.get("cooling_until_ms"),
                            s.get("n_completed")])

    print("symbols picked:", len(picked), picked)
    print("windows: T_boot", cm.iso(T_boot), "T0", cm.iso(T0), "Tm", cm.iso(Tm), "T1", cm.iso(T1))
    for p in (p1, p2, p3, p3b):
        print(f"  {p['name']}: cands_all={len(p['candidates'])} "
              f"in_window={len(in_window(p['candidates']))} steps={p['steps_range']}")
    for k, v in res["candidate_comparison"].items():
        print(f"  cand {k}: identical={v['identical']} a_n={v['a_n']} b_n={v['b_n']} "
              f"only_a={v['only_in_a']} only_b={v['only_in_b']}")
    for k, v in res["state_comparison"].items():
        print(f"  state {k}: identical={v['identical']} n_diff={v['n_diff']}")
        for d in v["diffs"][:12]:
            print("      ", d)
    print("dup:", res["duplicate_processing"])
    print("VERDICT:", json.dumps(verdict, ensure_ascii=False), "ALL_PASS=", res["verdict_all_pass"])


if __name__ == "__main__":
    main()
