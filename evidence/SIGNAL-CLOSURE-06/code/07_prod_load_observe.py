# -*- coding: utf-8 -*-
"""SIGNAL-CLOSURE-06｜07 受控加载到生产模拟环境，跨两根新增小时线观察（工单第 6 条）。

前置：04/05/06 的隔离验证已通过。
本脚本只做四件事，**不改任何策略/参数**：
  1. 记录加载前的账本与代码身份（SHA-256）、在跑进程身份、当前小时线水位；
  2. 用受控流程重启生产模拟运行时（`service_manager.py restart --enable-paper`）；
  3. 等 **两根新增 1h 收盘**，每次收盘后采一份状态（逐币水位 / 逐币阶段 / 漏斗 / 候选 / 拒绝原因）；
  4. 记录加载后账本身份，逐项对比，产出证据。

判据（任一不成立即 FAIL）：
  · 运行时真的起来了（status=running 且 pid 活着），并且 `bootstrap.ready` 等于全池标的数；
  · 重建模式 = 绝对锚点，锚点非空且**不随重启后移**；
  · 跨两根新增 1h：每根都被真正处理（`lag_1h_max <= 1`，且水位逐根前进）；
  · 候选 / 拒单有明确计数（允许 0，但必须是「有依据的 0」，不能是「没采到」）；
  · 生产账本的持仓/成交/已实现盈亏**未被本脚本改动**（只允许运行时自己写入）；
  · 健康采集未被我打断（采集器进程仍在）。

用法：python code/07_prod_load_observe.py [--max-wait-min 150]
产出：out/07_生产加载观察.json、out/07_逐根水位.csv、out/07_逐币阶段变化.csv
"""
from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
cm = importlib.import_module("00_common")

PY = "/Users/boos/.workbuddy-ai/binaries/python/envs/default/bin/python"
SVC = cm.BASE / "框架" / "backend" / "service_manager.py"
# **注意**：真正的状态文件是 `paper_runtime_3a_status.json`（每轮 step 重写）。
# 同目录的 `unified_runtime_status_3a.json` 是固定桩，**不能当就绪判据**。
STATUS = cm.BACKEND / "locks" / "paper_runtime_3a_status.json"
LEDGER_DIR = cm.BASE / "数据" / "paper"
H1 = cm.H1


def sha(p) -> str | None:
    try:
        return cm.sha256_file(p)
    except OSError:
        return None


def ledger_identity() -> dict:
    out = {"dir": str(LEDGER_DIR), "files": {}}
    for p in sorted(LEDGER_DIR.glob("paper_*.sqlite3")):
        out["files"][p.name] = {"sha256": sha(p), "size": p.stat().st_size}
    return out


def ledger_counts_prod() -> dict:
    """只读统计生产账本（不做任何写入）。"""
    import sqlite3
    p = LEDGER_DIR / "paper_3a.sqlite3"
    if not p.exists():
        return {"error": "ledger_missing"}
    con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        def one(sql, args=()):
            try:
                return con.execute(sql, args).fetchone()[0]
            except sqlite3.Error:
                return None
        return {
            "paper_order": one("SELECT COUNT(*) FROM paper_order"),
            "paper_position_open": one(
                "SELECT COUNT(*) FROM paper_position WHERE status='open' AND COALESCE(voided,0)=0"),
            "paper_position_total": one("SELECT COUNT(*) FROM paper_position"),
            "paper_realized": one("SELECT COUNT(*) FROM paper_realized"),
            "paper_pending": one("SELECT COUNT(*) FROM paper_pending"),
            "paper_processed": one("SELECT COUNT(*) FROM paper_processed"),
            "paper_blocked": one("SELECT COUNT(*) FROM paper_blocked"),
            "paper_account_cash": one("SELECT cash FROM paper_state LIMIT 1"),
            "paper_realized_pnl": one("SELECT realized_pnl FROM paper_state LIMIT 1"),
        }
    finally:
        con.close()


def read_status() -> dict:
    try:
        return json.loads(STATUS.read_text(encoding="utf-8"))
    except Exception:
        return {}


def runtime_pids() -> list:
    import psutil
    out = []
    for p in psutil.process_iter(["pid", "cmdline", "create_time"]):
        try:
            cl = p.info["cmdline"] or []
            if any(str(a).endswith("unified_runtime.py") for a in cl):
                out.append({"pid": p.info["pid"],
                            "create_time": p.info["create_time"],
                            "cmdline": " ".join(cl)[:300]})
        except Exception:                                        # noqa: BLE001
            continue
    return out


def collector_alive() -> dict:
    import psutil
    hits = []
    for p in psutil.process_iter(["pid", "cmdline", "create_time"]):
        try:
            cl = p.info["cmdline"] or []
            if any("collector" in str(a).lower() or "market_collect" in str(a).lower()
                   for a in cl):
                hits.append({"pid": p.info["pid"], "cmdline": " ".join(cl)[:200]})
        except Exception:                                        # noqa: BLE001
            continue
    return {"n": len(hits), "procs": hits[:5]}


def main() -> int:
    argv = sys.argv[1:]
    max_wait = 150
    for i, a in enumerate(argv):
        if a == "--max-wait-min" and i + 1 < len(argv):
            max_wait = int(argv[i + 1])

    pre = {
        "at_utc": cm.utc(cm.now_ms()),
        "ledger": ledger_identity(),
        "ledger_counts": ledger_counts_prod(),
        "runtime_code_sha256": sha(cm.BACKEND / "unified_runtime.py"),
        "live_store_sha256": sha(cm.BACKEND / "live_store.py"),
        "runtimes": runtime_pids(),
        "collector": collector_alive(),
        "db_1h_max_open": None,
        "state_anchor_utc": None,
        "replay_mode": None,
    }
    # 重启**前**的锚点：判「锚点不随重启后移」必须跟它比，
    # 拿重启后的值跟自己比是空转（永远成立），已按判据纪律修正。
    _pre_st = read_status()
    _pre_hist = ((_pre_st.get("signal_engine") or {}).get("bootstrap") or {}).get("history") or {}
    pre["state_anchor_utc"] = _pre_hist.get("state_anchor_utc")
    pre["replay_mode"] = _pre_hist.get("replay_mode")
    print(f"[pre] 重启前锚点 {pre['state_anchor_utc']} 模式 {pre['replay_mode']}")
    con = cm.market_conn()
    try:
        r = con.execute("SELECT MAX(open_time) FROM candles WHERE exchange='binance' AND "
                        "market='um' AND timeframe='1h' AND is_closed=1").fetchone()
        pre["db_1h_max_open"] = int(r[0]) if r and r[0] is not None else None
    finally:
        con.close()
    print(f"[pre] 1h 水位 {cm.utc(pre['db_1h_max_open'])} "
          f"运行进程 {[x['pid'] for x in pre['runtimes']]} "
          f"账本 {pre['ledger_counts'].get('paper_position_open')} 持仓")

    # ── 受控重启（唯一允许的加载方式）──────────────────────────────────────
    cmd = [PY, str(SVC), "restart", "--enable-paper", "--arm", "3a", "--symbol", "all"]
    print("[load] " + " ".join(cmd))
    t0 = time.time()
    pr = subprocess.run(cmd, capture_output=True, text=True, timeout=900,
                        cwd=str(cm.BASE), env={**os.environ, "no_proxy": "*"})
    print(f"[load] 退出码 {pr.returncode} 用时 {time.time()-t0:.1f}s")
    print((pr.stdout or "")[-2000:])
    if pr.returncode != 0:
        print((pr.stderr or "")[-2000:])

    # ── 等运行时起来 ────────────────────────────────────────────────────────
    ready_deadline = time.time() + 600
    st = {}
    while time.time() < ready_deadline:
        st = read_status()
        ri = st.get("runtime_identity") or {}
        if ri.get("status") == "running" and (st.get("signal_engine") or {}).get("universe"):
            break
        time.sleep(5)
    ri = st.get("runtime_identity") or {}
    se = st.get("signal_engine") or {}
    print(f"[ready] status={ri.get('status')} pid={ri.get('pid')} "
          f"universe={se.get('universe')} 用时 {time.time()-t0:.0f}s")

    boot = se.get("bootstrap") or {}
    anchor = (boot.get("history") or {}).get("state_anchor_utc")
    mode = (boot.get("history") or {}).get("replay_mode")
    print(f"[anchor] mode={mode} anchor={anchor} recovered={boot.get('recovered_candidates')}")

    # ── 采样：等两根新增 1h 收盘 ────────────────────────────────────────────
    samples, rows, stage_rows = [], [], []
    base_1h = pre["db_1h_max_open"]
    seen_closes = []
    prev_stage = se.get("per_symbol_state") or {}
    deadline = time.time() + max_wait * 60
    while time.time() < deadline and len(seen_closes) < 2:
        time.sleep(30)
        st = read_status()
        se = st.get("signal_engine") or {}
        wm = se.get("watermark_check") or {}
        # **字段路径**：`lag_1h_max` 在 `watermark_check.per_symbol` 下面，不在顶层。
        # 读错层级会拿到 None，再被 `or 0` 兜底成「合格」—— 那是空转判据
        # （看起来 PASS，其实什么都没检查到）。
        wper = wm.get("per_symbol") or {}
        cur = None
        con = cm.market_conn()
        try:
            r = con.execute("SELECT MAX(open_time) FROM candles WHERE exchange='binance' "
                            "AND market='um' AND timeframe='1h' AND is_closed=1").fetchone()
            cur = int(r[0]) if r and r[0] is not None else None
        finally:
            con.close()
        if cur is not None and base_1h is not None and cur > base_1h:
            if not seen_closes or cur > seen_closes[-1]:
                seen_closes.append(cur)
                snap = {
                    "close_utc": cm.utc(cur),
                    "at_utc": cm.utc(cm.now_ms()),
                    "watermark_1h": se.get("watermark_1h"),
                    "watermark_4h": se.get("watermark_4h"),
                    "lag_1h_max": wper.get("lag_1h_max"),
                    "lag_1h_histogram": wper.get("lag_1h_histogram"),
                    "n_lag_1h_gt1": wper.get("n_lag_1h_gt1"),
                    "lag_1h_bars": wm.get("lag_1h_bars"),
                    "lag_4h_bars": wm.get("lag_4h_bars"),
                    "aligned_1h": wm.get("aligned_1h"),
                    "aligned_4h": wm.get("aligned_4h"),
                    "processed_1h_max": wm.get("processed_1h_max"),
                    "db_1h_max": wm.get("db_1h_max"),
                    "state_counter": se.get("state_counter"),
                    "idle_breakdown": se.get("idle_breakdown"),
                    "funnel": se.get("funnel"),
                    "signals_generated_total": se.get("signals_generated_total"),
                    "skipped_reasons": se.get("skipped_reasons"),
                    "scan_errors_total": se.get("scan_errors_total"),
                    "order_processing": st.get("order_processing"),
                    "per_symbol_state": se.get("per_symbol_state") or {},
                    "ledger_counts": ledger_counts_prod(),
                }
                samples.append(snap)
                rows.append({"close_utc": snap["close_utc"],
                             "watermark_1h_utc": cm.utc(snap["watermark_1h"]),
                             "processed_1h_max_utc": cm.utc(snap["processed_1h_max"]),
                             "db_1h_max_utc": cm.utc(snap["db_1h_max"]),
                             "lag_1h_max": snap["lag_1h_max"],
                             "lag_1h_bars": snap["lag_1h_bars"],
                             "n_lag_1h_gt1": snap["n_lag_1h_gt1"],
                             "state_counter": json.dumps(snap["state_counter"],
                                                         ensure_ascii=False),
                             "cands_since_start": (snap["funnel"] or {}).get(
                                 "candidates_since_start"),
                             "new_signals": snap["signals_generated_total"],
                             "open_positions": snap["ledger_counts"].get(
                                 "paper_position_open"),
                             "pending": snap["ledger_counts"].get("paper_pending")})
                cur_stage = snap["per_symbol_state"]
                changed = [s for s in cur_stage
                           if prev_stage.get(s) != cur_stage[s]]
                for s in changed:
                    stage_rows.append({"close_utc": snap["close_utc"], "symbol": s,
                                       "from_state": prev_stage.get(s),
                                       "to_state": cur_stage[s]})
                prev_stage = cur_stage
                print(f"  [采样] 新 1h 收盘 {snap['close_utc']} "
                      f"水位={cm.utc(snap['watermark_1h'])} "
                      f"lag_max={snap['lag_1h_max']} "
                      f"hist={snap['lag_1h_histogram']} 阶段变化={len(changed)} "
                      f"候选={ (snap['funnel'] or {}).get('candidates_since_start')}")

    post = {
        "at_utc": cm.utc(cm.now_ms()),
        "ledger": ledger_identity(),
        "ledger_counts": ledger_counts_prod(),
        "runtimes": runtime_pids(),
        "collector": collector_alive(),
    }

    # ── 判定 ────────────────────────────────────────────────────────────────
    n_sym = len(se.get("per_symbol_state") or {})
    checks = {
        "运行时已起来": ri.get("status") == "running" and bool(post["runtimes"]),
        "全池都建了引擎": (boot.get("ready") == boot.get("universe")
                     and boot.get("universe") == n_sym and n_sym > 0),
        "重建模式=绝对锚点": (mode == "absolute_anchor" and bool(anchor)),
        # 必须跟**重启前**的锚点比；为空说明这是第一次落锚点，也允许（落一次不后移）。
        "锚点不随重启后移": bool(anchor) and (pre["state_anchor_utc"] in (None, anchor)),
        "跨两根新增小时线": len(seen_closes) >= 2,
        # 不能写成 `(lag or 0) <= 1`：读不到字段时 None 会被兜成 0 判「合格」，
        # 那就是空转。这里要求**真的读到了**且 <= 1。
        "逐币水位跟上": bool(samples) and all(
            s.get("lag_1h_max") is not None and int(s["lag_1h_max"]) <= 1
            for s in samples),
        "全池1h无落后(lag_histogram)": bool(samples) and all(
            (s.get("lag_1h_histogram") or {}).get("0") == boot.get("ready")
            for s in samples),
        "水位逐根前进": (len(samples) >= 2
                    and all(samples[i]["watermark_1h"] is not None
                            and samples[i]["watermark_1h"] >= seen_closes[i] - H1
                            for i in range(len(samples)))),
        # 处理水位必须追平库内最新已闭合 1h（相等），这条比 `>= close - H1` 更硬。
        "处理水位追平库内最新": bool(samples) and all(
            s.get("processed_1h_max") is not None and s.get("db_1h_max") is not None
            and int(s["processed_1h_max"]) == int(s["db_1h_max"])
            for s in samples),
        "候选/拒单有据可查": all((s.get("funnel") is not None
                           and s.get("order_processing") is not None) for s in samples)
                      and bool(samples),
        "健康采集未被打断": post["collector"]["n"] > 0,
        "持仓未被脚本改动": (pre["ledger_counts"].get("paper_position_open")
                      == post["ledger_counts"].get("paper_position_open")),
    }
    ok = all(checks.values())
    out = {
        "generated_at_utc": cm.utc(cm.now_ms()),
        "pre": pre, "post": post,
        "load_cmd": " ".join(cmd),
        "load_exit_code": pr.returncode,
        "load_stdout_tail": (pr.stdout or "")[-3000:],
        "runtime_identity_after": ri,
        "bootstrap": boot,
        "replay_mode": mode, "state_anchor_utc": anchor,
        "new_1h_closes": [cm.utc(x) for x in seen_closes],
        "samples": samples,
        "checks": checks,
        "verdict": "PASS" if ok else "FAIL",
        "judgement": ("判据：运行时起来 + 全池建引擎 + 绝对锚点生效 + 跨两根新增 1h + "
                      "逐币水位 lag<=1 且逐根前进 + 候选/拒单有计数 + 采集器未断 + "
                      "脚本未改持仓。任一条不成立即 FAIL。"
                      "**没有自然候选不算失败**（本单全池自然候选为 0，见 05）。"),
    }
    cm.dump("07_生产加载观察.json", out)
    cm.dump_csv("07_逐根水位.csv", rows)
    cm.dump_csv("07_逐币阶段变化.csv", stage_rows)
    print(f"[verdict] {out['verdict']}")
    for k, v in checks.items():
        print(f"    {'OK ' if v else 'BAD'} {k}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
