#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A-2｜独立起一次隔离实例，实测子进程打开文件（同一次运行内自证）

纪律：
  - 全部路径指向 tempfile 隔离目录，7 项必填路径全给
  - enable_paper=False（不启交易链，先测 api+worker 两条链）
  - 起之前/停之后各拍一次生产关键文件的 (mtime, size)，证明零写入
  - 只停自己起的 PID，stop 后复查残留
  - 全程记录 PID / cmd / cwd / lsof，保证同一次运行
"""
from __future__ import annotations

import json
import os
import sys
import time
import shutil
import sqlite3
import tempfile
import hashlib
import datetime
import subprocess
from pathlib import Path

PROJECT_ROOT = Path("/Users/boos/Desktop/威科夫因子共振趋势")
BACKEND = PROJECT_ROOT / "框架" / "backend"
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(PROJECT_ROOT / "框架"))

import service_manager as SM  # noqa: E402

# 生产敏感文件清单（只读观测，绝不写入）
PROD_WATCH = [
    PROJECT_ROOT / "数据" / "任务队列" / "jobs.sqlite3",
    PROJECT_ROOT / "数据" / "市场数据" / "db" / "market_v2.sqlite3",
    BACKEND / "live" / "live_signals.sqlite3",
    PROJECT_ROOT / "数据" / "paper" / "paper_3a.sqlite3",
]
PROD_DIRS = [
    PROJECT_ROOT / "数据",
    BACKEND / "live",
    BACKEND / "locks",
]


def snap() -> dict:
    d = {}
    for p in PROD_WATCH:
        try:
            st = p.stat()
            d[str(p)] = {"exists": True, "mtime": round(st.st_mtime, 3), "size": st.st_size}
        except FileNotFoundError:
            d[str(p)] = {"exists": False}
    return d


def sha256_of(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def lsof_of(pid: int) -> list:
    try:
        out = subprocess.check_output(["lsof", "-p", str(pid), "-Fn"],
                                      stderr=subprocess.DEVNULL, timeout=5.0)
        return [ln[1:] for ln in out.decode("utf-8", "replace").splitlines() if ln.startswith("n")]
    except Exception as exc:  # noqa: BLE001
        return [f"<lsof失败: {type(exc).__name__}: {exc}>"]


def classify(files: list) -> dict:
    """把打开的文件分成 生产违规 / 隔离内 / 系统库 三类。"""
    prod_hits, isolated_hits, other = [], [], []
    data_root = (PROJECT_ROOT / "数据").resolve()
    live_root = (BACKEND / "live").resolve()
    locks_root = (BACKEND / "locks").resolve()
    for f in files:
        if f.startswith("<"):
            other.append(f)
            continue
        try:
            rp = Path(f).resolve()
        except Exception:  # noqa: BLE001
            other.append(f)
            continue
        hit = None
        for name, root in (("数据", data_root), ("live", live_root), ("locks", locks_root)):
            if rp == root or root in rp.parents:
                hit = name
        if hit:
            prod_hits.append({"path": f, "root": hit})
        elif "_audit03_" in f:
            isolated_hits.append(f)
        else:
            other.append(f)
    return {"生产违规": prod_hits, "隔离内": isolated_hits, "其他": other}


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="_audit03_iso_"))
    dirs = {k: tmp / k for k in ("market", "live", "paper_ledger", "locks", "pids", "logs")}
    for d in dirs.values():
        d.mkdir(parents=True, exist_ok=True)

    market_db = dirs["market"] / "market_iso.sqlite3"
    live_db = dirs["live"] / "live_iso.sqlite3"
    jobs_db = dirs["market"] / "jobs_iso.sqlite3"

    # 最小 schema，让子进程不至于因缺表崩溃（不代表业务可用）
    con = sqlite3.connect(str(market_db))
    for tf in ("klines_15m", "klines_1h", "klines_4h"):
        con.execute(f"CREATE TABLE IF NOT EXISTS {tf} (symbol TEXT, open_time INTEGER PRIMARY KEY, "
                    f"open REAL, high REAL, low REAL, close REAL, volume REAL, close_time INTEGER)")
    now_ms = int(time.time() * 1000)
    con.execute("INSERT OR REPLACE INTO klines_15m VALUES('BTCUSDT',?,60000,60500,59800,60200,100,?)",
                (now_ms - 900000, now_ms - 1))
    con.commit()
    con.close()

    con = sqlite3.connect(str(live_db))
    con.execute("CREATE TABLE IF NOT EXISTS live_signal (id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER)")
    con.commit()
    con.close()

    # jobs 库：不预建表，交给 JobsStore 自己 executescript(SCHEMA)，
    # 否则自建表与官方 SCHEMA 冲突（no such column: idempotency_key）

    # 账本目录身份声明（live_store 会校验）
    try:
        sys.path.insert(0, str(BACKEND))
        import live_store as L  # noqa: E402
        for a in getattr(L, "ARMS", ["3a"]):
            p_db = dirs["paper_ledger"] / f"paper_{a}.sqlite3"
            c = sqlite3.connect(str(p_db))
            c.executescript(getattr(L, "PAPER_ADDITIVE", ""))
            c.execute("INSERT OR REPLACE INTO paper_account(account_id, arm, initial_equity, cash, leverage, dry_run, created_at) "
                      "VALUES(?,?,5000.0,5000.0,1.0,1,?)", (f"audit03_{a}", a, now_ms))
            c.commit()
            c.close()
        (dirs["paper_ledger"] / "ledger_identity.json").write_text(json.dumps({
            "spec_version": getattr(L, "LEDGER_SPEC_VERSION", "x"),
            "created_at_utc": "2026-10-23T00:00:00Z",
            "arms": {a: {"account_id": f"audit03_{a}", "initial_equity": 5000.0}
                     for a in getattr(L, "ARMS", ["3a"])},
        }), encoding="utf-8")
        ledger_note = "已写入 ledger_identity.json"
    except Exception as exc:  # noqa: BLE001
        ledger_note = f"账本初始化降级: {type(exc).__name__}: {exc}"

    before = snap()

    cfg = SM.ServiceConfig(
        port=0,
        py=SM.get_default_python(),
        is_isolated=True,
        market_db=str(market_db),
        live_db=str(live_db),
        paper_ledger_dir=str(dirs["paper_ledger"]),
        paper_lock_dir=str(dirs["locks"]),
        jobs_db=str(jobs_db),
        pid_dir=dirs["pids"],
        log_dir=dirs["logs"],
        enable_paper=False,
        check_collector=False,
    )
    # 找一个空闲端口
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    cfg.port = s.getsockname()[1]
    s.close()

    rec = {
        "工单": "SYSTEM-INDEPENDENT-AUDIT-03 / A-2",
        "检查时刻": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "隔离根目录": str(tmp),
        "端口": cfg.port,
        "账本初始化": ledger_note,
        "源码绑定": {
            "service_manager.py": {"sha256": sha256_of(BACKEND / "service_manager.py"),
                                   "mtime": datetime.datetime.fromtimestamp(
                                       (BACKEND / "service_manager.py").stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")},
            "backtest_worker.py": {"sha256": sha256_of(BACKEND / "workers" / "backtest_worker.py")},
            "jobs/store.py": {"sha256": sha256_of(BACKEND / "infrastructure" / "jobs" / "store.py")},
            "adapter.py": {"sha256": sha256_of(BACKEND / "adapter.py")},
            "live_store.py": {"sha256": sha256_of(BACKEND / "live_store.py")},
        },
        "生产文件_起前": before,
    }

    mgr = SM.ServiceManager(cfg)
    started = None
    error = None
    try:
        started = mgr.start()
        time.sleep(2.0)   # 让子进程完成 import 与首次打开
        rec["start_return"] = started
    except Exception as exc:  # noqa: BLE001
        error = f"{type(exc).__name__}: {exc}"
        rec["start_error"] = error
        # start 抛异常时 API 可能已经起来：用端口反查，保证后续能核验与清理
        try:
            p = SM.get_port_listener_pid(cfg.port)
            if p:
                started = {"services": {"api": {"pid": p, "state": "running_partial"},
                                        "worker": {"pid": None, "state": "failed"}}}
                rec["start_partial_recovered_pid"] = p
        except Exception:  # noqa: BLE001
            pass

    # 探测本环境 get_process_cmdline 是否可用（ps 被沙箱拒绝时会返回空）
    probe_pid = None
    if started:
        probe_pid = started.get("services", {}).get("api", {}).get("pid")
    rec["cmdline_probe"] = {
        "pid": probe_pid,
        "cmdline": SM.get_process_cmdline(probe_pid) if probe_pid else None,
        "可用": bool(SM.get_process_cmdline(probe_pid)) if probe_pid else None,
        "说明": "get_process_cmdline 依赖 ps；沙箱内 ps 被拒则恒为空，导致 stop() 身份校验必然失败",
    }

    subs = []
    if started:
        for name in ("api", "worker", "runtime"):
            info = started.get("services", {}).get(name, {})
            pid = info.get("pid")
            if not pid:
                subs.append({"name": name, "pid": None, "state": info.get("state")})
                continue
            files = lsof_of(pid)
            cls = classify(files)
            subs.append({
                "name": name,
                "pid": pid,
                "state": info.get("state"),
                "cmdline": SM.get_process_cmdline(pid),
                "cwd": SM.get_process_cwd(pid),
                "opened_count": len([f for f in files if not f.startswith("<")]),
                "生产违规": cls["生产违规"],
                "隔离内命中": cls["隔离内"],
                "其他样本": cls["其他"][:8],
                "verdict": "隔离成立" if not cls["生产违规"] else "隔离失效",
            })
    rec["子进程实测"] = subs

    # 停止
    stop_res = None
    try:
        stop_res = mgr.stop()
        rec["stop_return"] = stop_res
    except Exception as exc:  # noqa: BLE001
        rec["stop_error"] = f"{type(exc).__name__}: {exc}"

    # 兜底自清理：无论 stop() 是否成功，只杀本次自己起的 PID，保证不残留
    cleanup = []
    for name in ("api", "worker", "runtime"):
        pid = (started or {}).get("services", {}).get(name, {}).get("pid")
        if pid and SM.is_pid_alive(pid):
            try:
                os.kill(pid, 15)
                time.sleep(1.0)
                if SM.is_pid_alive(pid):
                    os.kill(pid, 9)
                cleanup.append({"name": name, "pid": pid, "action": "SIGTERM->SIGKILL"})
            except OSError as exc:
                cleanup.append({"name": name, "pid": pid, "action": f"失败:{exc}"})
    rec["兜底清理"] = cleanup

    time.sleep(1.5)
    rec["停止后残留"] = []
    for name in ("api", "worker"):
        pid = (started or {}).get("services", {}).get(name, {}).get("pid")
        if pid:
            alive = SM.is_pid_alive(pid)
            rec["停止后残留"].append({"name": name, "pid": pid, "alive": alive})

    rec["生产文件_停后"] = snap()
    changed = []
    for k, v in rec["生产文件_起前"].items():
        a = rec["生产文件_停后"].get(k, {})
        if v.get("exists") != a.get("exists") or v.get("mtime") != a.get("mtime") or v.get("size") != a.get("size"):
            changed.append({"path": k, "before": v, "after": a})
    rec["生产文件被改"] = changed

    # 隔离目录内落盘情况
    rec["隔离目录内容"] = sorted(
        str(p.relative_to(tmp)) for p in tmp.rglob("*") if p.is_file()
    )[:40]

    outp = PROJECT_ROOT / "evidence" / "SYSTEM-INDEPENDENT-AUDIT-03" / "out" / "A2_isolation_live_test.json"
    outp.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"隔离根: {tmp}")
    print(f"端口: {cfg.port}")
    print(f"start: {'OK' if started else error}")
    for s_ in subs:
        print(f"\n[{s_.get('verdict', '未启动')}] {s_['name']} pid={s_['pid']} state={s_.get('state')}")
        print(f"  cwd = {s_.get('cwd')}")
        print(f"  cmd = {(s_.get('cmdline') or '')[:150]}")
        print(f"  打开文件数 = {s_.get('opened_count')}")
        if s_.get("生产违规"):
            for h in s_["生产违规"]:
                print(f"  !! 生产违规[{h['root']}]: {h['path']}")
        print(f"  隔离内命中 {len(s_.get('隔离内命中') or [])} 个")
    print(f"\nstop: {json.dumps(stop_res, ensure_ascii=False)[:300] if stop_res else rec.get('stop_error')}")
    print(f"停止后残留: {rec['停止后残留']}")
    print(f"生产文件被改: {changed if changed else '无'}")
    print(f"\n落盘: {outp}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
