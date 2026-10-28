# -*- coding: utf-8 -*-
"""SIGNAL-NOW-01-WORK｜步骤9：收尾最小重载（只为「磁盘版本 == 运行版本」零瑕疵）。

为什么需要这一次：
- 07 的加载证明用的是「脚本启动时」采集的源码 mtime（≈17:48:14 之前）。
- 之后（17:48:50）又对 `paper_engine.py` 做了**纯文档改动**（`FILL_POLICY` 增 2 个字符串键 +
  一处 docstring），落在进程启动（17:48:27）之后 23 秒 ⇒ 严格讲该进程**未加载**这两行文本。
- 两者都不参与任何判定（字典里的说明性字符串 + docstring），但为了不给验收留口径瑕疵，
  再重载一次，使「进程启动时刻 > 全部源文件 mtime」成立。

纪律同 07：只停 Runtime + API；不动 Worker（只消费回测队列）；不动采集器；不写 live_run；
不重置 epoch；失败即停、保留现场、不做破坏性恢复。
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
OUT = ROOT / "out"
LOGS = ROOT / "logs"
BACKEND = PROJ / "框架/backend"

WATCH_FILES = ["框架/backend/paper_engine.py", "框架/backend/unified_runtime.py"]
API_URL = "http://127.0.0.1:8787"


def utc(ts=None):
    t = time.time() if ts is None else float(ts)
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def find_procs():
    out = {"api": None, "worker": None, "runtime": None, "collector": None}
    for p in psutil.process_iter(["pid", "cmdline", "create_time", "cwd"]):
        try:
            cl = " ".join(p.info["cmdline"] or [])
            cwd = p.info["cwd"] or ""
        except Exception:
            continue
        if str(BACKEND) not in cwd:
            continue
        rec = {"pid": p.info["pid"], "create_time": p.info["create_time"],
               "create_time_utc": utc(p.info["create_time"])}
        if "adapter.py" in cl:
            out["api"] = rec
        elif "backtest_worker.py" in cl:
            out["worker"] = rec
        elif "unified_runtime.py" in cl:
            out["runtime"] = rec
        elif "realtime_collector.py" in cl:
            out["collector"] = rec
    return out


def http_get(url, timeout=5):
    cp = subprocess.run(["curl", "-s", "-m", str(timeout), "-w", "\n__H__%{http_code}", url],
                        capture_output=True, text=True, timeout=timeout + 5)
    body, code = cp.stdout, ""
    if "__H__" in body:
        body, code = body.rsplit("__H__", 1)
    try:
        js = json.loads(body)
    except Exception:
        js = None
    return {"http": code.strip(), "json": js}


def rt_status():
    p = BACKEND / "locks" / "paper_runtime_3a_status.json"
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        return {"error": str(e)}
    ri = d.get("runtime_identity") or {}
    se = d.get("signal_engine") or {}
    return {"pid": ri.get("pid"), "started_at": ri.get("started_at"),
            "cycle_count": ri.get("cycle_count"),
            "universe": se.get("universe"),
            "watermark_1h": se.get("watermark_1h"),
            "watermark_4h": se.get("watermark_4h")}


def wait_gone(pid, timeout=40.0):
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
    R = {"ticket": "SIGNAL-NOW-01-WORK", "step": "9 收尾最小重载（磁盘==运行）",
         "started_at_utc": utc(), "actions": [], "errors": []}

    newest = 0.0
    R["watch_files"] = {}
    for rel in WATCH_FILES:
        p = PROJ / rel
        st = p.stat()
        newest = max(newest, st.st_mtime)
        R["watch_files"][rel] = {"mtime_utc": utc(st.st_mtime), "mtime": st.st_mtime,
                                 "sha256": sha256_file(p), "size": st.st_size}
    R["newest_source_mtime_utc"] = utc(newest)
    R["newest_source_mtime"] = newest

    before = find_procs()
    R["before"] = {"procs": before, "rt_status": rt_status(), "at_utc": utc()}
    print(f"[前] runtime={before['runtime'] and before['runtime']['pid']} "
          f"api={before['api'] and before['api']['pid']} "
          f"worker={before['worker'] and before['worker']['pid']} "
          f"collector={before['collector'] and before['collector']['pid']}")
    print(f"[前] 最新源码 mtime={R['newest_source_mtime_utc']}")

    if not before["runtime"] or not before["api"]:
        R["errors"].append("目标进程缺失")
        (OUT / "09_收尾重载.json").write_text(
            json.dumps(R, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        return 1

    def verify(pid, needle):
        try:
            p = psutil.Process(pid)
            return needle in " ".join(p.cmdline()) and str(BACKEND) in p.cwd()
        except Exception:
            return False

    if not verify(before["runtime"]["pid"], "unified_runtime.py"):
        R["errors"].append("runtime 身份核验失败")
    if not verify(before["api"]["pid"], "adapter.py"):
        R["errors"].append("api 身份核验失败")
    if R["errors"]:
        (OUT / "09_收尾重载.json").write_text(
            json.dumps(R, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        print("[!] " + "；".join(R["errors"]))
        return 1

    for role in ("runtime", "api"):
        pid = before[role]["pid"]
        print(f"[*] SIGTERM {role} pid={pid}")
        R["actions"].append({"action": f"sigterm_{role}", "pid": pid, "at_utc": utc()})
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        gone = wait_gone(pid)
        R["actions"].append({"action": f"{role}_exit", "pid": pid, "gone": gone,
                             "at_utc": utc()})
        if not gone:
            R["errors"].append(f"{role} 40s 未退出；停止")
            break

    if R["errors"]:
        (OUT / "09_收尾重载.json").write_text(
            json.dumps(R, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        print("[!] " + "；".join(R["errors"]))
        return 1

    time.sleep(2.0)
    env = dict(os.environ)
    env["ENABLE_PAPER_RUNTIME"] = "1"
    env["NO_PROXY"] = "127.0.0.1,localhost,::1"
    env["no_proxy"] = env["NO_PROXY"]
    cp = subprocess.run(["sh", str(BACKEND / "start.sh"), "--enable-paper"],
                        cwd=str(BACKEND), env=env, capture_output=True, text=True, timeout=300)
    (LOGS / "reload2_start.log").write_text(
        (cp.stdout or "") + "\n---STDERR---\n" + (cp.stderr or ""), encoding="utf-8")
    R["actions"].append({"action": "start_sh", "rc": cp.returncode,
                         "stdout_tail": (cp.stdout or "")[-2000:], "at_utc": utc()})
    print(f"    rc={cp.returncode}")

    health = False
    for _ in range(60):
        if http_get(f"{API_URL}/api/health", timeout=3)["http"] == "200":
            health = True
            break
        time.sleep(1.0)
    R["health_ok"] = health
    print(f"[*] API 健康={health}")

    after = None
    for _ in range(90):
        after = find_procs()
        if after["runtime"] and after["api"]:
            break
        time.sleep(2.0)
    after = after or find_procs()
    R["after"] = {"procs": after, "at_utc": utc()}
    print(f"[后] runtime={after['runtime'] and after['runtime']['pid']} "
          f"api={after['api'] and after['api']['pid']} "
          f"worker={after['worker'] and after['worker']['pid']} "
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
        proof[f"{role}_why"] = ("启动时刻晚于全部改动文件 mtime ⇒ 磁盘版本即运行版本"
                               if newer else "启动早于改动文件 mtime ⇒ 仍是旧代码")
    proof["worker_pid_unchanged"] = bool(before["worker"] and after["worker"]
                                         and before["worker"]["pid"] == after["worker"]["pid"])
    proof["collector_pid_unchanged"] = bool(
        before["collector"] and after["collector"]
        and before["collector"]["pid"] == after["collector"]["pid"])
    R["load_proof"] = proof

    # 水位未被改写
    import sqlite3
    con = sqlite3.connect(f"file:{BACKEND}/live/live_signals.sqlite3?mode=ro",
                          uri=True, timeout=20)
    try:
        lv = {r[0]: int(r[1]) for r in con.execute("SELECT key,value_json FROM live_run")}
    finally:
        con.close()
    R["levels_after"] = lv
    R["levels_unchanged"] = (lv.get("paper_epoch_ms") == 1791939600000
                             and lv.get("live_start_ms") == 1792748844240
                             and lv.get("state_anchor_ms") == 1782542715686)

    R["rt_status_after"] = rt_status()
    R["finished_at_utc"] = utc()
    p = OUT / "09_收尾重载.json"
    p.write_text(json.dumps(R, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    print("\n[加载证明]")
    print(f"  源码最新 mtime : {proof['newest_source_mtime_utc']}")
    print(f"  api     pid={proof.get('api_pid')} 启动={proof.get('api_create_time_utc')} "
          f"⇒ 已加载={proof.get('api_loaded')}")
    print(f"  runtime pid={proof.get('runtime_pid')} 启动={proof.get('runtime_create_time_utc')} "
          f"⇒ 已加载={proof.get('runtime_loaded')}")
    print(f"  worker 未变={proof['worker_pid_unchanged']} "
          f"collector 未变={proof['collector_pid_unchanged']}")
    print(f"  水位未变={R['levels_unchanged']}")
    print(f"\n产物：{p}")
    return 0 if not R["errors"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
