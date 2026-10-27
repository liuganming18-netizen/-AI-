"""尾段拉取（架构重构第 3 期）。

从 `adapter._tail_fetch()` **原样搬出**，改成「网关对象由调用方传入」的纯函数，
这样同一个实现可以同时服务：

* web（`WEB_READONLY=0` 的旧回退路径）；
* worker（第 3 期起，正常路径：拉取 + 写库都在后台进程完成）。

为什么 web 不能继续拉
----------------------
网关不是纯读：它要写 `api_calls` / `api_leases` / `api_scope_state` 三张表，
用于共享缓存、限频与冷却。web 连接改成只读后，这一步会直接报
`attempt to write a readonly database`（实测）。所以「拉取」本身也必须离开请求路径，
否则只读只是半截——读请求仍会抢写锁。
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import Any, Optional

from data_hub.store import INTERVAL_MS, now_ms

TAIL_LIMIT = {"binance": 1000, "okx": 300}
# 欧易周期映射：12h 必须用官方 UTC 口径 "12Hutc"（"12H" 是 UTC+8 锚点，且不在网关白名单）。
OKX_BAR = {"5m": "5m", "15m": "15m", "30m": "30m",
           "1h": "1H", "4h": "4H", "12h": "12Hutc", "1d": "1Dutc"}


def fetch_tail(gw, exchange: str, symbol: str, tf: str,
               fetched_at: Optional[int] = None) -> tuple[list[dict[str, Any]], str]:
    """经统一网关拉取**有限近期原生尾段**；返回 `(bars, source_url)`。

    只做网络与解析，不写库。`is_closed` 由 `close_time < fetched_at` 判定。
    """
    fetched_at = now_ms() if fetched_at is None else fetched_at
    if exchange == "binance":
        r = gw.get("binance.candles",
                   {"symbol": symbol, "interval": tf, "limit": TAIL_LIMIT["binance"]})
        out = [{"open_time": int(k[0]), "close_time": int(k[6]),
                "open": k[1], "high": k[2], "low": k[3], "close": k[4],
                "volume": k[5], "quote_volume": k[7], "trade_count": int(k[8]),
                "taker_buy_volume": k[9], "taker_buy_quote_volume": k[10],
                "is_closed": int(k[6]) < fetched_at, "available_at": fetched_at}
               for k in (r["数据"] or [])]
        src = f"https://fapi.binance.com/fapi/v1/klines?symbol={symbol}&interval={tf}"
    else:
        r = gw.get("okx.candles_recent",
                   {"instId": symbol, "bar": OKX_BAR.get(tf, tf),
                    "limit": TAIL_LIMIT["okx"]})
        iv = INTERVAL_MS[tf]
        out = [{"open_time": int(c[0]), "close_time": int(c[0]) + iv - 1,
                "open": c[1], "high": c[2], "low": c[3], "close": c[4],
                # 欧易无 taker / trade_count 字段：占位并另行标注，不冒充真值
                "volume": c[6], "quote_volume": c[7], "trade_count": 0,
                "taker_buy_volume": "", "taker_buy_quote_volume": "",
                "is_closed": c[8] == "1", "available_at": fetched_at}
               for c in (r["数据"].get("data") or [])]
        src = "https://www.okx.com/api/v5/market/candles"
    out.sort(key=lambda b: b["open_time"])
    return out, src


class WriteStore:
    """给网关用的**最小可写存储**：只要 `clock()` 与 `transaction()`。

    刻意不继承 `MarketStore`：后者的构造会执行 schema 脚本与版本表写入，
    worker 只需要读写调用记录，不需要动 schema。
    """

    def __init__(self, path: str):
        self.path = str(path)

    def clock(self) -> int:
        return now_ms()

    def connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path, timeout=10)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA busy_timeout=10000")
        return con

    @contextmanager
    def transaction(self):
        con = self.connect()
        try:
            con.execute("BEGIN IMMEDIATE")
            yield con
            con.commit()
        except BaseException:
            con.rollback()
            raise
        finally:
            con.close()


__all__ = ["fetch_tail", "WriteStore", "TAIL_LIMIT", "OKX_BAR"]
