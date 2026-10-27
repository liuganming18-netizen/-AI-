#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SIGNAL-LIVE-CHECK-09 探针④：真实候选隔离入口（一键启动 / 停止 / 状态 / 清理）

给反重力直接用。**复用 07-R1 已经造好的 live / recovered 两份样例，不另造候选。**

为什么需要这一层包装
------------------
07-R1 已有 `probes/15_iso_api_launcher.py`（隔离 API 启动内核）与两份隔离库，
但「怎么起、起在哪、怎么确认起对了、怎么收尾」散在 README 里，复验方要自己拼。
本脚本把它压成四条命令，每条都可原样复制：

    python 40_iso_serve.py --mode live --start      # 起（端口 8799）
    python 40_iso_serve.py --mode live --status     # 查（进程 + 接口 + 候选）
    python 40_iso_serve.py --mode live --stop       # 停
    python 40_iso_serve.py --mode live --clean      # 清理（只删本入口产生的 PID 文件）

隔离边界（与 07-R1 完全一致，不新增、不放宽）
------------------------------------------
  · 数据：`evidence/SIGNAL-UI-LINK-07-R1/iso_live|iso_recovered/live_signals.sqlite3`
  · 启用时点：**只在该进程内存**改成模拟值 1790870400000（2026-10-01T16:00:00Z），
    磁盘文件一字节不动（`live_store.py` 的 sha 由 `20_version_check.py` 看守）
  · 端口：live=8799 / recovered=8798（避开生产 8787、前端 3000、重复入口 3100）
  · 账本与锁：各隔离目录下的 `paper/` 与 `locks/`
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve()
ROOT = HERE.parents[3]
BACKEND = ROOT / "框架/backend"
BASE = HERE.parents[1]
OUT = BASE / "out"
PREV = ROOT / "evidence/SIGNAL-UI-LINK-07-R1"
LAUNCHER = PREV / "probes/15_iso_api_launcher.py"

PY = "/Users/boos/.workbuddy-ai/binaries/python/envs/default/bin/python"
ISO_EPOCH = 1790870400000              # 2026-10-01T16:00:00Z（模拟启用时点）
PROD_EPOCH = 1791939600000             # 2026-10-14T01:00:00Z（生产真实值，不动）
CAND = "cand_ev_WX_EVENT_TREND_V0_1_binance_VELVETUSDT_1790308799999_1790953200000"

MODES = {
    "live": {"dir": "iso_live", "port": 8799},
    "recovered": {"dir": "iso_recovered", "port": 8798},
}

_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def pidfile(mode: str) -> Path:
    return OUT / f"40_iso_{mode}.pid"


def logfile(mode: str) -> Path:
    return OUT / f"40_iso_{mode}.log"


def get_json(url: str):
    with _opener.open(url, timeout=15) as r:
        return json.loads(r.read().decode("utf-8"))


def port_pid(port: int):
    try:
        r = subprocess.run(["lsof", "-nP", f"-i:{port}", "-sTCP:LISTEN", "-t"],
                           capture_output=True, text=True, timeout=10)
        out = r.stdout.strip().splitlines()
        return int(out[0]) if out else None
    except Exception:                                        # noqa: BLE001
        return None


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def do_start(mode: str) -> int:
    cfg = MODES[mode]
    iso = PREV / cfg["dir"]
    port = cfg["port"]
    db = iso / "live_signals.sqlite3"

    if not LAUNCHER.exists():
        print(f"[FAIL] 找不到隔离启动器：{LAUNCHER}")
        return 2
    if not db.exists():
        print(f"[FAIL] 找不到隔离库：{db}\n"
              f"       请先跑 evidence/SIGNAL-UI-LINK-07-R1/probes/40_two_paths.py --mode {mode}")
        return 2
    if port_pid(port):
        print(f"[SKIP] 端口 {port} 已被占用（pid={port_pid(port)}），未重复启动")
        return 0

    OUT.mkdir(parents=True, exist_ok=True)
    env = {
        **os.environ,
        "PORT": str(port),
        "ISO_PAPER_EPOCH_MS": str(ISO_EPOCH),
        "LIVE_DB": str(db),
        "PAPER_LEDGER_DIR": str(iso / "paper"),
        "PAPER_LOCK_DIR": str(iso / "locks"),
        "MARKET_DB": str(ROOT / "数据/市场数据/db/market_v2.sqlite3"),
        "HTTP_PROXY": "", "HTTPS_PROXY": "", "http_proxy": "", "https_proxy": "",
        "no_proxy": "*",
    }
    lf = open(logfile(mode), "w", encoding="utf-8")
    proc = subprocess.Popen(
        [PY, str(LAUNCHER)], cwd=str(BACKEND), env=env,
        stdout=lf, stderr=subprocess.STDOUT, start_new_session=True)
    pidfile(mode).write_text(str(proc.pid), encoding="utf-8")

    t0 = time.time()
    while time.time() - t0 < 180:
        try:
            get_json(f"http://127.0.0.1:{port}/api/v1/health")
            print(f"[OK] 隔离后端已就绪 mode={mode} 端口={port} pid={proc.pid} "
                  f"（{time.time() - t0:.1f}s）")
            print(f"     LIVE_DB={db}")
            print(f"     日志={logfile(mode)}")
            return 0
        except Exception:                                    # noqa: BLE001
            if not alive(proc.pid):
                print(f"[FAIL] 进程已退出，日志尾部：")
                print(logfile(mode).read_text(encoding="utf-8", errors="replace")[-1200:])
                return 1
            time.sleep(1.5)
    print(f"[FAIL] 180 秒内未就绪，日志={logfile(mode)}")
    return 1


def do_status(mode: str) -> int:
    cfg = MODES[mode]
    port = cfg["port"]
    pid = port_pid(port)
    print(f"mode={mode} 端口={port} pid={pid}")
    if not pid:
        print("[INFO] 未在运行")
        return 1
    sig = get_json(f"http://127.0.0.1:{port}/api/v1/signals?scope=current&limit=500")
    items = sig.get("items") or []
    q = sig.get("quality") or {}
    print(f"  quality.status={q.get('status')} missing_reason={sig.get('missing_reason')}")
    print(f"  effective_floor_ms={q.get('effective_floor_ms')} "
          f"（期望 {ISO_EPOCH}）")
    print(f"  total={sig.get('total')} ids={[i.get('signal_id') for i in items]}")
    for it in items:
        print(f"    {it.get('signal_id')}")
        print(f"    symbol={it.get('symbol')} direction={it.get('direction')} "
              f"confirmed_at={it.get('confirmed_at')} "
              f"executable_state={it.get('executable_state')} origin={it.get('origin')}")
    ok = (sig.get("total") == 1
          and items and items[0].get("signal_id") == CAND
          and q.get("effective_floor_ms") == ISO_EPOCH)
    print(f"  == {'与交付口径一致' if ok else '与交付口径不一致（见上）'} ==")
    return 0 if ok else 1


def do_stop(mode: str) -> int:
    cfg = MODES[mode]
    pid = port_pid(cfg["port"])
    pf = pidfile(mode)
    if pf.exists():
        try:
            p2 = int(pf.read_text(encoding="utf-8").strip())
            pid = pid or p2
        except ValueError:
            pass
    if not pid:
        print(f"[INFO] mode={mode} 未在运行，无需停止")
        pf.unlink(missing_ok=True)
        return 0
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError as exc:
        print(f"[WARN] 发送 SIGTERM 失败：{exc}")
    for _ in range(40):
        if not alive(pid):
            break
        time.sleep(0.5)
    else:
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
    pf.unlink(missing_ok=True)
    print(f"[OK] 已停止 mode={mode} pid={pid}")
    return 0


def do_clean(mode: str) -> int:
    """只清本入口产生的 PID / 日志文件，**不动隔离库**（那是证据）。"""
    cfg = MODES[mode]
    for p in (pidfile(mode), logfile(mode)):
        if p.exists():
            p.unlink()
            print(f"[OK] 删除 {p.name}")
    print(f"[INFO] 隔离库与账本**未删除**（属交付证据）："
          f"{PREV / cfg['dir']}")
    print("       若确需删除，请显式执行：")
    print(f"       rm -rf '{PREV / cfg['dir']}'")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", required=True, choices=sorted(MODES))
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--start", action="store_true")
    g.add_argument("--status", action="store_true")
    g.add_argument("--stop", action="store_true")
    g.add_argument("--clean", action="store_true")
    args = ap.parse_args()

    if args.start:
        return do_start(args.mode)
    if args.status:
        return do_status(args.mode)
    if args.stop:
        return do_stop(args.mode)
    return do_clean(args.mode)


if __name__ == "__main__":
    sys.exit(main())
