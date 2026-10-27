#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""模拟盘/纸面独立执行守护进程（SYSTEM-CLOSELOOP-04）。

用法：
    python workers/paper_execution_worker.py --arm 3a --interval 5.0
    python workers/paper_execution_worker.py --arm 3a --once
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import paper_loop as PL


def main():
    parser = argparse.ArgumentParser(description="Paper execution worker daemon")
    parser.add_argument("--arm", default="3a", choices=["3a", "3b", "3c", "3d"], help="Paper account arm")
    parser.add_argument("--interval", type=float, default=5.0, help="Polling interval in seconds")
    parser.add_argument("--once", action="store_true", help="Run a single cycle and exit")
    args = parser.parse_args()

    daemon = PL.PaperExecutionDaemon(arm=args.arm, poll_interval_sec=args.interval)

    if args.once:
        res = daemon.execute_cycle()
        print(f"[*] Cycle finished: {res}")
        return 0

    print(f"[*] Starting Paper Execution Worker for arm {args.arm} (interval={args.interval}s)...")
    try:
        daemon.run_forever()
    except KeyboardInterrupt:
        print("[*] Worker stopped by user.")
        daemon.stop()
    except Exception as exc:
        print(f"[!] Worker failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
