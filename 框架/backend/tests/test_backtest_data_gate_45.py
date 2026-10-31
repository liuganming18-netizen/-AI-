#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GM-BACKTEST-4H-GATE-ENTRYPOINT-FIX-45｜4H 数据完整性通道 + runner 接线的专项测试。

本模块要钉死的一件事
────────────────────
43 号审计用三重证据（函数签名 / 快照键集 / AST）证明：4H 序列**从来没有进过门禁快照**，
而它恰恰是 ATR、箱体宽度、跟踪止损的真实输入。于是「门禁 PASS」这句话在 45 号之前
**不覆盖 4H**——1H/15M 再干净，也管不住一根 `high < low` 的 4H bar 把箱体宽度算成负数。

45 号在门禁里加了一条**辅助序列（aux）通道**，并把 `_run_event_trend` 的 `df_4h`
接进去。本模块按工单 §五 的 12 条强制测试逐项落地。

分组（对应工单 §五 编号）
────────────────────────
  T01  4H 正常数据允许进入后续校验（不得被新判据误伤）
  T02  4H `high < low` 双模式硬阻断
  T03  4H 缺失 / 乱序 / 非有限 / 缺口 / 未收盘 / 离网格
  T04  4H 异常对箱体与 ATR 的影响被门禁捕获（调真实 `check_and_form_box`）
  T05  未闭合 5m OI 桶不被读取（38 号因果约束不退化）
  T06  OI 到达时间缺失或回填保持 PIT_UNPROVEN
  T07  TRUSTED 未证明时不得撮合
  T08  ISOLATED 合法研究运行不得升级 TRUSTED
  T09  严重异常即使 ISOLATED 也不得撮合
  T10  `run.json` 保留门禁、拒绝原因与严重异常审计字段（含 4H 覆盖声明）
  T11  31/37/38/39/40 号契约不退化（旧快照零新增判据）
  T12  两条 runner 执行路径均接受验证，且门禁先于引擎

运行：cd 框架/backend && python3 -m unittest tests.test_backtest_data_gate_45 -v
"""
from __future__ import annotations

import inspect
import sys
import unittest
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
STRATEGY_DIR = BACKEND_DIR.parent.parent / "策略"
for _p in (str(BACKEND_DIR), str(STRATEGY_DIR), str(STRATEGY_DIR / "WyckoffMaster")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import backtest_data_gate as G          # noqa: E402
import backtest_gate_wiring as W        # noqa: E402
import backtest_runner as R             # noqa: E402

HOUR = 3_600_000
Q = 900_000                     # 15m
FOUR_H = 14_400_000             # 4h
DAY = 86_400_000
OI_IV = 300_000                 # 5m OI 观测步长

# 2026-09-21T00:00:00Z：同时落在 1h / 15m / 5m / **4h** 网格上
# （1_789_948_800_000 % 14_400_000 == 0，aux 的网格对齐判据依赖这一点）
WIN_START = 1_789_948_800_000
WARMUP = 2                      # 1h 预热根数（合成测试够用）
AUX_WARMUP = 220                # 与 `EventTrendRunner.load_symbol_data` 一致


# ══════════════════════ 合成数据构造器（本模块自足，不 import 其它工单的测试）══════════════════════

def mk_bars(start_ms, count, iv, price=100.0, closed=1, step=0.5):
    """等间隔合成 K 线；数值自洽（high >= low，open/close 在区间内）。"""
    out = []
    for i in range(count):
        ot = start_ms + i * iv
        o = price + i * step
        out.append({"open_time": ot, "close_time": ot + iv - 1,
                    "open": o, "high": o + 1.0, "low": o - 1.0, "close": o + 0.25,
                    "volume": 10.0 + i, "is_closed": closed})
    return out


def mk_oi(start_ms, count, iv=OI_IV, base=1_000_000.0, with_pit=True):
    out = []
    for i in range(count):
        ts = start_ms + i * iv
        row = {"ts": ts, "open_interest": base + i}
        if with_pit:
            row["available_at"] = ts + iv + 2_400
        out.append(row)
    return out


def mk_funding(win_start, win_end, interval=8 * HOUR, lookback=7 * DAY, rate=0.0001):
    t = win_start - lookback
    t -= t % interval
    out = []
    while t < win_end:
        out.append({"funding_time": t, "funding_rate": rate,
                    "mark_price": 100.0, "rate_type": "settled"})
        t += interval
    return out


def base_snapshot(win_days=2, **over):
    """生产基线口径：1h 信号（含预热）+ 15m 执行 + OI 5m 全覆盖带 PIT + 资金费 8h。

    与 31 号同构，但**不含任何 aux 键** —— 这就是 17/27/31/40 号手工快照的样子。
    """
    win_end = WIN_START + win_days * DAY
    sig_start = WIN_START - WARMUP * HOUR
    sig_n = WARMUP + win_days * 24
    snap = {
        "exchange": "binance", "market": "um", "symbol": "BTCUSDT",
        "signal_timeframe": "1h", "exec_timeframe": "15m",
        "win_start_ms": WIN_START, "win_end_ms": win_end, "warmup_bars": WARMUP,
        "signal_bars": mk_bars(sig_start, sig_n, HOUR),
        "exec_bars": mk_bars(WIN_START, win_days * 96, Q),
        "oi_rows": mk_oi(sig_start, sig_n * 12),
        "funding_rows": mk_funding(WIN_START, win_end),
    }
    snap.update(over)
    return snap


def mk_aux_bars(win_days=2, count_before=AUX_WARMUP, atr=1.0,
                high=101.5, low=98.5, closed=1):
    """4H 辅助序列：窗口前 `count_before` 根预热 + 窗口内 `win_days*6` 根。

    收盘价做成小幅锯齿（100.2 / 99.8 交替）⇒ 方向效率 ER ≈ 0.024（远低于 0.30），
    上下沿固定 101.5 / 98.5 ⇒ 箱体宽度 3.0，配 atr=1.0 得 width_atr = 3.0，
    正好落在 `[2.0, 8.0]` 带内 ⇒ **干净数据下箱体应当形成**。
    """
    start = WIN_START - count_before * FOUR_H
    total = count_before + win_days * 6
    out = []
    for i in range(total):
        ot = start + i * FOUR_H
        out.append({
            "open_time": ot, "close_time": ot + FOUR_H - 1,
            "open": 100.0, "high": high, "low": low,
            "close": 100.2 if i % 2 == 0 else 99.8,
            "volume": 500.0, "is_closed": closed,
            # 真实调用链里 `df_4h` 带着在全历史上算好的 atr 列
            "atr": atr,
        })
    return out


def aux_snapshot(win_days=2, **over):
    """基线 + 一条**声明为必需**的 4H 辅助序列（= 45 号之后的生产形态）。"""
    snap = base_snapshot(win_days)
    snap.update({
        "aux_required": True,
        "aux_timeframe": "4h",
        "aux_role": G.AUX_ROLE_ATR_BOX_TRAIL,
        "aux_warmup_bars": AUX_WARMUP,
        "aux_bars": mk_aux_bars(win_days),
    })
    snap.update(over)
    return snap


def aux_checks(v):
    return [c for c in v["checks"] if str(c["id"]).startswith("AUX_")]


def aux_failing(v):
    return [c["id"] for c in aux_checks(v) if c["status"] != G.PASS]


def bar4h(snap, idx=-3):
    return snap["aux_bars"][idx]


# ══════════════════════ T01：4H 正常数据允许进入后续校验 ══════════════════════

class T01_AuxClean(unittest.TestCase):

    def test_clean_aux_produces_no_failing_aux_check(self):
        """干净 4H 不得被新判据误伤：aux 判据全 PASS，verdict 仍 PASS。"""
        v = G.evaluate(aux_snapshot())
        self.assertEqual(aux_failing(v), [], f"干净 4H 被判异常：{aux_failing(v)}")
        ids = [c["id"] for c in aux_checks(v)]
        self.assertIn("AUX_ATR_OK", ids)
        self.assertIn("AUX_OHLC_OK", ids)
        self.assertEqual(v["verdict"], G.PASS)
        self.assertEqual(v["claim_level"], "TRUSTED")
        self.assertTrue(v["run_allowed"])
        self.assertTrue(v["data_qualifies_for_trusted"])

    def test_aux_declaration_visible_in_decision(self):
        """判定里必须能直接读到「这份判定到底看没看 4H」，不靠人去猜。"""
        v = G.evaluate(aux_snapshot())
        self.assertEqual(v["aux"]["declared"], True)
        self.assertEqual(v["aux"]["timeframe"], "4h")
        self.assertEqual(v["aux"]["role"], G.AUX_ROLE_ATR_BOX_TRAIL)
        self.assertEqual(v["aux"]["required"], True)
        self.assertEqual(v["aux"]["policy_version"], G.AUX_POLICY_VERSION)
        self.assertEqual(v["aux"]["warmup_bars"], AUX_WARMUP)
        self.assertEqual(v["aux"]["bars"], AUX_WARMUP + 12)
        self.assertEqual(v["aux"]["consumed_by"], ["atr", "box_width", "trailing_stop"])

    def test_snapshot_from_frames_always_requires_aux(self):
        """接线层必须恒置 `aux_required=True`：忘记传 4H 的后果是降级，不是静默通过。"""
        snap = W.snapshot_from_frames(
            symbol="BTCUSDT", win_start_ms=WIN_START, win_end_ms=WIN_START + 2 * DAY,
            df_signal=None, df_exec=None, oi_rows=[], funding_events=[])
        self.assertTrue(snap["aux_required"])
        self.assertIsNone(snap["aux_timeframe"])
        self.assertEqual(snap["aux_bars"], [])
        self.assertIn("aux=未提供", snap["provenance"])

    def test_frames_snapshot_with_df_aux_carries_bars(self):
        """传了 df_aux ⇒ 快照里必须真的有那些 bar，且 provenance 写明根数。"""
        pd = __import__("pandas")
        df = pd.DataFrame(mk_aux_bars())
        snap = W.snapshot_from_frames(
            symbol="BTCUSDT", win_start_ms=WIN_START, win_end_ms=WIN_START + 2 * DAY,
            df_signal=None, df_exec=None, df_aux=df)
        self.assertEqual(len(snap["aux_bars"]), len(df))
        self.assertEqual(snap["aux_timeframe"], "4h")
        self.assertIn(f"aux=4h×{len(df)}", snap["provenance"])
        v = G.evaluate(snap)
        self.assertEqual(aux_failing(v), [])


# ══════════════════════ T02：4H `high < low` 双模式硬阻断 ══════════════════════

class T02_AuxSevereBothModes(unittest.TestCase):

    def test_high_below_low_blocked_in_trusted_and_isolated(self):
        snap = aux_snapshot()
        bar4h(snap).update({"high": 50.0})          # low 恒为 98.5 ⇒ high < low
        for mode in (G.MODE_TRUSTED, G.MODE_ISOLATED):
            v = G.evaluate(snap, mode=mode)
            self.assertFalse(v["run_allowed"], f"{mode} 下 4H high<low 未阻断")
            self.assertTrue(v["severe_blocked"], f"{mode} 下未标记 severe")
            self.assertIn("AUX_OHLC_INVALID", v["severe_check_ids"])
            self.assertEqual(v["claim_level"], "ISOLATED_RESEARCH")
            self.assertFalse(v["trusted_backtest_allowed"])
            hit = [c for c in aux_checks(v) if c["id"] == "AUX_OHLC_INVALID"][0]
            self.assertEqual(hit["severity"], G.SEVERITY_SEVERE)

    def test_aux_severe_is_attributed_to_aux_not_signal(self):
        """必须是 AUX_ 前缀的判据在报警 —— 否则等于又拿 1H 的结论冒充 4H。"""
        snap = aux_snapshot()
        bar4h(snap).update({"high": 50.0})
        v = G.evaluate(snap)
        plain = [c for c in v["checks"] if c["id"] == "OHLC_INVALID"]
        self.assertEqual(plain, [], "信号/执行序列是干净的，不该出 OHLC_INVALID")
        self.assertTrue([c for c in aux_checks(v) if c["id"] == "AUX_OHLC_INVALID"])

    def test_aux_price_zero_and_non_finite_are_severe(self):
        for mut in ({"low": 0.0, "high": 0.0},
                    {"close": float("nan")},
                    {"high": float("inf")},
                    {"open": None}):
            snap = aux_snapshot()
            bar4h(snap).update(mut)
            v = G.evaluate(snap, mode=G.MODE_ISOLATED)
            self.assertFalse(v["run_allowed"], f"{mut} 在隔离模式未阻断")
            self.assertIn("AUX_OHLC_INVALID", v["severe_check_ids"], f"{mut} 未判 severe")


# ══════════════════════ T03：缺失 / 乱序 / 非有限 / 缺口 / 未收盘 / 离网格 ══════════════════════

class T03_AuxStructure(unittest.TestCase):

    def test_absent_aux_is_unproven_not_pass(self):
        """声明必需却没有 bar ⇒ UNPROVEN（不是 FAIL、更不是 PASS）。"""
        snap = aux_snapshot(aux_bars=[])
        v = G.evaluate(snap)
        self.assertEqual(v["verdict"], G.UNPROVEN)
        self.assertIn("AUX_ABSENT", [c["id"] for c in aux_checks(v)])
        self.assertFalse(v["run_allowed"])                      # trusted 拒绝
        self.assertFalse(v["data_qualifies_for_trusted"])
        # 隔离研究仍可跑（没有 severe），但主张等级不得升格
        vi = G.evaluate(snap, mode=G.MODE_ISOLATED)
        self.assertTrue(vi["run_allowed"])
        self.assertEqual(vi["claim_level"], "ISOLATED_RESEARCH")

    def test_disordered_aux_is_fail(self):
        snap = aux_snapshot()
        b = snap["aux_bars"]
        b[10], b[11] = b[11], b[10]
        v = G.evaluate(snap)
        self.assertEqual(v["verdict"], G.FAIL)
        hit = [c for c in aux_checks(v) if c["id"] == "AUX_ORDER"]
        self.assertTrue(hit, "乱序未被捕获")
        self.assertEqual(hit[0]["status"], G.FAIL)

    def test_duplicate_aux_is_fail(self):
        snap = aux_snapshot()
        b = snap["aux_bars"]
        b.insert(20, dict(b[19]))
        v = G.evaluate(snap)
        self.assertEqual(v["verdict"], G.FAIL)
        self.assertIn("AUX_DUPLICATE", [c["id"] for c in aux_checks(v)])

    def test_gap_aux_is_unproven(self):
        """缺一根 4H ⇒ 42 根箱体窗口跨过更长的时间跨度 ⇒ 箱体口径不可证明。"""
        snap = aux_snapshot()
        del snap["aux_bars"][100]
        v = G.evaluate(snap)
        self.assertEqual(v["verdict"], G.UNPROVEN)
        hit = [c for c in aux_checks(v) if c["id"] == "AUX_GAP"]
        self.assertTrue(hit, "缺口未被捕获")
        self.assertEqual(hit[0]["evidence"]["missing_bars"], 1)

    def test_unclosed_aux_bar_is_fail(self):
        snap = aux_snapshot()
        bar4h(snap, -1)["is_closed"] = 0
        v = G.evaluate(snap)
        self.assertEqual(v["verdict"], G.FAIL)
        self.assertIn("AUX_UNCLOSED", [c["id"] for c in aux_checks(v)])

    def test_missing_is_closed_is_unproven(self):
        """源数据没给 is_closed ⇒ 收盘状态无法证明 ⇒ UNPROVEN，不猜一个 1 出来。"""
        snap = aux_snapshot()
        for b in snap["aux_bars"]:
            b.pop("is_closed", None)
        v = G.evaluate(snap)
        self.assertEqual(v["verdict"], G.UNPROVEN)
        hit = [c for c in aux_checks(v) if c["id"] == "AUX_UNCLOSED"]
        self.assertEqual(hit[0]["status"], G.UNPROVEN)

    def test_off_grid_aux_is_fail(self):
        snap = aux_snapshot()
        b = bar4h(snap, -1)
        b["open_time"] += 1_000
        b["close_time"] = b["open_time"] + FOUR_H - 1
        v = G.evaluate(snap)
        self.assertEqual(v["verdict"], G.FAIL)
        self.assertIn("AUX_ALIGNMENT", [c["id"] for c in aux_checks(v)])

    def test_aux_timeframe_same_as_signal_is_rejected(self):
        """工单 §四.A 明文：不得用「只检查 1H」代替 4H 检查。"""
        snap = aux_snapshot(aux_timeframe="1h")
        v = G.evaluate(snap)
        self.assertEqual(v["verdict"], G.FAIL)
        hit = [c for c in aux_checks(v) if c["id"] == "AUX_TF_MISMATCH"]
        self.assertTrue(hit, "把 1H 当 aux 未被拒绝")
        self.assertIn("不是检查 4H", hit[0]["detail"])

    def test_insufficient_warmup_before_window_is_unproven(self):
        """窗口前不足 42 根 ⇒ 箱体回看窗不成立 ⇒ UNPROVEN。"""
        snap = aux_snapshot()
        snap["aux_bars"] = [b for b in snap["aux_bars"]
                            if b["open_time"] >= WIN_START - 10 * FOUR_H]
        v = G.evaluate(snap)
        self.assertEqual(v["verdict"], G.UNPROVEN)
        hit = [c for c in aux_checks(v) if c["id"] == "AUX_COVERAGE"]
        self.assertTrue(hit)
        self.assertEqual(hit[0]["evidence"]["rows_before_window"], 10)


# ══════════════════════ T04：4H 异常对箱体与 ATR 的影响被门禁捕获 ══════════════════════

class T04_BoxAndAtrImpact(unittest.TestCase):
    """用**真实的** `EventStateMachine.check_and_form_box` 证明影响确实存在。

    这一组不做「门禁自己说自己对」的自证：先证明污染真的会改变箱体形成结果，
    再证明门禁在污染进入计算之前就把它拦下。
    """

    @classmethod
    def setUpClass(cls):
        from WyckoffMaster.event_trend_v0_1.state_machine import EventStateMachine
        cls.SM = EventStateMachine(symbol="BTCUSDT")
        cls.close_ms = WIN_START + 2 * DAY

    def _form(self, bars):
        import numpy as np
        w = bars[-42:]
        highs = np.array([b["high"] for b in w], dtype=np.float64)
        lows = np.array([b["low"] for b in w], dtype=np.float64)
        closes = np.array([b["close"] for b in w], dtype=np.float64)
        atr = float(w[-1]["atr"])
        return self.SM.check_and_form_box(self.close_ms, highs, lows, closes, atr)

    def test_clean_aux_forms_box(self):
        """基准：干净 4H 下箱体**应当**形成，否则后面的对照没有意义。"""
        box = self._form(mk_aux_bars())
        self.assertIsNotNone(box, "干净 4H 下箱体未形成，对照组失效")
        self.assertAlmostEqual(box.box.box_width_atr, 3.0, places=6)
        self.assertLessEqual(box.box.efficiency_ratio_er, 0.30)

    def test_zero_atr_kills_box_and_gate_catches_it(self):
        """atr=0 ⇒ `check_and_form_box` 直接 return None ⇒ 静默 0 候选。门禁必须先拦。"""
        bars = mk_aux_bars(atr=0.0)
        self.assertIsNone(self._form(bars), "atr=0 竟然还能形成箱体")

        snap = aux_snapshot(aux_bars=bars)
        v = G.evaluate(snap)
        self.assertEqual(v["verdict"], G.FAIL)
        hit = [c for c in aux_checks(v) if c["id"] == "AUX_ATR_INVALID"]
        self.assertTrue(hit, "atr=0 未被门禁捕获")
        self.assertIn("箱体永不形成", hit[0]["detail"])

    def test_nan_atr_kills_box_and_gate_catches_it(self):
        bars = mk_aux_bars(atr=float("nan"))
        self.assertIsNone(self._form(bars))
        v = G.evaluate(aux_snapshot(aux_bars=bars))
        self.assertEqual(v["verdict"], G.FAIL)
        self.assertIn("AUX_ATR_INVALID", [c["id"] for c in aux_checks(v)])

    def test_high_below_low_distorts_box_and_gate_catches_it(self):
        """一根 high<low ⇒ max(high)/min(low) 口径被污染；门禁判 severe 双模式阻断。"""
        bars = mk_aux_bars()
        bars[-5].update({"high": 40.0})            # low 仍是 98.5
        snap = aux_snapshot(aux_bars=bars)
        v = G.evaluate(snap, mode=G.MODE_ISOLATED)
        self.assertFalse(v["run_allowed"])
        self.assertIn("AUX_OHLC_INVALID", v["severe_check_ids"])

    def test_absurd_but_physically_valid_extreme_is_NOT_caught(self):
        """诚实登记未覆盖边界：物理自洽但数量级荒谬的极值**不会**被 OHLC 判据拦下。

        一根 low=60（high=101.5、close=99.8 仍在区间内）会把箱体宽度从 3.0 拉到 41.5 ATR，
        箱体从此永不形成；但它在「high >= low / 价格 > 0 / 有限」三条物理判据下全部合法。
        本单**不**为此加数值阈值（阈值必然与品种/周期耦合，且会误伤真实极端行情）。
        该边界写入 README 的「未解决的跨文件阻断清单」。
        """
        bars = mk_aux_bars()
        bars[-7].update({"low": 60.0})
        box = self._form(bars)
        self.assertIsNone(box, "low=60 竟然还能形成箱体")

        v = G.evaluate(aux_snapshot(aux_bars=bars))
        self.assertEqual(v["verdict"], G.PASS,
                         "本单**没有**给数量级异常加判据；若此处变 FAIL，说明有人偷偷加了阈值")
        self.assertNotIn("AUX_OHLC_INVALID", [c["id"] for c in aux_checks(v)])


# ══════════════════════ T05 / T06：OI 闭合桶因果 + PIT 不升级（38/37 号不退化）══════════════════════

class _MockRepo:
    """可控数据仓库：注入指定的 1h / 15m / OI / 资金费行（38 号同构）。"""

    def __init__(self, sig_rows=None, exec_rows=None, oi_rows=None, fu_rows=None):
        self._sig = sig_rows or []
        self._exec = exec_rows or []
        self._oi = oi_rows or []
        self._fu = fu_rows or []

    def candles(self, exchange, market, symbol, timeframe, start=None, end=None, limit=20000):
        return self._sig if timeframe == "1h" else (self._exec if timeframe == "15m" else [])

    def oi(self, exchange, market, symbol, period="5m", start=None, end=None, limit=20000):
        return self._oi

    def funding(self, exchange, market, symbol, start=None, end=None, limit=2000, prefer="hot"):
        return self._fu


OI_WIN_START = 1_704_067_200_000        # 2024-01-01T00:00:00Z
OI_WARMUP_H = 168
OI_WIN_DAYS = 2


def _oi_fixture(oi_rows):
    win_end = OI_WIN_START + OI_WIN_DAYS * DAY
    sig_start = OI_WIN_START - OI_WARMUP_H * HOUR
    spec = {"symbols": ["BTCUSDT"], "strategy": {"id": "WX_EVENT_TREND_V0_1"},
            "window": {"mode": "explicit", "start_ms": OI_WIN_START, "end_ms": win_end,
                       "warmup_bars_requested": OI_WARMUP_H},
            "signal_params": {}, "exec_params": {}}
    win = {"exchange": "binance", "market": "um", "symbol": "BTCUSDT",
           "signal_timeframe": "1h", "exec_timeframe": "15m",
           "signal_tf": "1h", "exec_tf": "15m",
           "win_start_ms": OI_WIN_START, "win_end_ms": win_end,
           "sig_start_ms": OI_WIN_START, "sig_end_ms": win_end,
           "signal_series_start_ms": sig_start,
           "warmup_bars_requested": OI_WARMUP_H}
    n_sig = OI_WARMUP_H + OI_WIN_DAYS * 24
    sig_rows = mk_bars(sig_start, n_sig, HOUR)
    exec_rows = mk_bars(OI_WIN_START, OI_WIN_DAYS * 96, Q)
    fu_rows = mk_funding(OI_WIN_START, win_end)
    repo = _MockRepo(sig_rows, exec_rows, oi_rows, fu_rows)
    return R.load_inputs(spec, win, repo), sig_rows


def _closed_oi_rows(sig_rows, pit="proven"):
    """每个 1h 决策点前恰好一个**已闭合**的 5m 桶（bucket_end = close - 150s <= close）。"""
    rows = []
    for i, r in enumerate(sig_rows):
        close_ms = int(r["open_time"]) + HOUR
        ts = close_ms - 450_000
        row = {"ts": ts, "open_interest": 1_000.0 + i}
        if pit == "proven":
            row["available_at"] = ts + OI_IV          # == bucket_end，物理最早到达
            row["source"] = "realtime"
        elif pit == "missing":
            row["source"] = "realtime"                # 无 available_at
        elif pit == "backfill":
            row["available_at"] = ts + OI_IV
            row["source"] = "binance.archive.metrics"  # 回填
        elif pit == "late":
            row["available_at"] = ts + OI_IV + 10 * DAY
            row["source"] = "realtime"
        rows.append(row)
    return rows


class T05_OiBucketCausal(unittest.TestCase):

    def test_only_closed_buckets_are_used(self):
        """因果不变式：每个决策点用到的桶，其 bucket_end 必须 <= 该 bar 的收盘时刻。"""
        data, sig_rows = _oi_fixture(_closed_oi_rows(_oi_fixture_rows()))
        closes = [int(r["open_time"]) + HOUR for r in sig_rows]
        obs = [float(x) for x in data["sig_df"]["oi_observed_at"].tolist()]
        used = [(c, o) for c, o in zip(closes, obs) if o == o]      # 排除 NaN
        self.assertTrue(used, "没有任何一个决策点用到了 OI，测试前提失效")
        for c, o in used:
            self.assertLessEqual(o, c, "使用了尚未闭合的 5m 桶")

    def test_unclosed_bucket_is_not_read(self):
        """把某个决策点的闭合桶换成「还没闭合」的桶 ⇒ 该点必须退回更早的闭合桶。"""
        sig_rows = _oi_fixture_rows()
        rows = _closed_oi_rows(sig_rows)
        k = 100
        close_ms = int(sig_rows[k]["open_time"]) + HOUR
        rows[k] = {"ts": close_ms - 150_000, "open_interest": 9_999_999.0,
                   "available_at": close_ms + 150_000, "source": "realtime"}
        data, _ = _oi_fixture(rows)
        obs = [float(x) for x in data["sig_df"]["oi_observed_at"].tolist()]
        val = [float(x) for x in data["sig_df"]["open_interest"].tolist()]
        self.assertLessEqual(obs[k], close_ms, "读了未闭合的桶")
        self.assertNotEqual(val[k], 9_999_999.0, "未闭合桶的数值被采用了")
        self.assertEqual(obs[k], close_ms - 3_750_000, "未退回上一根已闭合桶")


def _oi_fixture_rows():
    sig_start = OI_WIN_START - OI_WARMUP_H * HOUR
    return mk_bars(sig_start, OI_WARMUP_H + OI_WIN_DAYS * 24, HOUR)


class T06_PitNotUpgraded(unittest.TestCase):

    def _status(self, pit):
        rows = _closed_oi_rows(_oi_fixture_rows(), pit=pit)
        data, _ = _oi_fixture(rows)
        return data["coverage"]["oi"]

    def test_proven_case_is_proven(self):
        meta = self._status("proven")
        self.assertEqual(meta["pit_status"], "PIT_PROVEN")
        self.assertEqual(meta["bars_pit_unproven"], 0)

    def test_missing_available_at_stays_unproven(self):
        meta = self._status("missing")
        self.assertEqual(meta["pit_status"], "PIT_UNPROVEN")
        self.assertIn("available_at_missing", meta["pit_unproven_reasons"])

    def test_backfilled_source_stays_unproven(self):
        meta = self._status("backfill")
        self.assertEqual(meta["pit_status"], "PIT_UNPROVEN")
        self.assertIn("backfilled_data_unproven", meta["pit_unproven_reasons"])

    def test_late_available_at_stays_unproven(self):
        meta = self._status("late")
        self.assertEqual(meta["pit_status"], "PIT_UNPROVEN")
        self.assertIn("available_at_after_decision", meta["pit_unproven_reasons"])

    def test_gate_and_stamp_never_upgrade_pit(self):
        """门禁与落盘层都不得把 PIT_UNPROVEN 说成 PIT_PROVEN。"""
        snap = aux_snapshot(oi_rows=mk_oi(WIN_START - WARMUP * HOUR,
                                          (WARMUP + 48) * 12, with_pit=False))
        v = G.evaluate(snap, mode=G.MODE_ISOLATED)
        self.assertEqual(W.pit_status(v), W.PIT_UNPROVEN)
        self.assertEqual(W.stamp(v)["pit_status"], W.PIT_UNPROVEN)
        self.assertEqual(v["claim_level"], "ISOLATED_RESEARCH")


# ══════════════════════ T07 / T08 / T09：三个出口 ══════════════════════

class _FakeStore:
    STATUS_FAILED = "failed"

    def __init__(self):
        self.calls = []

    def set_status(self, run_id, status, **kw):
        self.calls.append({"run_id": run_id, "status": status, **kw})


class T07_T08_T09_Exits(unittest.TestCase):

    def test_trusted_unproven_rejects_before_engine(self):
        """TRUSTED 未证明 ⇒ 抛拒绝，且 `data_gate` 已落到 run.json（不是只在异常里带一句）。"""
        snap = aux_snapshot(aux_bars=[])                     # UNPROVEN
        st = _FakeStore()
        with self.assertRaises(W.DataGateRejected) as ctx:
            W.enforce_snapshot("r45-t07", snap,
                               spec={"data_gate_mode": "trusted"}, store=st)
        self.assertEqual(ctx.exception.decision["run_allowed"], False)
        self.assertEqual(len(st.calls), 1)
        self.assertEqual(st.calls[0]["status"], _FakeStore.STATUS_FAILED)
        self.assertEqual(st.calls[0]["data_gate"]["claim_level"], "ISOLATED_RESEARCH")
        self.assertTrue(st.calls[0]["data_gate"]["unproven_reasons"])

    def test_isolated_valid_run_is_not_upgraded(self):
        """隔离研究可以跑，但主张等级恒为 ISOLATED_RESEARCH、可信出口恒 False。"""
        v = G.evaluate(aux_snapshot(), mode=G.MODE_ISOLATED)
        self.assertTrue(v["run_allowed"])
        self.assertEqual(v["claim_level"], "ISOLATED_RESEARCH")
        self.assertFalse(v["trusted_backtest_allowed"])
        self.assertTrue(v["isolation_required"])
        st = W.stamp(v)
        self.assertEqual(st["data_gate"]["claim_level"], "ISOLATED_RESEARCH")
        self.assertFalse(st["data_gate"]["trusted_backtest_allowed"])

    def test_severe_blocks_even_isolated(self):
        snap = aux_snapshot()
        bar4h(snap).update({"high": 10.0})
        st = _FakeStore()
        with self.assertRaises(W.DataGateRejected):
            W.enforce_snapshot("r45-t09", snap,
                               spec={"data_gate_mode": "isolated_research"}, store=st)
        dg = st.calls[0]["data_gate"]
        self.assertTrue(dg["severe_blocked"])
        self.assertIn("AUX_OHLC_INVALID", dg["severe_check_ids"])
        self.assertFalse(dg["run_allowed"])


# ══════════════════════ T10：run.json 审计字段 ══════════════════════

class T10_RunJsonAuditFields(unittest.TestCase):

    REQUIRED = ("gate", "gate_version", "mode", "verdict", "claim_level",
                "run_allowed", "run_allowed_reason", "severe_blocked",
                "severe_check_ids", "severe_reasons", "severity_policy",
                "isolation_required", "isolation_label", "counts",
                "block_reasons", "unproven_reasons", "checks",
                "verdict_sha256", "input_sha256", "aux")

    def test_stamp_keeps_every_audit_field_including_aux(self):
        """白名单之外的键会被**静默丢弃**（33 号 OBS-33-1 / 43 号 C 节）⇒ 必须显式钉住。"""
        snap = aux_snapshot()
        bar4h(snap).update({"high": 10.0})
        v = G.evaluate(snap, mode=G.MODE_ISOLATED)
        dg = W.stamp(v)["data_gate"]
        missing = [k for k in self.REQUIRED if k not in dg]
        self.assertEqual(missing, [], f"run.json 丢失审计字段：{missing}")
        self.assertEqual(dg["aux"]["policy_version"], G.AUX_POLICY_VERSION)
        self.assertEqual(dg["aux"]["timeframe"], "4h")
        self.assertTrue(dg["aux"]["required"])
        self.assertEqual(dg["aux"]["bars"], AUX_WARMUP + 12)

    def test_rejection_path_carries_aux_and_reasons(self):
        snap = aux_snapshot(aux_bars=[])
        st = _FakeStore()
        with self.assertRaises(W.DataGateRejected):
            W.enforce_snapshot("r45-t10", snap, spec={"data_gate_mode": "trusted"}, store=st)
        kw = st.calls[0]
        self.assertIn("AUX_ABSENT", " ".join(kw["data_gate"]["unproven_reasons"]))
        self.assertEqual(kw["data_gate"]["aux"]["bars"], 0)
        self.assertIn("failure_reason", kw)
        self.assertIn("数据质量门禁拒绝运行", kw["failure_reason"])

    def test_disabled_gate_does_not_pose_as_pass(self):
        """总开关关掉 ⇒ `enabled=False` + 「未执行≠通过」，绝不冒充 PASS。"""
        st = W.stamp({}, enabled=False)
        self.assertFalse(st["data_gate"]["enabled"])
        self.assertNotIn("verdict", st["data_gate"])
        self.assertIn("不等于", st["data_gate"]["note"])
        self.assertEqual(st["pit_status"], W.PIT_UNPROVEN)


# ══════════════════════ T11：31/37/38/39/40 契约不退化 ══════════════════════

class T11_NoRegression(unittest.TestCase):

    def test_version_axes(self):
        self.assertEqual(G.GATE_VERSION, "v3")
        self.assertEqual(G.SEVERITY_POLICY_VERSION, "s1")
        self.assertEqual(G.AUX_POLICY_VERSION, "a1")

    def test_old_snapshot_gains_no_new_check(self):
        """不含 aux 键的老快照：判据集合与判定必须逐条不变（零新增判据）。"""
        v = G.evaluate(base_snapshot())
        self.assertEqual(aux_checks(v), [], "老快照被塞进了 aux 判据")
        self.assertEqual(v["verdict"], G.PASS)
        self.assertEqual(v["claim_level"], "TRUSTED")
        self.assertFalse(v["aux"]["declared"])
        self.assertFalse(v["aux"]["required"])
        self.assertEqual(v["aux"]["bars"], 0)
        self.assertEqual(v["counts"]["checks"], len(v["checks"]))

    def test_aux_cannot_be_turned_off(self):
        """`aux_required` 只能被打开，不能被关掉（fail-closed）。"""
        # 1) policy 想关掉快照已声明的 aux ⇒ 关不掉
        v = G.evaluate(aux_snapshot(), policy={"aux_required": False})
        self.assertTrue(v["aux"]["required"])
        self.assertEqual(v["verdict"], G.PASS)
        # 2) 快照给了 aux bar 却把 required 写成 False ⇒ 照样按必需处理
        snap = aux_snapshot()
        snap["aux_required"] = False
        snap["aux_bars"] = []                       # 声明关了，数据也没给
        self.assertFalse(G.evaluate(snap)["aux"]["required"])
        snap2 = aux_snapshot()
        snap2["aux_required"] = False               # 声明关了，但数据在
        v2 = G.evaluate(snap2)
        self.assertTrue(v2["aux"]["required"], "带了 aux bar 却不检查")
        self.assertEqual(v2["aux"]["bars"], AUX_WARMUP + 12)

    def test_severe_still_blocks_both_modes_for_signal_series(self):
        """31 号结论未变：信号序列 high<low 在两种模式都阻断。"""
        snap = base_snapshot()
        snap["signal_bars"][5].update({"high": 1.0})
        for mode in (G.MODE_TRUSTED, G.MODE_ISOLATED):
            v = G.evaluate(snap, mode=mode)
            self.assertFalse(v["run_allowed"])
            self.assertIn("OHLC_INVALID", v["severe_check_ids"])

    def test_default_policy_keeps_old_behaviour(self):
        """默认 policy 必须让 aux 关闭：否则 17/27/31/40 号手工快照会集体变红。"""
        self.assertIs(G.DEFAULT_POLICY["aux_required"], False)
        self.assertEqual(G.DEFAULT_POLICY["aux_min_bars_before_window"], 42)
        self.assertEqual(G.DEFAULT_POLICY["aux_atr_tail_bars"], 42)


# ══════════════════════ T12：两条 runner 执行路径 ══════════════════════

class T12_TwoRunnerPaths(unittest.TestCase):

    def _src(self, fn):
        return inspect.getsource(fn)

    def test_event_trend_path_passes_df_4h(self):
        src = self._src(R._run_event_trend)
        self.assertIn("snapshot_from_frames", src)
        self.assertIn("df_aux=df_4h", src, "_run_event_trend 没有把 4H 送进门禁")
        self.assertIn('aux_timeframe="4h"', src)
        self.assertIn("aux_warmup_bars=220", src)

    def test_event_trend_gate_precedes_engine(self):
        """门禁必须在 `runner.run_variant_abc`（真正撮合）之前调用。"""
        src = self._src(R._run_event_trend)
        self.assertLess(src.index("_GW.enforce_snapshot"),
                        src.index("runner.run_variant_abc("),
                        "门禁调用在引擎之后 ⇒ 拒绝时已经留下半截产物")

    def test_generic_path_gate_precedes_engine(self):
        src = self._src(R._run)
        self.assertIn("snapshot_from_inputs", src)
        self.assertLess(src.index("_GW.enforce_snapshot"),
                        src.index("BR.EXEC.run_portfolio_execution("))

    def test_generic_path_does_not_consume_4h(self):
        """通用路径不读 4H ⇒ 它不该声明 aux（声明了反而是伪造覆盖）。"""
        src = self._src(R._run)
        self.assertNotIn("df_aux", src)
        self.assertNotIn("df_4h", src)

    def test_generic_path_snapshot_declares_no_aux(self):
        data = {"sig_df": None, "exec_df": None, "funding_events": [], "oi_rows": [],
                "coverage": {}}
        snap = W.snapshot_from_inputs(
            {"win_start_ms": WIN_START, "win_end_ms": WIN_START + 2 * DAY,
             "signal_timeframe": "1h", "exec_timeframe": "15m",
             "warmup_bars_requested": 0}, data)
        self.assertFalse(snap.get("aux_required"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
