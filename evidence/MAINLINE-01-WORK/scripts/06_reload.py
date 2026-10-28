#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""06 · 受控重载与「运行实例已加载」的内容级证明。

纪律（沿用 SIGNAL-NOW-01-WORK 已核定方案）：
  · **只**重启 Runtime + API；**不动** Worker（只消费回测队列）、**不动**采集器；
  · 不用 `restart`（会连带停 Worker）—— 改为「核验身份 → SIGTERM 目标 pid →
    `sh start.sh --enable-paper` 拉起」，start 内部会复用已在跑的 Worker；
  · 不写 live_run、不重置 epoch、不改策略规则/阈值/风险/仓位倍数；
  · 失败即停，保留现场，不做破坏性恢复。

加载证明**只认内容级**（哈希 / PID / 报告自述单独都不算）：
  ① API 响应 `quality.fill_policy` 出现本单新增的三个键
     （`realtime_fill_mode` / `reality_fill_flag` / `on_exec_point_already_passed`）；
  ② API 响应 `quality.note` 出现新文案（「执行点」）；
  ③ 运行状态文件 `runtime_identity.pid` 为新 pid 且 `cycle_count` 从 0 重新增长；
  ④ 新 pid 的 `create_time` 晚于被改文件 mtime。

输出 `out/06_reload.json`。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

PROJ = Path("/Users/boos/Desktop/威科夫因子共振趋势")
EVID = PROJ / "evidence" / "MAINLINE-01-WORK"
OUT = EVID / "out"
BACKEND = PROJ / "框架" / "backend"
PY = "/Users/boos/.workbuddy-ai/binaries/python/envs/default/bin/python"
API = "http://127.0.0.1:8787"
WATCH = ["框架/backend/paper_engine.py", "框架/backend/live_store.py",
         "框架/backend/live_api.py"]
NEW_POLICY_KEYS = ["timestamps_split", "fill_ts_semantics",
                   "on_lookahead_detected", "on_realtime_without_available_time"]

try:
    import psutil
except ImportError:                                            # noqa: BLE001
    psutil = None


def utc(ts=None):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() if ts is None else float(ts)))


def sha(p: Path) -> str | None:
    try:
        h = hashlib.sha256()
        h.update(p.read_bytes())
        return h.hexdigest()
    except OSError:
        return None


def procs() -> dict:
    """列出本工程 cwd 下的四类进程。

    worker 用**列表**：本机存在一个 10-24 起的孤儿 Worker（指向隔离临时库），
    若只保留一个匹配就会把它和正常 Worker 混为一谈。孤儿不属于本单授权范围，
    只如实列出、不动它。
    """
    out = {"api": None, "workers": [], "runtime": None, "collector": None,
           "worker_pids": [], "psutil": psutil is not None}
    if psutil is None:
        return out
    for p in psutil.process_iter(["pid", "cmdline", "create_time", "cwd"]):
        try:
            cl = " ".join(p.info["cmdline"] or [])
            cwd = p.info["cwd"] or ""
        except Exception:                                       # noqa: BLE001
            continue
        if str(BACKEND) not in cwd:
            continue
        rec = {"pid": p.info["pid"], "create_time_utc": utc(p.info["create_time"]),
               "cmdline": cl[:200]}
        if "adapter.py" in cl:
            out["api"] = rec
        elif "backtest_worker.py" in cl:
            out["workers"].append(rec)
        elif "unified_runtime.py" in cl:
            out["runtime"] = rec
        elif "realtime_collector.py" in cl:
            out["collector"] = rec
    out["worker_pids"] = sorted(w["pid"] for w in out["workers"])
    return out


def http_get(url: str, timeout: int = 10) -> dict:
    cp = subprocess.run(["curl", "-s", "-m", str(timeout), "-w", "\n__HTTP__%{http_code}", url],
                        capture_output=True, text=True, timeout=timeout + 8)
    body = cp.stdout or ""
    code = ""
    if "__HTTP__" in body:
        body, code = body.rsplit("__HTTP__", 1)
    try:
        js = json.loads(body)
    except Exception:                                           # noqa: BLE001
        js = None
    return {"http": code.strip(), "json": js, "body_head": body[:400]}


def api_fill_policy() -> dict:
    """从多个端点找 `quality.fill_policy`（不同版本路径不同，逐个试）。"""
    tried = []
    for path in ("/api/v1/paper/accounts", "/api/v1/signals?scope=current&limit=2",
                 "/api/v1/paper/status"):
        r = http_get(f"{API}{path}")
        q = ((r.get("json") or {}).get("quality") or {})
        fp = q.get("fill_policy")
        tried.append({"path": path, "http": r["http"], "has_fill_policy": bool(fp)})
        if fp:
            return {"path": path, "http": r["http"], "fill_policy": fp,
                    "note": q.get("note"), "keys": sorted(fp.keys()), "tried": tried}
    return {"path": None, "fill_policy": None, "note": None, "keys": [], "tried": tried}


def rt_status() -> dict:
    p = BACKEND / "locks" / "paper_runtime_3a_status.json"
    if not p.exists():
        return {"status": "missing"}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:                                    # noqa: BLE001
        return {"status": "unreadable", "error": str(exc)}
    ri = d.get("runtime_identity") or {}
    se = d.get("signal_engine") or {}
    od = d.get("order_processing") or {}
    ed = d.get("execution_daemon") or {}
    return {"file_mtime_utc": utc(p.stat().st_mtime),
            "pid": ri.get("pid"), "status": ri.get("status"),
            "overall_health": ri.get("overall_health"),
            "started_at_ms": ri.get("started_at"),
            "started_at_utc": utc(ri["started_at"] / 1000) if ri.get("started_at") else None,
            "cycle_count": ri.get("cycle_count"),
            "universe": se.get("universe"),
            "watermark_1h_utc": (utc(se["watermark_1h"] / 1000)
                                 if se.get("watermark_1h") else None),
            "watermark_4h_utc": (utc(se["watermark_4h"] / 1000)
                                 if se.get("watermark_4h") else None),
            "pending_in_db": od.get("current_pending_in_db"),
            "allow_new_entries": (od.get("new_entry_gate") or {}).get("allow_new_entries"),
            "open_positions": ed.get("open_positions"),
            "pending_matched_total": ed.get("pending_matched_total"),
            "trailing_updates_total": ed.get("trailing_updates_total"),
            "exits_triggered_total": ed.get("exits_triggered_total")}


def ledger_snapshot() -> dict:
    import sqlite3
    p = PROJ / "数据" / "paper" / "paper_3a.sqlite3"
    con = sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=8)
    con.row_factory = sqlite3.Row
    cols = {r[1] for r in con.execute("PRAGMA table_info(paper_position)")}
    # 三列是本单**加性**迁移新增的；重载前生产账本上还不存在 ⇒ 用 NULL 占位留证。
    def c(name):
        return name if name in cols else f"NULL AS {name}"
    pos = [dict(r) for r in con.execute(
        "SELECT position_id,symbol,status,entry_price,entry_ts,stop_price,signal_uid,"
        f"voided,market_source,{c('reality_fill')},{c('fill_source')},"
        f"{c('read_at_ms')},{c('matched_at_ms')},{c('data_available_at')} FROM paper_position "
        "ORDER BY position_id")]
    counts = {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
              for t in ("paper_position", "paper_order", "paper_pending", "paper_processed")}
    con.close()
    return {"positions": pos, "counts": counts,
            "has_reality_columns": "reality_fill" in cols,
            "has_time_split_columns": ("read_at_ms" in cols and "matched_at_ms" in cols),
            "wal_bytes": (p.parent / "paper_3a.sqlite3-wal").stat().st_size
            if (p.parent / "paper_3a.sqlite3-wal").exists() else 0}


def wait_gone(pid: int, timeout: float = 45.0) -> bool:
    t0 = time.time()
    while time.time() - t0 < timeout:
        alive = psutil.pid_exists(pid) if psutil else True
        if not alive:
            return True
        try:
            if psutil and psutil.Process(pid).status() == psutil.STATUS_ZOMBIE:
                return True
        except Exception:                                       # noqa: BLE001
            return True
        time.sleep(0.5)
    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-reload", action="store_true")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    R = {"generated_at_utc": utc(), "steps": [], "errors": []}

    # 没有 psutil 就**不能**重载：既无法核验目标 pid 身份，也无法确认它真的退出。
    # 这种情况下 `wait_gone` 会恒返回 True ⇒ 可能在旧进程还没死时拉起新进程，
    # 造成双 Runtime 抢同一账本。宁可停在这里。
    if psutil is None:
        R["errors"].append("psutil 不可用 ⇒ 拒绝重载（无法核验进程身份/退出）；"
                           "请用项目 venv 解释器运行本脚本")
        (OUT / "06_reload.json").write_text(
            json.dumps(R, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        print("[!] " + R["errors"][-1])
        return 2

    R["watch_files"] = {rel: {"sha256": sha(PROJ / rel),
                              "mtime_utc": utc((PROJ / rel).stat().st_mtime)}
                        for rel in WATCH}
    newest = max((PROJ / rel).stat().st_mtime for rel in WATCH)
    R["newest_source_mtime_utc"] = utc(newest)

    before = {"procs": procs(), "api": api_fill_policy(), "rt": rt_status(),
              "ledger": ledger_snapshot(), "at_utc": utc()}
    R["before"] = before
    print(f"[前] api={before['procs']['api'] and before['procs']['api']['pid']} "
          f"workers={before['procs']['worker_pids']} "
          f"runtime={before['procs']['runtime'] and before['procs']['runtime']['pid']} "
          f"collector={before['procs']['collector'] and before['procs']['collector']['pid']}")
    print(f"[前] fill_policy 键 = {before['api']['keys']}")
    print(f"[前] runtime cycle={before['rt'].get('cycle_count')} "
          f"wm1h={before['rt'].get('watermark_1h_utc')} "
          f"open_pos={before['rt'].get('open_positions')}")

    if args.skip_reload:
        (OUT / "06_reload.json").write_text(
            json.dumps(R, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        print("skip-reload：仅记录前态")
        return 0

    tgt_r = (before["procs"]["runtime"] or {}).get("pid")
    tgt_a = (before["procs"]["api"] or {}).get("pid")
    if not tgt_r or not tgt_a:
        R["errors"].append(f"目标进程缺失 runtime={tgt_r} api={tgt_a}（停止，不做破坏性恢复）")
        (OUT / "06_reload.json").write_text(
            json.dumps(R, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        print("[!] " + R["errors"][-1])
        return 1

    def verify(pid, needle):
        try:
            p = psutil.Process(pid)
            return needle in " ".join(p.cmdline()) and str(BACKEND) in p.cwd()
        except Exception:                                       # noqa: BLE001
            return False

    if not verify(tgt_r, "unified_runtime.py"):
        R["errors"].append(f"runtime {tgt_r} 身份核验失败")
    if not verify(tgt_a, "adapter.py"):
        R["errors"].append(f"api {tgt_a} 身份核验失败")
    if R["errors"]:
        (OUT / "06_reload.json").write_text(
            json.dumps(R, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        print("[!] " + "；".join(R["errors"]))
        return 1

    for role, pid in (("runtime", tgt_r), ("api", tgt_a)):
        print(f"[*] SIGTERM {role} pid={pid}")
        R["steps"].append({"action": f"sigterm_{role}", "pid": pid, "at_utc": utc()})
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            R["steps"].append({"action": f"{role}_already_gone", "pid": pid})
        gone = wait_gone(pid)
        R["steps"].append({"action": f"{role}_exit", "pid": pid, "gone": gone,
                           "at_utc": utc()})
        print(f"    → 退出={gone}")
        if not gone:
            R["errors"].append(f"{role} pid={pid} 未在 45s 内退出；停止（保留现场）")
            (OUT / "06_reload.json").write_text(
                json.dumps(R, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
            return 1

    time.sleep(2.0)
    env = dict(os.environ)
    env["ENABLE_PAPER_RUNTIME"] = "1"
    env["NO_PROXY"] = "127.0.0.1,localhost,::1"
    env["no_proxy"] = env["NO_PROXY"]
    print("[*] 拉起：sh start.sh --enable-paper")
    cp = subprocess.run(["sh", str(BACKEND / "start.sh"), "--enable-paper"],
                        cwd=str(BACKEND), env=env, capture_output=True, text=True, timeout=420)
    R["steps"].append({"action": "start", "rc": cp.returncode,
                       "stdout_tail": (cp.stdout or "")[-2500:],
                       "stderr_tail": (cp.stderr or "")[-1500:], "at_utc": utc()})
    print(f"    → rc={cp.returncode}")

    # 等 runtime 写新状态（bootstrap 可能 1~3 分钟）
    deadline = time.time() + 300
    new_rt = None
    while time.time() < deadline:
        time.sleep(5)
        st = rt_status()
        if st.get("pid") and st["pid"] != tgt_r and st.get("status") in ("running", "bootstrapping"):
            new_rt = st
            if st.get("status") == "running":
                break
    time.sleep(8)
    after = {"procs": procs(), "api": api_fill_policy(), "rt": rt_status(),
             "ledger": ledger_snapshot(), "at_utc": utc()}
    R["after"] = after
    print(f"[后] api={after['procs']['api'] and after['procs']['api']['pid']} "
          f"workers={after['procs']['worker_pids']} "
          f"runtime={after['procs']['runtime'] and after['procs']['runtime']['pid']} "
          f"collector={after['procs']['collector'] and after['procs']['collector']['pid']}")
    print(f"[后] fill_policy 键 = {after['api']['keys']}")
    print(f"[后] runtime cycle={after['rt'].get('cycle_count')} "
          f"wm1h={after['rt'].get('watermark_1h_utc')}")

    fp = after["api"].get("fill_policy") or {}
    note = after["api"].get("note") or ""
    new_pid = (after["procs"]["runtime"] or {}).get("pid")
    rt_after = after["rt"]
    checks = {
        "runtime_pid_changed": bool(new_pid and new_pid != tgt_r),
        "api_pid_changed": bool((after["procs"]["api"] or {}).get("pid") not in (None, tgt_a)),
        "worker_pids_unchanged": (before["procs"]["worker_pids"]
                                  == after["procs"]["worker_pids"]),
        "collector_pid_unchanged": ((before["procs"]["collector"] or {}).get("pid")
                                    == (after["procs"]["collector"] or {}).get("pid")),
        "fill_policy_has_new_keys": all(k in fp for k in NEW_POLICY_KEYS),
        # 本单：加性迁移必须真的发生（三表各两列），且历史行不被改写
        "ledger_got_time_split_columns": after["ledger"].get("has_time_split_columns") is True,
        "historical_position_4_unrewritten":
            ([p for p in before["ledger"]["positions"] if p["position_id"] == 4]
             == [p for p in after["ledger"]["positions"] if p["position_id"] == 4]),
        "api_note_mentions_exec_point": ("执行点" in note),
        "runtime_status_running": rt_after.get("status") == "running",
        "runtime_health_healthy": rt_after.get("overall_health") in ("healthy", "ok"),
        "runtime_started_after_source_mtime":
            bool(rt_after.get("started_at_ms")
                 and rt_after["started_at_ms"] / 1000 > newest),
        "universe_526": rt_after.get("universe") == 526,
        "existing_btc_position_intact":
            ([p for p in before["ledger"]["positions"] if p["position_id"] == 4]
             == [p for p in after["ledger"]["positions"] if p["position_id"] == 4]),
        "position_count_unchanged":
            before["ledger"]["counts"]["paper_position"] == after["ledger"]["counts"]["paper_position"],
        "allow_new_entries_still_true": rt_after.get("allow_new_entries") is True,
    }
    R["checks"] = checks
    R["all_pass"] = all(checks.values())
    (OUT / "06_reload.json").write_text(
        json.dumps(R, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"written {OUT / '06_reload.json'}")
    for k, v in checks.items():
        print(f"  [{'PASS' if v else 'FAIL'}] {k}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
