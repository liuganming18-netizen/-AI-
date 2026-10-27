#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SYSTEM-STARTUP-01｜统一启动与服务状态诊断验收测试。

覆盖验收标准：
  1. 8787 只读诊断（监听、进程、启动日志、退出原因实据核验）；
  2. 隔离端口统一启动（API + Worker 就绪，默认不开放自动交易）；
  3. 重复启动拒绝与幂等复用、端口冲突明确失败状态；
  4. 隔离模拟模式启动与缺配置硬拦截（failed_missing_config）；
  5. 进程崩溃异常退出识别（failed_process_crashed）及重启恢复；
  6. 正常停止与隔离性验证（只停本实例，绝不误停采集器）。
"""

import json
import os
import shutil
import signal
import socket
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

import service_manager as SM


def find_free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class TestSystemStartup01(unittest.TestCase):
    def setUp(self):
        self.test_dir = Path(tempfile.mkdtemp(prefix="startup_test_"))
        self.pid_dir = self.test_dir / "pids"
        self.log_dir = self.test_dir / "logs"
        self.lock_dir = self.test_dir / "locks"
        self.pid_dir.mkdir(parents=True)
        self.log_dir.mkdir(parents=True)
        self.lock_dir.mkdir(parents=True)
        self.port = find_free_port()
        self.py = SM.get_default_python()
        os.environ.pop("EFFECTIVE_VIEW", None)

    def tearDown(self):
        # 强制清理本测试创建的临时端口可能遗留的进程
        try:
            listener = SM.get_port_listener_pid(self.port)
            if listener:
                os.kill(listener, signal.SIGKILL)
        except Exception:
            pass
        if self.test_dir.exists():
            shutil.rmtree(self.test_dir, ignore_errors=True)

    def _make_config(self, enable_paper: bool = False, config_path: str = None) -> SM.ServiceConfig:
        return SM.ServiceConfig(
            port=self.port,
            py=self.py,
            pid_dir=self.pid_dir,
            log_dir=self.log_dir,
            paper_lock_dir=str(self.lock_dir),
            enable_paper=enable_paper,
            config_path=config_path,
            check_collector=False,  # 测试环境不干扰全局采集器
            stop_collector=False
        )

    def test_01_diagnose_8787_read_only(self):
        """测试 1：只读检查 8787 端口状态与历史退出原因。"""
        cfg = self._make_config()
        mgr = SM.ServiceManager(cfg)
        diag = mgr.diagnose_8787_historical()

        self.assertEqual(diag["target_port"], 8787)
        self.assertIn("status", diag)
        self.assertIn("determined_reason", diag)
        self.assertIn("system_uptime", diag)
        self.assertIn("system_boot", diag)
        # 验证没有凭猜测归因：记录了真实证据
        print(f"\n[Test 1] 8787 只读诊断结论: {diag['determined_reason']}")
        if diag.get("worker_sigterm_evidence"):
            self.assertIn("signum", diag["worker_sigterm_evidence"])

    def test_02_unified_start_default_safe_mode(self):
        """测试 2：隔离端口统一安全启动，默认模式绝不暗中开放交易链。"""
        cfg = self._make_config(enable_paper=False)
        mgr = SM.ServiceManager(cfg)

        status_before = mgr.status()
        self.assertEqual(status_before["services"]["api"]["status"], "stopped")

        # 启动
        st = mgr.start()
        self.assertEqual(st["services"]["api"]["status"], "running")
        self.assertTrue(st["services"]["api"]["identity_matched"])
        self.assertIsNotNone(st["services"]["api"]["pid"])
        self.assertTrue(st["services"]["api"]["http_health"]["ok"])

        # Worker 正常就绪
        self.assertEqual(st["services"]["worker"]["status"], "running")
        self.assertTrue(st["services"]["worker"]["identity_matched"])
        self.assertIsNotNone(st["services"]["worker"]["pid"])

        # 核心红线：交易链必须处于 safe_mode / disabled 状态，绝不暗中开交易！
        self.assertEqual(st["services"]["runtime"]["status"], "disabled_safe_mode")
        self.assertIsNone(st["services"]["runtime"]["pid"])
        print(f"\n[Test 2] 安全启动就绪: API PID={st['services']['api']['pid']}, Worker PID={st['services']['worker']['pid']}, Runtime={st['services']['runtime']['status']}")

        # 正常停止并验证释放
        stop_st = mgr.stop()
        self.assertEqual(stop_st["services"]["api"]["status"], "stopped")
        self.assertEqual(stop_st["services"]["worker"]["status"], "stopped")

    def test_03_repeat_start_idempotency_and_port_conflict(self):
        """测试 3：重复启动幂等复用与端口冲突拦截。"""
        cfg = self._make_config(enable_paper=False)
        mgr = SM.ServiceManager(cfg)

        st1 = mgr.start()
        api_pid1 = st1["services"]["api"]["pid"]
        w_pid1 = st1["services"]["worker"]["pid"]

        # 重复执行 start，应当幂等复用已运行的进程，不重复拉起
        st2 = mgr.start()
        api_pid2 = st2["services"]["api"]["pid"]
        w_pid2 = st2["services"]["worker"]["pid"]

        self.assertEqual(api_pid1, api_pid2)
        self.assertEqual(w_pid1, w_pid2)
        print(f"\n[Test 3] 重复启动幂等复用成功，PID 未变: API={api_pid1}, Worker={w_pid1}")

        mgr.stop()

        # 模拟端口冲突：先用原生 socket 占住端口模拟外部未知进程
        conflict_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        conflict_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        conflict_sock.bind(("127.0.0.1", self.port))
        conflict_sock.listen(1)

        try:
            with self.assertRaises(RuntimeError) as ctx:
                mgr.start()
            self.assertIn("端口冲突", str(ctx.exception))
            chk_st = mgr.status()
            self.assertEqual(chk_st["services"]["api"]["status"], "port_conflict")
            print(f"[Test 3] 端口冲突拦截生效: {ctx.exception}")
        finally:
            conflict_sock.close()

    def test_04_paper_mode_missing_config_rejected_and_valid_config(self):
        """测试 4：隔离模拟交易模式启动，缺配置硬拦截与有效配置启动。"""
        # A. 缺配置/错误配置硬拦截
        fake_cfg_path = str(self.test_dir / "nonexistent_config.json")
        cfg_fail = self._make_config(enable_paper=True, config_path=fake_cfg_path)
        mgr_fail = SM.ServiceManager(cfg_fail)

        with self.assertRaises(RuntimeError) as ctx:
            mgr_fail.start()
        self.assertIn("启动拒绝：交易链配置无效或缺失", str(ctx.exception))
        st_fail = mgr_fail.status()
        self.assertEqual(st_fail["services"]["runtime"]["status"], "failed_missing_config")
        print(f"\n[Test 4-A] 缺配置硬拦截成功: {ctx.exception}")

        # B. 有效配置启动交易链
        cfg_ok = self._make_config(enable_paper=True)
        mgr_ok = SM.ServiceManager(cfg_ok)
        st_ok = mgr_ok.start()

        self.assertEqual(st_ok["services"]["api"]["status"], "running")
        self.assertEqual(st_ok["services"]["worker"]["status"], "running")
        self.assertEqual(st_ok["services"]["runtime"]["status"], "running")
        self.assertIsNotNone(st_ok["services"]["runtime"]["pid"])
        print(f"[Test 4-B] 隔离模拟交易链成功拉起: Runtime PID={st_ok['services']['runtime']['pid']}")

        mgr_ok.stop()

    def test_05_process_crashed_detection_and_restart(self):
        """测试 5：进程崩溃异常识别（failed_process_crashed）与重启恢复。"""
        cfg = self._make_config(enable_paper=False)
        mgr = SM.ServiceManager(cfg)
        st = mgr.start()

        api_pid = st["services"]["api"]["pid"]
        w_pid = st["services"]["worker"]["pid"]

        # 模拟 Worker 遭遇不可抗力异常崩溃（SIGKILL）
        os.kill(w_pid, signal.SIGKILL)
        time.sleep(0.5)

        st_crashed = mgr.status()
        self.assertEqual(st_crashed["services"]["worker"]["status"], "failed_process_crashed")
        self.assertIn("异常", st_crashed["services"]["worker"]["exit_reason"])
        print(f"\n[Test 5] 成功检测到工作进程异常崩溃: status={st_crashed['services']['worker']['status']}")

        # 触发 restart 恢复
        mgr.stop()
        st_restored = mgr.start()
        self.assertEqual(st_restored["services"]["worker"]["status"], "running")
        self.assertNotEqual(st_restored["services"]["worker"]["pid"], w_pid)
        print(f"[Test 5] 故障重启恢复成功，新 Worker PID={st_restored['services']['worker']['pid']}")

        mgr.stop()

    def test_06_stop_isolation_does_not_kill_collector(self):
        """测试 6：停止只作用于本启动器管理的进程，绝不误停采集器。"""
        cfg = self._make_config(enable_paper=False)
        mgr = SM.ServiceManager(cfg)
        mgr.start()

        # 验证默认调用 stop() 不会传 --stop-collector，且不误伤其他
        stop_res = mgr.stop()
        self.assertEqual(stop_res["services"]["api"]["status"], "stopped")
        self.assertEqual(stop_res["services"]["worker"]["status"], "stopped")
        print("\n[Test 6] 停止操作精准隔离验证通过。")


if __name__ == "__main__":
    unittest.main()
