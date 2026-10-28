# -*- coding: utf-8 -*-
"""SIGNAL-NOW-01-WORK｜步骤7：受控重载（最小范围 Runtime + API）+ 加载证明。

纪律（沿用 02 已核定方案）：
- **只**重启 Runtime 与 API；**不动** Worker（只消费回测队列）；**不动**采集器（pid 42278）。
- 不用 `restart`（会连带停 Worker），改为「核验身份 → SIGTERM 目标 pid → start.sh 拉起」。
- 不写 `live_run`、不重置 epoch、不改策略规则/阈值/风险/仓位倍数。
- 失败即停，保留现场，不做破坏性恢复（不整库恢复、不强平）。

加载证明（本单改动**不新增 API 字段**，故只能用前两条 + 源码 diff 指纹，如实标注）：
  ① 进程身份：新 pid 的 `create_time` **晚于**全部改动文件 mtime；
  ② 运行时自述：状态文件 `runtime_identity.pid/started_at` 为新值、`cycle_count` 从 0 重新增长；
  ③ 源码 diff：`baseline/src_before/` ↔ 现行文件的净差异哈希（证明磁盘版本就是被测版本）。
"""
from __future__ import annotations

import hashlib
import json
import os
import signal
import subprocess
import time
from pathlib import Path

import psutil

PROJ = Path("/Users/boos/Desktop/威科夫因子共振趋势")
ROOT = PROJ / "evidence" / "SIGNAL-NOW-01-WORK"
BASE = ROOT / "baseline"
OUT = ROOT / "out"
LOGS = ROOT / "logs"
BACKEND = PROJ / "框架/backend"

WATCH_FILES = [
    "框架/backend/paper_engine.py",
    "框架/backend/unified_runtime.py",
]
API_URL = "http://127.0.0.1:8787"


def utc(ts=None) -> str:
    t = time.time() if ts is None else float(ts)
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def find_procs() -> dict:
    out = {"api": None, "worker": None, "runtime": None, "collector": None, "others": []}
    for p in psutil.process_iter(["pid", "cmdline", "create_time", "cwd"]):
        try:
            cl = " ".join(p.info["cmdline"] or [])
            cwd = p.info["cwd"] or ""
        except Exception:
            continue
        if str(BACKEND) not in cwd:
            continue
        rec = {"pid": p.info["pid"], "create_time_utc": utc(p.info["create_time"]),
               "create_time": p.info["create_time"], "cmdline": cl[:220], "cwd": cwd}
        if "adapter.py" in cl:
            out["api"] = rec
        elif "backtest_worker.py" in cl:
            if "数据/任务队列/jobs.sqlite3" in cl:
                out["worker"] = rec
            else:
                out["others"].append(rec)
        elif "unified_runtime.py" in cl:
            out["runtime"] = rec
        elif "realtime_collector.py" in cl:
            out["collector"] = rec
    return out


def http_get(url: str, timeout: int = 8) -> dict:
    cp = subprocess.run(["curl", "-s", "-m", str(timeout), "-w", "\n__HTTP__%{http_code}", url],
                        capture_output=True, text=True, timeout=timeout + 6)
    body, code = cp.stdout, ""
    if "__HTTP__" in body:
        body, code = body.rsplit("__HTTP__", 1)
    try:
        js = json.loads(body)
    except Exception:
        js = None
    return {"http": code.strip(), "json": js, "body_head": body[:600]}


def api_sig() -> dict:
    r = http_get(f"{API_URL}/api/v1/signals?scope=current&limit=2")
    js = r.get("json") or {}
    q = (js or {}).get("quality") or {}
    eb = q.get("excluded_breakdown") or {}
    return {"http": r["http"],
            "strategy_version": (js or {}).get("strategy_version"),
            "rule_version": (js or {}).get("rule_version"),
            "param_hash": (js or {}).get("param_hash"),
            "total": (js or {}).get("total"),
            "excluded_n": q.get("excluded_n"),
            "by_reason": eb.get("by_reason"),
            "effective_floor_ms": q.get("effective_floor_ms"),
            "live_start_ms": q.get("live_start_ms"),
            "paper_epoch_ms": q.get("paper_epoch_ms")}


def rt_status() -> dict:
    p = BACKEND / "locks" / "paper_runtime_3a_status.json"
    if not p.exists():
        return {"status": "missing"}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        return {"status": "unreadable", "error": str(e)}
    ri = d.get("runtime_identity") or {}
    se = d.get("signal_engine") or {}
    od = d.get("order_processing") or {}
    ed = d.get("execution_daemon") or {}
    return {"status": "ok", "file_mtime_utc": utc(p.stat().st_mtime),
            "pid": ri.get("pid"), "started_at": ri.get("started_at"),
            "started_at_utc": utc(ri["started_at"] / 1000) if ri.get("started_at") else None,
            "cycle_count": ri.get("cycle_count"), "overall_health": ri.get("overall_health"),
            "signal_engine": {k: se.get(k) for k in
                              ("cycles", "watermark_1h", "watermark_4h", "universe",
                               "watermark_1h_utc", "watermark_4h_utc")},
            "order_processing": {k: od.get(k) for k in
                                 ("current_pending_in_db", "processed_total",
                                  "registered_total")},
            "execution_daemon": ed}


def wait_gone(pid: int, timeout: float = 40.0) -> bool:
    t0 = time.time()
    while time.time() - t0 < timeout:
        if not psutil.pid_exists(pid):
            return True
        try:
            if psutil.Process(pid).status() == psutil.STATUS_ZOMBIE:
                return True
        except Exception:
            return True
        time.sleep(0.5)
    return not psutil.pid_exists(pid)


def main() -> int:
    R = {"ticket": "SIGNAL-NOW-01-WORK", "step": "7 受控重载与加载证明",
         "started_at_utc": utc(), "actions": [], "errors": []}

    R["watch_files"] = {}
    newest = 0.0
    for rel in WATCH_FILES:
        p = PROJ / rel
        st = p.stat()
        newest = max(newest, st.st_mtime)
        R["watch_files"][rel] = {"mtime_utc": utc(st.st_mtime), "mtime": st.st_mtime,
                                 "sha256": sha256_file(p), "size": st.st_size}
    R["newest_source_mtime_utc"] = utc(newest)
    # 与改动前备份的净差异（证明磁盘版本就是被测版本）
    R["diff_vs_backup"] = {}
    for rel in WATCH_FILES:
        name = Path(rel).name
        b = BASE / "src_before" / name
        R["diff_vs_backup"][name] = {
            "before_sha256": sha256_file(b) if b.exists() else None,
            "now_sha256": R["watch_files"][rel]["sha256"],
            "changed": (sha256_file(b) != R["watch_files"][rel]["sha256"]) if b.exists() else None}

    before = find_procs()
    R["before"] = {"procs": before, "api_sig": api_sig(), "rt_status": rt_status(),
                   "at_utc": utc()}
    print(f"[前] api={before['api'] and before['api']['pid']} "
          f"worker={before['worker'] and before['worker']['pid']} "
          f"runtime={before['runtime'] and before['runtime']['pid']} "
          f"collector={before['collector'] and before['collector']['pid']}")
    print(f"[前] 最新源码 mtime = {R['newest_source_mtime_utc']}")
    print(f"[前] API total={R['before']['api_sig']['total']} "
          f"excluded_n={R['before']['api_sig']['excluded_n']} "
          f"floor={R['before']['api_sig']['effective_floor_ms']}")
    print(f"[前] runtime cycles={R['before']['rt_status'].get('cycle_count')} "
          f"watermark_1h={R['before']['rt_status'].get('signal_engine', {}).get('watermark_1h_utc')}")

    tgt_r = before["runtime"]["pid"] if before["runtime"] else None
    tgt_a = before["api"]["pid"] if before["api"] else None
    if not tgt_r or not tgt_a:
        R["errors"].append(f"未找到 runtime/api 目标进程 runtime={tgt_r} api={tgt_a}")
        (OUT / "07_重载前后身份.json").write_text(
            json.dumps(R, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        print("[!] 目标进程缺失，停止（不做破坏性恢复）")
        return 1

    def verify(pid: int, needle: str) -> bool:
        try:
            p = psutil.Process(pid)
            return needle in " ".join(p.cmdline()) and str(BACKEND) in p.cwd()
        except Exception:
            return False

    if not verify(tgt_r, "unified_runtime.py"):
        R["errors"].append(f"runtime {tgt_r} 身份核验失败，停止")
    if not verify(tgt_a, "adapter.py"):
        R["errors"].append(f"api {tgt_a} 身份核验失败，停止")
    if R["errors"]:
        (OUT / "07_重载前后身份.json").write_text(
            json.dumps(R, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        print("[!] " + "；".join(R["errors"]))
        return 1

    for role, pid in (("runtime", tgt_r), ("api", tgt_a)):
        print(f"[*] SIGTERM {role} pid={pid} …")
        R["actions"].append({"action": f"sigterm_{role}", "pid": pid, "at_utc": utc()})
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            R["actions"].append({"action": f"{role}_already_gone", "pid": pid})
        gone = wait_gone(pid)
        R["actions"].append({"action": f"{role}_exit", "pid": pid, "gone": gone,
                             "at_utc": utc()})
        print(f"    → 已退出={gone}")
        if not gone:
            R["errors"].append(f"{role} pid={pid} 40s 内未退出；停止后续动作（保留现场）")
            break

    if R["errors"]:
        (OUT / "07_重载前后身份.json").write_text(
            json.dumps(R, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        print("[!] 退出失败，停止： " + "；".join(R["errors"]))
        return 1

    time.sleep(2.0)
    print("[*] 拉起：ENABLE_PAPER_RUNTIME=1 sh start.sh --enable-paper …")
    env = dict(os.environ)
    env["ENABLE_PAPER_RUNTIME"] = "1"
    env["NO_PROXY"] = "127.0.0.1,localhost,::1"
    env["no_proxy"] = env["NO_PROXY"]
    cp = subprocess.run(["sh", str(BACKEND / "start.sh"), "--enable-paper"],
                        cwd=str(BACKEND), env=env, capture_output=True, text=True, timeout=300)
    (LOGS / "reload_start.log").write_text(
        (cp.stdout or "") + "\n---STDERR---\n" + (cp.stderr or ""), encoding="utf-8")
    R["actions"].append({"action": "start_sh", "rc": cp.returncode,
                         "stdout_tail": (cp.stdout or "")[-3000:],
                         "stderr_tail": (cp.stderr or "")[-2000:], "at_utc": utc()})
    print(f"    rc={cp.returncode}")
    print("    " + (cp.stdout or "").replace("\n", "\n    ")[:1600])

    health_ok = False
    for _ in range(60):
        if http_get(f"{API_URL}/api/health", timeout=3)["http"] == "200":
            health_ok = True
            break
        time.sleep(1.0)
    R["health_ok"] = health_ok
    print(f"[*] API 健康 = {health_ok}")

    after = None
    for _ in range(90):
        after = find_procs()
        if after["runtime"] and after["api"]:
            break
        time.sleep(2.0)
    after = after or find_procs()
    R["after"] = {"procs": after, "at_utc": utc()}
    print(f"[后] api={after['api'] and after['api']['pid']} "
          f"worker={after['worker'] and after['worker']['pid']} "
          f"runtime={after['runtime'] and after['runtime']['pid']} "
          f"collector={after['collector'] and after['collector']['pid']}")

    proof = {"newest_source_mtime_utc": R["newest_source_mtime_utc"]}
    for role in ("api", "runtime"):
        rec = after.get(role)
        if not rec:
            proof[f"{role}_loaded"] = False
            proof[f"{role}_why"] = "进程不存在"
            continue
        newer = rec["create_time"] > newest
        proof[f"{role}_pid"] = rec["pid"]
        proof[f"{role}_create_time_utc"] = rec["create_time_utc"]
        proof[f"{role}_started_after_source_change"] = newer
        proof[f"{role}_loaded"] = bool(newer)
        proof[f"{role}_why"] = ("进程启动时刻晚于全部改动文件 mtime ⇒ 磁盘版本已加载"
                               if newer else "进程启动早于改动文件 mtime ⇒ 仍是旧代码")
    proof["worker_pid_unchanged"] = bool(before["worker"] and after["worker"]
                                         and before["worker"]["pid"] == after["worker"]["pid"])
    proof["collector_pid_unchanged"] = bool(
        before["collector"] and after["collector"]
        and before["collector"]["pid"] == after["collector"]["pid"])
    proof["api_sig_after"] = api_sig()
    R["load_proof"] = proof

    # 等预热（526 标的，02 实测约 10 分钟），期间每 30 秒看一次
    print("[*] 等预热完成（universe 满 + cycles>0）…")
    warm = []
    for i in range(60):
        s = rt_status()
        se = s.get("signal_engine") or {}
        warm.append({"at_utc": utc(), "pid": s.get("pid"),
                     "cycle_count": s.get("cycle_count"),
                     "cycles": se.get("cycles"), "universe": se.get("universe"),
                     "watermark_1h_utc": se.get("watermark_1h_utc"),
                     "watermark_4h_utc": se.get("watermark_4h_utc")})
        print(f"    [{i:02d}] {warm[-1]}")
        if (s.get("pid") == (after.get("runtime") or {}).get("pid")
                and (se.get("cycles") or 0) > 0):
            break
        time.sleep(30)
    R["warmup_samples"] = warm
    R["after"]["rt_status"] = rt_status()

    import sqlite3
    con = sqlite3.connect(f"file:{PROJ}/框架/backend/live/live_signals.sqlite3?mode=ro",
                          uri=True, timeout=20)
    try:
        lv = {r[0]: int(r[1]) for r in con.execute("SELECT key,value_json FROM live_run")}
    finally:
        con.close()
    R["levels_after_reload"] = lv
    R["levels_unchanged"] = (lv.get("paper_epoch_ms") == 1791939600000
                             and lv.get("live_start_ms") == 1792748844240
                             and lv.get("state_anchor_ms") == 1782542715686)

    R["finished_at_utc"] = utc()
    p = OUT / "07_重载前后身份.json"
    p.write_text(json.dumps(R, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    print("\n[加载证明]")
    print(f"  源码最新 mtime  : {proof['newest_source_mtime_utc']}")
    print(f"  api     pid={proof.get('api_pid')} 启动={proof.get('api_create_time_utc')} "
          f"⇒ 已加载={proof.get('api_loaded')}")
    print(f"  runtime pid={proof.get('runtime_pid')} 启动={proof.get('runtime_create_time_utc')} "
          f"⇒ 已加载={proof.get('runtime_loaded')}")
    print(f"  worker 未变={proof['worker_pid_unchanged']}  "
          f"collector 未变={proof['collector_pid_unchanged']}")
    print(f"  水位未变={R['levels_unchanged']} {lv}")
    print(f"\n产物：{p}")
    return 0 if not R["errors"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
