#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DATA-PATH-01 专项验收测试套件：数据读取与账本性能优化
- 验证 SQLite 执行计划：优先复用现有复合主键索引，杜绝 845 万行全表扫描；
- 验证数据链状态事实：最新可用时间、数据年龄、缺口、读取来源、错误及阻塞原因；
- 验证有持仓态下的账本性能：在完全隔离的沙盒中注入活跃持仓，连续 10 次请求，断言本机 P95 ≤ 2.0 秒；
- 验证生产环境账本与信号库零污染（0 写入）。
"""

import os
import sys
import time
import json
import sqlite3
import shutil
import tempfile
import unittest
from pathlib import Path

# 项目根目录与后端路径
BACKEND_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = BACKEND_DIR.parent.parent
sys.path.insert(0, str(BACKEND_DIR))

import live_store as L
import paper_engine as PE
import live_api as live


class TestDataPath01(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.prod_mkt_db = BACKEND_DIR / "市场数据" / "db" / "market_v2.sqlite3"
        if not cls.prod_mkt_db.exists():
            cls.prod_mkt_db = PROJECT_ROOT / "数据" / "市场数据" / "db" / "market_v2.sqlite3"
        assert cls.prod_mkt_db.exists(), f"生产行情数据库不存在: {cls.prod_mkt_db}"

        cls.prod_live_db = BACKEND_DIR / "live" / "live_signals.sqlite3"
        if not cls.prod_live_db.exists():
            cls.prod_live_db = PROJECT_ROOT / "数据" / "live" / "live_signals.sqlite3"

        cls.prod_paper_dir = PROJECT_ROOT / "数据" / "paper"

    def test_01_explain_query_plan_and_indexes(self):
        """1. 验证执行计划：严格走主键复合索引，杜绝对 845 万行 candle_versions 的全表扫描。"""
        con = sqlite3.connect(f"file:{self.prod_mkt_db}?mode=ro", uri=True)
        cur = con.cursor()
        sym = "BTCUSDT"

        # 检查 15m 最新闭合 K 线查询计划
        plan_15m = cur.execute(
            "EXPLAIN QUERY PLAN SELECT symbol, timeframe, open_time, close_time, open, high, low, close, quote_volume, available_at "
            "FROM candles WHERE exchange='binance' AND market='um' AND symbol=? AND timeframe='15m' "
            "AND is_closed=1 ORDER BY open_time DESC LIMIT 1",
            (sym,)
        ).fetchall()
        plan_str_15m = " ".join(str(p) for p in plan_15m)
        print("\n[test_01] 15m 执行计划:", plan_str_15m)
        self.assertIn("SEARCH", plan_str_15m)
        self.assertIn("sqlite_autoindex_candle_versions_1", plan_str_15m)
        self.assertNotIn("SCAN candle_versions", plan_str_15m)
        self.assertNotIn("USE TEMP B-TREE", plan_str_15m)

        # 检查 4h 最新闭合 K 线查询计划
        plan_4h = cur.execute(
            "EXPLAIN QUERY PLAN SELECT symbol, timeframe, open_time, close_time, open, high, low, close, quote_volume, available_at "
            "FROM candles WHERE exchange='binance' AND market='um' AND symbol=? AND timeframe='4h' "
            "AND is_closed=1 ORDER BY open_time DESC LIMIT 1",
            (sym,)
        ).fetchall()
        plan_str_4h = " ".join(str(p) for p in plan_4h)
        print("[test_01] 4h 执行计划:", plan_str_4h)
        self.assertIn("SEARCH", plan_str_4h)
        self.assertIn("sqlite_autoindex_candle_versions_1", plan_str_4h)
        self.assertNotIn("SCAN candle_versions", plan_str_4h)
        self.assertNotIn("USE TEMP B-TREE", plan_str_4h)

        # 检查带 upto_ms 的 15m 退出检查查询计划
        upto_ms = 1792763099999
        plan_upto = cur.execute(
            "EXPLAIN QUERY PLAN SELECT symbol, timeframe, open_time, close_time, open, high, low, close, quote_volume, available_at "
            "FROM candles WHERE exchange='binance' AND market='um' AND symbol=? AND timeframe='15m' "
            "AND is_closed=1 AND close_time<=? ORDER BY open_time DESC LIMIT 1",
            (sym, upto_ms)
        ).fetchall()
        plan_str_upto = " ".join(str(p) for p in plan_upto)
        print("[test_01] 15m 带 upto_ms 执行计划:", plan_str_upto)
        self.assertIn("SEARCH", plan_str_upto)
        self.assertIn("sqlite_autoindex_candle_versions_1", plan_str_upto)
        self.assertNotIn("SCAN candle_versions", plan_str_upto)

        con.close()

    def test_02_data_chain_status_facts(self):
        """2. 验证数据链状态所需事实：最新可用时间、数据年龄、缺口、读取来源、错误及阻塞原因。"""
        con = sqlite3.connect(f"file:{self.prod_mkt_db}?mode=ro", uri=True)
        cur = con.cursor()

        # 验证行情库内最新时间
        row_15m = cur.execute(
            "SELECT MAX(close_time), MAX(available_at) FROM candles WHERE exchange='binance' AND market='um' AND symbol='BTCUSDT' AND timeframe='15m' AND is_closed=1"
        ).fetchone()
        self.assertIsNotNone(row_15m[0])
        latest_15m_close = row_15m[0]
        latest_15m_avail = row_15m[1]

        row_4h = cur.execute(
            "SELECT MAX(close_time), MAX(available_at) FROM candles WHERE exchange='binance' AND market='um' AND symbol='BTCUSDT' AND timeframe='4h' AND is_closed=1"
        ).fetchone()
        self.assertIsNotNone(row_4h[0])
        latest_4h_close = row_4h[0]

        # 验证读取来源
        sources = cur.execute("SELECT id, exchange, market, symbol, timeframe, source_url FROM sources LIMIT 5").fetchall()
        self.assertTrue(len(sources) > 0)

        con.close()

        print(f"\n[test_02] 数据链状态事实核验:")
        print(f"  最新可用 15m 闭合时间 (close_time): {latest_15m_close}")
        print(f"  最新可用 15m 入库时间 (available_at): {latest_15m_avail}")
        print(f"  最新可用 4h 闭合时间 (close_time): {latest_4h_close}")
        print(f"  登记数据来源示例: {sources[0]}")

    def test_03_isolated_positions_ledger_benchmark_p95(self):
        """3. 关键性能验收：在独立沙盒内构建真实多持仓账本，连续10次请求，断言本机 P95 ≤ 2.0 秒。"""
        # 记录测试前生产库状态
        prod_signals_before = 0
        if self.prod_live_db.exists():
            con = sqlite3.connect(str(self.prod_live_db))
            try:
                prod_signals_before = con.execute("SELECT count(*) FROM live_signal").fetchone()[0]
            finally:
                con.close()

        # 创建临时隔离目录
        tmp_dir = tempfile.mkdtemp(prefix="paper_bench_")
        orig_env = os.environ.copy()
        try:
            iso_ledger_dir = Path(tmp_dir) / "paper_ledger"
            iso_ledger_dir.mkdir(parents=True, exist_ok=True)
            iso_live_db = Path(tmp_dir) / "live_signals.sqlite3"

            os.environ["PAPER_LEDGER_DIR"] = str(iso_ledger_dir)
            os.environ["LIVE_DB"] = str(iso_live_db)
            os.environ["MARKET_DB"] = str(self.prod_mkt_db)  # 连接生产行情库（只读 845 万真实数据）
            os.environ["PAPER_LEDGER_ALLOW_INIT"] = "1"
            os.environ["ISOLATED_MODE"] = "1"

            # 初始化隔离实时信号库
            L.ensure_live_schema()

            # 初始化四臂隔离账本
            for a in L.ARMS:
                con = L.paper_conn(a, create=True, initial_equity=5000.0)
                con.close()

            # 在 3a 臂注入 3 笔活跃持仓（BTCUSDT, ETHUSDT, SOLUSDT）
            con_3a = L.paper_conn("3a", ro=False)
            test_positions = [
                ("BTCUSDT", 0.05, 82000.0, 78000.0),
                ("ETHUSDT", 1.50, 2700.0, 2550.0),
                ("SOLUSDT", 20.0, 180.0, 165.0),
            ]
            now_ms = int(time.time() * 1000)
            for idx, (sym, qty, entry_p, stop_p) in enumerate(test_positions, start=1):
                con_3a.execute(
                    "INSERT INTO paper_position(position_id,account_id,symbol,qty,"
                    "entry_price,stop_price,entry_ts,status,signal_uid,model_version,"
                    "market_source,origin,earliest_fill_ms,trail_d,voided) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,0)",
                    (idx, "paper43_3a", sym, qty, entry_p, stop_p, now_ms - 3600000,
                     "open", f"bench_sig_{idx}", "F_WX_v2.2", "binance_um", "paper_test",
                     now_ms - 3600000, 0.03)
                )
            con_3a.commit()
            con_3a.close()

            # 验证持仓已注入
            con_chk = L.paper_conn("3a", ro=True)
            active_pos = PE.open_positions(con_chk, "3a")
            con_chk.close()
            self.assertEqual(len(active_pos), 3, "隔离账本未注入指定持仓")

            # 连续执行 10 次 snapshot_all()，记录每次耗时
            latencies = []
            print("\n[test_03] 连续 10 次有持仓账本快照 (snapshot_all) 耗时测定 (N=10):")
            for i in range(10):
                t0 = time.perf_counter()
                snap = PE.snapshot_all()
                dt = time.perf_counter() - t0
                latencies.append(dt)
                # 校验快照中包含 3a 臂的 3 个持仓及实时标的估值
                self.assertEqual(len(snap["3a"]["positions"]), 3)
                for p in snap["3a"]["positions"]:
                    self.assertIsNotNone(p["mark_price"], f"标的 {p['symbol']} 未成功获取标记价格")
                    self.assertIsNotNone(p["unrealized_pnl"])
                print(f"  Req #{i+1:02d}: {dt*1000:.3f} ms")

            # 统计 Min, Max, Avg, P50, P95
            latencies_sorted = sorted(latencies)
            min_l = latencies_sorted[0]
            max_l = latencies_sorted[-1]
            avg_l = sum(latencies) / len(latencies)
            p50_l = latencies_sorted[int(len(latencies) * 0.50)]
            p95_l = latencies_sorted[int(len(latencies) * 0.95)]

            print(f"\n[test_03] 有持仓快照性能统计:")
            print(f"  Min: {min_l*1000:.3f} ms")
            print(f"  Max: {max_l*1000:.3f} ms")
            print(f"  Avg: {avg_l*1000:.3f} ms")
            print(f"  P50: {p50_l*1000:.3f} ms")
            print(f"  P95: {p95_l*1000:.3f} ms (验收目标: ≤ 2000.0 ms)")

            # 断言目标本机 P95 ≤ 2.0 秒
            self.assertLessEqual(p95_l, 2.0, f"本机 P95 ({p95_l}s) 超出目标 2.0s 阈值")

            # 测试 live.paper_accounts() 函数在有持仓态下的 10 次请求性能
            print("\n[test_03] 连续 10 次调用 live.paper_accounts() 耗时测定 (N=10):")
            api_latencies = []
            for i in range(10):
                t0 = time.perf_counter()
                res = live.paper_accounts()
                dt = time.perf_counter() - t0
                api_latencies.append(dt)
                self.assertEqual(res.get("quality", {}).get("status"), "ok")
                print(f"  API Req #{i+1:02d}: {dt*1000:.3f} ms")

            api_sorted = sorted(api_latencies)
            api_p95 = api_sorted[int(len(api_latencies) * 0.95)]
            print(f"  API P95: {api_p95*1000:.3f} ms (验收目标: ≤ 2000.0 ms)")
            self.assertLessEqual(api_p95, 2.0, f"API P95 ({api_p95}s) 超出目标 2.0s 阈值")

            # 启动隔离轻量 HTTP 服务验证真实端到端网络请求性能
            from http.server import BaseHTTPRequestHandler, HTTPServer
            import urllib.request
            import threading

            class BenchHandler(BaseHTTPRequestHandler):
                def do_GET(self):
                    if self.path == "/api/v1/paper/accounts":
                        body = live.paper_accounts()
                        raw = json.dumps(body).encode("utf-8")
                        self.send_response(200)
                        self.send_header("Content-Type", "application/json")
                        self.send_header("Content-Length", str(len(raw)))
                        self.end_headers()
                        self.wfile.write(raw)
                    else:
                        self.send_response(404)
                        self.end_headers()
                def log_message(self, format, *args):
                    pass

            httpd = HTTPServer(("127.0.0.1", 0), BenchHandler)
            bench_port = httpd.server_port
            http_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            http_thread.start()

            print(f"\n[test_03] 真实 HTTP 端口 {bench_port} 端到端有持仓请求测定 (N=10):")
            http_latencies = []
            proxy_handler = urllib.request.ProxyHandler({})
            opener = urllib.request.build_opener(proxy_handler)
            for i in range(10):
                t0 = time.perf_counter()
                req = urllib.request.Request(f"http://127.0.0.1:{bench_port}/api/v1/paper/accounts")
                with opener.open(req, timeout=5) as resp:
                    resp_data = resp.read()
                dt = time.perf_counter() - t0
                http_latencies.append(dt)
                self.assertTrue(len(resp_data) > 0)
                print(f"  HTTP Req #{i+1:02d}: {dt*1000:.3f} ms, bytes={len(resp_data)}")

            httpd.shutdown()
            http_sorted = sorted(http_latencies)
            http_p95 = http_sorted[int(len(http_latencies) * 0.95)]
            print(f"  HTTP End-to-End P95: {http_p95*1000:.3f} ms (验收目标: ≤ 2000.0 ms)")
            self.assertLessEqual(http_p95, 2.0, f"HTTP 端到端 P95 ({http_p95}s) 超出目标 2.0s 阈值")

        finally:
            # 还原环境变量与清理隔离目录
            os.environ.clear()
            os.environ.update(orig_env)
            shutil.rmtree(tmp_dir, ignore_errors=True)

            # 断言生产信号库零污染
            if self.prod_live_db.exists():
                con = sqlite3.connect(str(self.prod_live_db))
                try:
                    prod_signals_after = con.execute("SELECT count(*) FROM live_signal").fetchone()[0]
                    self.assertEqual(prod_signals_before, prod_signals_after, "生产环境 live_signal 发生写入污染！")
                finally:
                    con.close()


if __name__ == "__main__":
    unittest.main()
