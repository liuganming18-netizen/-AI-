#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GM-BACKTEST-DATA-GATE-17 合成数据测试套件（不碰真实行情库、不联网、不写生产文件）。

覆盖四个场景族：
  A 正常窗口   —— 应 PASS，正式可信回测放行
  B 数据缺口   —— K 线缺根 / 尾段截断 / 未收盘 / 重复 / 网格错位 ⇒ FAIL
  C 数值异常   —— high<low / open|close 越界 / 价格 <= 0 / 非有限值 ⇒ FAIL
  D 降级场景   —— OI 不足、OI 无可得时刻、资金费缺格 / 无 settled 事件 ⇒ UNPROVEN
                  且必须**零伪造**（不补 0、不插值、不假设 8h）
另加：
  E 隔离出口   —— FAIL/UNPROVEN 时 trusted 拒绝运行、isolated 放行且强制打标记
  F 判定稳定   —— 同输入 verdict_sha256 恒等
  G 只读约束   —— 模块源码不含写库/写盘/补数调用（静态守卫）

运行：cd 框架/backend && python3 -m unittest tests.test_backtest_data_gate_17 -v
"""
import json
import math
import re
import sys
import tempfile
import unittest
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

import backtest_data_gate as G  # noqa: E402

HOUR = 3_600_000
Q = 900_000                      # 15m
H4 = 14_400_000                  # 4h
WIN_START = 1_789_992_000_000    # 2026-09-21T12:00:00Z，同时落在 15m / 4h / 12h 网格上
WARMUP = 8                       # 8 根 15m = 2 小时（够做合成测试）


# ══════════════════════ 合成数据构造器 ══════════════════════

def mk_bars(start_ms, count, iv, price=100.0, closed=1, step=0.5):
    """等间隔合成 K 线；数值自洽（high>=low，open/close 在区间内）。"""
    out = []
    for i in range(count):
        ot = start_ms + i * iv
        o = price + i * step
        h = o + 1.0
        l = o - 1.0
        out.append({"open_time": ot, "close_time": ot + iv - 1,
                    "open": o, "high": h, "low": l, "close": o + 0.25,
                    "volume": 10.0 + i, "is_closed": closed})
    return out


def mk_oi(start_ms, count, iv=300_000, base=1_000_000.0, with_pit=True):
    out = []
    for i in range(count):
        ts = start_ms + i * iv
        row = {"ts": ts, "open_interest": base + i}
        if with_pit:
            row["available_at"] = ts + 2_400          # 实测中位到达延迟 2.4s
        out.append(row)
    return out


def mk_funding(win_start, win_end, interval=8 * HOUR, lookback=7 * 86_400_000,
               rate=0.0001, drop=None):
    """按固定间隔生成 settled 事件；`drop` 为要**故意删掉**的网格点（造缺口）。"""
    t = win_start - lookback
    t -= t % interval
    out = []
    while t < win_end:
        if drop is None or t not in drop:
            out.append({"funding_time": t, "funding_rate": rate,
                        "mark_price": 100.0, "rate_type": "settled"})
        t += interval
    return out


def base_snapshot(win_days=2, **over):
    """正常窗口：2 天，15m 信号（含预热）+ 4h 执行，OI 5m 全覆盖且带可得时刻。"""
    win_end = WIN_START + win_days * 86_400_000
    sig_start = WIN_START - WARMUP * Q
    snap = {
        "exchange": "binance", "market": "um", "symbol": "BTCUSDT",
        "signal_timeframe": "15m", "exec_timeframe": "4h",
        "win_start_ms": WIN_START, "win_end_ms": win_end, "warmup_bars": WARMUP,
        "signal_bars": mk_bars(sig_start, WARMUP + win_days * 96, Q),
        "exec_bars": mk_bars(WIN_START, win_days * 6, H4),
        "oi_rows": mk_oi(sig_start, (WARMUP + win_days * 96) * 3, 300_000),
        "funding_rows": mk_funding(WIN_START, win_end),
    }
    snap.update(over)
    return snap


def ids(verdict, status=None):
    return [c["id"] for c in verdict["checks"] if status is None or c["status"] == status]


# ══════════════════════ A 正常窗口 ══════════════════════

class TestNormalWindow(unittest.TestCase):

    def test_normal_window_passes_and_allows_trusted(self):
        v = G.evaluate(base_snapshot())
        self.assertEqual(v["verdict"], G.PASS, v["block_reasons"] + v["unproven_reasons"])
        self.assertTrue(v["trusted_backtest_allowed"])
        self.assertTrue(v["run_allowed"])
        self.assertEqual(v["claim_level"], "TRUSTED")
        self.assertFalse(v["isolation_required"])
        self.assertIsNone(v["isolation_label"])
        self.assertEqual(v["counts"]["fail"], 0)
        self.assertEqual(v["counts"]["unproven"], 0)

    def test_pass_still_records_every_check(self):
        """PASS 必须留下逐项记录，否则「通过」看起来像「没查」。"""
        v = G.evaluate(base_snapshot())
        passed = [c["id"] for c in v["checks"] if c["status"] == G.PASS]
        for cid in ("KL_OK", "KL_ALIGN_OK", "KL_XTF_OK", "OHLC_OK", "OI_OK", "FUNDING_OK"):
            self.assertIn(cid, passed)
        self.assertEqual(v["counts"]["checks"], len(v["checks"]))
        self.assertEqual(v["counts"]["pass"], len(passed))
        self.assertGreaterEqual(v["counts"]["checks"], 8)

    def test_verdict_carries_recomputable_hashes(self):
        v = G.evaluate(base_snapshot())
        self.assertEqual(len(v["input_sha256"]), 64)
        self.assertEqual(len(v["verdict_sha256"]), 64)
        self.assertEqual(v["gate"], G.GATE_ID)

    def test_bars_outside_window_are_trimmed_not_failed(self):
        """load_inputs 的 sig_df 会延伸到窗口之后；窗口外的行不得判成覆盖超量。"""
        snap = base_snapshot()
        extra = mk_bars(snap["win_end_ms"], 20, Q)
        snap["signal_bars"] = snap["signal_bars"] + extra
        snap["exec_bars"] = snap["exec_bars"] + mk_bars(snap["win_end_ms"], 3, H4)
        v = G.evaluate(snap)
        self.assertEqual(v["verdict"], G.PASS, v["block_reasons"])
        self.assertEqual(v["subject"]["bars_outside_window"], {"signal": 20, "exec": 3})


# ══════════════════════ B 数据缺口 / 时间错位 ══════════════════════

class TestGapAndAlignment(unittest.TestCase):

    def test_missing_bar_is_fail(self):
        snap = base_snapshot()
        del snap["signal_bars"][10]
        v = G.evaluate(snap)
        self.assertEqual(v["verdict"], G.FAIL)
        self.assertIn("KL_GAP", ids(v, G.FAIL))
        self.assertIn("KL_COVERAGE", ids(v, G.FAIL))

    def test_truncated_tail_is_fail(self):
        """DATA-09 的核心痛点：尾段被截断时旧实现只报「不连续」，此处必须明确到根数。"""
        snap = base_snapshot()
        snap["exec_bars"] = snap["exec_bars"][:-2]
        v = G.evaluate(snap)
        self.assertEqual(v["verdict"], G.FAIL)
        cov = [c for c in v["checks"] if c["id"] == "KL_COVERAGE"]
        self.assertTrue(cov)
        ev = cov[0]["evidence"]
        self.assertEqual(ev["expected_bars"], 12)
        self.assertEqual(ev["actual_bars"], 10)
        self.assertFalse(ev["tail_ok"])

    def test_unclosed_bar_is_fail(self):
        snap = base_snapshot()
        snap["exec_bars"][3]["is_closed"] = 0
        v = G.evaluate(snap)
        self.assertEqual(v["verdict"], G.FAIL)
        self.assertIn("KL_UNCLOSED", ids(v, G.FAIL))

    def test_missing_is_closed_field_is_unproven_not_pass(self):
        snap = base_snapshot()
        for b in snap["exec_bars"]:
            b.pop("is_closed")
        v = G.evaluate(snap)
        self.assertEqual(v["verdict"], G.UNPROVEN)
        self.assertIn("KL_UNCLOSED", ids(v, G.UNPROVEN))

    def test_duplicate_timestamp_is_fail(self):
        snap = base_snapshot()
        snap["signal_bars"].append(dict(snap["signal_bars"][5]))
        v = G.evaluate(snap)
        self.assertEqual(v["verdict"], G.FAIL)
        self.assertIn("KL_DUPLICATE", ids(v, G.FAIL))

    def test_misaligned_open_time_is_fail(self):
        """整段平移时「相邻差 == 间隔」恒成立 ⇒ 旧校验查不出，必须靠网格对齐判。"""
        snap = base_snapshot()
        snap["exec_bars"] = mk_bars(WIN_START + 4 * HOUR + 7_000, 11, H4)
        v = G.evaluate(snap)
        self.assertEqual(v["verdict"], G.FAIL)
        self.assertIn("KL_ALIGN", ids(v, G.FAIL))

    def test_bad_close_time_is_fail(self):
        snap = base_snapshot()
        snap["exec_bars"][2]["close_time"] = snap["exec_bars"][2]["open_time"] + H4 + 30_000
        v = G.evaluate(snap)
        self.assertEqual(v["verdict"], G.FAIL)
        self.assertIn("KL_CLOSE_TIME", ids(v, G.FAIL))

    def test_empty_series_is_fail(self):
        snap = base_snapshot()
        snap["exec_bars"] = []
        v = G.evaluate(snap)
        self.assertEqual(v["verdict"], G.FAIL)
        self.assertIn("KL_EMPTY", ids(v, G.FAIL))

    def test_window_not_on_grid_is_fail(self):
        snap = base_snapshot()
        snap["win_end_ms"] = snap["win_end_ms"] + 60_000
        v = G.evaluate(snap)
        self.assertEqual(v["verdict"], G.FAIL)
        self.assertIn("KL_XTF_WINDOW", ids(v, G.FAIL))

    def test_signal_tf_not_divisor_is_fail(self):
        """执行周期不是信号周期整数倍 ⇒ 两序列无法在同一决策时刻对齐。"""
        snap = base_snapshot()
        snap["signal_timeframe"] = "8h"
        snap["exec_timeframe"] = "12h"
        snap["warmup_bars"] = 2
        snap["win_end_ms"] = WIN_START + 48 * HOUR
        snap["signal_bars"] = mk_bars(WIN_START - 2 * 8 * HOUR, 8, 8 * HOUR)
        snap["exec_bars"] = mk_bars(WIN_START, 4, 12 * HOUR)
        v = G.evaluate(snap)
        self.assertEqual(v["verdict"], G.FAIL)
        self.assertIn("KL_XTF_RATIO", ids(v, G.FAIL))

    def test_negative_warmup_is_fail(self):
        snap = base_snapshot()
        snap["warmup_bars"] = -1
        v = G.evaluate(snap)
        self.assertEqual(v["verdict"], G.FAIL)
        self.assertIn("KL_XTF_WARMUP", ids(v, G.FAIL))


# ══════════════════════ C 异常 OHLC ══════════════════════

class TestOhlcAnomaly(unittest.TestCase):

    def _assert_ohlc_fail(self, mutate, why_substr):
        snap = base_snapshot()
        mutate(snap["exec_bars"][4])
        v = G.evaluate(snap)
        self.assertEqual(v["verdict"], G.FAIL)
        hits = [c for c in v["checks"] if c["id"] == "OHLC_INVALID"]
        self.assertTrue(hits, "未触发 OHLC_INVALID")
        self.assertIn(why_substr, hits[0]["detail"] + json.dumps(hits[0]["evidence"],
                                                                ensure_ascii=False))
        self.assertEqual(hits[0]["evidence"]["bad_rows"], 1)
        return v

    def test_high_below_low(self):
        self._assert_ohlc_fail(lambda b: b.update({"high": b["low"] - 5}), "high < low")

    def test_close_above_high(self):
        self._assert_ohlc_fail(lambda b: b.update({"close": b["high"] + 5}),
                               "close 越出")

    def test_open_below_low(self):
        self._assert_ohlc_fail(lambda b: b.update({"open": b["low"] - 5}),
                               "open 越出")

    def test_non_positive_price(self):
        self._assert_ohlc_fail(lambda b: b.update({"low": 0.0}), "价格 <= 0")

    def test_nan_price_is_not_coerced_to_zero(self):
        v = self._assert_ohlc_fail(lambda b: b.update({"high": float("nan")}), "空值")
        self.assertEqual(v["no_fabrication"]["price_repaired_rows"], 0)

    def test_negative_volume(self):
        self._assert_ohlc_fail(lambda b: b.update({"volume": -1.0}), "volume < 0")

    def test_float_noise_is_tolerated(self):
        """1e-12 级浮点噪声不得判成越界（否则真实数据会被误杀）。"""
        snap = base_snapshot()
        b = snap["exec_bars"][4]
        b["high"] = b["close"] - 1e-12
        v = G.evaluate(snap)
        self.assertNotIn("OHLC_INVALID", ids(v, G.FAIL))


# ══════════════════════ D 降级场景：只出 UNPROVEN，零伪造 ══════════════════════

class TestDegradedUnproven(unittest.TestCase):

    def test_oi_empty_is_unproven_not_fail(self):
        snap = base_snapshot(oi_rows=[])
        v = G.evaluate(snap)
        self.assertEqual(v["verdict"], G.UNPROVEN)
        self.assertIn("OI_EMPTY", ids(v, G.UNPROVEN))
        self.assertNotIn("OI_EMPTY", ids(v, G.FAIL))

    def test_oi_partial_coverage_is_unproven(self):
        snap = base_snapshot()
        snap["oi_rows"] = snap["oi_rows"][: len(snap["oi_rows"]) // 3]
        v = G.evaluate(snap)
        self.assertEqual(v["verdict"], G.UNPROVEN)
        self.assertIn("OI_COVERAGE", ids(v, G.UNPROVEN))
        ev = [c for c in v["checks"] if c["id"] == "OI_COVERAGE"][0]["evidence"]
        self.assertLess(ev["coverage_ratio"], 0.95)
        self.assertEqual(ev["bars_missing_oi"], ev["bars"] - ev["bars_with_oi"])

    def test_oi_without_pit_is_unproven(self):
        snap = base_snapshot()
        snap["oi_rows"] = mk_oi(snap["win_start_ms"] - WARMUP * Q,
                                len(snap["signal_bars"]) * 3, 300_000, with_pit=False)
        v = G.evaluate(snap)
        self.assertEqual(v["verdict"], G.UNPROVEN)
        self.assertIn("OI_PIT", ids(v, G.UNPROVEN))

    def test_oi_starting_late_is_unproven(self):
        snap = base_snapshot()
        late = snap["win_start_ms"] + 12 * HOUR
        snap["oi_rows"] = mk_oi(late, 200, 300_000)
        v = G.evaluate(snap)
        self.assertEqual(v["verdict"], G.UNPROVEN)
        self.assertIn("OI_WINDOW_START", ids(v, G.UNPROVEN))

    def test_funding_gap_is_unproven_not_fail(self):
        """资金费缺格按工单要求出 UNPROVEN（不是 FAIL），但同样挡住可信回测。"""
        snap = base_snapshot()
        ws, we = snap["win_start_ms"], snap["win_end_ms"]
        rows = mk_funding(ws, we)
        target = [e["funding_time"] for e in rows if ws <= e["funding_time"] < we][1]
        snap["funding_rows"] = [e for e in rows if e["funding_time"] != target]
        v = G.evaluate(snap)
        self.assertEqual(v["verdict"], G.UNPROVEN)
        self.assertIn("FUNDING_GAP", ids(v, G.UNPROVEN))
        self.assertNotIn("FUNDING_GAP", ids(v, G.FAIL))
        ev = [c for c in v["checks"] if c["id"] == "FUNDING_GAP"][0]["evidence"]
        self.assertEqual(ev["missing_count"], 1)
        self.assertFalse(v["trusted_backtest_allowed"])

    def test_funding_empty_is_unproven(self):
        snap = base_snapshot(funding_rows=[])
        v = G.evaluate(snap)
        self.assertEqual(v["verdict"], G.UNPROVEN)
        self.assertIn("FUNDING_EMPTY", ids(v, G.UNPROVEN))

    def test_funding_single_event_cannot_prove_frequency(self):
        snap = base_snapshot()
        snap["funding_rows"] = [{"funding_time": snap["win_start_ms"], "funding_rate": 1e-4,
                                 "mark_price": 100.0, "rate_type": "settled"}]
        v = G.evaluate(snap)
        self.assertEqual(v["verdict"], G.UNPROVEN)
        self.assertIn("FUNDING_FREQ", ids(v, G.UNPROVEN))

    def test_funding_missing_rate_is_unproven(self):
        snap = base_snapshot()
        ws, we = snap["win_start_ms"], snap["win_end_ms"]
        target = [e for e in snap["funding_rows"] if ws <= e["funding_time"] < we][0]
        target["funding_rate"] = None
        v = G.evaluate(snap)
        self.assertEqual(v["verdict"], G.UNPROVEN)
        self.assertIn("FUNDING_FIELDS", ids(v, G.UNPROVEN))

    def test_funding_never_assumes_8h(self):
        """4h 结算的标的：按 8h 推算会漏一半，本门禁必须按观测间隔判。"""
        snap = base_snapshot()
        snap["funding_rows"] = mk_funding(snap["win_start_ms"], snap["win_end_ms"],
                                          interval=4 * HOUR)
        v = G.evaluate(snap)
        self.assertEqual(v["verdict"], G.PASS, v["unproven_reasons"])

    def test_no_fabrication_counters_are_zero(self):
        snap = base_snapshot(oi_rows=[])
        snap["funding_rows"] = []
        v = G.evaluate(snap)
        nf = v["no_fabrication"]
        self.assertEqual(nf["oi_filled_rows"], 0)
        self.assertEqual(nf["funding_filled_rows"], 0)
        self.assertEqual(nf["price_repaired_rows"], 0)
        # 门禁不改入参：OI 仍为空、资金费仍为空
        self.assertEqual(snap["oi_rows"], [])
        self.assertEqual(snap["funding_rows"], [])


# ══════════════════════ E 两个出口：拒绝可信 / 放行隔离 ══════════════════════

class TestTwoExits(unittest.TestCase):

    def _broken(self):
        snap = base_snapshot()
        del snap["signal_bars"][10]
        snap["funding_rows"] = []
        return snap

    def test_fail_rejects_trusted_run(self):
        v = G.evaluate(self._broken(), mode=G.MODE_TRUSTED)
        self.assertEqual(v["verdict"], G.FAIL)
        self.assertFalse(v["trusted_backtest_allowed"])
        self.assertFalse(v["run_allowed"])
        self.assertEqual(v["claim_level"], "ISOLATED_RESEARCH")

    def test_fail_allows_isolated_research_with_label(self):
        v = G.evaluate(self._broken(), mode=G.MODE_ISOLATED)
        self.assertEqual(v["verdict"], G.FAIL)
        self.assertTrue(v["run_allowed"])
        self.assertTrue(v["isolation_required"])
        self.assertEqual(v["claim_level"], "ISOLATED_RESEARCH")
        self.assertEqual(v["isolation_label"], G.ISOLATION_LABEL)
        self.assertFalse(v["trusted_backtest_allowed"])

    def test_unproven_rejects_trusted_run(self):
        v = G.evaluate(base_snapshot(oi_rows=[]), mode=G.MODE_TRUSTED)
        self.assertEqual(v["verdict"], G.UNPROVEN)
        self.assertFalse(v["trusted_backtest_allowed"])
        self.assertFalse(v["run_allowed"])

    def test_isolated_mode_never_upgrades_claim_on_clean_window(self):
        """隔离模式跑干净窗口：可以跑，但主张等级仍是隔离研究（不因数据干净就自称可信）。"""
        v = G.evaluate(base_snapshot(), mode=G.MODE_ISOLATED)
        self.assertEqual(v["verdict"], G.PASS)
        self.assertTrue(v["run_allowed"])
        self.assertEqual(v["claim_level"], "ISOLATED_RESEARCH")

    def test_unknown_mode_rejected(self):
        with self.assertRaises(ValueError):
            G.evaluate(base_snapshot(), mode="pass")


# ══════════════════════ F 判定稳定性 ══════════════════════

class TestDeterminism(unittest.TestCase):

    def test_same_snapshot_same_verdict_hash(self):
        snap = base_snapshot()
        a = G.evaluate(snap)
        b = G.evaluate(snap)
        self.assertEqual(a["verdict_sha256"], b["verdict_sha256"])
        self.assertEqual(a["verdict"], b["verdict"])

    def test_different_data_different_hash(self):
        a = G.evaluate(base_snapshot())
        snap = base_snapshot()
        del snap["signal_bars"][10]
        b = G.evaluate(snap)
        self.assertNotEqual(a["verdict_sha256"], b["verdict_sha256"])
        self.assertNotEqual(a["input_sha256"], b["input_sha256"])

    def test_verdict_is_json_serializable(self):
        v = G.evaluate(base_snapshot())
        text = json.dumps(v, ensure_ascii=False, default=str)
        self.assertIn("verdict", json.loads(text))


# ══════════════════════ G 只读 / 零伪造静态守卫 ══════════════════════

class TestReadOnlyGuard(unittest.TestCase):

    SRC = (BACKEND_DIR / "backtest_data_gate.py").read_text(encoding="utf-8")

    def test_no_database_write_calls(self):
        for pat in (r"\bINSERT\s+INTO\b", r"\bUPDATE\s+\w+\s+SET\b", r"\bDELETE\s+FROM\b",
                    r"CREATE\s+TABLE", r"DROP\s+TABLE", r"executemany\(", r"\.commit\("):
            self.assertIsNone(re.search(pat, self.SRC, re.I), f"门禁模块出现写库语句：{pat}")

    def test_no_data_fabrication_calls(self):
        for pat in (r"\.fillna\(", r"\.interpolate\(", r"\.ffill\(", r"\.bfill\(",
                    r"resample\(", r"replace\(\s*0"):
            self.assertIsNone(re.search(pat, self.SRC), f"门禁模块出现补数调用：{pat}")

    def test_no_network_or_db_driver_import(self):
        for mod in ("requests", "urllib", "httpx", "sqlite3", "duckdb", "pandas"):
            self.assertNotRegex(self.SRC, rf"^\s*(import|from)\s+{mod}\b",
                                f"门禁模块不应直接引入 {mod}（取数交给调用方传入的 repo）")

    def test_evaluate_writes_nothing(self):
        with tempfile.TemporaryDirectory() as td:
            before = set(Path(td).rglob("*"))
            G.evaluate(base_snapshot())
            self.assertEqual(before, set(Path(td).rglob("*")))

    def test_cli_exit_codes(self):
        with tempfile.TemporaryDirectory() as td:
            ok = Path(td) / "ok.json"
            ok.write_text(json.dumps(base_snapshot()), encoding="utf-8")
            bad_snap = base_snapshot(oi_rows=[])
            bad = Path(td) / "bad.json"
            bad.write_text(json.dumps(bad_snap), encoding="utf-8")
            out = Path(td) / "verdict.json"
            self.assertEqual(G.main(["--snapshot", str(ok), "--out", str(out)]), 0)
            self.assertTrue(out.exists())
            self.assertEqual(json.loads(out.read_text(encoding="utf-8"))["verdict"], G.PASS)
            self.assertEqual(G.main(["--snapshot", str(bad)]), 2)
            self.assertEqual(G.main(["--snapshot", str(bad), "--mode",
                                     G.MODE_ISOLATED]), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
