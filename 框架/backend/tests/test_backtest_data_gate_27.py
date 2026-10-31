#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GM-BACKTEST-DATA-GATE-FIX-27｜`KL_XTF_RATIO` 整除方向修正的专项测试。

为什么单独建一份（而不是继续往 17 号里塞）
────────────────────────────────────────
17 号的 45 项测试里，**只有一条**跨周期用例（`test_signal_tf_not_divisor_is_fail`），
用的是 `8h/12h` —— 这对周期在两个方向都不整除（`8h%12h=8h`、`12h%8h=4h`），
所以它对「整除方向写反」这件事**天然不敏感**。17 号的 `base_snapshot` 又恰好是
`15m 信号 / 4h 执行`（方向反了才 PASS 的一侧）。两者叠加的结果是：方向反了 100% 漏检。

本模块补的就是这个洞，分八族：
  A 方向敏感用例 —— 1h/15m、4h/15m、15m/4h、1h/4h、30m/15m、15m/30m、1d/4h、4h/1d
  B 全矩阵       —— `INTERVAL_MS × INTERVAL_MS` 共 81 对，逐对与 `s_iv % e_iv == 0` 对撞
  C 方向敏感性证明 —— 证明「旧规则 ≠ 新规则」的集合非空且可枚举（不是空转的探针）
  D 缺失/未知周期 —— 必须显式报错，不得静默放行或静默跳过
  E ratio 语义   —— `ratio` = 一根信号 bar 含几根执行 bar（不是反过来）
  F 端到端判定   —— 1h/15m 出 PASS+KL_XTF_OK；15m/4h 出 FAIL+KL_XTF_RATIO
  G 窗口/预热不受影响 —— 只翻方向，窗口与预热判据行为必须逐字不变
  H 三态不被升格 —— OI_PIT / FUNDING_GAP 仍是 UNPROVEN，绝不计入 PASS

运行：cd 框架/backend && python3 -m unittest tests.test_backtest_data_gate_27 -v
"""
import sys
import unittest
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

import backtest_data_gate as G  # noqa: E402

HOUR = 3_600_000
Q = 900_000                       # 15m
H4 = 14_400_000                   # 4h
DAY = 86_400_000
OI_IV = 300_000                   # OI 观测步长 5m
WIN_START = 1_789_948_800_000     # 2026-09-21T00:00:00Z —— **同时落在全部 9 个周期网格上**
# 为什么不用 17 号的 1_789_992_000_000（12:00Z）：它不在 1d 网格上（%1d=43,200,000），
# 会让 1d 相关用例被 `KL_XTF_WINDOW` 先挡掉，从而测不到 `KL_XTF_RATIO`（本模块踩过）。


# ══════════════════════ 合成数据构造器（与 17 号同口径，独立实现以便本模块自足）══════════════════════

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


def mk_funding(win_start, win_end, interval=8 * HOUR, lookback=7 * DAY, rate=0.0001,
               drop=None):
    """按固定间隔生成 settled 事件；`drop` 为要故意删掉的网格点（造缺口）。"""
    t = win_start - lookback
    t -= t % interval
    out = []
    while t < win_end:
        if drop is None or t not in drop:
            out.append({"funding_time": t, "funding_rate": rate,
                        "mark_price": 100.0, "rate_type": "settled"})
        t += interval
    return out


def snap(sig_tf, exec_tf, win_days=2, warmup=2, **over):
    """给任意 (信号周期, 执行周期) 造一份「除跨周期判据外全部干净」的快照。

    注意：**不保证**该周期对合法 —— 正是要用非法对去撞 `KL_XTF_RATIO`。
    """
    s_iv, e_iv = G.INTERVAL_MS[sig_tf], G.INTERVAL_MS[exec_tf]
    win_end = WIN_START + win_days * DAY
    span = win_end - WIN_START
    sig_start = WIN_START - warmup * s_iv
    sig_n = warmup + span // s_iv
    exec_n = span // e_iv
    oi_n = (sig_n * s_iv) // OI_IV
    d = {
        "exchange": "binance", "market": "um", "symbol": "BTCUSDT",
        "signal_timeframe": sig_tf, "exec_timeframe": exec_tf,
        "win_start_ms": WIN_START, "win_end_ms": win_end, "warmup_bars": warmup,
        "signal_bars": mk_bars(sig_start, sig_n, s_iv),
        "exec_bars": mk_bars(WIN_START, exec_n, e_iv),
        "oi_rows": mk_oi(sig_start, oi_n),
        "funding_rows": mk_funding(WIN_START, win_end),
    }
    d.update(over)
    return d


def xtf(sig_tf, exec_tf, ws=WIN_START, we=WIN_START + 2 * DAY, warmup=2):
    """直接调被判函数，返回 {id: status}（只含 KL_XTF_* 三项）。"""
    return {c["id"]: c["status"] for c in G._check_cross_tf(sig_tf, exec_tf, ws, we, warmup)}


def ok(sig_tf, exec_tf):
    """该周期对是否应被放行 —— 独立按语义复算，不复用被测代码。"""
    return G.INTERVAL_MS[sig_tf] % G.INTERVAL_MS[exec_tf] == 0


# ══════════════════════ A 方向敏感用例 ══════════════════════

class TestDirectionSensitiveCases(unittest.TestCase):
    """这些用例在 17 号原实现下**至少有一条会判反**，是新旧实现的分水岭。"""

    def test_1h_signal_15m_exec_passes(self):
        """项目真实基线「事件 1h / 执行 15m」：1 根 1h 装 4 根 15m ⇒ 必须 PASS。

        17 号原实现（`e_iv % s_iv`）把它判成 FAIL —— 这就是 21 号报告里的误判。
        """
        self.assertEqual(xtf("1h", "15m"), {"KL_XTF_OK": G.PASS})

    def test_4h_signal_15m_exec_passes(self):
        """1 根 4h 装 16 根 15m ⇒ PASS（21 号报告里的另一个被误判组合）。"""
        self.assertEqual(xtf("4h", "15m"), {"KL_XTF_OK": G.PASS})

    def test_15m_signal_4h_exec_fails(self):
        """1 根 15m **装不下** 4h ⇒ FAIL。

        17 号原实现判它 PASS —— 这是更危险的一侧：**假绿**。一根 15m 信号 bar 收盘时，
        4h 执行 bar 才刚开了 1/16，撮合时刻根本不存在，回测却会被放行为「可信」。
        """
        self.assertEqual(xtf("15m", "4h"), {"KL_XTF_RATIO": G.FAIL})

    def test_1h_signal_4h_exec_fails(self):
        self.assertEqual(xtf("1h", "4h"), {"KL_XTF_RATIO": G.FAIL})

    def test_8h_12h_fails(self):
        """两个方向都不整除 ⇒ 无论方向怎么改都应 FAIL（17 号唯一那条 XTF 用例）。"""
        self.assertEqual(xtf("8h", "12h"), {"KL_XTF_RATIO": G.FAIL})

    def test_30m_signal_15m_exec_passes(self):
        self.assertEqual(xtf("30m", "15m"), {"KL_XTF_OK": G.PASS})

    def test_15m_signal_30m_exec_fails(self):
        self.assertEqual(xtf("15m", "30m"), {"KL_XTF_RATIO": G.FAIL})

    def test_1d_signal_4h_exec_passes(self):
        """1 根 1d 装 6 根 4h ⇒ PASS（跨日线也要成立，不能只覆盖小时级）。"""
        self.assertEqual(xtf("1d", "4h"), {"KL_XTF_OK": G.PASS})

    def test_4h_signal_1d_exec_fails(self):
        self.assertEqual(xtf("4h", "1d"), {"KL_XTF_RATIO": G.FAIL})


# ══════════════════════ B 全矩阵对撞 ══════════════════════

class TestExhaustiveMatrix(unittest.TestCase):

    def test_all_pairs_match_divisibility_semantics(self):
        """81 对逐对复算：PASS ⟺ `sig_iv % exec_iv == 0`，不允许任何例外。"""
        tfs = sorted(G.INTERVAL_MS)
        wrong = []
        for s in tfs:
            for e in tfs:
                got = xtf(s, e)
                passed = got.get("KL_XTF_OK") == G.PASS
                failed = got.get("KL_XTF_RATIO") == G.FAIL
                if passed == failed:                      # 既没 PASS 也没 FAIL ⇒ 判据缺失
                    wrong.append((s, e, "判据缺失", got))
                elif passed != ok(s, e):                  # 与语义复算不一致
                    wrong.append((s, e, f"应为{'PASS' if ok(s, e) else 'FAIL'}", got))
        self.assertEqual(wrong, [], f"以下周期对判定与语义不符：{wrong}")

    def test_matrix_shape_is_81(self):
        self.assertEqual(len(G.INTERVAL_MS) ** 2, 81)
        self.assertEqual(sorted(G.INTERVAL_MS),
                         ["12h", "15m", "1d", "1h", "2h", "30m", "4h", "5m", "8h"])

    def test_pass_pair_count_is_recomputable(self):
        """放行对数必须等于复算值（防止悄悄放宽成「任意对都 PASS」）。"""
        tfs = sorted(G.INTERVAL_MS)
        expect = sum(1 for s in tfs for e in tfs if ok(s, e))
        got = sum(1 for s in tfs for e in tfs
                  if xtf(s, e).get("KL_XTF_OK") == G.PASS)
        self.assertEqual(got, expect)
        self.assertLess(got, 81, "不可能 81 对全放行（否则判据形同虚设）")


# ══════════════════════ C 方向敏感性证明（防探针空转）══════════════════════

class TestFixIsDirectionSensitive(unittest.TestCase):
    """A/B 探针必须自证「真的产生了差异」，否则结论无效（21 号踩过这个坑）。"""

    def _old_rule(self, s, e):
        """17 号原判据：执行周期整除信号周期。"""
        return G.INTERVAL_MS[e] % G.INTERVAL_MS[s] == 0

    def test_divergent_pairs_exist_and_are_enumerated(self):
        tfs = sorted(G.INTERVAL_MS)
        divergent = [(s, e) for s in tfs for e in tfs
                     if self._old_rule(s, e) != ok(s, e)]
        self.assertTrue(divergent, "新旧规则完全一致 ⇒ 探针对方向不敏感，结论无效")
        # 每一对分歧都必须真的是「方向翻转」：一方整除另一方不整除
        for s, e in divergent:
            s_iv, e_iv = G.INTERVAL_MS[s], G.INTERVAL_MS[e]
            self.assertTrue((s_iv % e_iv == 0) != (e_iv % s_iv == 0),
                            f"{s}/{e} 不是方向翻转对")
        # 逐对验证实际实现与「新规则」一致、与「旧规则」相反
        for s, e in divergent:
            passed = xtf(s, e).get("KL_XTF_OK") == G.PASS
            self.assertEqual(passed, ok(s, e), f"{s}/{e} 未按新规则判定")
            self.assertNotEqual(passed, self._old_rule(s, e), f"{s}/{e} 未体现方向翻转")

    def test_divergent_pair_count(self):
        """分歧对数量可复算：9×9=81 对里，方向翻转的有 70 对（固定值，变动即判据被改）。

        复算：`|A Δ B| = |A| + |B| - 2|A∩B| = 44 + 44 - 2×9 = 70`，其中 A=`s%e==0`、
        B=`e%s==0`、A∩B 恰为 9 个对角同周期对。
        不敏感的 11 对 = 9 个对角（两个方向都整除）+ 2 个「两个方向都不整除」的
        `8h/12h` 与 `12h/8h` —— 17 号唯一那条 XTF 用例正落在后者，所以测不出方向。
        """
        tfs = sorted(G.INTERVAL_MS)
        divergent = [(s, e) for s in tfs for e in tfs
                     if self._old_rule(s, e) != ok(s, e)]
        self.assertEqual(len(divergent), 70, f"实际 {len(divergent)} 对：{divergent}")
        blind = [(s, e) for s in tfs for e in tfs
                 if self._old_rule(s, e) == ok(s, e)]
        self.assertEqual(len(blind), 11)
        self.assertEqual(sorted(p for p in blind if p[0] != p[1]),
                         [("12h", "8h"), ("8h", "12h")])

    def test_17_case_8h_12h_is_direction_blind(self):
        """17 号那条唯一 XTF 用例为什么测不出方向反了：它对两个方向都不整除。"""
        self.assertFalse(self._old_rule("8h", "12h"))
        self.assertFalse(ok("8h", "12h"))


# ══════════════════════ D 缺失 / 未知周期 ══════════════════════

class TestMissingOrUnknownTimeframe(unittest.TestCase):

    def test_empty_signal_timeframe_raises(self):
        s = snap("1h", "15m")
        s["signal_timeframe"] = ""
        with self.assertRaises(ValueError):
            G.evaluate(s)

    def test_empty_exec_timeframe_raises(self):
        s = snap("1h", "15m")
        s["exec_timeframe"] = ""
        with self.assertRaises(ValueError):
            G.evaluate(s)

    def test_unknown_timeframe_raises_with_choices(self):
        s = snap("1h", "15m")
        s["signal_timeframe"] = "7m"
        with self.assertRaises(ValueError) as ctx:
            G.evaluate(s)
        self.assertIn("7m", str(ctx.exception))

    def test_missing_key_raises_not_silently_passes(self):
        """快照里**根本没有** exec_timeframe 键：必须报错，不得默认成某个周期放行。"""
        s = snap("1h", "15m")
        s.pop("exec_timeframe")
        with self.assertRaises(ValueError):
            G.evaluate(s)

    def test_none_timeframe_raises(self):
        s = snap("1h", "15m")
        s["exec_timeframe"] = None
        with self.assertRaises(ValueError):
            G.evaluate(s)


# ══════════════════════ E ratio 语义 ══════════════════════

class TestRatioSemantics(unittest.TestCase):

    def _ev(self, s, e):
        for c in G._check_cross_tf(s, e, WIN_START, WIN_START + 2 * DAY, 2):
            if c["id"] == "KL_XTF_OK":
                return c["evidence"]
        raise AssertionError(f"{s}/{e} 未产生 KL_XTF_OK")

    def test_ratio_is_exec_bars_per_signal_bar(self):
        self.assertEqual(self._ev("1h", "15m")["ratio"], 4)
        self.assertEqual(self._ev("4h", "15m")["ratio"], 16)
        self.assertEqual(self._ev("1d", "4h")["ratio"], 6)
        self.assertEqual(self._ev("30m", "15m")["ratio"], 2)

    def test_ratio_equals_explicit_key(self):
        for s, e in (("1h", "15m"), ("4h", "15m"), ("1d", "4h")):
            ev = self._ev(s, e)
            self.assertEqual(ev["ratio"], ev["exec_bars_per_signal_bar"])
            self.assertEqual(ev["ratio"], G.INTERVAL_MS[s] // G.INTERVAL_MS[e])

    def test_window_bar_counts(self):
        ev = self._ev("1h", "15m")
        self.assertEqual(ev["window_bars_signal"], 48)     # 2 天 / 1h
        self.assertEqual(ev["window_bars_exec"], 192)      # 2 天 / 15m
        self.assertEqual(ev["window_bars_exec"],
                         ev["window_bars_signal"] * ev["ratio"])

    def test_fail_evidence_carries_remainder(self):
        for c in G._check_cross_tf("15m", "4h", WIN_START, WIN_START + 2 * DAY, 2):
            if c["id"] == "KL_XTF_RATIO":
                self.assertEqual(c["evidence"]["sig_mod_exec"], Q)
                self.assertEqual(c["evidence"]["exec_bars_per_signal_bar"], 0.0625)
                break
        else:
            self.fail("未产生 KL_XTF_RATIO")


# ══════════════════════ F 端到端判定 ══════════════════════

class TestEndToEndVerdict(unittest.TestCase):

    def test_1h_15m_snapshot_passes_end_to_end(self):
        v = G.evaluate(snap("1h", "15m"))
        self.assertEqual(v["verdict"], G.PASS, v["block_reasons"] + v["unproven_reasons"])
        self.assertIn("KL_XTF_OK", [c["id"] for c in v["checks"] if c["status"] == G.PASS])
        self.assertTrue(v["data_qualifies_for_trusted"])
        self.assertTrue(v["trusted_backtest_allowed"])

    def test_15m_4h_snapshot_fails_end_to_end(self):
        """17 号夹具那个组合：修正后必须整条判定落 FAIL，而不是只在单测层面。"""
        v = G.evaluate(snap("15m", "4h"))
        self.assertEqual(v["verdict"], G.FAIL)
        self.assertIn("KL_XTF_RATIO", [c["id"] for c in v["checks"] if c["status"] == G.FAIL])
        self.assertFalse(v["data_qualifies_for_trusted"])
        self.assertFalse(v["trusted_backtest_allowed"])

    def test_4h_15m_snapshot_passes_end_to_end(self):
        v = G.evaluate(snap("4h", "15m"))
        self.assertEqual(v["verdict"], G.PASS, v["block_reasons"] + v["unproven_reasons"])

    def test_old_rule_would_have_passed_the_15m_4h_snapshot(self):
        """反证：同一份 15m/4h 快照在旧规则下是 PASS —— 说明修的是**误判**，不是数据。"""
        s_iv, e_iv = G.INTERVAL_MS["15m"], G.INTERVAL_MS["4h"]
        self.assertEqual(e_iv % s_iv, 0, "旧规则下 15m/4h 应整除")
        self.assertNotEqual(s_iv % e_iv, 0, "新规则下 15m/4h 应不整除")


# ══════════════════════ G 窗口 / 预热判据不受方向修正影响 ══════════════════════

class TestWindowWarmupUnaffected(unittest.TestCase):

    def test_off_grid_window_still_fails(self):
        got = xtf("1h", "15m", we=WIN_START + 2 * DAY + 60_000)
        self.assertEqual(got.get("KL_XTF_WINDOW"), G.FAIL)

    def test_missing_window_is_unproven(self):
        got = xtf("1h", "15m", ws=None, we=None)
        self.assertEqual(got.get("KL_XTF_WINDOW"), G.UNPROVEN)

    def test_negative_warmup_still_fails(self):
        got = xtf("1h", "15m", warmup=-1)
        self.assertEqual(got.get("KL_XTF_WARMUP"), G.FAIL)

    def test_ratio_fail_short_circuits_before_window(self):
        """非法周期对在窗口判据之前就返回 —— 与 17 号行为一致（不因修正而改变顺序）。"""
        got = xtf("15m", "4h", we=WIN_START + 2 * DAY + 60_000)
        self.assertEqual(got, {"KL_XTF_RATIO": G.FAIL})


# ══════════════════════ H 三态不被升格（OI_PIT / 资金费不足仍 UNPROVEN）══════════════════════

class TestThreeStatePreserved(unittest.TestCase):

    def test_oi_pit_still_unproven(self):
        s = snap("1h", "15m")
        s["oi_rows"] = mk_oi(WIN_START - 2 * HOUR, len(s["signal_bars"]) * 12,
                             OI_IV, with_pit=False)
        v = G.evaluate(s)
        self.assertEqual(v["verdict"], G.UNPROVEN)
        self.assertIn("OI_PIT", [c["id"] for c in v["checks"] if c["status"] == G.UNPROVEN])
        self.assertFalse(v["data_qualifies_for_trusted"], "OI_PIT 未证不得计入 data_qualifies")
        self.assertFalse(v["trusted_backtest_allowed"])

    def test_funding_gap_still_unproven_not_fail(self):
        s = snap("1h", "15m")
        ws, we = s["win_start_ms"], s["win_end_ms"]
        rows = mk_funding(ws, we)
        target = [e["funding_time"] for e in rows if ws <= e["funding_time"] < we][1]
        s["funding_rows"] = [e for e in rows if e["funding_time"] != target]
        v = G.evaluate(s)
        self.assertEqual(v["verdict"], G.UNPROVEN)
        self.assertIn("FUNDING_GAP", [c["id"] for c in v["checks"]
                                      if c["status"] == G.UNPROVEN])
        self.assertNotIn("FUNDING_GAP", [c["id"] for c in v["checks"]
                                         if c["status"] == G.FAIL])
        self.assertFalse(v["data_qualifies_for_trusted"])

    def test_direction_fix_does_not_promote_anything(self):
        """修正方向后，1h/15m 干净窗口能 PASS，但「不干净」的仍必须停在 UNPROVEN。"""
        clean = G.evaluate(snap("1h", "15m"))
        self.assertEqual(clean["verdict"], G.PASS)
        dirty = G.evaluate(snap("1h", "15m", oi_rows=[]))
        self.assertEqual(dirty["verdict"], G.UNPROVEN)
        self.assertNotEqual(clean["verdict_sha256"], dirty["verdict_sha256"])

    def test_gate_version_bumped(self):
        """判据变了，判定身份必须变；v1 是方向写反的那一版。

        45 号说明：本条在前像（21/25+38+40v3 组合树）里**是失败的** ——
        它钉死字面量 "v2"，而 40 号已经把 `GATE_VERSION` 推到 "v3"。原因是
        40 号的测试编辑留在了它的隔离树里、没有随补丁一起组合进来。
        本单把断言恢复成 40 号隔离树里的原值 "v3"（`assertEqual`，比原来更严，
        没有放宽口径），并额外钉住本单新增的辅助序列契约轴 `a1`。
        """
        self.assertEqual(G.GATE_VERSION, "v3")
        self.assertEqual(G.AUX_POLICY_VERSION, "a1")


if __name__ == "__main__":
    unittest.main(verbosity=2)
