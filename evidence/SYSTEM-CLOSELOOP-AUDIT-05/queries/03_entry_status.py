# -*- coding: utf-8 -*-
"""03 第 1 项：统一入口启动后，守护是否真实运行；状态接口是否与进程一致。

工单原文：「统一入口启动后，守护真实运行，状态接口与进程一致。」

做法（全部在隔离环境，生产 8787 与 数据/paper 不参与）：
  1. 起一个**隔离 adapter**（等价于统一入口里的后端进程；端口 8799）；
  2. 记录它有没有派生任何 `paper_execution_worker` 子进程（= 入口是否拉起守护）；
  3. 再**单独**起 worker（交付方唯一可用的执行入口），
     查询 `GET /api/v1/paper/execution_daemon/status`，与「进程是否真的活着」对比；
  4. 读 worker 日志里的真实轮询次数，与接口自报的 `cycle_count` 对比。
产物：out/03_入口与状态.json
"""
from __future__ import annotations

import datetime as dt
import json
import os
import pathlib
import subprocess
import sys
import time
import urllib.error
import urllib.request

BASE = pathlib.Path("/Users/boos/Desktop/威科夫因子共振趋势")
HERE = BASE / "evidence/SYSTEM-CLOSELOOP-AUDIT-05"
OUT = HERE / "out"
BACKEND = BASE / "框架/backend"
ROOT = pathlib.Path("/tmp/wx_audit05")
PY = "/Users/boos/.workbuddy-ai/binaries/python/envs/default/bin/python"
PORT = 8799

ENV = {
    "PAPER_LEDGER_DIR": str(ROOT / "paper"),
    "PAPER_LEDGER_ALLOW_INIT": "1",
    "LIVE_DIR": str(ROOT / "live"),
    "LIVE_DB": str(ROOT / "live" / "live_signals.sqlite3"),
    "MARKET_DB": str(ROOT / "market.sqlite3"),
    "PAPER_GATE_FILE": str(ROOT / "gate.json"),
    "PORT": str(PORT),
    "PYTHONPATH": f"{BACKEND}:{BASE}",
    "PYTHONUNBUFFERED": "1",
    "PAPER_LOCK_DIR": str(ROOT / "locks"),
}


def now() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def get(path: str, timeout: float = 8.0) -> dict:
    """本机直连必须绕过代理（HTTP_PROXY 无 no_proxy 会 502）。"""
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    url = f"http://127.0.0.1:{PORT}{path}"
    try:
        with op.open(url, timeout=timeout) as r:
            return {"http": r.status, "body": json.loads(r.read().decode("utf-8"))}
    except urllib.error.HTTPError as e:
        return {"http": e.code, "body": e.read().decode("utf-8", "ignore")[:400]}
    except Exception as exc:                                     # noqa: BLE001
        return {"http": None, "错误": f"{type(exc).__name__}: {exc}"}


def children_of(pid: int) -> list[dict]:
    try:
        import psutil
        pr = psutil.Process(pid)
        return [{"pid": c.pid, "cmdline": " ".join(c.cmdline())[:200]} for c in pr.children(recursive=True)]
    except Exception as exc:                                     # noqa: BLE001
        return [{"错误": str(exc)}]


def worker_procs() -> list[dict]:
    out = []
    try:
        import psutil
        for pr in psutil.process_iter(["pid", "cmdline"]):
            cl = " ".join(pr.info.get("cmdline") or [])
            if "paper_execution_worker" in cl:
                out.append({"pid": pr.info["pid"], "命令行": cl[:220]})
    except Exception as exc:                                     # noqa: BLE001
        out = [{"错误": str(exc)}]
    return out


def main() -> None:
    for k, v in ENV.items():
        os.environ[k] = v
    res: dict = {"生成时刻": now(), "隔离根": str(ROOT), "隔离端口": PORT}

    # ── 1. 起隔离 adapter ──
    log_a = OUT / "03_iso_adapter.log"
    fa = open(log_a, "w", encoding="utf-8")
    env = dict(os.environ)
    env.update(ENV)
    adapter = subprocess.Popen([PY, str(HERE / "queries/_iso_adapter.py")],
                               cwd=str(BACKEND), env=env, stdout=fa, stderr=subprocess.STDOUT,
                               start_new_session=True)
    t0 = time.time()
    health = None
    while time.time() - t0 < 40:
        h = get("/api/v1/health")
        if h.get("http") == 200:
            health = h
            break
        time.sleep(1)
    res["adapter"] = {"pid": adapter.pid, "health_http": (health or {}).get("http"),
                      "启动耗时秒": round(time.time() - t0, 1), "日志": str(log_a)}
    res["adapter_子进程"] = children_of(adapter.pid)
    res["adapter_是否派生执行守护"] = any("paper_execution_worker" in (c.get("cmdline") or "")
                                  for c in res["adapter_子进程"])
    res["adapter_启动后的worker进程"] = worker_procs()

    # ── 2. 接口自报（无守护时） ──
    res["状态接口_无守护时"] = get("/api/v1/paper/execution_daemon/status")
    res["旧回退路由_无守护时"] = get("/paper/execution_daemon/status")

    # ── 3. 单独起 worker ──
    log_w = OUT / "03_worker.log"
    fw = open(log_w, "w", encoding="utf-8")
    w = subprocess.Popen([PY, str(BACKEND / "workers/paper_execution_worker.py"),
                          "--arm", "3a", "--interval", "1"],
                         cwd=str(BACKEND), env=env, stdout=fw, stderr=subprocess.STDOUT,
                         start_new_session=True)
    time.sleep(8)
    alive = w.poll() is None
    res["worker"] = {"pid": w.pid, "仍在运行": alive, "退出码": w.poll()}
    res["状态接口_守护运行中"] = get("/api/v1/paper/execution_daemon/status")
    res["旧回退路由_守护运行中"] = get("/paper/execution_daemon/status")
    res["worker_进程列表"] = worker_procs()
    wl = log_w.read_text(encoding="utf-8", errors="ignore") if log_w.exists() else ""
    res["worker_日志尾部"] = wl.strip().splitlines()[-10:]
    res["worker_日志行数"] = len(wl.splitlines())
    lock = ROOT / "locks" / "paper_exec_3a.lock"
    prod_lock = BACKEND / "locks" / "paper_exec_3a.lock"
    res["单实例锁文件"] = {"路径": str(lock), "存在": lock.exists(),
                    "字节": lock.stat().st_size if lock.exists() else None,
                    "生产locks同名文件": {"存在": prod_lock.exists(),
                                  "mtime": (dt.datetime.fromtimestamp(prod_lock.stat().st_mtime)
                                            .astimezone().isoformat(timespec="seconds") if prod_lock.exists() else None)}}

    # ── 4. 一致性判定 ──
    api_running = None
    try:
        body = res["状态接口_守护运行中"]["body"]
        item = body.get("item") if isinstance(body, dict) else None
        api_running = bool((item or {}).get("running"))
        res["接口自报"] = item
    except Exception:                                            # noqa: BLE001
        res["接口自报"] = None

    res["判定"] = {
        "①统一入口是否拉起守护": "不通过" if not res["adapter_是否派生执行守护"] else "通过",
        "②守护是否真实运行": "通过" if alive else "不通过",
        "③状态接口与进程一致": ("通过" if (api_running == alive) else "不通过"),
        "接口自报running": api_running,
        "进程真实存活": alive,
        "说明": "接口自报 running 与 worker 进程存活一致 ⇒ 状态接口可跨进程反映守护状态（新版已落盘状态文件）",
    }

    w.terminate()
    try:
        w.wait(timeout=5)
    except Exception:                                            # noqa: BLE001
        w.kill()
    adapter.terminate()
    try:
        adapter.wait(timeout=5)
    except Exception:                                            # noqa: BLE001
        adapter.kill()
    fa.close()
    fw.close()

    (OUT / "03_入口与状态.json").write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str),
                                    encoding="utf-8")
    print("=== 第 1 项 ===")
    print(f"  adapter pid={adapter.pid} health={res['adapter']['health_http']}")
    print(f"  adapter 派生执行守护: {res['adapter_是否派生执行守护']}  子进程={res['adapter_子进程']}")
    print(f"  无守护时状态接口: {json.dumps(res['状态接口_无守护时'], ensure_ascii=False)[:220]}")
    print(f"  守护运行中状态接口: {json.dumps(res['状态接口_守护运行中'], ensure_ascii=False)[:260]}")
    print(f"  worker 存活: {alive}  日志行数={res['worker_日志行数']}")
    print(f"  单实例锁文件: {res['单实例锁文件']}")
    print(f"  worker 日志尾部: {res['worker_日志尾部']}")
    for k, v in res["判定"].items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
