# -*- coding: utf-8 -*-
"""AUTO-LOOP-01 · 02 常驻进程启动器（唯一时钟来源）

本脚本是**进程壳**，只做三件事：
  1. 注入隔离环境变量（行情库/账本/live 库/锁/闸门/参数视图全部指向 iso 目录）；
  2. 构造并启动 `UnifiedRuntime`（统一运行入口），由它自己的 `run_replay` /
     `run_forever` 驱动时钟与全部交易环节；
  3. 把入口返回的逐轮摘要与进程身份落盘。

**禁止事项（本脚本刻意不 import 也不调用）**：
  · 不调用 `paper_engine.open_position / advance_pending / close_at_price /
    advance_position_trailing / check_position_exit_on_bar / accrue_funding`；
  · 不写任何 `paper_*` 表、不改账本、不造成交；
  · 回放模式下时间只由 `UnifiedRuntime.run_replay` 的 `step_ms` 推进
    （`step_once(now_ms=..., origin="replay")`），外部不逐步喂时钟。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE.parent
ROOT = OUT.parents[1]


def iso_env(sym: str, iso_root: Path) -> dict:
    d = iso_root / sym
    if not d.exists():
        raise SystemExit(f"iso dir missing: {d}（先跑 00_prepare_iso.py）")
    env = {
        "MARKET_DB": str(d / "market.sqlite3"),
        "PAPER_LEDGER_DIR": str(d / "paper"),
        "PAPER_LOCK_DIR": str(d / "locks"),
        "LIVE_DIR": str(d / "live"),
        "LIVE_DB": str(d / "live" / "live_signals.sqlite3"),
        "PAPER_GATE_FILE": str(d / "gate.json"),
        "PAPER_LEDGER_ALLOW_INIT": "1",
    }
    # 参数视图也复制进隔离目录（只读复制，逐字节一致），不读生产视图
    view_src = (ROOT / "数据" / "研究输入" / "最新唐奇安威科夫接通与收尾_2026-10-11"
                / "5000U收尾与回测条件终验6" / "10_研究参数生效视图_v2_2_user5000_shared.json")
    view_dst = d / "params_view.json"
    if view_src.exists() and not view_dst.exists():
        shutil.copyfile(view_src, view_dst)
    if view_dst.exists():
        env["EFFECTIVE_VIEW"] = str(view_dst)
    return env


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", required=True, choices=["replay", "forever"])
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--iso", default=str(OUT / "iso"))
    ap.add_argument("--start", help="ISO8601 UTC，如 2026-10-10T00:00:00Z")
    ap.add_argument("--end", help="ISO8601 UTC")
    ap.add_argument("--step-min", type=int, default=15)
    ap.add_argument("--interval", type=float, default=1.0, help="forever 轮询间隔秒")
    ap.add_argument("--seconds", type=float, default=60.0, help="forever 模式运行时长上限")
    ap.add_argument("--tag", default="run")
    ap.add_argument("--arm", default="3a")
    ap.add_argument("--epoch-floor", help=("隔离回放的**启用下限**（ISO8601 UTC）。"
                                          "生产 epoch 恒为 2026-10-14T01:00Z 且不允许下调；"
                                          "历史回放要跑 10-14 之前的真实信号，只能在隔离进程内"
                                          "把下限前移到回放起点。此覆盖仅在本进程有效，"
                                          "不写库、不影响任何生产实例，且必须在证据里披露。"))
    args = ap.parse_args()

    env = iso_env(args.symbol, Path(args.iso))
    os.environ.update(env)
    sys.path.insert(0, str(ROOT / "框架" / "backend"))
    sys.path.insert(0, str(ROOT))

    import unified_runtime as U  # noqa: E402  （必须在 env 注入之后）
    import live_store as L      # noqa: E402

    def ms(s: str) -> int:
        import datetime as dt
        return int(dt.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp() * 1000)

    # ── 隔离回放启用下限（**只在本进程内**，不写库、不改生产文件）──────────────────
    # 生产 `live_store.get_paper_epoch_ms()` 是 fail-closed 的：恒取
    # `max(库内值, 2026-10-14T01:00Z)`，不允许下调。历史回放要跑 10-14 之前的真实信号，
    # 只能把「本隔离进程的启用下限」前移。披露口径见 README「判定边界」。
    epoch_override = None
    if args.epoch_floor:
        if args.mode != "replay":
            raise SystemExit("--epoch-floor 只允许在 replay 模式使用")
        epoch_override = ms(args.epoch_floor)
        L.get_paper_epoch_ms = lambda: int(epoch_override)  # noqa: E731

    rt = U.UnifiedRuntime(arm=args.arm, symbols=[args.symbol.upper()],
                          poll_interval_sec=args.interval)
    ident = {
        "tag": args.tag, "mode": args.mode, "symbol": args.symbol.upper(),
        "pid": os.getpid(), "arm": args.arm,
        "started_at_ms": rt.started_at_ms,
        "started_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "env": {k: v for k, v in env.items()},
        "lock_file": str(rt.lock_file_path),
    }
    if args.mode == "replay":
        if not (args.start and args.end):
            raise SystemExit("replay 模式必须给 --start/--end")
        ident.update({"replay_start_ms": ms(args.start), "replay_end_ms": ms(args.end),
                      "step_ms": args.step_min * 60_000,
                      "clock_source": "UnifiedRuntime.run_replay(step_ms) —— 回放时钟，非真实挂钟",
                      "production_epoch_ms": 1_791_939_600_000,
                      "epoch_floor_override_ms": epoch_override,
                      "epoch_floor_override_scope": ("仅本隔离进程内 monkeypatch "
                                                     "live_store.get_paper_epoch_ms；"
                                                     "不写库、不改生产文件、不影响生产实例")})
        t0 = time.time()
        hist = rt.run_replay(ms(args.start), ms(args.end), args.step_min * 60_000)
        ident["elapsed_sec"] = round(time.time() - t0, 2)
        ident["cycles"] = len(hist)
        ident["totals"] = {
            "new_signals": sum(h["new_signals"] for h in hist),
            "routed": sum(h["routed"] for h in hist),
            "waiting_gate": sum(h["waiting_gate"] for h in hist),
            "matched": sum(h["matched"] for h in hist),
            "trailing_updates": sum(h["trailing_updates"] for h in hist),
            "exits": sum(h["exits"] for h in hist),
        }
        ident["errors"] = sorted({e for h in hist for e in h.get("errors", [])})
        (OUT / "out" / f"replay_{args.tag}.json").write_text(
            json.dumps({"identity": ident, "history": hist}, ensure_ascii=False, indent=2),
            encoding="utf-8")
    else:
        ident.update({"clock_source": "run_forever 真实挂钟轮询",
                      "interval_sec": args.interval, "max_seconds": args.seconds})
        timer = threading.Timer(max(1.0, args.seconds), rt.stop)
        timer.daemon = True
        timer.start()
        t0 = time.time()
        try:
            rt.run_forever()
        finally:
            ident["elapsed_sec"] = round(time.time() - t0, 2)
            ident["cycle_count"] = rt.cycle_count
            ident["stats"] = dict(rt.stats)
            (OUT / "out" / f"forever_{args.tag}.json").write_text(
                json.dumps({"identity": ident}, ensure_ascii=False, indent=2),
                encoding="utf-8")
    print(json.dumps(ident, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
