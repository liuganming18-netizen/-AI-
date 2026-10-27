# -*- coding: utf-8 -*-
"""AUTO-LOOP-01 · 06 常驻轮询时序证据（只启动进程 + 读状态文件）

验证目标：`run_forever` 在**真实挂钟**下确实按 poll_interval 在跑（cycle_count 单调增长、
状态文件被持续重写、进程 PID 稳定），而不是靠回放时钟推进。
同时如实记录：真实挂钟下隔离库没有新 bar ⇒ 不产生信号、不成交 ⇒ **不宣称自动成交**。

本脚本不调用任何交易函数，只 subprocess 启动入口进程并读 JSON 状态文件。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE.parent
ROOT = OUT.parents[1]
PY = sys.executable


def main() -> int:
    sym = "BBUSDT"
    iso = OUT / "iso" / sym
    status_p = iso / "locks" / "paper_runtime_3a_status.json"
    seconds = 45
    cmd = [PY, str(HERE / "02_runtime_launcher.py"), "--mode", "forever",
           "--symbol", sym, "--seconds", str(seconds), "--interval", "1",
           "--tag", "poll_probe"]
    log = OUT / "logs" / "poll_probe.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    # 清掉上一次运行残留的状态文件：否则首个采样会读到**别的 PID** 的旧快照，
    # 把「换了进程」误判成「cycle 回退」。
    try:
        status_p.unlink()
    except FileNotFoundError:
        pass
    fh = open(log, "w", encoding="utf-8")
    proc = subprocess.Popen(cmd, stdout=fh, stderr=subprocess.STDOUT,
                            cwd=str(ROOT), env=dict(os.environ))
    samples = []
    t0 = time.time()
    while time.time() - t0 < seconds + 15:
        try:
            d = json.loads(status_p.read_text(encoding="utf-8"))
        except Exception:
            d = {}
        ident = d.get("runtime_identity", {})
        if ident.get("pid") == proc.pid:      # 只认本次启动的进程
            samples.append({
                "t_sec": round(time.time() - t0, 1),
                "file_mtime": round(status_p.stat().st_mtime, 3) if status_p.exists() else None,
                "pid": ident.get("pid"),
                "cycle_count": ident.get("cycle_count"),
                "status": ident.get("status"),
                "overall_health": ident.get("overall_health"),
                "open_positions": (d.get("execution_daemon") or {}).get("open_positions"),
                "latest_15m_close": (d.get("market_data") or {}).get("latest_15m_close_time"),
            })
        if proc.poll() is not None:
            break
        time.sleep(5)
    rc = proc.wait(timeout=60)
    fh.close()

    cycles = [s["cycle_count"] for s in samples if isinstance(s["cycle_count"], int)]
    pids = {s["pid"] for s in samples if s["pid"]}
    mtimes = [s["file_mtime"] for s in samples if s["file_mtime"]]
    rep = {
        "clock_source": "run_forever 真实挂钟轮询（interval=1s）",
        "exit_code": rc,
        "wall_elapsed_sec": round(time.time() - t0, 1),
        "samples": samples,
        "distinct_pid": sorted(pids),
        "cycle_monotonic": all(b >= a for a, b in zip(cycles, cycles[1:])) if len(cycles) > 1 else None,
        "cycle_first": cycles[0] if cycles else None,
        "cycle_last": cycles[-1] if cycles else None,
        "status_file_rewritten_n_times": len({m for m in mtimes}),
        "verdict": ("轮询实际执行：cycle_count 单调增长且状态文件持续重写"
                    if cycles and cycles[-1] > cycles[0] and len({m for m in mtimes}) >= 3
                    else "轮询未能证明执行"),
        "not_claimed": "真实挂钟窗口内隔离库无新 bar ⇒ 无信号、无成交；"
                       "本项只证明轮询在跑，不证明真实挂钟自动成交",
    }
    (OUT / "out" / "poll_samples.json").write_text(
        json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
