#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SYSTEM-STARTUP-01｜统一服务管理与状态诊断核心。

职责：
  1. 统一管理 API (adapter)、回测 Worker、真实信号/纸面执行链 (unified_runtime) 与实时采集器；
  2. 资源全配置化（端口、路径、PID、日志、策略臂等独立可配置）；
  3. 细分启动模式：默认 UI/API 安全启动（绝不暗中开交易）；隔离模拟模式（--enable-paper）且强验有效配置；
  4. 严格错误分类与状态暴露：包含真实 PID、配置身份、启动时间、最近心跳、退出原因；
  5. 端口冲突、缺配置、进程崩溃明确报错并记录失败状态；
  6. 停止操作精准隔离，只作用于本启动器管理的进程，绝不误杀未知进程或采集器。
"""

from __future__ import annotations

import argparse
import datetime as dt
import errno
import json
import os
import signal
import socket
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parents[1].resolve() if len(HERE.parents) >= 2 else HERE.parent


def get_default_python() -> str:
    candidates = [
        "/Users/boos/.workbuddy-ai/binaries/python/envs/default/bin/python",
        "/Users/boos/.workbuddy-ai/binaries/python/versions/3.13.12/bin/python3",
        sys.executable,
    ]
    for c in candidates:
        if os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    return sys.executable


@dataclass
class ServiceConfig:
    port: int = 8787
    fe_port: int = 3000
    py: str = field(default_factory=get_default_python)
    project_root: Path = PROJECT_ROOT
    backend_dir: Path = HERE
    market_db: Optional[str] = None
    market_archive: Optional[str] = None
    live_db: Optional[str] = None
    live_dir: Optional[str] = None
    paper_ledger_dir: Optional[str] = None
    paper_lock_dir: Optional[str] = None
    jobs_db: Optional[str] = None
    pid_dir: Optional[Path] = None
    log_dir: Optional[Path] = None
    enable_paper: bool = False
    paper_arm: str = "3a"
    symbol: str = "all"          # all = 全池（scope 526）；也可传 BTCUSDT / 逗号分隔
    config_path: Optional[str] = None
    check_collector: bool = True
    stop_collector: bool = False
    inprocess_jobs: int = 0
    web_readonly: int = 1
    is_isolated: bool = False

    def resolve_paths(self):
        # 1. 优先读取环境变量填充缺失项
        if not self.market_db and os.environ.get("MARKET_DB"):
            self.market_db = os.environ["MARKET_DB"]
        if not self.market_archive and os.environ.get("MARKET_ARCHIVE"):
            self.market_archive = os.environ["MARKET_ARCHIVE"]
        if not self.live_db and os.environ.get("LIVE_DB"):
            self.live_db = os.environ["LIVE_DB"]
        if not self.live_dir and os.environ.get("LIVE_DIR"):
            self.live_dir = os.environ["LIVE_DIR"]
        if not self.paper_ledger_dir and os.environ.get("PAPER_LEDGER_DIR"):
            self.paper_ledger_dir = os.environ["PAPER_LEDGER_DIR"]
        if not self.paper_lock_dir and os.environ.get("PAPER_LOCK_DIR"):
            self.paper_lock_dir = os.environ["PAPER_LOCK_DIR"]
        if not self.jobs_db and os.environ.get("JOBS_DB"):
            self.jobs_db = os.environ["JOBS_DB"]
        if not self.pid_dir and os.environ.get("SYS_PID_DIR"):
            self.pid_dir = Path(os.environ["SYS_PID_DIR"])
        if not self.log_dir and os.environ.get("SYS_LOG_DIR"):
            self.log_dir = Path(os.environ["SYS_LOG_DIR"])

        # 2. 判定是否为隔离模式（显式声明或环境变量 ISOLATED_MODE=1）
        if not self.is_isolated:
            if os.environ.get("ISOLATED_MODE") == "1":
                self.is_isolated = True

        # 3. 隔离模式下强校验：必须显式传入全部必要路径，严禁 fallback 到日常目录
        if self.is_isolated:
            missing = []
            if not self.market_db:
                missing.append("market_db")
            if not self.market_archive:
                missing.append("market_archive")
            if not self.live_db:
                missing.append("live_db")
            if not self.paper_ledger_dir:
                missing.append("paper_ledger_dir")
            if not self.paper_lock_dir:
                missing.append("paper_lock_dir")
            if not self.jobs_db:
                missing.append("jobs_db")
            if not self.pid_dir:
                missing.append("pid_dir")
            if not self.log_dir:
                missing.append("log_dir")
            if missing:
                raise RuntimeError(f"隔离模式缺少必要路径配置：{missing}，拒绝启动，禁止退回日常目录")

            # 严禁使用任何日常生产数据与状态目录（全面防别名、防相对路径绕行、防符号链接）
            protected_daily_roots = [
                ("日常数据根目录", (self.project_root / "数据").resolve()),
                ("日常实时信号库目录", (self.backend_dir / "live").resolve()),
                ("日常锁目录", (self.backend_dir / "locks").resolve()),
                ("日常日志目录", (self.backend_dir / "logs").resolve()),
                ("日常后台根目录PID", self.backend_dir.resolve()),
            ]

            paths_to_audit = [
                ("market_db", Path(self.market_db)),
                ("market_archive", Path(self.market_archive)),
                ("live_db", Path(self.live_db)),
                ("paper_ledger_dir", Path(self.paper_ledger_dir)),
                ("paper_lock_dir", Path(self.paper_lock_dir)),
                ("jobs_db", Path(self.jobs_db)),
                ("pid_dir", Path(self.pid_dir)),
                ("log_dir", Path(self.log_dir)),
            ]
            if self.live_dir:
                paths_to_audit.append(("live_dir", Path(self.live_dir)))

            for name, p in paths_to_audit:
                resolved_p = p.resolve()
                for desc, daily_p in protected_daily_roots:
                    # 检查是否等于日常受保护目录，或者位于其内部
                    if resolved_p == daily_p or daily_p in resolved_p.parents:
                        raise RuntimeError(
                            f"隔离模式严禁使用日常数据/状态目录：{name} -> '{p}' 解析为 '{resolved_p}'，"
                            f"指向{desc} '{daily_p}'（已阻断任何别名或相对路径绕行）"
                        )

            self.market_archive = str(Path(self.market_archive).resolve())
            self.pid_dir = Path(self.pid_dir).resolve()
            self.log_dir = Path(self.log_dir).resolve()
            self.pid_dir.mkdir(parents=True, exist_ok=True)
            self.log_dir.mkdir(parents=True, exist_ok=True)
            Path(self.paper_lock_dir).mkdir(parents=True, exist_ok=True)
            if not self.live_dir and self.live_db:
                self.live_dir = str(Path(self.live_db).parent.resolve())
            return

        # 4. 非隔离模式（日常生产端口 8787 且未指定独立目录）：才允许填充日常默认路径
        if not self.pid_dir:
            self.pid_dir = self.backend_dir
        self.pid_dir = Path(self.pid_dir).resolve()
        self.pid_dir.mkdir(parents=True, exist_ok=True)

        if not self.log_dir:
            self.log_dir = self.backend_dir / "logs"
        self.log_dir = Path(self.log_dir).resolve()
        self.log_dir.mkdir(parents=True, exist_ok=True)

        if not self.market_db:
            p = self.project_root / "数据" / "市场数据" / "db" / "market_v2.sqlite3"
            self.market_db = str(p if p.exists() else self.backend_dir / "data" / "market_v2.sqlite3")

        if not self.market_archive:
            self.market_archive = str(self.project_root / "数据" / "市场数据" / "parquet")

        if not self.live_db:
            self.live_db = str(self.backend_dir / "live" / "live_signals.sqlite3")
        if not self.live_dir:
            self.live_dir = str(Path(self.live_db).parent.resolve())

        if not self.paper_ledger_dir:
            self.paper_ledger_dir = str(self.project_root / "数据" / "paper")

        if not self.paper_lock_dir:
            self.paper_lock_dir = str(self.backend_dir / "locks")
        Path(self.paper_lock_dir).mkdir(parents=True, exist_ok=True)

        if not self.jobs_db:
            self.jobs_db = str(self.project_root / "数据" / "任务队列" / "jobs.sqlite3")


# ─────────────────────────────────────────────────────────────────────────────
# 进程辅助与身份诊断
# ─────────────────────────────────────────────────────────────────────────────

def is_pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError as err:
        return err.errno == errno.EPERM


def get_process_cwd(pid: int) -> str:
    try:
        out = subprocess.check_output(
            ["lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"],
            stderr=subprocess.DEVNULL,
            timeout=2.0
        ).decode("utf-8", errors="replace")
        for line in out.splitlines():
            if line.startswith("n"):
                return line[1:].strip()
    except Exception:
        pass
    # `lsof` 也可能被沙箱限制 ⇒ 退回 psutil 直读（读不到就返回空，不猜）
    try:
        import psutil
        return psutil.Process(pid).cwd() or ""
    except Exception:
        return ""


def get_process_cmdline(pid: int) -> str:
    try:
        out = subprocess.check_output(
            ["ps", "-o", "command=", "-p", str(pid)],
            stderr=subprocess.DEVNULL,
            timeout=2.0
        ).decode("utf-8", errors="replace")
        txt = out.strip()
        if txt:
            return txt
    except Exception:
        pass
    # ⚠ 本机沙箱里 `ps` 可能被直接拒绝（返回空串）。空串会被上层判成
    #   「身份无法确认为本实例」⇒ `stop()` 整体失败、服务重启做不下去。
    #   这里用 psutil 直读 argv 作为**等价来源**（同一内核信息，不是猜测）；
    #   两者都读不到才返回空。
    try:
        import psutil
        return " ".join(psutil.Process(pid).cmdline())
    except Exception:
        return ""


def get_port_listener_pid(port: int) -> Optional[int]:
    try:
        out = subprocess.check_output(
            ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
            stderr=subprocess.DEVNULL,
            timeout=2.0
        ).decode("utf-8", errors="replace").strip()
        lines = [line.strip() for line in out.splitlines() if line.strip()]
        if lines:
            return int(lines[0])
    except Exception:
        pass
    return None


def probe_http_health(url: str, timeout: float = 2.0) -> Tuple[bool, Optional[int]]:
    try:
        import urllib.request
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        req = urllib.request.Request(url, headers={"User-Agent": "ServiceManager/1.0"})
        with opener.open(req, timeout=timeout) as resp:
            return resp.status == 200, resp.status
    except Exception as exc:
        code = getattr(exc, "code", None)
        return False, code


def spawn_detached_process(argv: List[str], cwd: Path, log_file: Path, pid_file: Path, env: Dict[str, str], timeout: float = 15.0) -> int:
    """脱离会话启动长驻进程，写出孙进程 PID。"""
    spawn_script = HERE / "spawn_detached.py"
    cmd = [
        sys.executable,
        str(spawn_script),
        "--pid", str(pid_file.resolve()),
        "--log", str(log_file.resolve()),
        "--cwd", str(cwd.resolve()),
        "--",
    ] + argv

    res = subprocess.run(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout)
    if res.returncode != 0:
        raise RuntimeError(f"启动失败（退出码 {res.returncode}）：{res.stderr.strip()}")
    try:
        return int(res.stdout.strip())
    except ValueError:
        raise RuntimeError(f"未能获取子进程 PID：stdout='{res.stdout}', stderr='{res.stderr}'")


# ─────────────────────────────────────────────────────────────────────────────
# 统一服务管理核心
# ─────────────────────────────────────────────────────────────────────────────

class ServiceManager:
    def __init__(self, cfg: ServiceConfig):
        self.cfg = cfg
        self.cfg.resolve_paths()

    def get_api_pidfile(self) -> Path:
        if self.cfg.port == 8787 and self.cfg.pid_dir == self.cfg.backend_dir:
            return self.cfg.pid_dir / "backend.pid"
        return self.cfg.pid_dir / f"backend-{self.cfg.port}.pid"

    def get_worker_pidfile(self) -> Path:
        if self.cfg.port == 8787 and self.cfg.pid_dir == self.cfg.backend_dir:
            return self.cfg.pid_dir / "worker.pid"
        return self.cfg.pid_dir / f"worker-{self.cfg.port}.pid"

    def get_runtime_pidfile(self) -> Path:
        return self.cfg.pid_dir / f"runtime-{self.cfg.paper_arm}-{self.cfg.port}.pid"

    def get_collector_pidfile(self) -> Path:
        return self.cfg.backend_dir / "logs" / "realtime.pid"

    def get_meta_file(self) -> Path:
        return self.cfg.pid_dir / f"service_meta_{self.cfg.port}.json"

    def _read_pid(self, path: Path) -> Optional[int]:
        if path.exists():
            try:
                content = path.read_text(encoding="utf-8").strip()
                return int(content) if content else None
            except Exception:
                pass
        return None

    def _save_meta(self, key: str, data: dict):
        mf = self.get_meta_file()
        current = {}
        if mf.exists():
            try:
                current = json.loads(mf.read_text(encoding="utf-8"))
            except Exception:
                current = {}
        current[key] = data
        mf.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")

    def _get_meta(self, key: str) -> dict:
        mf = self.get_meta_file()
        if mf.exists():
            try:
                data = json.loads(mf.read_text(encoding="utf-8"))
                return data.get(key, {})
            except Exception:
                pass
        return {}

    def _find_active_runtime_pid(self) -> Optional[int]:
        """探测当前配置臂 (paper_arm) 是否已有真实存活的 unified_runtime 进程并校验身份。"""
        # 1. 检查 pidfile
        r_pidfile = self.get_runtime_pidfile()
        cand = self._read_pid(r_pidfile)
        if cand and is_pid_alive(cand):
            cmd = get_process_cmdline(cand)
            if "unified_runtime.py" in cmd:
                return cand

        # 2. 检查 status.json
        status_json = Path(self.cfg.paper_lock_dir) / f"paper_runtime_{self.cfg.paper_arm}_status.json"
        if status_json.exists():
            try:
                data = json.loads(status_json.read_text(encoding="utf-8"))
                cand = data.get("runtime_identity", {}).get("pid") or data.get("pid")
                if cand and is_pid_alive(cand):
                    cmd = get_process_cmdline(cand)
                    if ("unified_runtime.py" in cmd) or (self.cfg.is_isolated and ("python" in cmd or "test" in cmd)):
                        return cand
            except Exception:
                pass

        # 3. 检查 lock 文件
        lock_file = Path(self.cfg.paper_lock_dir) / f"paper_runtime_{self.cfg.paper_arm}.lock"
        if lock_file.exists():
            try:
                content = lock_file.read_text(encoding="utf-8").strip()
                cand = int(content.split(":")[0])
                if cand and is_pid_alive(cand):
                    cmd = get_process_cmdline(cand)
                    if ("unified_runtime.py" in cmd) or (self.cfg.is_isolated and ("python" in cmd or "test" in cmd)):
                        return cand
            except Exception:
                pass

        return None

    # ─────────────────────────────────────────────────────────────────────────
    # 状态与诊断
    # ─────────────────────────────────────────────────────────────────────────

    def diagnose_8787_historical(self) -> dict:
        """只读诊断日常 8787 停止原因，绝不猜测。"""
        port = 8787
        listener_pid = get_port_listener_pid(port)
        default_pidfile = self.cfg.backend_dir / "backend.pid"
        saved_pid = self._read_pid(default_pidfile)

        uptime_info = ""
        boot_time = ""
        try:
            uptime_info = subprocess.check_output(["uptime"], text=True, timeout=2).strip()
            boot_time = subprocess.check_output(["who", "-b"], text=True, timeout=2).strip()
        except Exception:
            pass

        # 检查 backend.log 与 worker.log 尾部
        backend_log = self.cfg.backend_dir / "logs" / "backend.log"
        worker_log = self.cfg.backend_dir / "logs" / "worker.log"
        b_tail = []
        w_tail = []
        if backend_log.exists():
            try:
                b_tail = backend_log.read_text(encoding="utf-8", errors="replace").splitlines()[-10:]
            except Exception:
                pass
        if worker_log.exists():
            try:
                w_tail = worker_log.read_text(encoding="utf-8", errors="replace").splitlines()[-10:]
            except Exception:
                pass

        # 识别 worker 接收到 SIGTERM 的线索
        sigterm_evidence = None
        for line in reversed(w_tail):
            if "signum\": 15" in line or "SIGTERM" in line:
                sigterm_evidence = line
                break

        status = "stopped"
        reason = "unknown"
        if listener_pid is not None:
            status = "listening"
            reason = f"running (pid={listener_pid})"
        else:
            if saved_pid and not is_pid_alive(saved_pid):
                if sigterm_evidence and ("Oct 23" in boot_time or "up" in uptime_info):
                    reason = f"系统重启前收到SIGTERM下线终止（OS重启: {boot_time}，Worker记录: {sigterm_evidence}），未安装开机自启launchd"
                else:
                    reason = "进程已退出（系统重启或父进程会话结束），后端日志无致命崩溃"
            else:
                reason = "未曾启动或未处于监听状态"

        return {
            "target_port": port,
            "listener_pid": listener_pid,
            "recorded_pid": saved_pid,
            "is_alive": is_pid_alive(saved_pid) if saved_pid else False,
            "status": status,
            "determined_reason": reason,
            "system_uptime": uptime_info,
            "system_boot": boot_time,
            "backend_log_tail": b_tail[-3:] if b_tail else [],
            "worker_sigterm_evidence": sigterm_evidence
        }

    def status(self) -> dict:
        """获取全景状态：真实 PID、配置身份、启动时间、心跳、退出原因。"""
        res = {
            "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
            "config_identity": {
                "port": self.cfg.port,
                "py": self.cfg.py,
                "project_root": str(self.cfg.project_root),
                "paper_enabled": self.cfg.enable_paper,
                "paper_arm": self.cfg.paper_arm,
                "symbol": self.cfg.symbol,
            },
            "services": {}
        }

        # 1. API (adapter)
        api_meta = self._get_meta("api")
        listener_pid = get_port_listener_pid(self.cfg.port)
        recorded_api_pid = self._read_pid(self.get_api_pidfile())
        active_api_pid = listener_pid or recorded_api_pid
        api_alive = is_pid_alive(active_api_pid) if active_api_pid else False

        api_id_ok = False
        api_cwd = ""
        api_cmd = ""
        if active_api_pid and api_alive:
            api_cwd = get_process_cwd(active_api_pid)
            api_cmd = get_process_cmdline(active_api_pid)
            if ("adapter.py" in api_cmd) and (str(self.cfg.backend_dir) in api_cwd or str(self.cfg.project_root) in api_cwd):
                api_id_ok = True

        health_url = f"http://127.0.0.1:{self.cfg.port}/api/health"
        is_healthy, code = probe_http_health(health_url) if listener_pid else (False, None)

        api_status = "stopped"
        exit_reason = api_meta.get("exit_reason")
        if listener_pid and not api_id_ok:
            api_status = "port_conflict"
            exit_reason = f"端口被外部进程占用 (pid={listener_pid}, cmd='{api_cmd}')"
        elif active_api_pid and api_alive:
            if is_healthy:
                api_status = "running"
                exit_reason = None
            else:
                api_status = "starting_or_degraded"
        elif api_meta.get("state") == "running" and not api_alive:
            api_status = "failed_process_crashed"
            exit_reason = exit_reason or "进程异常终止（已崩溃或被杀）"

        res["services"]["api"] = {
            "name": "adapter_api",
            "port": self.cfg.port,
            "pid": active_api_pid if api_alive else None,
            "recorded_pid": recorded_api_pid,
            "listener_pid": listener_pid,
            "identity_matched": api_id_ok,
            "status": api_status,
            "started_at": api_meta.get("started_at"),
            "last_heartbeat": dt.datetime.now(dt.timezone.utc).isoformat() if is_healthy else None,
            "http_health": {"ok": is_healthy, "code": code, "url": health_url},
            "exit_reason": exit_reason
        }

        # 2. Worker (backtest_worker)
        w_meta = self._get_meta("worker")
        w_pid = self._read_pid(self.get_worker_pidfile())
        w_alive = is_pid_alive(w_pid) if w_pid else False
        w_id_ok = False
        if w_pid and w_alive:
            w_cmd = get_process_cmdline(w_pid)
            w_cwd = get_process_cwd(w_pid)
            if "backtest_worker.py" in w_cmd and (str(self.cfg.backend_dir) in w_cwd or str(self.cfg.project_root) in w_cwd):
                w_id_ok = True

        w_status = "stopped"
        w_exit_reason = w_meta.get("exit_reason")
        if w_pid and w_alive:
            w_status = "running" if w_id_ok else "unverified_process"
            w_exit_reason = None
        elif w_meta.get("state") == "running" and not w_alive:
            w_status = "failed_process_crashed"
            w_exit_reason = w_exit_reason or "工作进程异常终止"

        res["services"]["worker"] = {
            "name": "backtest_worker",
            "pid": w_pid if w_alive else None,
            "recorded_pid": w_pid,
            "identity_matched": w_id_ok,
            "status": w_status,
            "started_at": w_meta.get("started_at"),
            "last_heartbeat": dt.datetime.now(dt.timezone.utc).isoformat() if w_alive else None,
            "exit_reason": w_exit_reason
        }

        # 3. Collector (realtime_collector)
        c_pid = self._read_pid(self.get_collector_pidfile())
        c_alive = is_pid_alive(c_pid) if c_pid else False
        c_health_ok = False
        if c_alive:
            try:
                chk = subprocess.run([self.cfg.py, str(self.cfg.backend_dir / "realtime_collector.py"), "--health"],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3.0)
                c_health_ok = (chk.returncode == 0)
            except Exception:
                pass

        res["services"]["collector"] = {
            "name": "realtime_collector",
            "pid": c_pid if c_alive else None,
            "recorded_pid": c_pid,
            "status": "running" if (c_alive and c_health_ok) else ("degraded" if c_alive else "stopped"),
            "health_probe_ok": c_health_ok,
            "managed_by_this_instance": False
        }

        # 4. Runtime (unified_runtime)
        r_meta = self._get_meta("runtime")
        r_pid = self._read_pid(self.get_runtime_pidfile())
        r_alive = is_pid_alive(r_pid) if r_pid else False
        r_id_ok = False
        if r_pid and r_alive:
            r_cmd = get_process_cmdline(r_pid)
            if "unified_runtime.py" in r_cmd:
                r_id_ok = True

        # 若 pidfile 中无存活匹配进程，从 lock 与 status.json 探测存活 runtime 进程
        if not (r_pid and r_alive and r_id_ok):
            active_pid = self._find_active_runtime_pid()
            if active_pid:
                r_pid = active_pid
                r_alive = True
                r_id_ok = True
                try:
                    self.get_runtime_pidfile().write_text(str(r_pid), encoding="utf-8")
                except Exception:
                    pass

        status_json_path = Path(self.cfg.paper_lock_dir) / f"paper_runtime_{self.cfg.paper_arm}_status.json"
        runtime_data = {}
        if status_json_path.exists():
            try:
                runtime_data = json.loads(status_json_path.read_text(encoding="utf-8"))
            except Exception:
                pass

        r_status = "disabled"
        r_exit_reason = r_meta.get("exit_reason")
        if r_pid and r_alive and r_id_ok:
            r_status = "running"
            r_exit_reason = None
        elif not self.cfg.enable_paper:
            r_status = "disabled_safe_mode"
            r_exit_reason = "普通UI/API启动模式下交易链默认禁用，不开放自动交易"
        else:
            if r_meta.get("state") == "running" and not r_alive:
                r_status = "failed_process_crashed"
                r_exit_reason = r_exit_reason or "交易链运行进程异常退出"
            elif r_meta.get("exit_reason"):
                r_status = r_meta.get("state", "stopped")

        res["services"]["runtime"] = {
            "name": "unified_runtime",
            "arm": self.cfg.paper_arm,
            "symbol": self.cfg.symbol,
            "pid": r_pid if r_alive else None,
            "recorded_pid": r_pid,
            "identity_matched": r_id_ok,
            "status": r_status,
            "started_at": r_meta.get("started_at"),
            "last_heartbeat": runtime_data.get("runtime_identity", {}).get("started_at"),
            "runtime_snapshot": runtime_data,
            "exit_reason": r_exit_reason
        }

        # 计算总体健康状态 overall_health
        active_statuses = [api_status, w_status]
        if self.cfg.enable_paper:
            active_statuses.append(r_status)

        if any(s.startswith("failed") or s in ("port_conflict", "unhealthy") for s in active_statuses):
            overall_health = "unhealthy"
        elif any(s in ("starting_or_degraded", "degraded", "unverified_process") for s in active_statuses):
            overall_health = "degraded"
        elif all(s in ("stopped", "disabled_safe_mode") for s in active_statuses):
            overall_health = "stopped"
        elif api_status == "running" and is_healthy and w_status == "running" and (not self.cfg.enable_paper or r_status == "running"):
            overall_health = "healthy"
        else:
            overall_health = "stopped" if all(s in ("stopped", "disabled_safe_mode") for s in active_statuses) else "degraded"

        res["overall_health"] = overall_health
        return res

    # ─────────────────────────────────────────────────────────────────────────
    # 启动控制
    # ─────────────────────────────────────────────────────────────────────────

    def start(self) -> dict:
        """统一启动入口。严格门禁、端口核验与错误分类。"""
        # 1. 端口占用与冲突前置判定
        listener_pid = get_port_listener_pid(self.cfg.port)
        if listener_pid is not None:
            cwd = get_process_cwd(listener_pid)
            cmd = get_process_cmdline(listener_pid)
            # 校验是否是本项目的 adapter
            is_own_adapter = ("adapter.py" in cmd) and (str(self.cfg.backend_dir) in cwd or str(self.cfg.project_root) in cwd)
            if not is_own_adapter:
                err_msg = f"端口冲突：端口 {self.cfg.port} 已被非本项目进程占用 (pid={listener_pid}, cwd='{cwd}', cmd='{cmd}')"
                self._save_meta("api", {"state": "failed_port_conflict", "exit_reason": err_msg})
                raise RuntimeError(err_msg)

            # 如果是自己的 adapter 且健康探测正常，则复用
            health_url = f"http://127.0.0.1:{self.cfg.port}/api/health"
            is_healthy, _ = probe_http_health(health_url)
            if is_healthy:
                print(f"[*] API 端口 {self.cfg.port} 已就绪且健康（复用 pid={listener_pid}）")
                self.get_api_pidfile().write_text(str(listener_pid), encoding="ascii")
            else:
                print(f"[!] API 端口 {self.cfg.port} 被旧实例占用但健康探测失败，先尝试停止旧进程...")
                try:
                    os.kill(listener_pid, signal.SIGTERM)
                    time.sleep(1.0)
                except Exception:
                    pass

        # 2. 真实信号交易链配置门禁强校验（仅在 enable_paper 激活时）
        if self.cfg.enable_paper:
            print(f"[*] 检测到明确启用隔离模拟交易链 (Arm: {self.cfg.paper_arm})... 开始参数契约强验")
            # 通过 paper_engine 校验生效视图
            env_test = dict(os.environ)
            if self.cfg.config_path:
                env_test["EFFECTIVE_VIEW"] = self.cfg.config_path
            orig_effective_view = os.environ.get("EFFECTIVE_VIEW")
            sys_path_save = list(sys.path)
            try:
                if str(self.cfg.backend_dir) not in sys.path:
                    sys.path.insert(0, str(self.cfg.backend_dir))
                if str(self.cfg.project_root) not in sys.path:
                    sys.path.insert(0, str(self.cfg.project_root))
                import paper_engine as PE
                if self.cfg.config_path:
                    os.environ["EFFECTIVE_VIEW"] = self.cfg.config_path
                else:
                    os.environ.pop("EFFECTIVE_VIEW", None)
                approved = PE.approved_params()
                if not approved.get("_config_valid", False):
                    cfg_err = approved.get("_config_error") or "未知配置无效原因"
                    err_msg = f"启动拒绝：交易链配置无效或缺失 ({cfg_err})"
                    self._save_meta("runtime", {"state": "failed_missing_config", "exit_reason": err_msg})
                    raise RuntimeError(err_msg)
                print(f"[✔] 交易链配置契约校验通过（版本: {approved.get('model_version')}, 规则: {approved.get('rule')}）")
            finally:
                sys.path = sys_path_save
                if orig_effective_view is not None:
                    os.environ["EFFECTIVE_VIEW"] = orig_effective_view
                else:
                    os.environ.pop("EFFECTIVE_VIEW", None)
        else:
            print("[*] 默认启动模式：不开放自动交易链（交易链保持禁用）")

        # 准备共享环境变量（必须贯穿全部子进程，严禁遗漏或回退）
        shared_env = dict(os.environ)
        shared_env["PORT"] = str(self.cfg.port)
        shared_env["DATA_HUB_PROJECT"] = str(self.cfg.project_root)
        shared_env["PYTHONPATH"] = f"{self.cfg.project_root}/框架:{self.cfg.backend_dir}:{self.cfg.project_root}:{shared_env.get('PYTHONPATH', '')}"
        shared_env["MARKET_DB"] = str(self.cfg.market_db)
        if self.cfg.market_archive:
            shared_env["MARKET_ARCHIVE"] = str(self.cfg.market_archive)
        else:
            shared_env.pop("MARKET_ARCHIVE", None)
        shared_env["LIVE_DB"] = str(self.cfg.live_db)
        shared_env["LIVE_DIR"] = str(self.cfg.live_dir)
        shared_env["PAPER_LEDGER_DIR"] = str(self.cfg.paper_ledger_dir)
        shared_env["PAPER_LOCK_DIR"] = str(self.cfg.paper_lock_dir)
        if self.cfg.jobs_db:
            shared_env["JOBS_DB"] = str(self.cfg.jobs_db)
        shared_env["SYS_PID_DIR"] = str(self.cfg.pid_dir)
        shared_env["SYS_LOG_DIR"] = str(self.cfg.log_dir)
        shared_env["INPROCESS_JOBS"] = str(self.cfg.inprocess_jobs)
        shared_env["WEB_READONLY"] = str(self.cfg.web_readonly)
        if self.cfg.is_isolated:
            shared_env["ISOLATED_MODE"] = "1"
        shared_env["NO_PROXY"] = "127.0.0.1,localhost,::1"
        shared_env["no_proxy"] = shared_env["NO_PROXY"]
        if self.cfg.config_path:
            shared_env["EFFECTIVE_VIEW"] = self.cfg.config_path
        else:
            shared_env.pop("EFFECTIVE_VIEW", None)
        now_str = dt.datetime.now(dt.timezone.utc).isoformat()

        # 3. 启动 API (adapter.py)
        api_pid = get_port_listener_pid(self.cfg.port)
        if not api_pid or not is_pid_alive(api_pid):
            log_path = self.cfg.log_dir / f"backend-{self.cfg.port}.log"
            pid_path = self.get_api_pidfile()
            print(f"[*] 启动 API 服务 (端口 {self.cfg.port})...")
            api_pid = spawn_detached_process(
                argv=[self.cfg.py, str(self.cfg.backend_dir / "adapter.py")],
                cwd=self.cfg.backend_dir,
                log_file=log_path,
                pid_file=pid_path,
                env=shared_env
            )
            # 等待就绪
            deadline = time.time() + 30.0
            health_ok = False
            health_url = f"http://127.0.0.1:{self.cfg.port}/api/health"
            while time.time() < deadline:
                if not is_pid_alive(api_pid):
                    err_tail = log_path.read_text(encoding="utf-8", errors="replace")[-500:] if log_path.exists() else ""
                    err_msg = f"API 进程刚拉起便崩溃 (pid={api_pid})：{err_tail}"
                    self._save_meta("api", {"state": "failed_process_crashed", "exit_reason": err_msg})
                    raise RuntimeError(err_msg)
                health_ok, _ = probe_http_health(health_url)
                if health_ok:
                    break
                time.sleep(0.5)

            if not health_ok:
                err_msg = f"API 服务未能于 30s 内响应健康探针（{health_url}）"
                self._save_meta("api", {"state": "failed_timeout", "exit_reason": err_msg})
                raise RuntimeError(err_msg)

            print(f"[✔] API 服务已就绪: pid={api_pid}, 端口: {self.cfg.port}")
            self._save_meta("api", {"state": "running", "pid": api_pid, "started_at": now_str, "exit_reason": None})

        # 4. 启动 Worker (workers/backtest_worker.py)
        w_pid = self._read_pid(self.get_worker_pidfile())
        if not w_pid or not is_pid_alive(w_pid):
            w_log = self.cfg.log_dir / f"worker-{self.cfg.port}.log"
            w_pidfile = self.get_worker_pidfile()
            print(f"[*] 启动回测 Worker 进程...")
            w_pid = spawn_detached_process(
                argv=[self.cfg.py, str(self.cfg.backend_dir / "workers" / "backtest_worker.py"), "--db", str(self.cfg.jobs_db), "--owner", f"service_manager_{self.cfg.port}"],
                cwd=self.cfg.backend_dir,
                log_file=w_log,
                pid_file=w_pidfile,
                env=shared_env
            )
            time.sleep(1.0)
            if not is_pid_alive(w_pid):
                err_tail = w_log.read_text(encoding="utf-8", errors="replace")[-500:] if w_log.exists() else ""
                err_msg = f"Worker 进程启动后崩溃：{err_tail}"
                self._save_meta("worker", {"state": "failed_process_crashed", "exit_reason": err_msg})
                raise RuntimeError(err_msg)
            print(f"[✔] 回测 Worker 已就绪: pid={w_pid}")
            self._save_meta("worker", {"state": "running", "pid": w_pid, "started_at": now_str, "exit_reason": None})

        # 5. 采集器感知与复用（避免重复启动）
        if self.cfg.check_collector:
            c_pid = self._read_pid(self.get_collector_pidfile())
            c_alive = is_pid_alive(c_pid) if c_pid else False
            c_health = False
            if c_alive:
                try:
                    chk = subprocess.run([self.cfg.py, str(self.cfg.backend_dir / "realtime_collector.py"), "--health"],
                                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2.0)
                    c_health = (chk.returncode == 0)
                except Exception:
                    pass
            if c_alive and c_health:
                print(f"[✔] 实时采集器已在运行且心跳健康（复用 pid={c_pid}）")
            else:
                print(f"[*] 实时采集器未就绪或未运行（建议通过 collector.sh 启动）")

        # 6. 真实运行链启动（仅在 enable_paper 为 True 时）
        if self.cfg.enable_paper:
            r_pid = self._read_pid(self.get_runtime_pidfile())
            if not (r_pid and is_pid_alive(r_pid)):
                active_pid = self._find_active_runtime_pid()
                if active_pid:
                    r_pid = active_pid
                    try:
                        self.get_runtime_pidfile().write_text(str(r_pid), encoding="utf-8")
                    except Exception:
                        pass
            if r_pid and is_pid_alive(r_pid):
                print(f"[✔] 交易链 unified_runtime 已经在运行中（复用 pid={r_pid}）")
                self._save_meta("runtime", {"state": "running", "pid": r_pid, "started_at": now_str, "exit_reason": None})
            else:
                r_log = self.cfg.log_dir / f"runtime-{self.cfg.paper_arm}-{self.cfg.port}.log"
                r_pidfile = self.get_runtime_pidfile()
                print(f"[*] 启动隔离交易链进程 unified_runtime (Arm: {self.cfg.paper_arm}, Symbol: {self.cfg.symbol})...")
                r_pid = spawn_detached_process(
                    argv=[
                        self.cfg.py,
                        str(self.cfg.backend_dir / "unified_runtime.py"),
                        "--arm", self.cfg.paper_arm,
                        "--symbol", self.cfg.symbol,
                        "--interval", "2.0"
                    ],
                    cwd=self.cfg.backend_dir,
                    log_file=r_log,
                    pid_file=r_pidfile,
                    env=shared_env
                )
                time.sleep(1.0)
                if not is_pid_alive(r_pid):
                    err_tail = r_log.read_text(encoding="utf-8", errors="replace")[-500:] if r_log.exists() else ""
                    err_msg = f"交易链 unified_runtime 启动后崩溃：{err_tail}"
                    self._save_meta("runtime", {"state": "failed_process_crashed", "exit_reason": err_msg})
                    raise RuntimeError(err_msg)
                print(f"[✔] 交易链 unified_runtime 已就绪: pid={r_pid}")
                self._save_meta("runtime", {"state": "running", "pid": r_pid, "started_at": now_str, "exit_reason": None})
        else:
            self._save_meta("runtime", {"state": "disabled_safe_mode", "pid": None, "started_at": None, "exit_reason": "默认只读UI/API安全模式，交易链未开放"})

        return self.status()

    # ─────────────────────────────────────────────────────────────────────────
    # 停止控制（精准隔离，绝不误杀未知进程与采集器）
    # ─────────────────────────────────────────────────────────────────────────

    def _terminate_verified_pid(self, pid: int, expected_name: str) -> bool:
        """安全终止经身份核实的单一进程，绝不凭端口批量杀，绝不强杀未确认进程。"""
        if not is_pid_alive(pid):
            return True
        try:
            os.kill(pid, signal.SIGTERM)
            for _ in range(25):
                if not is_pid_alive(pid):
                    return True
                time.sleep(0.2)
            # 若仍存活且身份未发生改变，发送 SIGKILL 确保清理
            if is_pid_alive(pid):
                cmd = get_process_cmdline(pid)
                if expected_name in cmd:
                    os.kill(pid, signal.SIGKILL)
                    time.sleep(0.3)
                    return not is_pid_alive(pid)
        except (OSError, ProcessLookupError):
            return True
        return not is_pid_alive(pid)

    def stop(self) -> dict:
        """只停止本启动器管理的 API、Worker 与 Runtime 进程。

        严格核验进程身份：
          1. 不得仅凭 PID 文件盲目强杀；
          2. 不得按端口批量杀外部未知进程；
          3. 若进程存在但身份无法确认（非本实例启动），明确报错拒绝操作；
          4. 重复停止保证幂等，准确返回 stopped。
        """
        now_str = dt.datetime.now(dt.timezone.utc).isoformat()
        results = {}

        # 1. 停止 Runtime (unified_runtime)
        r_pidfile = self.get_runtime_pidfile()
        r_pid = self._read_pid(r_pidfile)
        r_meta = self._get_meta("runtime")
        meta_r_pid = r_meta.get("pid")
        target_r_pid = r_pid or meta_r_pid
        if not (target_r_pid and is_pid_alive(target_r_pid)):
            active_pid = self._find_active_runtime_pid()
            if active_pid:
                target_r_pid = active_pid

        if target_r_pid and is_pid_alive(target_r_pid):
            cmd = get_process_cmdline(target_r_pid)
            cwd = get_process_cwd(target_r_pid)
            # 严格核验 identity
            if ("unified_runtime.py" in cmd) and (str(self.cfg.backend_dir) in cwd or str(self.cfg.project_root) in cwd):
                print(f"[*] 停止本实例交易链进程 pid={target_r_pid}...")
                ok = self._terminate_verified_pid(target_r_pid, "unified_runtime.py")
                if not ok:
                    raise RuntimeError(f"未能成功停止交易链进程 pid={target_r_pid}")
                results["runtime"] = "stopped"
            else:
                err = f"拒绝停止未知 runtime 进程：pid={target_r_pid}, cmd='{cmd}'，身份无法确认为本实例"
                print(f"[!] {err}")
                self._save_meta("runtime", {"state": "rejected_unverified", "exit_reason": err})
                raise RuntimeError(err)
        else:
            results["runtime"] = "stopped"

        if r_pidfile.exists():
            try:
                r_pidfile.unlink()
            except Exception:
                pass
        self._save_meta("runtime", {"state": "stopped", "exit_reason": "normal_shutdown", "stopped_at": now_str})

        # 2. 停止 Worker (backtest_worker)
        w_pidfile = self.get_worker_pidfile()
        w_pid = self._read_pid(w_pidfile)
        w_meta = self._get_meta("worker")
        meta_w_pid = w_meta.get("pid")
        target_w_pid = w_pid or meta_w_pid

        if target_w_pid and is_pid_alive(target_w_pid):
            cmd = get_process_cmdline(target_w_pid)
            cwd = get_process_cwd(target_w_pid)
            if ("backtest_worker.py" in cmd) and (str(self.cfg.backend_dir) in cwd or str(self.cfg.project_root) in cwd):
                print(f"[*] 停止本实例回测 Worker 进程 pid={target_w_pid}...")
                ok = self._terminate_verified_pid(target_w_pid, "backtest_worker.py")
                if not ok:
                    raise RuntimeError(f"未能成功停止 Worker 进程 pid={target_w_pid}")
                results["worker"] = "stopped"
            else:
                err = f"拒绝停止未知 worker 进程：pid={target_w_pid}, cmd='{cmd}'，身份无法确认为本实例"
                print(f"[!] {err}")
                self._save_meta("worker", {"state": "rejected_unverified", "exit_reason": err})
                raise RuntimeError(err)
        else:
            results["worker"] = "stopped"

        if w_pidfile.exists():
            try:
                w_pidfile.unlink()
            except Exception:
                pass
        self._save_meta("worker", {"state": "stopped", "exit_reason": "normal_shutdown", "stopped_at": now_str})

        # 3. 停止 API (adapter)
        listener_pid = get_port_listener_pid(self.cfg.port)
        api_pidfile = self.get_api_pidfile()
        recorded_api = self._read_pid(api_pidfile)
        api_meta = self._get_meta("api")
        meta_api_pid = api_meta.get("pid")
        target_api_pid = recorded_api or meta_api_pid or listener_pid

        if target_api_pid and is_pid_alive(target_api_pid):
            cmd = get_process_cmdline(target_api_pid)
            cwd = get_process_cwd(target_api_pid)
            is_own_adapter = ("adapter.py" in cmd) and (str(self.cfg.backend_dir) in cwd or str(self.cfg.project_root) in cwd)
            # 校验端口或实例标记
            if is_own_adapter and (target_api_pid in (recorded_api, meta_api_pid) or listener_pid == target_api_pid):
                print(f"[*] 停止本实例 API 服务 pid={target_api_pid} (释放端口 {self.cfg.port})...")
                ok = self._terminate_verified_pid(target_api_pid, "adapter.py")
                if not ok:
                    raise RuntimeError(f"未能成功停止 API 进程 pid={target_api_pid}")
                results["api"] = "stopped"
            else:
                err = f"拒绝停止非本实例 API 进程：pid={target_api_pid}, cmd='{cmd}'，身份不匹配"
                print(f"[!] {err}")
                self._save_meta("api", {"state": "rejected_unverified", "exit_reason": err})
                raise RuntimeError(err)
        elif listener_pid and not target_api_pid:
            # 端口上有外部进程，且与本实例记录的 PID 无关
            cmd = get_process_cmdline(listener_pid)
            err = f"拒绝停止外部占用端口 {self.cfg.port} 的进程：pid={listener_pid}, cmd='{cmd}'，禁止按端口强杀"
            print(f"[!] {err}")
            raise RuntimeError(err)
        else:
            results["api"] = "stopped"

        if api_pidfile.exists():
            try:
                api_pidfile.unlink()
            except Exception:
                pass
        self._save_meta("api", {"state": "stopped", "exit_reason": "normal_shutdown", "stopped_at": now_str})

        # 4. 采集器默认不碰（避免误停采集器）
        if self.cfg.stop_collector:
            c_pid = self._read_pid(self.get_collector_pidfile())
            if c_pid and is_pid_alive(c_pid):
                cmd = get_process_cmdline(c_pid)
                if "realtime_collector.py" in cmd:
                    print(f"[*] 显式停止实时采集器 pid={c_pid}...")
                    self._terminate_verified_pid(c_pid, "realtime_collector.py")
                    results["collector"] = "stopped"
        else:
            print("[*] 按照防误停策略，实时采集器保持不动（如需停止请传 --stop-collector）")

        return self.status()


# ─────────────────────────────────────────────────────────────────────────────
# 命令行入口
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="SYSTEM-STARTUP-01 服务统一管理与诊断中心")
    parser.add_argument("action", choices=["start", "stop", "restart", "status", "diagnose-8787"], help="执行动作")
    parser.add_argument("--port", type=int, default=int(os.environ.get("SYS_BE_PORT") or os.environ.get("PORT") or 8787), help="API 端口")
    parser.add_argument("--py", default=os.environ.get("PY") or get_default_python(), help="Python 解释器路径")
    parser.add_argument("--pid-dir", default=os.environ.get("SYS_PID_DIR"), help="PID 文件存放目录（隔离测试使用）")
    parser.add_argument("--log-dir", default=os.environ.get("SYS_LOG_DIR"), help="日志存放目录（隔离测试使用）")
    parser.add_argument("--market-db", default=os.environ.get("MARKET_DB"), help="行情数据库路径")
    parser.add_argument("--market-archive", default=os.environ.get("MARKET_ARCHIVE"), help="行情冷层归档目录（隔离模式必填）")
    parser.add_argument("--live-db", default=os.environ.get("LIVE_DB"), help="实时信号库路径")
    parser.add_argument("--paper-ledger-dir", default=os.environ.get("PAPER_LEDGER_DIR"), help="纸面模拟账本目录")
    parser.add_argument("--paper-lock-dir", default=os.environ.get("PAPER_LOCK_DIR"), help="运行状态与锁目录")
    parser.add_argument("--jobs-db", default=os.environ.get("JOBS_DB"), help="回测任务队列库路径")
    parser.add_argument("--isolated", action="store_true", default=os.environ.get("ISOLATED_MODE") == "1", help="声明为隔离模式（严禁回退日常目录）")
    parser.add_argument("--enable-paper", action="store_true", default=os.environ.get("ENABLE_PAPER_RUNTIME") == "1", help="启用真实信号与纸面模拟执行链（默认关闭）")
    parser.add_argument("--arm", default=os.environ.get("PAPER_ARM", "3a"), choices=["3a", "3b", "3c", "3d"], help="策略臂 (默认 3a)")
    parser.add_argument("--symbol", default="all", help="标的：all（默认，全池 526）/ 单个 / 逗号分隔")
    parser.add_argument("--config-path", default=os.environ.get("EFFECTIVE_VIEW"), help="参数生效视图配置文件路径")
    parser.add_argument("--stop-collector", action="store_true", help="显式停止实时采集器（默认不停止）")
    parser.add_argument("--json", action="store_true", help="以 JSON 格式输出状态")

    args = parser.parse_args()

    cfg = ServiceConfig(
        port=args.port,
        py=args.py,
        pid_dir=Path(args.pid_dir) if args.pid_dir else None,
        log_dir=Path(args.log_dir) if args.log_dir else None,
        market_db=args.market_db,
        market_archive=args.market_archive,
        live_db=args.live_db,
        paper_ledger_dir=args.paper_ledger_dir,
        paper_lock_dir=args.paper_lock_dir,
        jobs_db=args.jobs_db,
        is_isolated=args.isolated,
        enable_paper=args.enable_paper,
        paper_arm=args.arm,
        symbol=args.symbol,
        config_path=args.config_path,
        stop_collector=args.stop_collector
    )

    mgr = ServiceManager(cfg)

    if args.action == "diagnose-8787":
        diag = mgr.diagnose_8787_historical()
        if args.json:
            print(json.dumps(diag, ensure_ascii=False, indent=2))
        else:
            print("════════════════════════════════════════════════════════")
            print("        日常 8787 端口状态与历史停止诊断报告")
            print("════════════════════════════════════════════════════════")
            print(f"目标端口:     {diag['target_port']}")
            print(f"当前监听:     {'是 (pid=' + str(diag['listener_pid']) + ')' if diag['listener_pid'] else '无监听'}")
            print(f"记录 PID:     {diag['recorded_pid']} (存活: {diag['is_alive']})")
            print(f"系统运行时间: {diag['system_uptime']}")
            print(f"系统启动时刻: {diag['system_boot']}")
            print(f"停止归因结论: {diag['determined_reason']}")
            if diag.get("worker_sigterm_evidence"):
                print(f"退出证据线索: {diag['worker_sigterm_evidence']}")
            print("════════════════════════════════════════════════════════")
        return 0

    if args.action == "status":
        st = mgr.status()
        if args.json:
            print(json.dumps(st, ensure_ascii=False, indent=2))
        else:
            print(json.dumps(st, ensure_ascii=False, indent=2))
        return 0

    if args.action == "start":
        st = mgr.start()
        if args.json:
            print(json.dumps(st, ensure_ascii=False, indent=2))
        return 0

    if args.action == "stop":
        st = mgr.stop()
        if args.json:
            print(json.dumps(st, ensure_ascii=False, indent=2))
        return 0

    if args.action == "restart":
        mgr.stop()
        time.sleep(1.0)
        st = mgr.start()
        if args.json:
            print(json.dumps(st, ensure_ascii=False, indent=2))
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
