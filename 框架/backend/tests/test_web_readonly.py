"""web 只读与尾段后台补写（架构重构第 3 期）。

覆盖三件事：
1. 连接池给出的是**只读连接**（`query_only=1`，写入直接失败）；
2. 只读模式下 K 线读路径**不写库**，而是投递补写任务；
3. 回退开关 `WEB_READONLY=0` 仍可走旧的当场入库路径。

全部用临时库，不碰生产行情库。
"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

import adapter


class ReadOnlyConnectionTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Path(self._tmp.name) / "market.sqlite3"
        con = sqlite3.connect(self.db)
        con.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
        con.execute("INSERT INTO t(v) VALUES('x')")
        con.commit()
        con.close()
        self._old = adapter.DB_PATH
        adapter.DB_PATH = str(self.db)

    def tearDown(self) -> None:
        adapter.DB_PATH = self._old
        self._tmp.cleanup()

    def test_pool_connection_is_read_only(self) -> None:
        con = adapter._new_db_conn()
        try:
            self.assertEqual(con.execute("PRAGMA query_only").fetchone()[0], 1)
            # 读得到
            self.assertEqual(con.execute("SELECT v FROM t").fetchone()[0], "x")
            # 写不了：必须抛错，而不是静默成功
            with self.assertRaises(sqlite3.OperationalError):
                con.execute("INSERT INTO t(v) VALUES('y')")
        finally:
            con.close()

    def test_write_path_uses_own_connection(self) -> None:
        """`_tail_store` 自带写连接：即使池是只读的，旧路径/测试仍可写。"""
        adapter._TAIL_LAST.clear()
        bar = {"open_time": 1_700_000_000_000, "close_time": 1_700_003_599_999,
               "open": 1.0, "high": 1.1, "low": 0.9, "close": 1.05,
               "volume": 10.0, "quote_volume": 10.5, "trade_count": 3,
               "taker_buy_volume": 4.0, "taker_buy_quote_volume": 4.2,
               "is_closed": True, "available_at": None}
        # 临时库缺 schema：只要不是「只读库」错误即可（说明用的是可写连接）
        with self.assertRaises(sqlite3.OperationalError) as ctx:
            adapter._tail_store("binance", "TESTUSDT", "1h", [bar], "src")
        self.assertNotIn("readonly", str(ctx.exception).lower())


class TailBarsReadOnlyTest(unittest.TestCase):
    def setUp(self) -> None:
        self._old_mode = adapter.WEB_READONLY
        adapter.WEB_READONLY = True
        self.enqueued: list[tuple] = []
        self.fetched: list[tuple] = []
        self._old_enqueue = adapter._enqueue_tail_sync
        self._old_fetch = adapter._tail_fetch

    def tearDown(self) -> None:
        adapter.WEB_READONLY = self._old_mode
        adapter._enqueue_tail_sync = self._old_enqueue
        adapter._tail_fetch = self._old_fetch

    def test_read_path_enqueues_and_does_not_fetch_or_write(self) -> None:
        adapter._tail_fetch = lambda ex, sym, tf: self.fetched.append((ex, sym, tf)) or ([], "src")
        adapter._enqueue_tail_sync = (
            lambda ex, sym, tf: self.enqueued.append((ex, sym, tf)) or "已交后台")

        wrote: list[int] = []
        old_store = adapter._tail_store
        adapter._tail_store = lambda *a, **k: wrote.append(1) or 0
        try:
            added, forming, note = adapter._tail_bars("binance", "BTCUSDT", "1h")
        finally:
            adapter._tail_store = old_store

        self.assertEqual(added, 0)            # 本次请求确实没写
        self.assertEqual(wrote, [])           # 旧写路径未被调用
        self.assertEqual(self.fetched, [])    # 也不拉网络（网关会写调用记录）
        self.assertEqual(self.enqueued, [("binance", "BTCUSDT", "1h")])
        self.assertIsNone(forming)
        self.assertEqual(note, "已交后台")

    def test_legacy_mode_still_fetches_and_writes(self) -> None:
        adapter.WEB_READONLY = False
        bars = [{"is_closed": True, "open_time": 1, "close_time": 2,
                 "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0,
                 "volume": 1.0, "quote_volume": 1.0, "trade_count": 1,
                 "taker_buy_volume": 0.0, "taker_buy_quote_volume": 0.0,
                 "available_at": None}]
        adapter._tail_fetch = lambda ex, sym, tf: (bars, "src")
        adapter._enqueue_tail_sync = (
            lambda *a, **k: self.enqueued.append(a) or "不该走到这里")
        old_store = adapter._tail_store
        adapter._tail_store = lambda *a, **k: 7
        try:
            added, _forming, note = adapter._tail_bars("binance", "BTCUSDT", "1h")
        finally:
            adapter._tail_store = old_store

        self.assertEqual(added, 7)
        self.assertEqual(self.enqueued, [])
        self.assertIsNone(note)


if __name__ == "__main__":
    unittest.main()
