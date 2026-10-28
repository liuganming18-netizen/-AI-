# -*- coding: utf-8 -*-
"""SIGNAL-NOW-01-ANTIGRAVITY｜步骤1：源码身份 + 运行实例 + **内容级**加载证据。

反重力纪律：
- **只读**。不写生产任何文件、不改账本、不调用下单接口、不重启任何进程。
- 不采信 Work 的 JSON 自述：哈希、进程身份、加载结论全部自己重新取。
- 工单明确：「哈希、报告自述或 PID 变化单独不算运行加载证明」⇒ 本脚本额外去找
  **内容级**证据（运行实例输出的数据里带不带新版源码才有的内容），并对找不到内容级
  证据的进程如实标注「仅间接证据」。

内容级加载证据的原理：
  `paper_engine.FILL_POLICY` 是模块级字典；`live_api.py:802` 把它原样放进
  `/api/v1/paper/accounts` 的 `quality.fill_policy`。
  本单在 FILL_POLICY 里新增了两个键：`on_fill_point_before_process_start`、`known_gap`。
  ⇒ 若接口返回里出现这两个键，就证明**运行中的 API 进程加载的 paper_engine 是含本单改动的版本**。
     这与时间戳无关，是内容比对。
"""
from __future__ import annotations

import hashlib
import json
import os
import struct
import subprocess
import time
from pathlib import Path

import psutil

PROJ = Path("/Users/boos/Desktop/威科夫因子共振趋势")
ROOT = PROJ / "evidence" / "SIGNAL-NOW-01-ANTIGRAVITY"
OUT = ROOT / "out"
BACKEND = PROJ / "框架/backend"
WORK = PROJ / "evidence" / "SIGNAL-NOW-01-WORK"

WATCH = ["框架/backend/paper_engine.py", "框架/backend/unified_runtime.py",
         "框架/backend/live_api.py"]
API = "http://127.0.0.1:8787"

# 本单（NOW-01）新增进 FILL_POLICY 的两个键 —— 内容级判据
NEW_POLICY_KEYS = ["on_fill_point_before_process_start", "known_gap"]


def utc(ts=None):
    t = time.time() if ts is None else float(ts)
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def pyc_header(pyc: Path) -> dict:
    """读 .pyc 头：magic + flags + 源文件 mtime + 源文件 size。

    Python 3.7+ 头 16 字节：magic(4) flags(4) source_mtime(4) source_size(4)
    ⇒ 头里记的 source_mtime/size 就是**这份字节码是从哪个版本的源码编译来的**。
    """
    try:
        b = pyc.read_bytes()[:16]
        magic, flags, src_mtime, src_size = struct.unpack("<4sIII", b)
        return {"pyc": str(pyc), "magic": magic.hex(), "flags": flags,
                "embedded_source_mtime": src_mtime,
                "embedded_source_mtime_utc": utc(src_mtime),
                "embedded_source_size": src_size,
                "pyc_file_mtime_utc": utc(pyc.stat().st_mtime)}
    except Exception as e:
        return {"pyc": str(pyc), "error": str(e)}


def find_procs() -> dict:
    out = {"api": None, "runtime": None, "worker_prod": None, "worker_orphan": [],
           "collector": None, "others": []}
    for p in psutil.process_iter(["pid", "cmdline", "create_time", "cwd", "username"]):
        try:
            cl = p.info["cmdline"] or []
            cl_s = " ".join(cl)
            cwd = p.info["cwd"] or ""
        except Exception:
            continue
        if str(BACKEND) not in cwd:
            continue
        rec = {"pid": p.info["pid"], "create_time": p.info["create_time"],
               "create_time_utc": utc(p.info["create_time"]),
               "cmdline": cl_s[:300], "cwd": cwd}
        if "adapter.py" in cl_s:
            out["api"] = rec
        elif "unified_runtime.py" in cl_s:
            out["runtime"] = rec
        elif "backtest_worker.py" in cl_s:
            if "数据/任务队列/jobs.sqlite3" in cl_s:
                out["worker_prod"] = rec
            else:
                out["worker_orphan"].append(rec)
        elif "realtime_collector.py" in cl_s:
            out["collector"] = rec
        else:
            out["others"].append(rec)
    return out


def http_get(url, timeout=20):
    cp = subprocess.run(["curl", "-s", "-m", str(timeout), "-w", "\n__H__%{http_code}", url],
                        capture_output=True, text=True, timeout=timeout + 10)
    body, code = cp.stdout, ""
    if "__H__" in body:
        body, code = body.rsplit("__H__", 1)
    try:
        js = json.loads(body)
    except Exception:
        js = None
    return {"http": code.strip(), "json": js, "body_len": len(body)}


def main() -> int:
    R = {"ticket": "SIGNAL-NOW-01-ANTIGRAVITY",
         "step": "1 源码身份 + 运行实例 + 内容级加载证据",
         "at_utc": utc(), "note": "全程只读；不写生产、不调路由、不重启"}

    # ── 1. 源码身份（自己算，不抄 Work） ──
    R["sources"] = {}
    for rel in WATCH:
        p = PROJ / rel
        st = p.stat()
        R["sources"][rel] = {
            "sha256": sha256_file(p), "size": st.st_size,
            "mtime": st.st_mtime, "mtime_utc": utc(st.st_mtime),
        }
    newest = max(v["mtime"] for v in R["sources"].values())
    R["newest_source_mtime_utc"] = utc(newest)

    # ── 2. 与 Work 交付的哈希对撞（找漂移，不是采信） ──
    R["cross_check_vs_work"] = {}
    wb = WORK / "baseline" / "src_before"
    for rel in ("框架/backend/paper_engine.py", "框架/backend/unified_runtime.py"):
        name = Path(rel).name
        b = wb / name
        R["cross_check_vs_work"][name] = {
            "work_backup_sha256": sha256_file(b) if b.exists() else None,
            "now_sha256": R["sources"][rel]["sha256"],
            "unchanged_since_work_backup": (sha256_file(b) == R["sources"][rel]["sha256"])
            if b.exists() else None,
        }
    # Work 的 README 里自报的「现行版本」哈希，用来自查它有没有说错
    try:
        txt = (WORK / "README.md").read_text(encoding="utf-8")
        R["work_readme_claims_present"] = {
            "a99f8af00d4d82ca": "a99f8af00d4d82ca" in txt,
            "e30e213538686466": "e30e213538686466" in txt,
        }
    except Exception as e:
        R["work_readme_claims_present"] = {"error": str(e)}

    # ── 3. .pyc 取证：磁盘字节码是从哪版源码编译的 ──
    R["pycache"] = {}
    for rel in ("框架/backend/paper_engine.py", "框架/backend/unified_runtime.py"):
        p = PROJ / rel
        d = p.parent / "__pycache__"
        found = []
        if d.exists():
            for pyc in sorted(d.glob(p.stem + ".*.pyc")):
                found.append(pyc_header(pyc))
        R["pycache"][p.name] = {"dir": str(d), "exists": d.exists(), "pycs": found,
                                "source_mtime": p.stat().st_mtime,
                                "source_mtime_utc": utc(p.stat().st_mtime),
                                "source_size": p.stat().st_size}

    # ── 4. 进程身份 ──
    procs = find_procs()
    R["processes"] = procs
    R["process_vs_source"] = {}
    for role in ("api", "runtime", "worker_prod", "collector"):
        rec = procs.get(role)
        if not rec:
            R["process_vs_source"][role] = {"present": False}
            continue
        R["process_vs_source"][role] = {
            "present": True, "pid": rec["pid"],
            "create_time_utc": rec["create_time_utc"],
            "started_after_newest_source": rec["create_time"] > newest,
            "seconds_after_newest_source": round(rec["create_time"] - newest, 1),
        }

    # ── 5. **内容级**加载证据：接口返回的 fill_policy 有没有新版才有的键 ──
    r = http_get(f"{API}/api/v1/paper/accounts")
    js = r.get("json") or {}
    q = js.get("quality") or {}
    fp = q.get("fill_policy") or {}
    R["load_proof_content_api"] = {
        "endpoint": f"{API}/api/v1/paper/accounts",
        "http": r["http"],
        "fill_policy_keys": sorted(fp.keys()),
        "new_keys_expected": NEW_POLICY_KEYS,
        "new_keys_present": [k for k in NEW_POLICY_KEYS if k in fp],
        "new_keys_missing": [k for k in NEW_POLICY_KEYS if k not in fp],
        "verdict": ("API 进程加载的 paper_engine 含本单新增内容 ⇒ 内容级直接证据"
                    if all(k in fp for k in NEW_POLICY_KEYS)
                    else "API 进程的 fill_policy 不含本单新增键 ⇒ 要么未加载，要么该接口未暴露"),
        "fill_policy_full": fp,
        "model_version": js.get("model_version"),
        "param_hash": js.get("param_hash"),
        "strategy_version": js.get("strategy_version"),
        "rule_version": js.get("rule_version"),
        "new_entry_gate": q.get("new_entry_gate"),
    }

    # ── 6. 运行时状态（扫描/水位/漏斗/闸门） ──
    try:
        d = json.loads((BACKEND / "locks" / "paper_runtime_3a_status.json").read_text(encoding="utf-8"))
    except Exception as e:
        d = {"_error": str(e)}
    ri = d.get("runtime_identity") or {}
    se = d.get("signal_engine") or {}
    od = d.get("order_processing") or {}
    ed = d.get("execution_daemon") or {}
    R["runtime_status"] = {
        "pid": ri.get("pid"), "started_at": ri.get("started_at"),
        "started_at_utc": utc(ri["started_at"] / 1000) if ri.get("started_at") else None,
        "cycle_count": ri.get("cycle_count"), "overall_health": ri.get("overall_health"),
        "universe": se.get("universe"), "universe_declared": se.get("universe_declared"),
        "watermark_1h": se.get("watermark_1h"),
        "watermark_1h_utc": utc(se["watermark_1h"] / 1000) if se.get("watermark_1h") else None,
        "watermark_4h": se.get("watermark_4h"),
        "watermark_4h_utc": utc(se["watermark_4h"] / 1000) if se.get("watermark_4h") else None,
        "watermark_check": se.get("watermark_check"),
        "funnel": se.get("funnel"),
        "observe_summary": se.get("observe_summary"),
        "idle_breakdown": se.get("idle_breakdown"),
        "data_quality": se.get("data_quality"),
        "scan_errors_total": se.get("scan_errors_total"),
        "signals_generated_total": se.get("signals_generated_total"),
        "bootstrap_summary": {k: (se.get("bootstrap") or {}).get(k) for k in
                              ("universe", "ready", "skipped", "history_candidates_discarded",
                               "recovered_candidates", "data_source", "_window_hours")},
        "order_processing": od, "execution_daemon": ed,
    }

    # ── 7. 三水位（只读） ──
    import sqlite3
    con = sqlite3.connect(f"file:{BACKEND}/live/live_signals.sqlite3?mode=ro",
                          uri=True, timeout=20)
    try:
        R["live_run_levels"] = {k: v for k, v in
                                con.execute("SELECT key,value_json FROM live_run")}
    finally:
        con.close()
    R["levels_expected"] = {"paper_epoch_ms": "1791939600000",
                            "live_start_ms": "1792748844240",
                            "state_anchor_ms": "1782542715686"}
    R["levels_match"] = all(R["live_run_levels"].get(k) == v
                            for k, v in R["levels_expected"].items())

    OUT.mkdir(parents=True, exist_ok=True)
    p = OUT / "01_身份与加载证据.json"
    p.write_text(json.dumps(R, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    # ── 打印摘要 ──
    print("=== 源码 ===")
    for rel, v in R["sources"].items():
        print(f"  {rel}  sha={v['sha256'][:16]}  size={v['size']}  mtime={v['mtime_utc']}")
    print(f"  最新源码 mtime = {R['newest_source_mtime_utc']}")
    print("\n=== 与 Work 备份对撞 ===")
    for k, v in R["cross_check_vs_work"].items():
        print(f"  {k}: 备份={str(v['work_backup_sha256'])[:16]} 现在={v['now_sha256'][:16]} "
              f"未变={v['unchanged_since_work_backup']}")
    print("\n=== 进程 ===")
    for role, v in R["process_vs_source"].items():
        print(f"  {role:12s} {v}")
    print("\n=== 内容级加载证据（API） ===")
    lp = R["load_proof_content_api"]
    print(f"  http={lp['http']}  期望新键={lp['new_keys_expected']}")
    print(f"  实到新键={lp['new_keys_present']}  缺={lp['new_keys_missing']}")
    print(f"  判定：{lp['verdict']}")
    print(f"  fill_policy 实际键={lp['fill_policy_keys']}")
    print("\n=== pyc 取证 ===")
    for k, v in R["pycache"].items():
        print(f"  {k}: 源 mtime={v['source_mtime_utc']} size={v['source_size']}")
        for x in v["pycs"]:
            print(f"     pyc={Path(x['pyc']).name} 内嵌源mtime={x.get('embedded_source_mtime_utc')} "
                  f"内嵌源size={x.get('embedded_source_size')} pyc写入={x.get('pyc_file_mtime_utc')}")
    print("\n=== 运行时 ===")
    rs = R["runtime_status"]
    print(f"  pid={rs['pid']} started={rs['started_at_utc']} cycles={rs['cycle_count']} "
          f"health={rs['overall_health']} universe={rs['universe']}")
    print(f"  w1h={rs['watermark_1h_utc']} w4h={rs['watermark_4h_utc']}")
    print(f"  levels_match={R['levels_match']} {R['live_run_levels']}")
    print(f"\n产物：{p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
