# -*- coding: utf-8 -*-
"""纸面/模拟盘自动交易常驻执行循环（SYSTEM-CLOSELOOP-04 核心调度驱动）。

职责边界：
  1. 自动常驻驱动：定时轮询撮合挂单、驱动 4h K 线跟踪止损上移、驱动 15m 明细周期退出检查；
  2. 单实例保证：基于 fcntl 文件锁，同机严格单一进程执行，杜绝重复推进或重复成交；
  3. 参数与因果硬约束：沿用 FT-LOW4H 规则，禁止静默兜底，缺失参数明确报错；
  4. 状态暴露：维护可核验的运行状态、最近推进时刻、成功/失败轮次及错误详情。
"""
from __future__ import annotations

import fcntl
import json
import os
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import live_store as L
import paper_engine as PE

LOCK_DIR = Path(os.environ.get("PAPER_LOCK_DIR", str(HERE / "locks")))
LOCK_DIR.mkdir(parents=True, exist_ok=True)

# 全局执行状态登记（按 arm 分隔）
_STATUS: Dict[str, Dict[str, Any]] = {}
_STATUS_LOCK = threading.Lock()


def get_lock_dir() -> Path:
    if os.environ.get("PAPER_LOCK_DIR"):
        return Path(os.environ["PAPER_LOCK_DIR"])
    if os.environ.get("PAPER_LEDGER_DIR"):
        return Path(os.environ["PAPER_LEDGER_DIR"]).parent / "locks"
    return HERE / "locks"


def _status_file_path(arm: str) -> Path:
    lock_d = get_lock_dir()
    return lock_d / f"paper_daemon_{arm}_status.json"


def _save_status(arm: str, st: dict):
    try:
        p = _status_file_path(arm)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(p)
    except Exception:
        pass


class PaperExecutionDaemon:
    def __init__(self, arm: str = "3a", poll_interval_sec: float = 5.0, lock_path: Optional[str] = None):
        self.arm = arm
        self.poll_interval_sec = poll_interval_sec
        self.lock_dir = get_lock_dir()
        self.lock_dir.mkdir(parents=True, exist_ok=True)
        self.lock_file_path = lock_path or str(self.lock_dir / f"paper_exec_{arm}.lock")
        self.lock_fd = None
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.pid = os.getpid()
        self.started_at_ms = int(time.time() * 1000)

        with _STATUS_LOCK:
            st = {
                "arm": self.arm,
                "pid": self.pid,
                "status": "initialized",
                "running": False,
                "started_at": self.started_at_ms,
                "last_cycle_ms": None,
                "cycle_count": 0,
                "pending_matched_count": 0,
                "trailing_checked_count": 0,
                "stops_updated_count": 0,
                "exits_triggered_count": 0,
                "last_error": None,
                "health": "healthy"
            }
            _STATUS[self.arm] = st
            _save_status(self.arm, st)

    def acquire_lock(self) -> bool:
        """获取独占文件锁，证明同机只启动一次。"""
        try:
            self.lock_fd = open(self.lock_file_path, "w")
            fcntl.flock(self.lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.lock_fd.write(f"pid={self.pid} started_at={self.started_at_ms}\n")
            self.lock_fd.flush()
            return True
        except (IOError, BlockingIOError):
            if self.lock_fd:
                try:
                    self.lock_fd.close()
                except Exception:
                    pass
                self.lock_fd = None
            return False

    def release_lock(self):
        """释放独占文件锁。"""
        if self.lock_fd:
            try:
                fcntl.flock(self.lock_fd, fcntl.LOCK_UN)
                self.lock_fd.close()
            except Exception:
                pass
            self.lock_fd = None

    def execute_cycle(self, now_ms: Optional[int] = None, candle_4h_feed: Optional[dict] = None,
                      bar_15m_feed: Optional[dict] = None, trail_d: Optional[float] = None) -> dict:
        """执行单轮推进（幂等且具备因果防线）：
        1. 撮合待开仓挂单（paper_pending -> open_position）；
        2. 推进持仓 4h 跟踪止损线计算与单调上移（paper_stop_history）；
        3. 推进 15m 明细周期退出检查（跌穿生效止损即时退出）。
        """
        now = int(now_ms or time.time() * 1000)
        cycle_res = {
            "cycle_time_ms": now,
            "arm": self.arm,
            "pending_matched": 0,
            "trailing_updates": 0,
            "exits_triggered": 0,
            "errors": []
        }

        # 1. 撮合挂单
        try:
            match_res = PE.advance_pending(T=now, log=lambda *args: None)
            arm_stat = match_res.get("by_arm", {}).get(self.arm, {})
            matched = arm_stat.get("filled", 0)
            cycle_res["pending_matched"] = matched
        except Exception as exc:
            cycle_res["errors"].append(f"match_pending_failed: {exc}")

        # 2. 持仓跟踪与明细退出检查
        con_paper = L.paper_conn(self.arm, ro=True)
        open_positions = []
        try:
            cur = con_paper.execute(
                "SELECT position_id, symbol, stop_price, entry_price, entry_ts, earliest_fill_ms FROM paper_position "
                "WHERE account_id=? AND status='open' AND COALESCE(voided,0)=0",
                (f"paper43_{self.arm}",)
            )
            open_positions = [dict(r) for r in cur.fetchall()]
        except Exception as exc:
            cycle_res["errors"].append(f"query_positions_failed: {exc}")
        finally:
            con_paper.close()

        if open_positions:
            con_mkt = None
            try:
                for pos in open_positions:
                    pos_id = pos["position_id"]
                    sym = pos["symbol"]

                    # A) 4h 跟踪线更新
                    c4h = candle_4h_feed
                    if c4h is None:
                        if con_mkt is None:
                            con_mkt = L.market_conn()
                        c4h = PE.last_closed_4h(con_mkt, sym, upto_ms=now)

                    if c4h:
                        try:
                            adv = PE.advance_position_trailing(
                                self.arm, pos_id, c4h, now_ms=now, decision_at=now, trail_d=trail_d
                            )
                            if adv.get("ok") and adv.get("stop_updated"):
                                cycle_res["trailing_updates"] += 1
                            if adv.get("exit_triggered"):
                                cycle_res["exits_triggered"] += 1
                                continue  # 已在此处平仓，跳过后续明细检查
                        except Exception as exc:
                            cycle_res["errors"].append(f"advance_trailing_pos_{pos_id}_failed: {exc}")

                    # B) 15m 明细周期退出检查（对齐回测因果约定，必须发生在入场 bar 之后）
                    b15 = bar_15m_feed
                    if b15 is None:
                        if con_mkt is None:
                            con_mkt = L.market_conn()
                        min_t = int(pos.get("earliest_fill_ms") or pos.get("entry_ts") or 0)
                        cur_b15 = None
                        try:
                            cur_b15 = con_mkt.execute(
                                "SELECT symbol, timeframe, open_time, close_time, open, high, low, close, available_at "
                                "FROM candles WHERE exchange='binance' AND market='um' AND symbol=? AND timeframe='15m' "
                                "AND is_closed=1 AND open_time>? AND close_time<=? "
                                "ORDER BY open_time DESC LIMIT 1",
                                (sym, min_t, now)
                            ).fetchone()
                        except Exception:
                            try:
                                cur_b15 = con_mkt.execute(
                                    "SELECT symbol, timeframe, open_time, close_time, open, high, low, close, available_at "
                                    "FROM candle_versions WHERE exchange='binance' AND market='um' AND symbol=? AND timeframe='15m' "
                                    "AND is_closed=1 AND open_time>? AND close_time<=? "
                                    "ORDER BY open_time DESC LIMIT 1",
                                    (sym, min_t, now)
                                ).fetchone()
                            except Exception:
                                cur_b15 = None
                        b15 = dict(cur_b15) if cur_b15 else None

                    if b15:
                        try:
                            exit_chk = PE.check_position_exit_on_bar(
                                self.arm, pos_id, b15, now_ms=now, decision_at=now
                            )
                            if exit_chk.get("exit_triggered"):
                                cycle_res["exits_triggered"] += 1
                        except Exception as exc:
                            cycle_res["errors"].append(f"exit_check_pos_{pos_id}_failed: {exc}")
            finally:
                if con_mkt is not None:
                    try:
                        con_mkt.close()
                    except Exception:
                        pass

        # 更新全局状态统计
        with _STATUS_LOCK:
            st = _STATUS.setdefault(self.arm, {})
            st["last_cycle_ms"] = now
            st["cycle_count"] = st.get("cycle_count", 0) + 1
            st["pending_matched_count"] = st.get("pending_matched_count", 0) + cycle_res["pending_matched"]
            st["stops_updated_count"] = st.get("stops_updated_count", 0) + cycle_res["trailing_updates"]
            st["exits_triggered_count"] = st.get("exits_triggered_count", 0) + cycle_res["exits_triggered"]
            if cycle_res["errors"]:
                st["last_error"] = "; ".join(cycle_res["errors"])
                st["health"] = "degraded"
            else:
                st["health"] = "healthy"
            _save_status(self.arm, st)

        return cycle_res

    def run_forever(self):
        """常驻执行循环。"""
        # ── 启动前配置自检（SYSTEM-CONTRACT-01 / B-04 阻塞闭环）──────────────
        cfg = PE.approved_params()
        if not cfg.get("_config_valid", False):
            msg = f"startup_config_check_failed: {cfg.get('_config_error')}"
            with _STATUS_LOCK:
                _STATUS[self.arm]["status"] = "failed_config_check"
                _STATUS[self.arm]["last_error"] = msg
                _STATUS[self.arm]["health"] = "error"
                _save_status(self.arm, _STATUS[self.arm])
            raise RuntimeError(msg)

        if not self.acquire_lock():
            msg = f"Daemon for arm {self.arm} already running on this machine (file lock occupied: {self.lock_file_path})"
            with _STATUS_LOCK:
                _STATUS[self.arm]["status"] = "failed_lock_occupied"
                _STATUS[self.arm]["last_error"] = msg
                _STATUS[self.arm]["health"] = "error"
                _save_status(self.arm, _STATUS[self.arm])
            raise RuntimeError(msg)

        with _STATUS_LOCK:
            _STATUS[self.arm]["status"] = "running"
            _STATUS[self.arm]["running"] = True
            _save_status(self.arm, _STATUS[self.arm])

        try:
            while not self._stop_event.is_set():
                res = self.execute_cycle()
                if res.get("pending_matched") or res.get("trailing_updates") or res.get("exits_triggered") or res.get("errors"):
                    print(f"[{time.strftime('%X')}] [arm={self.arm}] cycle result: {res}", flush=True)
                self._stop_event.wait(self.poll_interval_sec)
        except Exception as exc:
            with _STATUS_LOCK:
                _STATUS[self.arm]["last_error"] = str(exc)
                _STATUS[self.arm]["health"] = "stopped_with_error"
                _save_status(self.arm, _STATUS[self.arm])
        finally:
            self.release_lock()
            with _STATUS_LOCK:
                _STATUS[self.arm]["status"] = "stopped"
                _STATUS[self.arm]["running"] = False
                _save_status(self.arm, _STATUS[self.arm])

    def start_background(self):
        """在后台线程中启动常驻执行。"""
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self.run_forever, daemon=True, name=f"PaperDaemon-{self.arm}")
        self._thread.start()

    def stop(self):
        """停止常驻执行。"""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=3.0)
            self._thread = None
        self.release_lock()


def get_daemon_status(arm: str = "3a") -> dict:
    """获取指定臂守护进程的最新状态（优先跨进程读取状态文件并核验证 PID 存活）。"""
    p = _status_file_path(arm)
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            pid = data.get("pid")
            is_alive = False
            if pid:
                try:
                    os.kill(int(pid), 0)
                    is_alive = True
                except (OSError, ProcessLookupError):
                    is_alive = False
            if is_alive and data.get("running"):
                return data
            elif not is_alive and data.get("running"):
                data["running"] = False
                data["status"] = "stopped_uncleanly"
                data["health"] = "dead"
                return data
            return data
        except Exception:
            pass
    with _STATUS_LOCK:
        st = _STATUS.get(arm)
        if not st:
            return {
                "arm": arm,
                "status": "not_started",
                "running": False,
                "health": "idle",
                "last_cycle_ms": None,
                "cycle_count": 0
            }
        return dict(st)
