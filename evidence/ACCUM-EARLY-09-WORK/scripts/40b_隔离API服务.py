# -*- coding: utf-8 -*-
"""40b｜隔离 API 服务（本单用；不碰生产 8787）。

为什么不用 `adapter.main()`：`main()` 还会跑 `_prewarm()`（预热会写本机缓存）和
`backtest.startup()`（把遗留回测运行标 interrupted —— 那是**生产写**）。
本 runner 只做一件事：用**真实的 `adapter.Handler` + 真实的 `live_api` 代码**在指定端口
监听，路由与响应体和生产完全同一条代码路径，但不触发启动期副作用。

环境（由调用方注入）：`LIVE_DB` / `LIVE_DIR` / `PAPER_LEDGER_DIR` / `PAPER_LOCK_DIR` / `PORT`。
"""
from __future__ import annotations

import json
import os
import signal
import sys
from pathlib import Path

PROJECT = Path("/Users/boos/Desktop/威科夫因子共振趋势")
BACKEND = PROJECT / "框架" / "backend"
for p in (str(BACKEND), str(PROJECT / "框架"), str(PROJECT),
          str(Path(__file__).resolve().parent)):
    if p not in sys.path:
        sys.path.insert(0, p)

import probe_sql  # noqa: E402

probe_sql.install()             # 必须在 import 业务模块之前：统计本进程对每个库的写语句

import adapter  # noqa: E402


def main() -> int:
    port = int(os.environ.get("PORT", "8899"))
    info = {
        "pid": os.getpid(),
        "port": port,
        "LIVE_DB": str(Path(__import__("live_store").LIVE_DB).resolve()),
        "PAPER_LEDGER_DIR": str(Path(__import__("live_store").LEDGER_DIR).resolve()),
        "adapter_file": adapter.__file__,
        "live_api_file": sys.modules.get("live_api").__file__
        if sys.modules.get("live_api") else None,
    }
    srv = adapter._BoundedThreadingHTTPServer(("127.0.0.1", port), adapter.Handler)
    print("@@READY@@" + json.dumps(info, ensure_ascii=False), flush=True)

    def _bye(signum, _frame):
        # 收到 SIGTERM/SIGINT 时把 SQL 级写探针报告打出来再退出（否则进程被杀就丢证据）
        print("@@PROBE@@" + json.dumps(
            {"signal": int(signum), "probe": probe_sql.report()}, ensure_ascii=False), flush=True)
        srv.server_close()
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, _bye)
    signal.signal(signal.SIGINT, _bye)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
