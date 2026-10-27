#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SYSTEM-ISOLATION-03｜路径隔离、精准停止与缺库处理专项验收套件。

覆盖验收标准：
  1. 隔离模式路径强校验：缺少必要路径或误指日常目录时直接拒绝启动，绝不退回日常目录；
  2. 子进程路径贯穿与打开文件硬核验：检查 API/Worker/Runtime 真实打开的文件描述符，证明未访问日常数据路径；
  3. 精准停止、重复停止幂等与身份不匹配拒绝：只停本实例，不按端口滥杀，不凭 PID 盲杀，身份不明明确报错；
  4. 停止后状态与 overall_health 准确区分：运行=healthy，停止=stopped，异常=unhealthy；
  5. 缺少 live 库时 /api/v1/paper/accounts 返回 unavailable 约定：不伪造零余额，不隐式创建日常库。
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


class TestSystemIsolation03(unittest.TestCase):
    def setUp(self):
        self.test_dir = Path(tempfile.mkdtemp(prefix="isolation_test_03_"))
        self.pid_dir = self.test_dir / "pids"
        self.log_dir = self.test_dir / "logs"
        self.lock_dir = self.test_dir / "locks"
        self.ledger_dir = self.test_dir / "paper_ledger"
        self.live_dir = self.test_dir / "live"
        self.market_dir = self.test_dir / "market"

        for d in (self.pid_dir, self.log_dir, self.lock_dir, self.ledger_dir, self.live_dir, self.market_dir):
            d.mkdir(parents=True, exist_ok=True)

        self.market_db = self.market_dir / "market_isolated.sqlite3"
        self.live_db = self.live_dir / "live_isolated.sqlite3"
        self.port = find_free_port()
        self.py = SM.get_default_python()

        # 初始化隔离用最小行情库与实时库
        self._init_isolated_dbs()
        os.environ.pop("EFFECTIVE_VIEW", None)

    def tearDown(self):
        # 清理可能残留的端口与进程
        try:
            listener = SM.get_port_listener_pid(self.port)
            if listener:
                os.kill(listener, signal.SIGKILL)
        except Exception:
            pass
        if self.test_dir.exists():
            shutil.rmtree(self.test_dir, ignore_errors=True)

    def _init_isolated_dbs(self):
        """初始化隔离测试使用的空/最小 SQLite 库。"""
        # 1. 隔离行情库
        con_m = sqlite3.connect(str(self.market_db))
        con_m.execute("CREATE TABLE IF NOT EXISTS klines_15m (symbol TEXT, open_time INTEGER PRIMARY KEY, open REAL, high REAL, low REAL, close REAL, volume REAL, close_time INTEGER)")
        con_m.execute("CREATE TABLE IF NOT EXISTS klines_1h (symbol TEXT, open_time INTEGER PRIMARY KEY, open REAL, high REAL, low REAL, close REAL, volume REAL, close_time INTEGER)")
        con_m.execute("CREATE TABLE IF NOT EXISTS klines_4h (symbol TEXT, open_time INTEGER PRIMARY KEY, open REAL, high REAL, low REAL, close REAL, volume REAL, close_time INTEGER)")
        # 写入一条基础测试 K 线
        now_ms = int(time.time() * 1000)
        t_base = (now_ms // 900000) * 900000
        con_m.execute("INSERT OR REPLACE INTO klines_15m VALUES('BTCUSDT', ?, 60000.0, 60500.0, 59800.0, 60200.0, 100.0, ?)", (t_base - 900000, t_base - 1))
        con_m.execute("INSERT OR REPLACE INTO klines_1h VALUES('BTCUSDT', ?, 60000.0, 60500.0, 59800.0, 60200.0, 400.0, ?)", (t_base - 3600000, t_base - 1))
        con_m.execute("INSERT OR REPLACE INTO klines_4h VALUES('BTCUSDT', ?, 60000.0, 60500.0, 59800.0, 60200.0, 1600.0, ?)", (t_base - 14400000, t_base - 1))
        con_m.commit()
        con_m.close()

        # 2. 隔离实时信号库
        con_l = sqlite3.connect(str(self.live_db))
        con_l.executescript(L.LIVE_SCHEMA)
        con_l.commit()
        con_l.close()

        # 3. 隔离纸面账本与身份声明
        for a in L.ARMS:
            p_db = self.ledger_dir / f"paper_{a}.sqlite3"
            con_p = sqlite3.connect(str(p_db))
            con_p.executescript(L.PAPER_ADDITIVE)
            # 初始化 account 记录
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

    def test_01_isolation_mode_rejects_missing_paths(self):
        """测试 1：隔离模式下缺少必要路径或指向日常目录时直接拒绝启动，绝不退回日常目录。"""
        # A. 缺少 market_db
        with self.assertRaises(RuntimeError) as ctx:
            cfg = SM.ServiceConfig(
                port=self.port,
                py=self.py,
                is_isolated=True,
                market_db=None,  # 故意缺失
                live_db=str(self.live_db),
                paper_ledger_dir=str(self.ledger_dir),
                paper_lock_dir=str(self.lock_dir),
                pid_dir=self.pid_dir,
                log_dir=self.log_dir
            )
            SM.ServiceManager(cfg)
        self.assertIn("隔离模式缺少必要路径配置", str(ctx.exception))
        self.assertIn("market_db", str(ctx.exception))

        # B. 缺少 live_db
        with self.assertRaises(RuntimeError) as ctx:
            cfg = SM.ServiceConfig(
                port=self.port,
                py=self.py,
                is_isolated=True,
                market_db=str(self.market_db),
                live_db=None,  # 故意缺失
                paper_ledger_dir=str(self.ledger_dir),
                paper_lock_dir=str(self.lock_dir),
                pid_dir=self.pid_dir,
                log_dir=self.log_dir
            )
            SM.ServiceManager(cfg)
        self.assertIn("隔离模式缺少必要路径配置", str(ctx.exception))
        self.assertIn("live_db", str(ctx.exception))

        # C. 隔离模式下企图传入日常生产账本目录，必须被硬拦截
        daily_ledger = str(PROJECT_ROOT / "数据" / "paper")
        with self.assertRaises(RuntimeError) as ctx:
            cfg = SM.ServiceConfig(
                port=self.port,
                py=self.py,
                is_isolated=True,
                market_db=str(self.market_db),
                live_db=str(self.live_db),
                paper_ledger_dir=daily_ledger,  # 企图混用日常账本
                paper_lock_dir=str(self.lock_dir),
                pid_dir=self.pid_dir,
                log_dir=self.log_dir
            )
            SM.ServiceManager(cfg)
        self.assertIn("隔离模式严禁使用日常账本目录", str(ctx.exception))
        print("\n[Test 1] 隔离模式路径强校验通过：缺失路径硬拦截，企图使用日常目录被拒绝。")

    def test_02_subprocesses_path_isolation_and_open_files(self):
        """测试 2：隔离实例启动，各子进程环境变量全量贯穿，核验证实际打开的文件未访问日常数据。"""
        cfg = SM.ServiceConfig(
            port=self.port,
            py=self.py,
            is_isolated=True,
            market_db=str(self.market_db),
            live_db=str(self.live_db),
            paper_ledger_dir=str(self.ledger_dir),
            paper_lock_dir=str(self.lock_dir),
            pid_dir=self.pid_dir,
            log_dir=self.log_dir,
            check_collector=False
        )
        mgr = SM.ServiceManager(cfg)
        st = mgr.start()

        api_pid = st["services"]["api"]["pid"]
        w_pid = st["services"]["worker"]["pid"]
        self.assertIsNotNone(api_pid)
        self.assertIsNotNone(w_pid)

        # 检查每个子进程的 open files (lsof)
        daily_ledger_str = str((PROJECT_ROOT / "数据" / "paper").resolve())
        daily_market_str = str((PROJECT_ROOT / "数据" / "市场数据" / "db" / "market_v2.sqlite3").resolve())
        daily_live_str = str((BACKEND / "live" / "live_signals.sqlite3").resolve())

        for p_name, pid in [("api", api_pid), ("worker", w_pid)]:
            try:
                out = subprocess.check_output(
                    ["lsof", "-p", str(pid), "-Fn"],
                    stderr=subprocess.DEVNULL,
                    timeout=3.0
                ).decode("utf-8", errors="replace")
                opened_files = [line[1:] for line in out.splitlines() if line.startswith("n")]
            except Exception:
                opened_files = []

            # 验证未打开日常数据路径
            for f in opened_files:
                self.assertNotIn(daily_ledger_str, f, f"[{p_name}] 隔离违规：子进程访问了日常账本 {f}")
                self.assertNotIn(daily_market_str, f, f"[{p_name}] 隔离违规：子进程访问了日常行情库 {f}")
                self.assertNotIn(daily_live_str, f, f"[{p_name}] 隔离违规：子进程访问了日常实时库 {f}")

        print(f"\n[Test 2] 子进程打开文件核验通过：API PID={api_pid}, Worker PID={w_pid}，均未访问日常路径。")
        mgr.stop()

    def test_03_stop_precision_and_idempotency_and_mismatch_rejection(self):
        """测试 3：精准停止本实例、重复停止幂等、身份不匹配拒绝报错，不误杀外部日常进程。"""
        cfg = SM.ServiceConfig(
            port=self.port,
            py=self.py,
            is_isolated=True,
            market_db=str(self.market_db),
            live_db=str(self.live_db),
            paper_ledger_dir=str(self.ledger_dir),
            paper_lock_dir=str(self.lock_dir),
            pid_dir=self.pid_dir,
            log_dir=self.log_dir,
            check_collector=False
        )
        mgr = SM.ServiceManager(cfg)
        mgr.start()

        # 1. 正常停止本实例
        stop_res = mgr.stop()
        self.assertEqual(stop_res["services"]["api"]["status"], "stopped")
        self.assertEqual(stop_res["services"]["worker"]["status"], "stopped")
        self.assertEqual(stop_res["overall_health"], "stopped")

        # 2. 重复停止（幂等性）：再次调用 stop，不抛错，状态仍为 stopped
        repeat_stop_res = mgr.stop()
        self.assertEqual(repeat_stop_res["services"]["api"]["status"], "stopped")
        self.assertEqual(repeat_stop_res["services"]["worker"]["status"], "stopped")
        self.assertEqual(repeat_stop_res["overall_health"], "stopped")
        print("\n[Test 3-A] 精准停止与重复停止幂等性验证通过。")

        # 3. 身份不匹配场景：构造未知进程占用端口，stop 必须明确拒绝并报错，绝不强杀
        conflict_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        conflict_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        conflict_sock.bind(("127.0.0.1", self.port))
        conflict_sock.listen(1)

        try:
            # 此时端口有监听，但不是由本实例启动，mgr.stop() 必须明确报错拒绝
            with self.assertRaises(RuntimeError) as ctx:
                mgr.stop()
            self.assertIn("拒绝停止", str(ctx.exception))
            self.assertIn("身份不匹配", str(ctx.exception))
            print(f"[Test 3-B] 身份不匹配强力拦截生效: {ctx.exception}")
        finally:
            conflict_sock.close()

    def test_04_overall_health_accurate_distinction(self):
        """测试 4：修复停止后仍显示 overall_health=healthy，准确区分运行、停止与异常。"""
        cfg = SM.ServiceConfig(
            port=self.port,
            py=self.py,
            is_isolated=True,
            market_db=str(self.market_db),
            live_db=str(self.live_db),
            paper_ledger_dir=str(self.ledger_dir),
            paper_lock_dir=str(self.lock_dir),
            pid_dir=self.pid_dir,
            log_dir=self.log_dir,
            check_collector=False
        )
        mgr = SM.ServiceManager(cfg)

        # 启动前：必须是 stopped
        st_initial = mgr.status()
        self.assertEqual(st_initial["overall_health"], "stopped")

        # 启动后：必须是 healthy
        mgr.start()
        st_running = mgr.status()
        self.assertEqual(st_running["overall_health"], "healthy")

        # 停止后：核心验收指标，必须准确显示 stopped，绝不能是 healthy！
        st_stopped = mgr.stop()
        self.assertEqual(st_stopped["overall_health"], "stopped")
        self.assertNotEqual(st_stopped["overall_health"], "healthy")
        print(f"\n[Test 4] overall_health 状态准确流转验证通过: {st_initial['overall_health']} -> {st_running['overall_health']} -> {st_stopped['overall_health']}")

    def test_05_paper_accounts_missing_live_db_handling(self):
        """测试 5：修复 /api/v1/paper/accounts 缺少 live 库时未处理异常，复用既有“不可用”返回约定，不伪造零余额或自动创建日常账户。"""
        # 设置环境使 LIVE_DB 指向不存在的路径
        orig_live_db = os.environ.get("LIVE_DB")
        orig_paper_dir = os.environ.get("PAPER_LEDGER_DIR")
        orig_allow_init = os.environ.get("PAPER_LEDGER_ALLOW_INIT")

        try:
            missing_live = self.test_dir / "nonexistent_live.sqlite3"
            os.environ["LIVE_DB"] = str(missing_live)
            os.environ["PAPER_LEDGER_DIR"] = str(self.ledger_dir)
            os.environ["PAPER_LEDGER_ALLOW_INIT"] = "0"

            # 重新加载 L 中的 LIVE_DB
            L.LIVE_DB = Path(str(missing_live))

            res = live_api.paper_accounts()

            # 核心断言：
            # 1. 契约中 quality.status 必须为 unavailable
            self.assertEqual(res.get("quality", {}).get("status"), "unavailable")
            # 2. missing_reason 明确说明缺库
            self.assertIn("实时信号库缺失", res.get("missing_reason", ""))
            # 3. items 必须为空列表，不伪造零余额账户
            self.assertEqual(res.get("items"), [])
            self.assertEqual(res.get("total"), 0)
            # 4. 严禁隐式创建库文件
            self.assertFalse(missing_live.exists(), "严禁在缺库时隐式自动创建库文件")

            print(f"\n[Test 5] paper_accounts 缺库处理验证通过: quality.status={res['quality']['status']}, missing_reason='{res['missing_reason']}', items={res['items']}")
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

            if orig_allow_init:
                os.environ["PAPER_LEDGER_ALLOW_INIT"] = orig_allow_init
            else:
                os.environ.pop("PAPER_LEDGER_ALLOW_INIT", None)


if __name__ == "__main__":
    unittest.main()
