#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SYSTEM-ISOLATION-05｜冷层归档隔离修复与全路径防绕行专项验收套件。

覆盖验收标准：
  1. market_archive 纳入必填项：缺失 market_archive 时直接拒绝启动，严禁回退日常 数据/市场数据/parquet；
  2. 隔离模式日常路径硬拦截：market_archive 指向日常生产目录均拒绝启动；
  3. 路径别名防绕行：符号链接（symlink）、相对路径（..）等任何别名绕回日常目录均被硬拦截；
  4. 全生命周期子进程打开文件核验与冷层读取触发：
     - 隔离模式启动子进程；
     - 真实触发冷层 Parquet 读取，证明读到隔离归档的专属数据（非日常数据）；
     - lsof 核验证子进程打开文件列表，证明 0 访问日常 数据/市场数据/parquet；
  5. 日常非隔离模式兼容性：未提供 market_archive 时正确回退日常 parquet，兼容日常启动；
  6. 保留已通过的 jobs_db 必填防绕行与账户风险约定。
"""

import json
import hashlib
import os
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parent
PROJECT_ROOT = BACKEND.parents[1].resolve() if len(BACKEND.parents) >= 2 else BACKEND.parent

if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import live_api
import live_store as L
import service_manager as SM


def find_free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class TestSystemIsolation05(unittest.TestCase):
    def setUp(self):
        self.test_dir = Path(tempfile.mkdtemp(prefix="isolation_test_05_"))
        self.pid_dir = self.test_dir / "pids"
        self.log_dir = self.test_dir / "logs"
        self.lock_dir = self.test_dir / "locks"
        self.ledger_dir = self.test_dir / "paper_ledger"
        self.live_dir = self.test_dir / "live"
        self.market_dir = self.test_dir / "market"
        self.jobs_dir = self.test_dir / "jobs"
        self.archive_dir = self.test_dir / "archive"

        for d in (self.pid_dir, self.log_dir, self.lock_dir, self.ledger_dir,
                  self.live_dir, self.market_dir, self.jobs_dir, self.archive_dir):
            d.mkdir(parents=True, exist_ok=True)

        self.market_db = self.market_dir / "market_isolated.sqlite3"
        self.live_db = self.live_dir / "live_isolated.sqlite3"
        self.jobs_db = self.jobs_dir / "jobs_isolated.sqlite3"
        self.port = find_free_port()
        self.py = SM.get_default_python()

        # 初始化隔离环境数据库与冷层数据
        self._init_isolated_dbs()
        self._init_isolated_archive()
        os.environ.pop("EFFECTIVE_VIEW", None)

    def tearDown(self):
        self.doCleanups()
        if self.test_dir.exists():
            shutil.rmtree(self.test_dir, ignore_errors=True)

    def _init_isolated_dbs(self):
        """初始化隔离测试使用的独立 SQLite 库。"""
        # 1. 行情库（含基础 funding_history 表与 K 线表）
        con_m = sqlite3.connect(str(self.market_db))
        con_m.execute("CREATE TABLE IF NOT EXISTS klines_15m (symbol TEXT, open_time INTEGER PRIMARY KEY, open REAL, high REAL, low REAL, close REAL, volume REAL, close_time INTEGER)")
        con_m.execute("CREATE TABLE IF NOT EXISTS klines_1h (symbol TEXT, open_time INTEGER PRIMARY KEY, open REAL, high REAL, low REAL, close REAL, volume REAL, close_time INTEGER)")
        con_m.execute("CREATE TABLE IF NOT EXISTS klines_4h (symbol TEXT, open_time INTEGER PRIMARY KEY, open REAL, high REAL, low REAL, close REAL, volume REAL, close_time INTEGER)")
        con_m.execute("CREATE TABLE IF NOT EXISTS funding_history (exchange TEXT, market TEXT, symbol TEXT, funding_time INTEGER, funding_rate REAL, mark_price REAL, rate_type TEXT, source TEXT, fetched_at INTEGER)")
        now_ms = int(time.time() * 1000)
        t_base = (now_ms // 900000) * 900000
        con_m.execute("INSERT OR REPLACE INTO klines_15m VALUES('BTCUSDT', ?, 60000.0, 60500.0, 59800.0, 60200.0, 100.0, ?)", (t_base - 900000, t_base - 1))
        con_m.execute("INSERT OR REPLACE INTO klines_1h VALUES('BTCUSDT', ?, 60000.0, 60500.0, 59800.0, 60200.0, 400.0, ?)", (t_base - 3600000, t_base - 1))
        con_m.execute("INSERT OR REPLACE INTO klines_4h VALUES('BTCUSDT', ?, 60000.0, 60500.0, 59800.0, 60200.0, 1600.0, ?)", (t_base - 14400000, t_base - 1))
        con_m.commit()
        con_m.close()

        # 2. 实时库
        con_l = sqlite3.connect(str(self.live_db))
        con_l.executescript(L.LIVE_SCHEMA)
        con_l.commit()
        con_l.close()

        # 3. 纸面账本
        for a in L.ARMS:
            p_db = self.ledger_dir / f"paper_{a}.sqlite3"
            con_p = sqlite3.connect(str(p_db))
            con_p.executescript(L.PAPER_ADDITIVE)
            con_p.execute(
                "INSERT OR REPLACE INTO paper_account(account_id, arm, initial_equity, cash, leverage, dry_run, created_at) "
                "VALUES(?, ?, 5000.0, 5000.0, 1.0, 1, ?)",
                (f"paper43_{a}", a, int(time.time() * 1000))
            )
            con_p.commit()
            con_p.close()

        manifest = self.ledger_dir / "ledger_identity.json"
        manifest.write_text(json.dumps({
            "spec_version": L.LEDGER_SPEC_VERSION,
            "created_at_utc": "2026-10-23T00:00:00Z",
            "arms": {a: {"account_id": f"paper43_{a}", "initial_equity": 5000.0} for a in L.ARMS}
        }), encoding="utf-8")

        # 4. 任务队列库
        from infrastructure.jobs.store import SCHEMA as JOBS_SCHEMA
        con_j = sqlite3.connect(str(self.jobs_db))
        con_j.executescript(JOBS_SCHEMA)
        con_j.commit()
        con_j.close()

    def _init_isolated_archive(self):
        """初始化隔离测试冷层 Parquet 数据与状态。"""
        # 写入状态文件
        (self.archive_dir / "_state.json").write_text(json.dumps({
            "watermark_ms": 1700000000000,
            "updated_at": "2026-10-23T00:00:00Z"
        }), encoding="utf-8")

        # 构造专属特征的冷层数据：特定费率 0.00777777 与专属 source 标签
        funding_dir = self.archive_dir / "funding" / "binance" / "um" / "BTCUSDT"
        funding_dir.mkdir(parents=True, exist_ok=True)
        df = pd.DataFrame([{
            "exchange": "binance",
            "market": "um",
            "symbol": "BTCUSDT",
            "funding_time": 1672531200000,
            "funding_rate": 0.00777777,
            "mark_price": 16500.0,
            "rate_type": "settled",
            "source": "isolated_test_05_cold_record",
            "fetched_at": 1672531200000
        }])
        df.to_parquet(funding_dir / "2023.parquet")

    def _get_valid_config(self) -> SM.ServiceConfig:
        return SM.ServiceConfig(
            port=self.port,
            py=self.py,
            is_isolated=True,
            market_db=str(self.market_db),
            market_archive=str(self.archive_dir),
            live_db=str(self.live_db),
            paper_ledger_dir=str(self.ledger_dir),
            paper_lock_dir=str(self.lock_dir),
            jobs_db=str(self.jobs_db),
            pid_dir=self.pid_dir,
            log_dir=self.log_dir,
            check_collector=False
        )

    def test_01_missing_market_archive_rejected(self):
        """测试 1：market_archive 必填，缺失时直接拒绝启动，严禁回退日常 数据/市场数据/parquet。"""
        cfg = self._get_valid_config()
        cfg.market_archive = None  # 故意缺失 market_archive

        with self.assertRaises(RuntimeError) as ctx:
            SM.ServiceManager(cfg)
        self.assertIn("隔离模式缺少必要路径配置", str(ctx.exception))
        self.assertIn("market_archive", str(ctx.exception))
        print("\n[Test 1] 缺少 market_archive 硬拦截验证通过：拒绝启动，未发生向日常目录的回退。")

    def test_02_direct_daily_market_archive_rejected(self):
        """测试 2：隔离模式下 market_archive 直接指向日常数据目录时均拒绝启动。"""
        # A. market_archive 指向日常 parquet 目录
        cfg_archive = self._get_valid_config()
        cfg_archive.market_archive = str(PROJECT_ROOT / "数据" / "市场数据" / "parquet")
        with self.assertRaises(RuntimeError) as ctx:
            SM.ServiceManager(cfg_archive)
        self.assertIn("隔离模式严禁使用日常数据/状态目录", str(ctx.exception))
        self.assertIn("market_archive", str(ctx.exception))

        # B. market_archive 指向日常数据根目录
        cfg_daily_data = self._get_valid_config()
        cfg_daily_data.market_archive = str(PROJECT_ROOT / "数据")
        with self.assertRaises(RuntimeError) as ctx:
            SM.ServiceManager(cfg_daily_data)
        self.assertIn("隔离模式严禁使用日常数据/状态目录", str(ctx.exception))
        self.assertIn("market_archive", str(ctx.exception))

        print("[Test 2] market_archive 直接指向日常路径全部被硬拦截通过。")

    def test_03_alias_and_symlink_and_relative_path_rejected(self):
        """测试 3：符号链接（symlink）和相对路径（..）别名绕回日常目录均被硬拦截。"""
        # A. 符号链接别名绕行
        symlink_archive = self.test_dir / "symlink_daily_parquet"
        daily_parquet = PROJECT_ROOT / "数据" / "市场数据" / "parquet"
        try:
            symlink_archive.symlink_to(daily_parquet)
        except OSError:
            pass

        if symlink_archive.is_symlink():
            cfg_sym = self._get_valid_config()
            cfg_sym.market_archive = str(symlink_archive)
            with self.assertRaises(RuntimeError) as ctx:
                SM.ServiceManager(cfg_sym)
            self.assertIn("隔离模式严禁使用日常数据/状态目录", str(ctx.exception))
            self.assertIn("market_archive", str(ctx.exception))
            print("[Test 3-A] 符号链接 symlink 别名绕行硬拦截通过。")

        # B. 相对路径 .. 绕行
        rel_archive = str(PROJECT_ROOT / "策略" / ".." / "数据" / "市场数据" / "parquet")
        cfg_rel = self._get_valid_config()
        cfg_rel.market_archive = rel_archive
        with self.assertRaises(RuntimeError) as ctx:
            SM.ServiceManager(cfg_rel)
        self.assertIn("隔离模式严禁使用日常数据/状态目录", str(ctx.exception))
        self.assertIn("market_archive", str(ctx.exception))
        print("[Test 3-B] 相对路径 .. 别名绕行硬拦截通过。")

    def test_04_cold_archive_isolation_and_actual_read_verification(self):
        """测试 4：启动隔离服务，实际触发冷层读取，证明读到隔离归档中的特征数据，子进程零访问日常数据。"""
        cfg = self._get_valid_config()
        mgr = SM.ServiceManager(cfg)
        self.addCleanup(mgr.stop)
        st = mgr.start()

        api_pid = st["services"]["api"]["pid"]
        w_pid = st["services"]["worker"]["pid"]
        self.assertIsNotNone(api_pid)
        self.assertIsNotNone(w_pid)

        # 1. 实际触发冷层读取：调用 HTTP API 读取 funding 历史
        url = f"http://127.0.0.1:{self.port}/api/v1/markets/binance/BTCUSDT/funding?limit=10"
        req = urllib.request.Request(url, headers={"Connection": "close", "User-Agent": "test_isolation_05"})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(req, timeout=5.0) as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode("utf-8"))

        # 断言读取到了隔离冷层中的专属记录
        items = data if isinstance(data, list) else data.get("items", [])
        self.assertTrue(len(items) > 0, f"API 返回列表为空：{data}")
        found_isolated_record = any(
            item.get("source") == "isolated_test_05_cold_record" and
            abs(item.get("funding_rate", 0) - 0.00777777) < 1e-7
            for item in items
        )
        self.assertTrue(
            found_isolated_record,
            f"必须实际读取到隔离冷层 parquet 中的特征数据 (source='isolated_test_05_cold_record')，实际返回: {items}"
        )
        print("\n[Test 4-A] 真实冷层读取断言通过：接口成功返回隔离归档专属特征数据。")

        # 真实 HTTP 投影必须保留挂单方向和来源臂；只写本次临时账本。
        for arm, direction in (("3a", "long"), ("3b", "short")):
            with sqlite3.connect(str(self.ledger_dir / f"paper_{arm}.sqlite3")) as con:
                con.execute("INSERT INTO paper_pending(account_id,arm,symbol,direction,requested_at,earliest_fill_ms,status,created_at) VALUES(?,?,?,?,?,?,?,?)",
                            (f"paper43_{arm}", arm, "TESTFIXUSDT", direction, 1, 9999999999999, "pending", 1))
        with opener.open(f"http://127.0.0.1:{self.port}/api/v1/paper/accounts", timeout=5) as resp:
            accounts_response = json.loads(resp.read())
        pending = [p for a in accounts_response.get("items", []) for p in a.get("pending", [])]
        self.assertEqual({(p["arm"], p["direction"]) for p in pending}, {("3a", "long"), ("3b", "short")}, accounts_response)

        # 2. 检查子进程打开文件列表（lsof）
        protected_patterns = [
            str((PROJECT_ROOT / "数据").resolve()),
            str((PROJECT_ROOT / "数据" / "任务队列").resolve()),
            str((PROJECT_ROOT / "数据" / "paper").resolve()),
            str((BACKEND / "live" / "live_signals.sqlite3").resolve()),
            str((BACKEND / "locks").resolve()),
            str((BACKEND / "logs").resolve()),
        ]

        sub_evidence = {}
        for p_name, pid in [("api", api_pid), ("worker", w_pid)]:
            out = subprocess.check_output(
                ["lsof", "-p", str(pid), "-Fn"],
                stderr=subprocess.DEVNULL,
                timeout=3.0
            ).decode("utf-8", errors="replace")
            opened_files = [line[1:] for line in out.splitlines() if line.startswith("n") and line[1:].strip()]
            sub_evidence[p_name] = opened_files

            # 断言绝对未打开日常受保护数据文件
            for f in opened_files:
                for pat in protected_patterns:
                    self.assertNotIn(pat, f, f"[{p_name}] 隔离违规：子进程访问了日常受保护数据文件：{f} (匹配规则: {pat})")

        print("[Test 4-B] 全量子进程打开文件核验通过：0 访问日常 数据/市场数据/parquet 与其他日常目录。")
        mgr.stop()
        if os.environ.get("ISO_EVIDENCE_DIR"):
            dest = Path(os.environ["ISO_EVIDENCE_DIR"])
            dest.mkdir(parents=True, exist_ok=True)
            record = {
                "run_id": os.environ["ISO_RUN_ID"], "port": self.port,
                "api_pid": api_pid, "worker_pid": w_pid,
                "cold_response": data, "pending_response": pending, "opened_files": sub_evidence,
                "protected_paths": protected_patterns,
                "stopped": mgr.status(),
                "source_sha256": {
                    str(p.relative_to(PROJECT_ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in (Path(__file__).resolve(), BACKEND / "service_manager.py", BACKEND / "paper_engine.py")
                },
            }
            (dest / "isolation.json").write_text(json.dumps(record, indent=2, ensure_ascii=False))
            for log in self.log_dir.glob("*"):
                if log.is_file():
                    shutil.copy2(log, dest / log.name)
            print("EVIDENCE " + json.dumps({k: record[k] for k in ("run_id", "port", "api_pid", "worker_pid")}))

    def test_05_non_isolated_daily_mode_compatibility(self):
        """测试 5：日常非隔离模式兼容性，未提供 market_archive 时正确回退日常 parquet。"""
        # 非隔离模式配置
        cfg_daily = SM.ServiceConfig(
            port=self.port,
            py=self.py,
            is_isolated=False,
            market_archive=None  # 未提供归档路径
        )
        cfg_daily.resolve_paths()

        expected_daily_parquet = str((PROJECT_ROOT / "数据" / "市场数据" / "parquet").resolve())
        self.assertEqual(
            str(Path(cfg_daily.market_archive).resolve()),
            expected_daily_parquet,
            "非隔离模式必须兼容回退到日常 数据/市场数据/parquet"
        )
        print("\n[Test 5] 非隔离模式日常兼容性回退验证通过。")

    def test_06_jobs_db_and_account_risk_retention(self):
        """测试 6：保留已通过的 jobs_db 隔离与账户风险逻辑。"""
        # A. jobs_db 缺失依然拒绝
        cfg_no_jobs = self._get_valid_config()
        cfg_no_jobs.jobs_db = None
        with self.assertRaises(RuntimeError) as ctx:
            SM.ServiceManager(cfg_no_jobs)
        self.assertIn("jobs_db", str(ctx.exception))

        # B. paper_accounts 缺 live 库返回 unavailable 约定，不伪造零余额
        orig_live_db = os.environ.get("LIVE_DB")
        orig_paper_dir = os.environ.get("PAPER_LEDGER_DIR")

        try:
            missing_live = self.test_dir / "nonexistent_live.sqlite3"
            os.environ["LIVE_DB"] = str(missing_live)
            os.environ["PAPER_LEDGER_DIR"] = str(self.ledger_dir)
            L.LIVE_DB = Path(str(missing_live))

            res = live_api.paper_accounts()
            self.assertEqual(res.get("quality", {}).get("status"), "unavailable")
            self.assertIn("实时信号库缺失", res.get("missing_reason", ""))
            self.assertEqual(res.get("items"), [])
            self.assertEqual(res.get("total"), 0)
            self.assertFalse(missing_live.exists())
            print("[Test 6] jobs_db 隔离与账户风险逻辑保留验证通过。")
        finally:
            if orig_live_db:
                os.environ["LIVE_DB"] = orig_live_db
                L.LIVE_DB = Path(orig_live_db)
            else:
                os.environ.pop("LIVE_DB", None)
                L.LIVE_DB = Path(str(BACKEND / "live" / "live_signals.sqlite3"))

            if orig_paper_dir:
                os.environ["PAPER_LEDGER_DIR"] = orig_paper_dir
            else:
                os.environ.pop("PAPER_LEDGER_DIR", None)


if __name__ == "__main__":
    unittest.main()
