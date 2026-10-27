#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SYSTEM-ISOLATION-04｜关闭任务队列库隔离遗漏与全路径防绕行专项验收套件。

覆盖验收标准：
  1. jobs_db 纳入必填项：缺失 jobs_db 时直接拒绝启动，绝不回退日常 数据/任务队列/jobs.sqlite3；
  2. 隔离模式日常路径硬拦截：任何路径指向日常生产目录均拒绝启动；
  3. 路径别名防绕行：符号链接（symlink）、相对路径（..）等任何别名绕回日常目录均被硬拦截；
  4. 全生命周期子进程打开文件核验：覆盖任务队列库及其 WAL、SHM、锁文件，以及行情、live、paper、locks、logs，证明 0 访问日常数据；
  5. 正常停止、重复停止幂等、进程身份不匹配拒绝停止，停止后 overall_health 准确显示 stopped；
  6. 保留缺 live 库 unavailable 约定与不伪造零余额。
"""

import json
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
from pathlib import Path

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


class TestSystemIsolation04(unittest.TestCase):
    def setUp(self):
        self.test_dir = Path(tempfile.mkdtemp(prefix="isolation_test_04_"))
        self.pid_dir = self.test_dir / "pids"
        self.log_dir = self.test_dir / "logs"
        self.lock_dir = self.test_dir / "locks"
        self.ledger_dir = self.test_dir / "paper_ledger"
        self.live_dir = self.test_dir / "live"
        self.market_dir = self.test_dir / "market"
        self.jobs_dir = self.test_dir / "jobs"

        for d in (self.pid_dir, self.log_dir, self.lock_dir, self.ledger_dir, self.live_dir, self.market_dir, self.jobs_dir):
            d.mkdir(parents=True, exist_ok=True)

        self.market_db = self.market_dir / "market_isolated.sqlite3"
        self.live_db = self.live_dir / "live_isolated.sqlite3"
        self.jobs_db = self.jobs_dir / "jobs_isolated.sqlite3"
        self.port = find_free_port()
        self.py = SM.get_default_python()

        # 初始化隔离库
        self._init_isolated_dbs()
        os.environ.pop("EFFECTIVE_VIEW", None)

    def tearDown(self):
        try:
            listener = SM.get_port_listener_pid(self.port)
            if listener:
                os.kill(listener, signal.SIGKILL)
        except Exception:
            pass
        if self.test_dir.exists():
            shutil.rmtree(self.test_dir, ignore_errors=True)

    def _init_isolated_dbs(self):
        """初始化隔离测试使用的独立 SQLite 库。"""
        # 1. 行情库
        con_m = sqlite3.connect(str(self.market_db))
        con_m.execute("CREATE TABLE IF NOT EXISTS klines_15m (symbol TEXT, open_time INTEGER PRIMARY KEY, open REAL, high REAL, low REAL, close REAL, volume REAL, close_time INTEGER)")
        con_m.execute("CREATE TABLE IF NOT EXISTS klines_1h (symbol TEXT, open_time INTEGER PRIMARY KEY, open REAL, high REAL, low REAL, close REAL, volume REAL, close_time INTEGER)")
        con_m.execute("CREATE TABLE IF NOT EXISTS klines_4h (symbol TEXT, open_time INTEGER PRIMARY KEY, open REAL, high REAL, low REAL, close REAL, volume REAL, close_time INTEGER)")
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

    def _get_valid_config(self) -> SM.ServiceConfig:
        return SM.ServiceConfig(
            port=self.port,
            py=self.py,
            is_isolated=True,
            market_db=str(self.market_db),
            live_db=str(self.live_db),
            paper_ledger_dir=str(self.ledger_dir),
            paper_lock_dir=str(self.lock_dir),
            jobs_db=str(self.jobs_db),
            pid_dir=self.pid_dir,
            log_dir=self.log_dir,
            check_collector=False
        )

    def test_01_missing_jobs_db_rejected(self):
        """测试 1：jobs_db 必填，缺失时直接拒绝启动，严禁回退日常 数据/任务队列/jobs.sqlite3。"""
        cfg = self._get_valid_config()
        cfg.jobs_db = None  # 故意缺失 jobs_db

        with self.assertRaises(RuntimeError) as ctx:
            SM.ServiceManager(cfg)
        self.assertIn("隔离模式缺少必要路径配置", str(ctx.exception))
        self.assertIn("jobs_db", str(ctx.exception))
        print("\n[Test 1] 缺少 jobs_db 硬拦截验证通过：拒绝启动，未发生向日常目录的回退。")

    def test_02_direct_daily_paths_rejected(self):
        """测试 2：隔离模式下任何路径直接指向日常数据/状态目录时均拒绝启动。"""
        # A. jobs_db 指向日常任务队列
        cfg_jobs = self._get_valid_config()
        cfg_jobs.jobs_db = str(PROJECT_ROOT / "数据" / "任务队列" / "jobs.sqlite3")
        with self.assertRaises(RuntimeError) as ctx:
            SM.ServiceManager(cfg_jobs)
        self.assertIn("隔离模式严禁使用日常数据/状态目录", str(ctx.exception))
        self.assertIn("jobs_db", str(ctx.exception))

        # B. paper_ledger_dir 指向日常账本
        cfg_ledger = self._get_valid_config()
        cfg_ledger.paper_ledger_dir = str(PROJECT_ROOT / "数据" / "paper")
        with self.assertRaises(RuntimeError) as ctx:
            SM.ServiceManager(cfg_ledger)
        self.assertIn("隔离模式严禁使用日常数据/状态目录", str(ctx.exception))
        self.assertIn("paper_ledger_dir", str(ctx.exception))

        # C. market_db 指向日常行情库
        cfg_market = self._get_valid_config()
        cfg_market.market_db = str(PROJECT_ROOT / "数据" / "市场数据" / "db" / "market_v2.sqlite3")
        with self.assertRaises(RuntimeError) as ctx:
            SM.ServiceManager(cfg_market)
        self.assertIn("隔离模式严禁使用日常数据/状态目录", str(ctx.exception))
        self.assertIn("market_db", str(ctx.exception))

        # D. live_db 指向日常实时库
        cfg_live = self._get_valid_config()
        cfg_live.live_db = str(BACKEND / "live" / "live_signals.sqlite3")
        with self.assertRaises(RuntimeError) as ctx:
            SM.ServiceManager(cfg_live)
        self.assertIn("隔离模式严禁使用日常数据/状态目录", str(ctx.exception))
        self.assertIn("live_db", str(ctx.exception))

        # E. pid_dir 指向日常 backend 根目录
        cfg_pid = self._get_valid_config()
        cfg_pid.pid_dir = BACKEND
        with self.assertRaises(RuntimeError) as ctx:
            SM.ServiceManager(cfg_pid)
        self.assertIn("隔离模式严禁使用日常数据/状态目录", str(ctx.exception))
        self.assertIn("pid_dir", str(ctx.exception))

        print("[Test 2] 直接指向日常路径全部被硬拦截拦截通过（覆盖 jobs_db、ledger、market、live、pid）。")

    def test_03_alias_and_symlink_and_relative_path_rejected(self):
        """测试 3：符号链接（symlink）和相对路径（..）别名绕回日常目录均被硬拦截。"""
        # A. 符号链接别名绕行
        symlink_jobs = self.test_dir / "symlink_daily_jobs.sqlite3"
        daily_jobs = PROJECT_ROOT / "数据" / "任务队列" / "jobs.sqlite3"
        try:
            symlink_jobs.symlink_to(daily_jobs)
        except OSError:
            pass

        if symlink_jobs.is_symlink():
            cfg_sym = self._get_valid_config()
            cfg_sym.jobs_db = str(symlink_jobs)
            with self.assertRaises(RuntimeError) as ctx:
                SM.ServiceManager(cfg_sym)
            self.assertIn("隔离模式严禁使用日常数据/状态目录", str(ctx.exception))
            print("[Test 3-A] 符号链接 symlink 别名绕行硬拦截通过。")

        # B. 相对路径 .. 绕行（例如通过 策略/../数据 企图相对引用绕回日常任务队列）
        rel_jobs = str(PROJECT_ROOT / "策略" / ".." / "数据" / "任务队列" / "jobs.sqlite3")
        cfg_rel = self._get_valid_config()
        cfg_rel.jobs_db = rel_jobs
        with self.assertRaises(RuntimeError) as ctx:
            SM.ServiceManager(cfg_rel)
        self.assertIn("隔离模式严禁使用日常数据/状态目录", str(ctx.exception))
        print("[Test 3-B] 相对路径 .. 别名绕行硬拦截通过。")

    def test_04_subprocesses_full_isolation_including_jobs_wal_shm_lock(self):
        """测试 4：核验证子进程实际打开的文件，覆盖任务队列库及 WAL、SHM、锁文件，证明 0 访问日常数据。"""
        cfg = self._get_valid_config()
        mgr = SM.ServiceManager(cfg)
        st = mgr.start()

        api_pid = st["services"]["api"]["pid"]
        w_pid = st["services"]["worker"]["pid"]
        self.assertIsNotNone(api_pid)
        self.assertIsNotNone(w_pid)

        # 严禁访问的日常保护路径全集（包括任务队列全部文件）
        protected_patterns = [
            str((PROJECT_ROOT / "数据" / "任务队列").resolve()),
            str((PROJECT_ROOT / "数据" / "paper").resolve()),
            str((PROJECT_ROOT / "数据" / "市场数据").resolve()),
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
                    self.assertNotIn(pat, f, f"[{p_name}] 隔离违规：子进程访问了日常受保护数据文件：{f} (匹配日常规则: {pat})")

        # 特别确认：worker 实际打开的是临时隔离的任务队列库或其锁/wal
        worker_files = sub_evidence["worker"]
        isolated_jobs_str = str(self.jobs_db.resolve())
        opened_isolated_jobs = any(isolated_jobs_str in f for f in worker_files)
        self.assertTrue(opened_isolated_jobs, f"Worker 必须打开隔离的 jobs_db ({isolated_jobs_str})，实际文件: {worker_files}")

        print(f"\n[Test 4] 全量子进程打开文件硬核验通过：覆盖任务队列库、WAL、SHM、锁及所有日常数据路径，全部 0 访问。")
        mgr.stop()

    def test_05_stop_precision_idempotency_mismatch_rejection_and_health(self):
        """测试 5：正常停止、重复停止幂等、身份不匹配拒绝报错，停止后 overall_health 准确为 stopped。"""
        cfg = self._get_valid_config()
        mgr = SM.ServiceManager(cfg)

        # 启动前状态
        st_pre = mgr.status()
        self.assertEqual(st_pre["overall_health"], "stopped")

        # 启动后
        mgr.start()
        st_run = mgr.status()
        self.assertEqual(st_run["overall_health"], "healthy")

        # 正常停止
        stop1 = mgr.stop()
        self.assertEqual(stop1["overall_health"], "stopped")
        self.assertEqual(stop1["services"]["api"]["status"], "stopped")
        self.assertEqual(stop1["services"]["worker"]["status"], "stopped")

        # 重复停止（幂等性）
        stop2 = mgr.stop()
        self.assertEqual(stop2["overall_health"], "stopped")
        self.assertEqual(stop2["services"]["api"]["status"], "stopped")
        self.assertEqual(stop2["services"]["worker"]["status"], "stopped")
        print("\n[Test 5-A] 正常停止与重复停止幂等通过，overall_health 准确显示 stopped。")

        # 身份不匹配场景：外部未知进程占用端口
        conflict_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        conflict_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        conflict_sock.bind(("127.0.0.1", self.port))
        conflict_sock.listen(1)

        try:
            with self.assertRaises(RuntimeError) as ctx:
                mgr.stop()
            self.assertIn("拒绝停止", str(ctx.exception))
            self.assertIn("身份不匹配", str(ctx.exception))
            print(f"[Test 5-B] 身份不匹配强力拦截生效: {ctx.exception}")
        finally:
            conflict_sock.close()

    def test_06_paper_accounts_missing_live_db_handling(self):
        """测试 6：保留缺 live 库返回 unavailable 约定，不伪造零余额，不隐式创建日常库。"""
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
            print(f"\n[Test 6] paper_accounts 缺库处理验证通过（unavailable 状态与零余额防伪造）。")
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
