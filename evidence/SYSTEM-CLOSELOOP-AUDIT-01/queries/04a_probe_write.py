#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""04a_probe_write.py —— 单次探针：起隔离实例 → 打一次写请求 → 打印原始响应 → 收工

用途：`04_lifecycle_isolated.py` 里 open(带 atr) 返回 500，但响应体被吞成
`{"错误":"写入失败","类型":...}`。本探针把原始响应与异常类型打出来，
用于定位到底是引擎哪一步炸了（不改任何被审代码）。

用法：python 04a_probe_write.py
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJ = HERE.parent.parent.parent
BACKEND = PROJ / "框架" / "backend"
ISO = Path("/tmp/wx_audit_iso")
PORT = 8899
BASE = f"http://127.0.0.1:{PORT}"
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def http(path, method="GET", body=None, timeout=180):
    data = json.dumps(body).encode() if body is not None else None
    hdr = {"Content-Type": "application/json"} if body is not None else {}
    req = urllib.request.Request(BASE + path, data=data, headers=hdr, method=method)
    try:
        with OPENER.open(req, timeout=timeout) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:                                    # noqa: BLE001
        return -1, f"{type(e).__name__}: {e}"


def main():
    (ISO / "paper").mkdir(parents=True, exist_ok=True)
    (ISO / "live").mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env.update({"PORT": str(PORT),
                "PAPER_LEDGER_DIR": str(ISO / "paper"),
                "PAPER_LEDGER_ALLOW_INIT": "1",
                "LIVE_DIR": str(ISO / "live"),
                "LIVE_DB": str(ISO / "live" / "live_signals.sqlite3"),
                "PYTHONUNBUFFERED": "1"})
    code = ("import socket, adapter\n"
            "srv = adapter._loopback_server('127.0.0.1', socket.AF_INET)\n"
            "print('[iso] up', flush=True)\n"
            "srv.serve_forever()\n")
    log = open(ISO / "probe.log", "wb")
    p = subprocess.Popen([sys.executable, "-c", code], cwd=str(BACKEND),
                         env=env, stdout=log, stderr=subprocess.STDOUT)
    try:
        t0 = time.time()
        while time.time() - t0 < 90:
            st, _ = http("/api/v1/health", timeout=5)
            if st == 200:
                break
            time.sleep(0.5)
        print("health ok")

        probes = [
            ("带atr+注入19:00", {"arm": "3a", "action": "open", "symbol": "BTCUSDT",
                              "atr": 500, "requested_at": 1792666800000,
                              "signal_uid": "probe_atr_A"}),
            ("带atr+注入19:00 第二次", {"arm": "3a", "action": "open", "symbol": "BTCUSDT",
                                    "atr": 500, "requested_at": 1792666800000,
                                    "signal_uid": "probe_atr_B"}),
            ("带stop_price+注入19:00", {"arm": "3a", "action": "open", "symbol": "BTCUSDT",
                                     "stop_price": 80470.0, "requested_at": 1792666800000,
                                     "signal_uid": "probe_stop_C"}),
            ("无数据标的", {"arm": "3a", "action": "open", "symbol": "NOSUCHPAIRUSDT",
                        "atr": 500, "requested_at": 1792666800000,
                        "signal_uid": "probe_nodata_D"}),
        ]
        for name, body in probes:
            st, raw = http("/api/v1/paper/orders", "POST", body)
            print("=" * 20, name, "HTTP", st)
            try:
                b = json.loads(raw)
                print("  quality:", b.get("quality"))
                print("  missing_reason:", b.get("missing_reason"))
                print("  错误:", b.get("错误"), "| 类型:", b.get("类型"))
                print("  items:", json.dumps(b.get("items"), ensure_ascii=False)[:600])
            except ValueError:
                print("  原始:", raw[:600])
    finally:
        p.terminate()
        try:
            p.wait(timeout=10)
        except Exception:                                     # noqa: BLE001
            p.kill()
        log.close()
        print("--- server log ---")
        print((ISO / "probe.log").read_text(encoding="utf-8", errors="replace")[-2500:])


if __name__ == "__main__":
    main()
