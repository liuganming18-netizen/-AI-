# -*- coding: utf-8 -*-
"""隔离 API 服务的启停封装（供 21/30/50/60 复用）。

设计要点
--------
· 服务进程 = `40b_隔离API服务.py`，用的是**生产同一份** `adapter.Handler` + `live_api` 代码，
  只是把 `LIVE_DB` / `PAPER_LEDGER_DIR` / `PAPER_LOCK_DIR` 指到 `09/isolated/`，端口换 8899；
· 收 `SIGTERM` 时它会把进程内 SQL 写探针报告打到 stdout（`@@PROBE@@`），本模块负责抓回来，
  这样「隔离服务有没有写生产库」有**进程内**证据，而不是靠文件指纹猜；
· 起停都留痕：pid、端口、实际库路径、启动耗时、退出码。
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import threading
import time
from pathlib import Path

import lib09 as U

SCRIPTS = Path(__file__).resolve().parent
RUNNER = SCRIPTS / "40b_隔离API服务.py"


class IsoApi:
    def __init__(self, port: int = U.ISO_API_PORT, live_db: Path | None = None,
                 paper_dir: Path | None = None, lock_dir: Path | None = None,
                 market_db: Path | None = None):
        self.port = port
        self.live_db = Path(live_db) if live_db else (U.ISO / "live" / "live_signals.sqlite3")
        self.paper_dir = Path(paper_dir) if paper_dir else (U.ISO / "paper")
        self.lock_dir = Path(lock_dir) if lock_dir else (U.ISO / "locks")
        self.market_db = Path(market_db) if market_db else U.MARKET_DB
        self.proc: subprocess.Popen | None = None
        self.lines: list[str] = []
        self.ready: dict | None = None
        self.probe: dict | None = None
        self._evt = threading.Event()
        self._t: threading.Thread | None = None

    # ── 启动 ────────────────────────────────────────────────────────────
    def start(self, timeout_s: int = 60) -> dict:
        self.live_db.parent.mkdir(parents=True, exist_ok=True)
        self.paper_dir.mkdir(parents=True, exist_ok=True)
        self.lock_dir.mkdir(parents=True, exist_ok=True)
        env = U.iso_env({
            "PORT": str(self.port),
            "LIVE_DB": str(self.live_db.resolve()),
            "LIVE_DIR": str(self.live_db.parent.resolve()),
            "PAPER_LEDGER_DIR": str(self.paper_dir.resolve()),
            "PAPER_LOCK_DIR": str(self.lock_dir.resolve()),
            "MARKET_DB": str(self.market_db.resolve()),
            # 关掉 adapter 的大响应字节缓存（`_RESP_TTL`，默认 3s + 10 倍保留期 ⇒ 最长
            # 30s 陈旧）。`/api/v1/signals` 正在缓存前缀里，若不关，跨进程恢复测试会
            # 读到**上一个进程**留下的陈旧字节，把「读回失败」和「缓存未过期」混淆。
            # 代码自身写明「设为 0 即完全关闭（回到原行为）」，不改任何业务语义。
            "ADAPTER_RESP_CACHE_TTL": "0",
        })
        t0 = time.time()
        self.proc = subprocess.Popen(
            [U.PY, str(RUNNER)], cwd=str(U.PROJECT), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        self._t = threading.Thread(target=self._reader, daemon=True)
        self._t.start()
        ok = self._evt.wait(timeout_s)
        started = {"ok": bool(ok and self.ready), "pid": self.proc.pid,
                   "port": self.port, "elapsed_s": round(time.time() - t0, 2),
                   "LIVE_DB": str(self.live_db), "PAPER_LEDGER_DIR": str(self.paper_dir),
                   "MARKET_DB": str(self.market_db),
                   "ADAPTER_RESP_CACHE_TTL": "0（关闭大响应字节缓存，保证跨进程读回不是陈旧字节）",
                   "ready": self.ready, "tail": self.lines[-8:]}
        if not started["ok"]:
            self.kill()
            started["error"] = "未在超时内就绪（端口被占 / import 失败 / 库不可读）"
            return started
        # 就绪后再等一次 HTTP，确认真的能应答（不只是打了日志）
        started["http"] = U.wait_http(f"http://127.0.0.1:{self.port}/api/v1/health", 30)
        started["ok"] = bool(started["http"].get("ok"))
        return started

    def _reader(self) -> None:
        assert self.proc and self.proc.stdout
        for line in self.proc.stdout:
            self.lines.append(line.rstrip("\n"))
            if line.startswith("@@READY@@"):
                try:
                    self.ready = json.loads(line[len("@@READY@@"):])
                except ValueError:
                    self.ready = {"raw": line}
                self._evt.set()
            elif line.startswith("@@PROBE@@"):
                try:
                    self.probe = json.loads(line[len("@@PROBE@@"):])
                except ValueError:
                    self.probe = {"raw": line}

    # ── 停止 ────────────────────────────────────────────────────────────
    def stop(self, timeout_s: int = 25) -> dict:
        if not self.proc:
            return {"stopped": False, "reason": "未启动"}
        if self.proc.poll() is not None:
            return {"stopped": True, "returncode": self.proc.returncode,
                    "note": "进程已自行退出", "probe": self.probe}
        self.proc.send_signal(signal.SIGTERM)
        try:
            rc = self.proc.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            rc = self.proc.wait(timeout=10)
        if self._t:
            self._t.join(timeout=5)
        return {"stopped": True, "returncode": rc, "pid": self.proc.pid,
                "probe": self.probe, "tail": self.lines[-6:]}

    def kill(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.kill()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                pass

    # ── 便捷 ────────────────────────────────────────────────────────────
    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def get(self, path: str, timeout: int = 120) -> dict:
        return U.get_json(self.base + path, timeout=timeout)

    def listeners(self) -> dict:
        """端口占用者（用 lsof，沙箱里 ps 被禁）。"""
        r = U.run(["lsof", "-nP", f"-iTCP:{self.port}", "-sTCP:LISTEN"], timeout=20)
        return {"cmd": " ".join(r["cmd"]), "returncode": r["returncode"],
                "stdout": r["stdout"].strip(), "stderr": r["stderr"].strip()}


class IsoWeb:
    """隔离前端（vite dev，第二实例）。

    三处隔离，缺一不可：
      · `API_PROXY_TARGET` → 隔离后端 8899（否则页面读的是生产 8787，证据无效）；
      · `VITE_CACHE_DIR`   → `09/isolated/vite-cache`（不与生产 dev 抢同一份预构建缓存）；
      · 端口 3101（3100 上有来源不明的遗留 vite 进程，不动它）。
    """

    def __init__(self, port: int = U.ISO_WEB_PORT, api_port: int = U.ISO_API_PORT):
        self.port = port
        self.api_port = api_port
        self.cache = U.ISO / "vite-cache"
        self.proc: subprocess.Popen | None = None
        self.lines: list[str] = []
        self._t: threading.Thread | None = None

    def start(self, timeout_s: int = 120) -> dict:
        self.cache.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ)
        env.update({
            "API_PROXY_TARGET": f"http://127.0.0.1:{self.api_port}",
            "VITE_CACHE_DIR": str(self.cache.resolve()),
            "DISABLE_HMR": "true",
            # 依赖树的既有缺陷（**不是本单引入**）：`node_modules/@tailwindcss/oxide` 是指向
            # `node_modules/.codex_new_packages/@tailwindcss/oxide` 的软链，而原生绑定
            # `@tailwindcss/oxide-darwin-arm64` 只存在于 `.codex_new_packages/@tailwindcss/` 下，
            # 不在 `node_modules/@tailwindcss/` 里 ⇒ 按标准 node_modules 链解析必然 MODULE_NOT_FOUND，
            # vite 直接起不来。这里用 **环境变量兜底**（NODE_PATH 指到 `.codex_new_packages`），
            # 不新建软链、不改 package.json、不动任何文件。
            "NODE_PATH": str((U.WEB / "node_modules" / ".codex_new_packages").resolve()),
        })
        t0 = time.time()
        self.proc = subprocess.Popen(
            [U.NODE, str(U.WEB / "node_modules" / "vite" / "bin" / "vite.js"),
             "--port", str(self.port), "--host", "127.0.0.1", "--strictPort"],
            cwd=str(U.WEB), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        self._t = threading.Thread(target=self._reader, daemon=True)
        self._t.start()
        url = f"http://127.0.0.1:{self.port}"
        w = U.wait_http(url, timeout_s)
        return {"ok": bool(w.get("ok")), "pid": self.proc.pid, "port": self.port,
                "url": url, "api_proxy_target": env["API_PROXY_TARGET"],
                "vite_cache_dir": env["VITE_CACHE_DIR"],
                "elapsed_s": round(time.time() - t0, 2), "http": w,
                "tail": self.lines[-6:]}

    def _reader(self) -> None:
        assert self.proc and self.proc.stdout
        for line in self.proc.stdout:
            self.lines.append(line.rstrip("\n"))

    def stop(self, timeout_s: int = 20) -> dict:
        if not self.proc:
            return {"stopped": False, "reason": "未启动"}
        if self.proc.poll() is None:
            self.proc.terminate()
        try:
            rc = self.proc.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            rc = self.proc.wait(timeout=10)
        if self._t:
            self._t.join(timeout=5)
        return {"stopped": True, "returncode": rc, "pid": self.proc.pid,
                "tail": self.lines[-6:]}

    def listeners(self) -> dict:
        r = U.run(["lsof", "-nP", f"-iTCP:{self.port}", "-sTCP:LISTEN"], timeout=20)
        return {"returncode": r["returncode"], "stdout": r["stdout"].strip()}
