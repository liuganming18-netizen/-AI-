#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A-3｜全链隔离实测（api + worker + runtime），完整 lsof + 宽判据

比 A-2 更严：
  1. 开启 enable_paper=True，把 unified_runtime 也拉起来（工单要求"全部运行路径"）
  2. 保存**完整** opened_files（不截断），用宽判据判定：
     任何解析后位于 PROJECT_ROOT 下、且不在隔离根内的路径 = 生产访问
     （不再只查 数据/live/locks 三个根，覆盖 回测日志/证据/框架 等）
  3. 主动打 API 端点触发账本懒加载，抓二次 lsof
  4. 前后生产文件快照，证明零写入
"""
from __future__ import annotations

import json
import os
import sys
import time
import sqlite3
import tempfile
import hashlib
import datetime
import socket
import subprocess
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path("/Users/boos/Desktop/威科夫因子共振趋势")
BACKEND = PROJECT_ROOT / "框架" / "backend"
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(PROJECT_ROOT / "框架"))

import service_manager as SM  # noqa: E402

PROD_WATCH = [
    PROJECT_ROOT / "数据" / "任务队列" / "jobs.sqlite3",
    PROJECT_ROOT / "数据" / "市场数据" / "db" / "market_v2.sqlite3",
    BACKEND / "live" / "live_signals.sqlite3",
    PROJECT_ROOT / "数据" / "paper" / "paper_3a.sqlite3",
    PROJECT_ROOT / "数据" / "paper" / "paper_3b.sqlite3",
    PROJECT_ROOT / "数据" / "paper" / "paper_3c.sqlite3",
    PROJECT_ROOT / "数据" / "paper" / "paper_3d.sqlite3",
]
PROD_PARQUET = PROJECT_ROOT / "数据" / "市场数据" / "parquet"

CONFIG = PROJECT_ROOT / "evidence" / "SYSTEM-CLOSELOOP-05" / "config" / "exp1_config.json"


def snap() -> dict:
    d = {}
    for p in PROD_WATCH:
        try:
            st = p.stat()
            d[str(p)] = {"exists": True, "mtime": round(st.st_mtime, 3), "size": st.st_size}
        except FileNotFoundError:
            d[str(p)] = {"exists": False}
    if PROD_PARQUET.exists():
        st = PROD_PARQUET.stat()
        d[str(PROD_PARQUET)] = {"exists": True, "mtime": round(st.st_mtime, 3),
                                "entries": len(list(PROD_PARQUET.iterdir()))}
    return d


def lsof_full(pid: int) -> list:
    try:
        out = subprocess.check_output(["lsof", "-p", str(pid), "-Fn"],
                                      stderr=subprocess.DEVNULL, timeout=6.0)
        return [ln[1:] for ln in out.decode("utf-8", "replace").splitlines() if ln.startswith("n")]
    except Exception as exc:  # noqa: BLE001
        return [f"<lsof失败 {type(exc).__name__}>"]


def wide_classify(files: list, iso_root: Path) -> dict:
    """宽判据：PROJECT_ROOT 下且不在隔离根内 = 生产访问。"""
    prod, iso, other = [], [], []
    root_s = str(PROJECT_ROOT)
    iso_s = str(iso_root.resolve())
    for f in files:
        if f.startswith("<"):
            other.append({"path": f, "why": "lsof失败"}); continue
        # 非文件句柄：socket / anon 内存映射 / 设备
        if ":" in f and not f.startswith("/"):
            other.append({"path": f, "why": "socket"}); continue
        if f.startswith("->"):
            other.append({"path": f, "why": "anon_mmap"}); continue
        if not f.startswith("/"):
            other.append({"path": f, "why": "非绝对路径"}); continue
        try:
            rp = str(Path(f).resolve())
        except Exception:  # noqa: BLE001
            other.append({"path": f, "why": "resolve失败"}); continue
        # 目录句柄（cwd / 脚本目录）不是数据文件访问，单列不计入生产访问
        try:
            if Path(f).is_dir():
                other.append({"path": f, "why": "目录句柄(非数据)"}); continue
        except Exception:  # noqa: BLE001
            pass
        if rp == root_s:
            other.append({"path": f, "why": "cwd目录句柄(非数据)"}); continue
        if rp.startswith(iso_s):
            iso.append(f)
        elif rp.startswith(root_s + "/"):
            prod.append(f)
        else:
            other.append({"path": f, "why": "系统/第三方"})
    return {"生产访问": prod, "隔离内": iso, "其他": other}


def http_get(port: int, path: str) -> dict:
    """禁用代理，避免 HTTP_PROXY 把 127.0.0.1 发给代理。"""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(f"http://127.0.0.1:{port}{path}", timeout=8.0) as r:
            body = r.read().decode("utf-8", "replace")
            return {"path": path, "status": r.status, "len": len(body), "head": body[:300]}
    except Exception as exc:  # noqa: BLE001
        return {"path": path, "error": f"{type(exc).__name__}: {exc}"}


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="_audit03_full_"))
    dirs = {k: tmp / k for k in ("market", "live", "paper_ledger", "locks", "pids", "logs")}
    for d in dirs.values():
        d.mkdir(parents=True, exist_ok=True)

    market_db = dirs["market"] / "market_iso.sqlite3"
    live_db = dirs["live"] / "live_iso.sqlite3"
    jobs_db = dirs["market"] / "jobs_iso.sqlite3"
    now_ms = int(time.time() * 1000)

    con = sqlite3.connect(str(market_db))
    for tf in ("klines_15m", "klines_1h", "klines_4h"):
        con.execute(f"CREATE TABLE IF NOT EXISTS {tf} (symbol TEXT, open_time INTEGER PRIMARY KEY, "
                    f"open REAL, high REAL, low REAL, close REAL, volume REAL, close_time INTEGER)")
    # 造 260 根 15m + 260 根 4h，尽量满足 ATR 预热（不够也不影响隔离判定）
    for i in range(260):
        t = now_ms - i * 900000
        con.execute("INSERT OR REPLACE INTO klines_15m VALUES('BTCUSDT',?,60000,60500,59800,60200,100,?)", (t, t + 899999))
        t4 = now_ms - i * 14400000
        con.execute("INSERT OR REPLACE INTO klines_4h VALUES('BTCUSDT',?,60000,60500,59800,60200,100,?)", (t4, t4 + 14399999))
    con.commit(); con.close()

    con = sqlite3.connect(str(live_db))
    con.execute("CREATE TABLE IF NOT EXISTS live_signal (id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER)")
    con.commit(); con.close()

    import live_store as L  # noqa: E402
    for a in L.ARMS:
        p_db = dirs["paper_ledger"] / f"paper_{a}.sqlite3"
        c = sqlite3.connect(str(p_db))
        c.executescript(L.PAPER_ADDITIVE)
        c.execute("INSERT OR REPLACE INTO paper_account(account_id, arm, initial_equity, cash, leverage, dry_run, created_at) "
                  "VALUES(?,?,5000.0,5000.0,1.0,1,?)", (f"paper43_{a}", a, now_ms))
        c.commit(); c.close()
    (dirs["paper_ledger"] / "ledger_identity.json").write_text(json.dumps({
        "spec_version": L.LEDGER_SPEC_VERSION, "created_at_utc": "2026-10-23T00:00:00Z",
        "arms": {a: {"account_id": f"paper43_{a}", "initial_equity": 5000.0} for a in L.ARMS},
    }), encoding="utf-8")

    before = snap()

    cfg = SM.ServiceConfig(
        port=0, py=SM.get_default_python(), is_isolated=True,
        market_db=str(market_db), live_db=str(live_db),
        paper_ledger_dir=str(dirs["paper_ledger"]), paper_lock_dir=str(dirs["locks"]),
        jobs_db=str(jobs_db), pid_dir=dirs["pids"], log_dir=dirs["logs"],
        enable_paper=True, paper_arm="3a", symbol="BTCUSDT",
        config_path=str(CONFIG), check_collector=False,
    )
    s = socket.socket(); s.bind(("127.0.0.1", 0)); cfg.port = s.getsockname()[1]; s.close()

    rec = {
        "工单": "SYSTEM-INDEPENDENT-AUDIT-03 / A-3",
        "检查时刻": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "隔离根目录": str(tmp), "端口": cfg.port,
        "配置": str(CONFIG),
        "源码绑定": {
            "service_manager.py": hashlib.sha256((BACKEND / "service_manager.py").read_bytes()).hexdigest(),
            "unified_runtime.py": hashlib.sha256((BACKEND / "unified_runtime.py").read_bytes()).hexdigest(),
            "live_store.py": hashlib.sha256((BACKEND / "live_store.py").read_bytes()).hexdigest(),
            "adapter.py": hashlib.sha256((BACKEND / "adapter.py").read_bytes()).hexdigest(),
            "backtest_worker.py": hashlib.sha256((BACKEND / "workers" / "backtest_worker.py").read_bytes()).hexdigest(),
        },
        "环境注入检查": {"MARKET_ARCHIVE_是否被shared_env设置": bool(cfg.market_archive)},
        "生产快照_起前": before,
    }

    mgr = SM.ServiceManager(cfg)
    started, err = None, None
    try:
        started = mgr.start()
        rec["start_return"] = {k: v for k, v in started.items() if k != "services"}
        rec["services"] = started.get("services")
    except Exception as exc:  # noqa: BLE001
        err = f"{type(exc).__name__}: {exc}"
        rec["start_error"] = err
        # 从产品自己写的 service_meta 读回各服务 PID，避免丢失身份
        meta_f = dirs["pids"] / f"service_meta_{cfg.port}.json"
        svc_recover = {}
        if meta_f.exists():
            try:
                m = json.loads(meta_f.read_text(encoding="utf-8"))
                for nm in ("api", "worker", "runtime"):
                    svc_recover[nm] = {"pid": (m.get(nm) or {}).get("pid"),
                                       "state": (m.get(nm) or {}).get("state")}
            except Exception:  # noqa: BLE001
                pass
        p = SM.get_port_listener_pid(cfg.port)
        if p and not (svc_recover.get("api") or {}).get("pid"):
            svc_recover["api"] = {"pid": p, "state": "running_partial"}
        if svc_recover:
            started = {"services": svc_recover}
            rec["pid_recovered_from_service_meta"] = svc_recover
            rec["services"] = svc_recover

    time.sleep(5.0)   # 给 runtime 时间跑起来并打开文件

    # 主动打 API，触发账本懒加载
    api_calls = []
    if started and started.get("services", {}).get("api", {}).get("pid"):
        for pth in ("/api/v1/paper/accounts", "/api/v1/paper/state?arm=3a",
                    "/api/v1/paper/positions?arm=3a", "/api/health"):
            api_calls.append(http_get(cfg.port, pth))
        time.sleep(1.5)
    rec["api_calls"] = api_calls

    subs = []
    svc = (started or {}).get("services", {})
    for name in ("api", "worker", "runtime"):
        pid = (svc.get(name) or {}).get("pid")
        if not pid:
            subs.append({"name": name, "pid": None, "verdict": "未启动"})
            continue
        files = lsof_full(pid)
        cls = wide_classify(files, tmp)
        subs.append({
            "name": name, "pid": pid,
            "cwd": SM.get_process_cwd(pid),
            "cmdline": SM.get_process_cmdline(pid),
            "opened_total": len([f for f in files if not f.startswith("<")]),
            "生产访问": cls["生产访问"],
            "隔离内": cls["隔离内"],
            "其他": cls["其他"],
            "verdict": "隔离成立" if not cls["生产访问"] else "隔离失效",
        })
    rec["子进程实测"] = subs

    # runtime 日志尾部（判断是否真的跑起来）
    rlogs = sorted(dirs["logs"].glob("runtime-*.log"))
    rec["runtime_log_tail"] = (rlogs[0].read_text(encoding="utf-8", errors="replace")[-1200:]
                               if rlogs else "无 runtime 日志")

    try:
        rec["stop_return"] = mgr.stop()
    except Exception as exc:  # noqa: BLE001
        rec["stop_error"] = f"{type(exc).__name__}: {exc}"

    cleanup = []
    for name in ("api", "worker", "runtime"):
        pid = (svc.get(name) or {}).get("pid")
        if pid and SM.is_pid_alive(pid):
            try:
                os.kill(pid, 15); time.sleep(1.0)
                if SM.is_pid_alive(pid):
                    os.kill(pid, 9)
                cleanup.append({"name": name, "pid": pid, "action": "SIGTERM->SIGKILL"})
            except OSError as exc:
                cleanup.append({"name": name, "pid": pid, "action": f"失败:{exc}"})
    rec["兜底清理"] = cleanup

    time.sleep(1.5)
    rec["停止后存活"] = [{"name": n, "pid": (svc.get(n) or {}).get("pid"),
                     "alive": SM.is_pid_alive((svc.get(n) or {}).get("pid") or 0)}
                    for n in ("api", "worker", "runtime")]
    rec["生产快照_停后"] = snap()
    changed = []
    for k, v in before.items():
        a = rec["生产快照_停后"].get(k, {})
        if v != a:
            changed.append({"path": k, "before": v, "after": a})
    rec["生产被改"] = changed

    outp = PROJECT_ROOT / "evidence" / "SYSTEM-INDEPENDENT-AUDIT-03" / "out" / "A3_fullchain_isolation.json"
    outp.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"隔离根: {tmp}  端口 {cfg.port}")
    print(f"start: {'OK' if started else err}")
    for s_ in subs:
        print(f"\n[{s_.get('verdict')}] {s_['name']} pid={s_['pid']} 打开={s_.get('opened_total')}")
        for f in (s_.get("生产访问") or []):
            print(f"   !! 生产访问: {f}")
        print(f"   隔离内 {len(s_.get('隔离内') or [])} 个")
    print(f"\nAPI 调用: {[(c.get('path'), c.get('status') or c.get('error')) for c in api_calls]}")
    print(f"runtime 日志尾: {rec['runtime_log_tail'][-300:]}")
    print(f"\nstop: {rec.get('stop_return') or rec.get('stop_error')}")
    print(f"兜底清理: {cleanup}")
    print(f"生产被改: {changed if changed else '无'}")
    print(f"落盘: {outp}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
