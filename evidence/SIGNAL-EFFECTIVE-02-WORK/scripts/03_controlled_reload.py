# -*- coding: utf-8 -*-
"""SIGNAL-EFFECTIVE-02-WORK｜受控重载 + 加载证明（最小范围：Runtime + API）。

纪律：
- **只**重启 Runtime 与 API；**不动** Worker（与 01 六处改动无关，只消费回测队列）；
  **不动**采集器（pid 42278）。
- 不用 `restart`（它会连带停 Worker），改为「核验身份 → SIGTERM 目标 pid → start.sh 拉起」。
- 每步前后落盘身份与哈希；失败即停，保留现场，不做破坏性恢复。
- 不改策略规则、阈值、风险参数、评分权重、仓位倍数；不写 `live_run`、不重置 epoch。

加载证明（三者缺一不算）：
  ① 进程身份：新 pid 的 `create_time` **晚于**所有改动文件的 mtime ⇒ 磁盘版本已被加载；
  ② 运行时行为：API 返回里出现旧代码不存在的字段（`excluded_breakdown.unapproved_config`）；
  ③ 运行时自述：Runtime 状态文件 / 日志里的启动行与水位。
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
ROOT = PROJ / "evidence" / "SIGNAL-EFFECTIVE-02-WORK"
BASE = ROOT / "baseline"
OUT = ROOT / "out"
LOGS = ROOT / "logs"
BACKEND = PROJ / "框架/backend"
OUT.mkdir(parents=True, exist_ok=True)
LOGS.mkdir(parents=True, exist_ok=True)

# 01 六处改动所在文件（加载判据的 mtime 基准）
WATCH_FILES = [
    "框架/backend/paper_engine.py",
    "框架/backend/unified_runtime.py",
    "框架/backend/live_api.py",
    "框架/backend/live_worker.py",
]
API_URL = "http://127.0.0.1:8787"
SIGNALS_URL = f"{API_URL}/api/v1/signals?scope=current&limit=2&include_excluded=1"


def utc(ms_or_ts=None) -> str:
    t = time.time() if ms_or_ts is None else float(ms_or_ts)
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def find_procs() -> dict:
    """按命令行识别本实例的 api / worker / runtime / collector。"""
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
            # 只认 jobs_db 指向生产库的
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
    return {"http": code.strip(), "json": js, "body_head": body[:400]}


def api_signature() -> dict:
    """取 API 的「指纹」：字段集合 + 关键计数（用于判断是否加载了新代码）。

    加载判据选 `quality.excluded_breakdown.by_reason.unapproved_config`：
    该键**只由 01 改动⑤新增**（SIGNAL-UI-LINK-07 引入的 excluded_breakdown 本身有 6 个键，
    不含 unapproved_config）⇒ 它的出现与否，等价于「api 进程是否加载了 15:05:48 的版本」。
    """
    r = http_get(f"{API_URL}/api/v1/signals?scope=current&limit=2")
    js = r.get("json") or {}
    q = (js or {}).get("quality") or {}
    eb = q.get("excluded_breakdown") or {}
    by_reason = eb.get("by_reason") or {}
    sig = {
        "http": r["http"],
        "top_keys": sorted(js.keys()) if isinstance(js, dict) else None,
        "quality_keys": sorted(q.keys()) if isinstance(q, dict) else None,
        "excluded_n": q.get("excluded_n"),
        "excluded_breakdown": eb,
        "by_reason_keys": sorted(by_reason.keys()),
        "has_unapproved_config": "unapproved_config" in by_reason,
        "unapproved_config": by_reason.get("unapproved_config"),
        "total": (js or {}).get("total"),
        "has_more": (js or {}).get("has_more"),
        "missing_reason": (js or {}).get("missing_reason"),
        "strategy_version": (js or {}).get("strategy_version"),
        "param_hash": (js or {}).get("param_hash"),
        "ledger_source": (js or {}).get("ledger_source"),
        "as_of": (js or {}).get("as_of"),
        "live_start_ms": q.get("live_start_ms"),
        "paper_epoch_ms": q.get("paper_epoch_ms"),
        "effective_floor_ms": q.get("effective_floor_ms"),
    }
    return sig


def item_field_probe() -> dict:
    """取一条研究信号的 item 字段集合（证明 live_api 的共振/研究子对象是否在跑）。"""
    r = http_get(f"{API_URL}/api/v1/signals?scope=all&limit=1")
    js = r.get("json") or {}
    items = (js.get("items") or [])
    return {
        "http": r["http"], "total": js.get("total"),
        "item_keys": sorted(items[0].keys()) if items else None,
        "has_resonance_fields": bool(items) and ("resonance_score" in (items[0] or {})),
        "has_research_block": bool(items) and ("research" in (items[0] or {})),
    }


def wait_gone(pid: int, timeout: float = 30.0) -> bool:
    t0 = time.time()
    while time.time() - t0 < timeout:
        if not psutil.pid_exists(pid):
            return True
        try:
            p = psutil.Process(pid)
            if p.status() == psutil.STATUS_ZOMBIE:
                return True
        except Exception:
            return True
        time.sleep(0.5)
    return not psutil.pid_exists(pid)


def main() -> int:
    R = {"ticket": "SIGNAL-EFFECTIVE-02-WORK", "step": "3-4 受控重载与加载证明",
         "started_at_utc": utc(), "actions": [], "errors": []}

    # ── 0. 前置：文件 mtime 基准 ────────────────────────────────────────────
    R["watch_files"] = {}
    newest_mtime = 0.0
    for rel in WATCH_FILES:
        p = PROJ / rel
        st = p.stat()
        newest_mtime = max(newest_mtime, st.st_mtime)
        R["watch_files"][rel] = {"mtime_utc": utc(st.st_mtime), "mtime": st.st_mtime,
                                 "sha256": sha256_file(p), "size": st.st_size}
    R["newest_source_mtime_utc"] = utc(newest_mtime)

    # ── 1. 重载前身份 ───────────────────────────────────────────────────────
    before = find_procs()
    R["before"] = {"procs": before, "api_sig": api_signature(), "item_probe": item_field_probe(),
                   "at_utc": utc()}
    print(f"[前] api={before['api'] and before['api']['pid']} "
          f"worker={before['worker'] and before['worker']['pid']} "
          f"runtime={before['runtime'] and before['runtime']['pid']} "
          f"collector={before['collector'] and before['collector']['pid']}")
    print(f"[前] API 有 unapproved_config 计数 = {R['before']['api_sig']['has_unapproved_config']}"
          f"  by_reason={R['before']['api_sig']['by_reason_keys']}"
          f"  total={R['before']['api_sig']['total']}")
    print(f"[前] 最新源码 mtime = {R['newest_source_mtime_utc']}")

    tgt_r = before["runtime"]["pid"] if before["runtime"] else None
    tgt_a = before["api"]["pid"] if before["api"] else None
    if not tgt_r or not tgt_a:
        R["errors"].append(f"未找到 runtime/api 目标进程 runtime={tgt_r} api={tgt_a}")
        (OUT / "05_重载前后身份.json").write_text(
            json.dumps(R, ensure_ascii=False, indent=2), encoding="utf-8")
        print("[!] 目标进程缺失，停止（不做破坏性恢复）")
        return 1

    # 身份二次核验（防止误杀）
    def verify(pid: int, needle: str) -> bool:
        try:
            p = psutil.Process(pid)
            cl = " ".join(p.cmdline())
            cwd = p.cwd()
        except Exception:
            return False
        return needle in cl and str(BACKEND) in cwd

    if not verify(tgt_r, "unified_runtime.py"):
        R["errors"].append(f"runtime {tgt_r} 身份核验失败，停止")
    if not verify(tgt_a, "adapter.py"):
        R["errors"].append(f"api {tgt_a} 身份核验失败，停止")
    if R["errors"]:
        (OUT / "05_重载前后身份.json").write_text(
            json.dumps(R, ensure_ascii=False, indent=2), encoding="utf-8")
        print("[!] " + "；".join(R["errors"]))
        return 1

    # ── 2. SIGTERM runtime（先停执行侧，避免 api 先死导致状态读不到）───────
    for role, pid in (("runtime", tgt_r), ("api", tgt_a)):
        print(f"[*] SIGTERM {role} pid={pid} …")
        R["actions"].append({"action": f"sigterm_{role}", "pid": pid, "at_utc": utc()})
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            R["actions"].append({"action": f"{role}_already_gone", "pid": pid})
        gone = wait_gone(pid, 40.0)
        R["actions"].append({"action": f"{role}_exit", "pid": pid,
                             "gone": gone, "at_utc": utc()})
        print(f"    → 已退出={gone}")
        if not gone:
            R["errors"].append(f"{role} pid={pid} 40s 内未退出；停止后续动作（保留现场）")
            break

    if R["errors"]:
        (OUT / "05_重载前后身份.json").write_text(
            json.dumps(R, ensure_ascii=False, indent=2), encoding="utf-8")
        print("[!] 退出失败，停止： " + "；".join(R["errors"]))
        return 1

    # 留 2s 让端口释放
    time.sleep(2.0)

    # ── 3. 拉起（start.sh，带 --enable-paper；Worker 存活 ⇒ 自动复用）──────
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
                         "stdout_tail": (cp.stdout or "")[-2500:],
                         "stderr_tail": (cp.stderr or "")[-1500:], "at_utc": utc()})
    print(f"    rc={cp.returncode}")
    print("    " + (cp.stdout or "").replace("\n", "\n    ")[:1500])

    # ── 4. 等 API 健康 + Runtime 起来 ──────────────────────────────────────
    health_ok = False
    for _ in range(60):
        h = http_get(f"{API_URL}/api/health", timeout=3)
        if h["http"] == "200":
            health_ok = True
            break
        time.sleep(1.0)
    R["health_ok"] = health_ok
    print(f"[*] API 健康 = {health_ok}")

    # runtime 可能需要更久（预热 526 标的），先等到进程出现
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

    # ── 5. 加载证明 ────────────────────────────────────────────────────────
    proof = {"newest_source_mtime_utc": R["newest_source_mtime_utc"]}
    for role in ("api", "runtime"):
        rec = after.get(role)
        if not rec:
            proof[f"{role}_loaded"] = False
            proof[f"{role}_why"] = "进程不存在"
            continue
        newer = rec["create_time"] > newest_mtime
        proof[f"{role}_pid"] = rec["pid"]
        proof[f"{role}_create_time_utc"] = rec["create_time_utc"]
        proof[f"{role}_started_after_source_change"] = newer
        proof[f"{role}_loaded"] = bool(newer)
        proof[f"{role}_why"] = ("进程启动时刻晚于全部改动文件 mtime ⇒ 磁盘版本已加载"
                               if newer else "进程启动早于改动文件 mtime ⇒ 仍是旧代码")
    proof["worker_pid_unchanged"] = (before["worker"] and after["worker"]
                                     and before["worker"]["pid"] == after["worker"]["pid"])
    proof["collector_pid_unchanged"] = (before["collector"] and after["collector"]
                                        and before["collector"]["pid"] == after["collector"]["pid"])

    sig_after = api_signature()
    probe_after = item_field_probe()
    R["after"]["api_sig"] = sig_after
    R["after"]["item_probe"] = probe_after
    proof["api_has_unapproved_config_before"] = R["before"]["api_sig"]["has_unapproved_config"]
    proof["api_has_unapproved_config_after"] = sig_after["has_unapproved_config"]
    proof["api_unapproved_config_counter"] = sig_after.get("unapproved_config")
    proof["api_by_reason_keys_before"] = R["before"]["api_sig"]["by_reason_keys"]
    proof["api_by_reason_keys_after"] = sig_after["by_reason_keys"]
    proof["behavior_changed"] = (proof["api_has_unapproved_config_before"] is False
                                 and proof["api_has_unapproved_config_after"] is True)
    proof["item_has_research_block_after"] = probe_after.get("has_research_block")
    proof["item_has_resonance_fields_after"] = probe_after.get("has_resonance_fields")
    R["load_proof"] = proof

    print("\n[加载证明]")
    print(f"  源码最新 mtime        : {proof['newest_source_mtime_utc']}")
    print(f"  api   pid={proof.get('api_pid')} 启动={proof.get('api_create_time_utc')} "
          f"⇒ 已加载={proof.get('api_loaded')}")
    print(f"  runtime pid={proof.get('runtime_pid')} 启动={proof.get('runtime_create_time_utc')} "
          f"⇒ 已加载={proof.get('runtime_loaded')}")
    print(f"  worker 未变={proof['worker_pid_unchanged']}  collector 未变={proof['collector_pid_unchanged']}")
    print(f"  API 行为变化: unapproved_config 前={proof['api_has_unapproved_config_before']} "
          f"后={proof['api_has_unapproved_config_after']} 值={proof['api_unapproved_config_counter']}")
    print(f"  by_reason 键 前={proof['api_by_reason_keys_before']}")
    print(f"  by_reason 键 后={proof['api_by_reason_keys_after']}")

    # ── 6. 重载后的水位核对（不得被改写）──────────────────────────────────
    import sqlite3
    con = sqlite3.connect(f"file:{PROJ}/框架/backend/live/live_signals.sqlite3?mode=ro",
                          uri=True, timeout=20)
    try:
        lv = {r[0]: int(r[1]) for r in con.execute("SELECT key,value_json FROM live_run")}
    finally:
        con.close()
    R["levels_after_reload"] = lv
    R["levels_unchanged"] = (
        lv.get("paper_epoch_ms") == 1791939600000
        and lv.get("live_start_ms") == 1792748844240
        and lv.get("state_anchor_ms") == 1782542715686)
    print(f"\n[水位] {lv}  未变={R['levels_unchanged']}")

    R["finished_at_utc"] = utc()
    p = OUT / "05_重载前后身份.json"
    p.write_text(json.dumps(R, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n产物：{p}")
    return 0 if not R["errors"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
