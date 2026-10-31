#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GM-BACKTEST-OHLC-SEVERE-GATE-31｜严重 OHLC 分级 + isolated 出口阻断的专项测试。

本模块要钉死的一件事
────────────────────
28 号独立验证发现（K3=False）：`isolated_research` 出口把 `run_allowed` 硬编码为 `True`，
与 `verdict` 完全解耦 ⇒ `high<low`、价格 <= 0、OHLC 为 None 的序列在隔离模式下照样跑完。
31 号在门禁内部给 OHLC 异常加了**严重度**，并让 severe 在两种出口都阻断。

九族：
  A 严重判据     —— high<low / 任一价格<=0 / 缺失 / NaN / ±Inf / 不可转，逐项都必须 severe
  B 非严重判据   —— open|close 越界、volume<0 必须是 normal（隔离研究仍可跑）
  C 混合故障     —— 同一行、同一序列、跨序列（信号/执行）都要能分别定性
  D 边界价格     —— 极小正价、high==low、open==low、close==high、容差内浮点噪声
  E isolated 出口 —— severe 必须拒；normal-only 放行但 verdict 仍 FAIL、主张恒为隔离研究
  F trusted 出口 —— 未改：FAIL/UNPROVEN 一律拒，只有 PASS 放行
  G OI_PIT       —— 隔离研究可跑，但不得获得可信资格（任务 5）
  H 可解释性     —— severity / 行计数 / 原因分布 / 出口理由都要能从产物直接读到
  I 不回归       —— 27 号周期判据、OI/资金费三态、GATE_VERSION、runner 未接线

运行：cd 框架/backend && python3 -m unittest tests.test_backtest_data_gate_31 -v
"""
import sys
import unittest
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

import backtest_data_gate as G  # noqa: E402

HOUR = 3_600_000
Q = 900_000                       # 15m
DAY = 86_400_000
OI_IV = 300_000                   # OI 观测步长 5m
WIN_START = 1_789_948_800_000     # 2026-09-21T00:00:00Z，落在 1h / 15m / 5m 网格上
WARMUP = 2                        # 2 根 1h 预热（合成测试够用）


# ══════════════════════ 合成数据构造器（本模块自足，不 import 17/27 测试）══════════════════════

def mk_bars(start_ms, count, iv, price=100.0, closed=1, step=0.5):
    """等间隔合成 K 线；数值自洽（high>=low，open/close 在区间内）。"""
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
    """生产基线口径：1h 信号（含预热）+ 15m 执行 + OI 5m 全覆盖带 PIT + 资金费 8h。"""
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


def ohlc_checks(v):
    return [c for c in v["checks"] if c["id"] in ("OHLC_OK", "OHLC_INVALID")]


def run(bar_mutate=None, series="exec", **snap_over):
    """造一份快照 → 改一根 bar → 跑 evaluate（默认 trusted）。"""
    snap = base_snapshot(**snap_over)
    if bar_mutate is not None:
        bar_mutate(snap["%s_bars" % series][4])
    return G.evaluate(snap)


# ══════════════════════ A 严重判据：物理上不可能的价格 ══════════════════════

class TestSevereClassification(unittest.TestCase):
    """这些行不是「数值不合逻辑」，是「价格本身不存在」⇒ 隔离模式也必须拒。"""

    def _assert_severe(self, mutate, why_substr):
        v = run(mutate)
        self.assertEqual(v["verdict"], G.FAIL)
        hits = [c for c in v["checks"] if c["id"] == "OHLC_INVALID"]
        self.assertTrue(hits, "未触发 OHLC_INVALID")
        self.assertEqual(hits[0]["severity"], G.SEVERITY_SEVERE)
        self.assertEqual(hits[0]["evidence"]["severe_rows"], 1)
        self.assertEqual(hits[0]["evidence"]["normal_rows"], 0)
        blob = hits[0]["detail"] + str(hits[0]["evidence"])
        self.assertIn(why_substr, blob)
        return v

    def test_high_below_low_is_severe(self):
        self._assert_severe(lambda b: b.update({"high": b["low"] - 5}), "high < low")

    def test_low_zero_is_severe(self):
        self._assert_severe(lambda b: b.update({"low": 0.0}), "价格 <= 0")

    def test_high_zero_is_severe(self):
        self._assert_severe(lambda b: b.update({"high": 0.0, "low": 0.0}), "价格 <= 0")

    def test_open_zero_is_severe(self):
        self._assert_severe(lambda b: b.update({"open": 0.0}), "价格 <= 0")

    def test_close_negative_is_severe(self):
        self._assert_severe(lambda b: b.update({"close": -1.0}), "价格 <= 0")

    def test_missing_open_is_severe(self):
        self._assert_severe(lambda b: b.update({"open": None}), "空值")

    def test_missing_low_is_severe(self):
        self._assert_severe(lambda b: b.update({"low": None}), "空值")

    def test_nan_high_is_severe(self):
        v = self._assert_severe(lambda b: b.update({"high": float("nan")}), "空值")
        self.assertEqual(v["no_fabrication"]["price_repaired_rows"], 0)

    def test_pos_inf_close_is_severe(self):
        self._assert_severe(lambda b: b.update({"close": float("inf")}), "空值")

    def test_neg_inf_low_is_severe(self):
        self._assert_severe(lambda b: b.update({"low": float("-inf")}), "空值")

    def test_non_numeric_price_is_severe(self):
        self._assert_severe(lambda b: b.update({"high": "abc"}), "空值")


# ══════════════════════ B 非严重：数值不合逻辑，但价格存在 ══════════════════════

class TestNormalClassification(unittest.TestCase):

    def _assert_normal(self, mutate, why_substr):
        v = run(mutate)
        self.assertEqual(v["verdict"], G.FAIL, "普通异常仍必须是 FAIL")
        hits = [c for c in v["checks"] if c["id"] == "OHLC_INVALID"]
        self.assertTrue(hits)
        self.assertEqual(hits[0]["severity"], G.SEVERITY_NORMAL)
        self.assertEqual(hits[0]["evidence"]["severe_rows"], 0)
        self.assertEqual(hits[0]["evidence"]["normal_rows"], 1)
        self.assertIn(why_substr, hits[0]["detail"] + str(hits[0]["evidence"]))
        return v

    def test_open_above_high_is_normal(self):
        self._assert_normal(lambda b: b.update({"open": b["high"] + 5}), "open 越出")

    def test_close_below_low_is_normal(self):
        self._assert_normal(lambda b: b.update({"close": b["low"] - 5}), "close 越出")

    def test_negative_volume_is_normal(self):
        self._assert_normal(lambda b: b.update({"volume": -1.0}), "volume < 0")

    def test_missing_volume_is_not_anomaly(self):
        """volume 缺失（None）在 17 号口径里不算异常，本单不得顺手升级为 severe。"""
        v = run(lambda b: b.update({"volume": None}))
        self.assertNotIn("OHLC_INVALID", [c["id"] for c in v["checks"]])


# ══════════════════════ C 混合故障 ══════════════════════

class TestMixedFaults(unittest.TestCase):

    def test_same_row_severe_wins_over_normal(self):
        """一行同时有 severe 与 normal 项：必须按 severe 记，不允许被普通项稀释。"""
        def mut(b):
            b["high"] = b["low"] - 5      # severe: high < low
            b["close"] = b["low"] - 50    # normal: close 越界
        v = run(mut)
        hit = [c for c in v["checks"] if c["id"] == "OHLC_INVALID"][0]
        self.assertEqual(hit["severity"], G.SEVERITY_SEVERE)
        self.assertEqual(hit["evidence"]["severe_rows"], 1)
        self.assertEqual(hit["evidence"]["normal_rows"], 0)
        self.assertIn("high < low", hit["detail"] + str(hit["evidence"]))

    def test_severe_and_normal_rows_counted_separately(self):
        snap = base_snapshot()
        snap["exec_bars"][4]["high"] = snap["exec_bars"][4]["low"] - 5     # severe
        snap["exec_bars"][6]["volume"] = -1.0                              # normal
        v = G.evaluate(snap)
        hit = [c for c in v["checks"] if c["id"] == "OHLC_INVALID"][0]
        self.assertEqual(hit["evidence"]["bad_rows"], 2)
        self.assertEqual(hit["evidence"]["severe_rows"], 1)
        self.assertEqual(hit["evidence"]["normal_rows"], 1)
        self.assertEqual(hit["severity"], G.SEVERITY_SEVERE)

    def test_severe_in_signal_series_only(self):
        v = run(lambda b: b.update({"high": b["low"] - 5}), series="signal")
        hit = [c for c in v["checks"] if c["id"] == "OHLC_INVALID"][0]
        self.assertIn("信号序列", hit["detail"])
        self.assertEqual(hit["severity"], G.SEVERITY_SEVERE)

    def test_severe_in_exec_series_only(self):
        v = run(lambda b: b.update({"high": b["low"] - 5}), series="exec")
        hit = [c for c in v["checks"] if c["id"] == "OHLC_INVALID"][0]
        self.assertIn("执行序列", hit["detail"])
        self.assertEqual(hit["severity"], G.SEVERITY_SEVERE)

    def test_both_series_severe_are_both_reported(self):
        snap = base_snapshot()
        snap["signal_bars"][3]["high"] = snap["signal_bars"][3]["low"] - 5
        snap["exec_bars"][4]["high"] = snap["exec_bars"][4]["low"] - 5
        v = G.evaluate(snap)
        hits = [c for c in v["checks"] if c["id"] == "OHLC_INVALID"]
        self.assertEqual(len(hits), 2)
        self.assertTrue(all(h["severity"] == G.SEVERITY_SEVERE for h in hits))
        self.assertEqual(v["severe_check_ids"], ["OHLC_INVALID", "OHLC_INVALID"])


# ══════════════════════ D 边界价格 ══════════════════════

class TestBoundaryPrices(unittest.TestCase):

    def test_tiny_positive_price_is_not_severe(self):
        """1e-12 是正数 ⇒ 不是「非正价格」；不得因为量级小就被判 severe。"""
        v = run(lambda b: b.update({"open": 1e-12, "high": 1e-12,
                                    "low": 1e-12, "close": 1e-12}))
        self.assertNotIn("OHLC_INVALID", [c["id"] for c in v["checks"]])

    def test_price_exactly_zero_is_severe(self):
        """0 是非正价格的边界：必须 severe（0 元成交价在撮合里无意义）。"""
        v = run(lambda b: b.update({"open": 0.0, "high": 0.0, "low": 0.0, "close": 0.0}))
        hit = [c for c in v["checks"] if c["id"] == "OHLC_INVALID"][0]
        self.assertEqual(hit["severity"], G.SEVERITY_SEVERE)

    def test_high_equals_low_is_legal(self):
        """一字板：high == low 不违反 high>=low，价格仍为正 ⇒ 不报异常。"""
        v = run(lambda b: b.update({"open": 100.0, "high": 100.0,
                                    "low": 100.0, "close": 100.0}))
        self.assertNotIn("OHLC_INVALID", [c["id"] for c in v["checks"]])

    def test_open_equals_low_and_close_equals_high_is_legal(self):
        v = run(lambda b: b.update({"open": b["low"], "close": b["high"]}))
        self.assertNotIn("OHLC_INVALID", [c["id"] for c in v["checks"]])

    def test_float_noise_within_tolerance_is_not_flagged(self):
        """1e-12 级噪声不得判成越界（否则真实数据会被误杀），也不得被升成 severe。"""
        v = run(lambda b: b.update({"high": b["close"] - 1e-12}))
        self.assertNotIn("OHLC_INVALID", [c["id"] for c in v["checks"]])

    def test_one_ulp_beyond_tolerance_is_normal_not_severe(self):
        """刚超出容差的越界是 normal —— 分级不得把「越界」误升为「价格不存在」。"""
        snap = base_snapshot()
        b = snap["exec_bars"][4]
        b["close"] = b["high"] + 1.0        # 远超 1e-9 相对容差
        v = G.evaluate(snap)
        hit = [c for c in v["checks"] if c["id"] == "OHLC_INVALID"][0]
        self.assertEqual(hit["severity"], G.SEVERITY_NORMAL)


# ══════════════════════ E isolated 出口（本单核心）══════════════════════

class TestIsolatedExit(unittest.TestCase):

    def test_isolated_high_lt_low_blocks(self):
        v = G.evaluate(base_snapshot(), mode=G.MODE_ISOLATED)
        self.assertTrue(v["run_allowed"], "干净快照必须放行（对照组）")
        snap = base_snapshot()
        snap["exec_bars"][4]["high"] = snap["exec_bars"][4]["low"] - 5
        v = G.evaluate(snap, mode=G.MODE_ISOLATED)
        self.assertEqual(v["verdict"], G.FAIL)
        self.assertFalse(v["run_allowed"], "isolated 遇 high<low 必须阻断")
        self.assertTrue(v["severe_blocked"])

    def test_isolated_zero_price_blocks(self):
        snap = base_snapshot()
        snap["exec_bars"][4]["low"] = 0.0
        v = G.evaluate(snap, mode=G.MODE_ISOLATED)
        self.assertFalse(v["run_allowed"])

    def test_isolated_missing_price_blocks(self):
        snap = base_snapshot()
        snap["signal_bars"][10]["low"] = None
        v = G.evaluate(snap, mode=G.MODE_ISOLATED)
        self.assertFalse(v["run_allowed"])

    def test_isolated_nan_price_blocks(self):
        snap = base_snapshot()
        snap["exec_bars"][4]["high"] = float("nan")
        v = G.evaluate(snap, mode=G.MODE_ISOLATED)
        self.assertFalse(v["run_allowed"])

    def test_isolated_normal_only_allows_with_label(self):
        snap = base_snapshot()
        snap["exec_bars"][4]["volume"] = -1.0
        v = G.evaluate(snap, mode=G.MODE_ISOLATED)
        self.assertEqual(v["verdict"], G.FAIL, "普通异常仍是 FAIL")
        self.assertTrue(v["run_allowed"], "普通异常不阻断隔离研究")
        self.assertFalse(v["severe_blocked"])
        self.assertEqual(v["claim_level"], "ISOLATED_RESEARCH")
        self.assertEqual(v["isolation_label"], G.ISOLATION_LABEL)
        self.assertFalse(v["trusted_backtest_allowed"])

    def test_isolated_unproven_allows(self):
        v = G.evaluate(base_snapshot(oi_rows=[]), mode=G.MODE_ISOLATED)
        self.assertEqual(v["verdict"], G.UNPROVEN)
        self.assertTrue(v["run_allowed"])
        self.assertFalse(v["severe_blocked"])

    def test_isolated_clean_allows_but_never_trusted(self):
        v = G.evaluate(base_snapshot(), mode=G.MODE_ISOLATED)
        self.assertEqual(v["verdict"], G.PASS)
        self.assertTrue(v["run_allowed"])
        self.assertFalse(v["trusted_backtest_allowed"])
        self.assertEqual(v["claim_level"], "ISOLATED_RESEARCH")


# ══════════════════════ F trusted 出口（未改）══════════════════════

class TestTrustedExit(unittest.TestCase):

    def test_trusted_pass_allows(self):
        v = G.evaluate(base_snapshot(), mode=G.MODE_TRUSTED)
        self.assertEqual(v["verdict"], G.PASS)
        self.assertTrue(v["run_allowed"])
        self.assertTrue(v["trusted_backtest_allowed"])
        self.assertEqual(v["claim_level"], "TRUSTED")
        self.assertFalse(v["severe_blocked"])

    def test_trusted_severe_blocks(self):
        snap = base_snapshot()
        snap["exec_bars"][4]["high"] = snap["exec_bars"][4]["low"] - 5
        v = G.evaluate(snap, mode=G.MODE_TRUSTED)
        self.assertFalse(v["run_allowed"])

    def test_trusted_normal_blocks(self):
        snap = base_snapshot()
        snap["exec_bars"][4]["volume"] = -1.0
        v = G.evaluate(snap, mode=G.MODE_TRUSTED)
        self.assertEqual(v["verdict"], G.FAIL)
        self.assertFalse(v["run_allowed"], "trusted 对普通 FAIL 也必须拒")

    def test_trusted_unproven_blocks(self):
        v = G.evaluate(base_snapshot(oi_rows=[]), mode=G.MODE_TRUSTED)
        self.assertEqual(v["verdict"], G.UNPROVEN)
        self.assertFalse(v["run_allowed"], "trusted 对 UNPROVEN 也必须拒")
        self.assertFalse(v["data_qualifies_for_trusted"])


# ══════════════════════ G OI_PIT UNPROVEN（任务 5）══════════════════════

class TestOiPitIsolated(unittest.TestCase):

    def _snap(self):
        s = base_snapshot()
        s["oi_rows"] = mk_oi(WIN_START - WARMUP * HOUR, (WARMUP + 48) * 12, with_pit=False)
        return s

    def test_isolated_oi_pit_unproven_still_runs(self):
        v = G.evaluate(self._snap(), mode=G.MODE_ISOLATED)
        self.assertEqual(v["verdict"], G.UNPROVEN)
        self.assertIn("OI_PIT", [c["id"] for c in v["checks"] if c["status"] == G.UNPROVEN])
        self.assertTrue(v["run_allowed"], "OI_PIT 未证属 UNPROVEN，不阻断隔离研究")

    def test_isolated_oi_pit_unproven_never_trusted(self):
        v = G.evaluate(self._snap(), mode=G.MODE_ISOLATED)
        self.assertEqual(v["claim_level"], "ISOLATED_RESEARCH")
        self.assertFalse(v["trusted_backtest_allowed"])
        self.assertFalse(v["data_qualifies_for_trusted"])
        self.assertTrue(v["isolation_required"])

    def test_isolated_oi_pit_with_severe_blocks(self):
        s = self._snap()
        s["exec_bars"][4]["high"] = s["exec_bars"][4]["low"] - 5
        v = G.evaluate(s, mode=G.MODE_ISOLATED)
        self.assertFalse(v["run_allowed"], "UNPROVEN 可跑，但 severe 仍必须拒")


# ══════════════════════ H 可解释性 ══════════════════════

class TestExplainability(unittest.TestCase):

    def test_ok_check_carries_severity_none(self):
        v = G.evaluate(base_snapshot())
        oks = [c for c in v["checks"] if c["id"] == "OHLC_OK"]
        self.assertEqual(len(oks), 2)
        for c in oks:
            self.assertEqual(c["severity"], G.SEVERITY_NONE)
            self.assertEqual(c["evidence"]["severe_rows"], 0)

    def test_severe_reasons_and_ids_exposed(self):
        snap = base_snapshot()
        snap["exec_bars"][4]["high"] = snap["exec_bars"][4]["low"] - 5
        v = G.evaluate(snap, mode=G.MODE_ISOLATED)
        self.assertEqual(v["severe_check_ids"], ["OHLC_INVALID"])
        self.assertEqual(len(v["severe_reasons"]), 1)
        self.assertIn("OHLC_INVALID", v["severe_reasons"][0])

    def test_severity_policy_is_self_describing(self):
        v = G.evaluate(base_snapshot())
        pol = v["severity_policy"]
        self.assertEqual(pol["version"], G.SEVERITY_POLICY_VERSION)
        self.assertEqual(len(pol["severe_criteria"]), 3)
        self.assertIn("high < low", " ".join(pol["severe_criteria"]))
        self.assertIn("isolated_blocks", pol)

    def test_run_allowed_reason_is_never_empty(self):
        cases = [
            (base_snapshot(), G.MODE_TRUSTED),
            (base_snapshot(), G.MODE_ISOLATED),
            (base_snapshot(oi_rows=[]), G.MODE_TRUSTED),
            (base_snapshot(oi_rows=[]), G.MODE_ISOLATED),
        ]
        for snap, mode in cases:
            v = G.evaluate(snap, mode=mode)
            self.assertTrue(v["run_allowed_reason"])
            self.assertIsInstance(v["run_allowed_reason"], str)

    def test_summary_mentions_severe_when_blocked(self):
        snap = base_snapshot()
        snap["exec_bars"][4]["high"] = snap["exec_bars"][4]["low"] - 5
        v = G.evaluate(snap, mode=G.MODE_ISOLATED)
        self.assertIn("严重异常", v["summary_zh"])
        self.assertIn("拒绝运行", v["summary_zh"])

    def test_verdict_hash_differs_between_severe_and_normal(self):
        s1 = base_snapshot()
        s1["exec_bars"][4]["high"] = s1["exec_bars"][4]["low"] - 5      # severe
        s2 = base_snapshot()
        s2["exec_bars"][4]["volume"] = -1.0                            # normal
        a, b = G.evaluate(s1), G.evaluate(s2)
        self.assertEqual(a["verdict"], b["verdict"])
        self.assertNotEqual(a["verdict_sha256"], b["verdict_sha256"],
                            "严重度是判定身份的一部分，不能与普通异常同哈希")

    def test_row_level_severity_is_marked_in_sample(self):
        """逐行证据也要带 severity：只标整条判据、不标具体行，等于定位不到坏在哪根。"""
        cases = [
            (lambda b: b.update({"open": None}), G.SEVERITY_SEVERE),
            (lambda b: b.update({"high": b["low"] - 5}), G.SEVERITY_SEVERE),
            (lambda b: b.update({"low": 0.0}), G.SEVERITY_SEVERE),
            (lambda b: b.update({"volume": -1.0}), G.SEVERITY_NORMAL),
            (lambda b: b.update({"open": b["high"] + 5}), G.SEVERITY_NORMAL),
        ]
        for mutate, expect in cases:
            v = run(mutate)
            hit = [c for c in v["checks"] if c["id"] == "OHLC_INVALID"][0]
            row = hit["evidence"]["sample"][0]
            self.assertEqual(row["severity"], expect, f"{hit['evidence']['sample'][0]['why']}")
            self.assertEqual(hit["severity"], expect)


# ══════════════════════ I 不回归 ══════════════════════

class TestNoRegression(unittest.TestCase):

    def test_period_1h_15m_still_pass(self):
        v = G.evaluate(base_snapshot())
        self.assertEqual(v["verdict"], G.PASS)
        self.assertIn("KL_XTF_OK", [c["id"] for c in v["checks"]])

    def test_period_15m_4h_still_fail(self):
        s = base_snapshot()
        s["signal_timeframe"] = "15m"
        s["exec_timeframe"] = "4h"
        v = G.evaluate(s)
        self.assertIn("KL_XTF_RATIO", [c["id"] for c in v["checks"] if c["status"] == G.FAIL])

    def test_gate_version_untouched(self):
        """27 号判据未改 ⇒ 31 号不擅自改判据；版本号只随**判据集合**前进。

        45 号说明：本条在前像里**是失败的** —— 它钉死 "v2"，而 40 号已推到 "v3"，
        原因是 40 号的测试编辑没有随补丁组合进来。本单恢复为 40 号树里的原值 "v3"，
        并保留真正属于 31 号的锚点 `SEVERITY_POLICY_VERSION == "s1"`（该轴不随
        GATE_VERSION 漂移），再加钉本单新增的辅助序列契约轴 `a1`。
        """
        self.assertEqual(G.GATE_VERSION, "v3")
        self.assertEqual(G.SEVERITY_POLICY_VERSION, "s1")
        self.assertEqual(G.AUX_POLICY_VERSION, "a1")

    def test_oi_pit_rule_untouched(self):
        s = base_snapshot()
        s["oi_rows"] = mk_oi(WIN_START - WARMUP * HOUR, (WARMUP + 48) * 12, with_pit=False)
        v = G.evaluate(s)
        self.assertNotIn("OI_PIT", [c["id"] for c in v["checks"] if c["status"] == G.FAIL])

    def test_funding_gap_rule_untouched(self):
        s = base_snapshot()
        rows = mk_funding(WIN_START, s["win_end_ms"])
        target = [e["funding_time"] for e in rows
                  if WIN_START <= e["funding_time"] < s["win_end_ms"]][1]
        s["funding_rows"] = [e for e in rows if e["funding_time"] != target]
        v = G.evaluate(s)
        self.assertIn("FUNDING_GAP", [c["id"] for c in v["checks"]
                                      if c["status"] == G.UNPROVEN])
        self.assertNotIn("FUNDING_GAP", [c["id"] for c in v["checks"]
                                         if c["status"] == G.FAIL])

    def test_runner_still_not_wired(self):
        """本单不接线：runner 里不得出现门禁引用或 severity 判据副本。"""
        src = (BACKEND_DIR / "backtest_runner.py").read_text(encoding="utf-8")
        self.assertNotIn("backtest_data_gate", src)
        self.assertNotIn("severity", src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
