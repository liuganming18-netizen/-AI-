# -*- coding: utf-8 -*-
"""回测运行日志 · **单一实现**（CODE-REVIEW-02，2026-10-21）。

背景（CODE-REVIEW-01 症状 B）：`框架/backend/logs/backend.log` 85 行全是
`[adapter]` 预热，0 条 run/trade 级日志 ⇒ 「跑过什么」只能靠翻目录。本模块
把「一次回测发生过」变成**一行可 grep 的 JSON 日志**。

纪律
────
· **单一实现**（R5）：运行级日志只走 `run_log()`；逐笔日志只走
  `log_trade()`。runner / api / worker / 批量脚本共用本模块，不各自造轮子。
· **双写**：stdout（落进 `logs/backend.log` / `logs/worker.log`，随进程重定向）
  + 追加 `logs/backtest_runs.log`（专门给「回测留痕」用的文件，行追加+flush）。
· **逐笔默认关**：`BT_TRADE_LOG=1` 才写逐笔（一个 run 动辄几千笔权益点，
  逐笔全写会把文件撑爆）；运行级一行永久记录，不受开关影响。
· **只记事实**：每个字段都取自调用方传入的真实值；本模块不补默认值、不推断。
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

LOG_DIR = Path(__file__).resolve().parent / "logs"
# 日志文件路径可用 BT_RUN_LOG 覆盖（测试隔离用：单测跑 execute_run 时把
# 运行日志写 /tmp，不往生产日志里灌测试噪声；worker/web 不设该变量，走默认）。
RUN_LOG_PATH = Path(os.environ.get("BT_RUN_LOG", str(LOG_DIR / "backtest_runs.log")))
_WRITE_LOCK = threading.Lock()


def _proc_name() -> str:
    """日志里的进程角色（web / worker / script），只按启动命令判，不猜。"""
    argv = " ".join(sys.argv)
    if "backtest_worker" in argv:
        return "worker"
    if "adapter" in argv:
        return "web"
    return "script"


def run_log(event: str, **fields: Any) -> None:
    """运行级日志：单行 JSON，stdout + `logs/backtest_runs.log` 双写。"""
    record: Dict[str, Any] = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "proc": _proc_name(),
        "event": f"backtest.{event}",
    }
    record.update(fields)
    line = json.dumps(record, ensure_ascii=False, default=str)
    try:
        print(line, flush=True)
    except Exception:                                         # noqa: BLE001
        pass
    try:
        RUN_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _WRITE_LOCK:
            with RUN_LOG_PATH.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
    except Exception:                                         # noqa: BLE001
        # 日志写不进去**不能**影响回测本身；stdout 那条仍在。
        pass


def trade_log_enabled() -> bool:
    """逐笔日志开关（默认关）。置 `BT_TRADE_LOG=1` 开启。"""
    return os.environ.get("BT_TRADE_LOG", "0") == "1"


def log_trade(run_id: str, idx: int, trade: Dict[str, Any]) -> None:
    """逐笔日志（**默认关**，见 `trade_log_enabled`）。

    `trade` 为调用方给的真实字段；缺的字段不进日志（不补 0/不补 null 占位）。
    """
    if not trade_log_enabled():
        return
    keep = {k: v for k, v in (trade or {}).items()
            if k in ("trade_id", "symbol", "direction", "entry_time_ms",
                     "exit_time_ms", "entry_price", "exit_price", "qty",
                     "net_pnl", "exit_reason")}
    run_log("trade", run_id=run_id, idx=idx, **keep)


def count_field(run_id: str, field: str, n: int) -> None:
    """计数型日志（如「图表兜底重建跳过 N 行坏行」）。真实计数才记。"""
    if n:
        run_log("counter", run_id=run_id, field=field, n=n)


__all__ = ["run_log", "trade_log_enabled", "log_trade", "count_field",
           "RUN_LOG_PATH", "LOG_DIR"]
