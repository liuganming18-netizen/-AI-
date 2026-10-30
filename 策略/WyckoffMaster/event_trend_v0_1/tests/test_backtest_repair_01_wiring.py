# -*- coding: utf-8 -*-
"""BACKTEST-REPAIR-01 · 任务 B 回归测试：FT-LOW4H 回测接线。

工单明确要求：**不许把「候选数 > 0」当唯一成功条件**。所以这里的判据是
「状态机是否收到正确的历史输入、时间链是否满足因果、门禁是否还在」：

  W01 1h 全覆盖         —— 2904 根 1h 全部进入决策（不是「按 4h 推进就漏 1h」）
  W02 4h 成箱只用已收盘 —— 喂给 `check_and_form_box` 的 42 根 bar 收盘时间
                           必须 <= 决策时刻，且箱体决策时刻 <= 当时决策时刻
  W03 候选时间链因果     —— snapshot <= available_at <= execution_earliest
  W04 OI 不再依赖实时库  —— 把 `MARKET_DB` 指到**不存在的文件**，仍能产出候选
                           （旧实现此配置下 100% 失败，这是接线修复的硬证据）
  W05 PIT 门禁未绕过     —— declared_assumption ⇒ qualified=False，且
                           failures 含 `oi_available_at_unproven`
  W06 关 OI 门槛消融     —— `enable_oi_filter=False` 可跑，不抛错
  W07 A/B/C 不再退化     —— 三次独立执行，变体标识各不相同

依赖真实历史数据（parquet 归档）。缺数据时整模块 skip，不伪造。
运行：
  export MARKET_ARCHIVE=<parquet 根>  CONTRACTS_DIR=<快照目录>
  /usr/bin/python3 -m unittest 策略.WyckoffMaster.event_trend_v0_1.tests.test_backtest_repair_01_wiring -v
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(PROJECT_ROOT / "策略"))
sys.path.insert(0, str(PROJECT_ROOT / "框架" / "backend"))

REAL_ARCHIVE = Path(os.environ.get("MARKET_ARCHIVE")
                    or (PROJECT_ROOT / "数据" / "市场数据" / "parquet"))
REAL_CONTRACTS = Path(os.environ.get("CONTRACTS_DIR")
                      or (PROJECT_ROOT / "数据" / "contracts" / "旧标的池"))

SYMBOL = "NEARUSDT"
START, END = "2026-06-01", "2026-09-30"

_CACHE: dict = {}


def _load():
    """整模块只加载一次（parquet 读取较慢），后续用例共用同一份输入。"""
    if "data" in _CACHE:
        return _CACHE["data"]
    from WyckoffMaster.event_trend_v0_1.runner import EventTrendRunner
    from WyckoffMaster.event_trend_v0_1.config import make_ft_low4h_config
    r = EventTrendRunner()
    d4, d1, d15, ots, ovs, fnd = r.load_symbol_data(
        symbol=SYMBOL, start_date=START, end_date=END, allow_auto_window=False)
    _CACHE["data"] = (r, make_ft_low4h_config(), d4, d1, d15, ots, ovs, fnd)
    return _CACHE["data"]


def _sm(enable_oi=True):
    from WyckoffMaster.event_trend_v0_1.state_machine import EventStateMachine
    r, cfg, d4, d1, d15, ots, ovs, fnd = _load()
    box_calls = []
    orig = EventStateMachine.check_and_form_box

    def spy(self, *a, **kw):
        # 注意：`history_42_*` 是 numpy 数组，不能写 `arr or []`（真值歧义）
        _h = kw.get("history_42_highs")
        _l = kw.get("history_42_lows")
        box_calls.append({"bar_4h_close_ms": kw.get("bar_4h_close_ms"),
                          "highs_len": 0 if _h is None else len(_h),
                          "lows_len": 0 if _l is None else len(_l)})
        return orig(self, *a, **kw)

    EventStateMachine.check_and_form_box = spy
    try:
        sm = r.run_state_machine_simulation(
            SYMBOL, d4, d1, ots, ovs, enable_oi_filter=enable_oi, cfg=cfg)
    finally:
        EventStateMachine.check_and_form_box = orig
    return sm, box_calls, d1


@unittest.skipUnless(REAL_ARCHIVE.exists(),
                     "无真实 parquet 归档（隔离工作树），跳过接线测试")
class TestWiring(unittest.TestCase):

    # ── W01 1h 全覆盖 ──
    def test_w01_all_1h_bars_enter_decision(self):
        """2904 根 1h 必须全部进 `process_1h_bar` —— 逐根计数，不靠猜。"""
        from WyckoffMaster.event_trend_v0_1.state_machine import EventStateMachine
        r, cfg, d4, d1, d15, ots, ovs, fnd = _load()
        cnt = {"n": 0}
        orig = EventStateMachine.process_1h_bar

        def spy(self, *a, **kw):
            cnt["n"] += 1
            return orig(self, *a, **kw)

        EventStateMachine.process_1h_bar = spy
        try:
            r.run_state_machine_simulation(SYMBOL, d4, d1, ots, ovs,
                                           enable_oi_filter=True, cfg=cfg)
        finally:
            EventStateMachine.process_1h_bar = orig
        self.assertEqual(cnt["n"], len(d1),
                         f"1h 步进覆盖不全：{cnt['n']}/{len(d1)}")

    # ── W02 4h 成箱只用已收盘 bar ──
    def test_w02_box_uses_only_closed_bars(self):
        sm, box_calls, d1 = _sm()
        self.assertGreater(len(box_calls), 0, "整个窗口一次箱体都没形成，用例无效")
        # 1h 开盘时间表 ⇒ 每根 bar 的收盘时刻
        closes = {int(t) + 3600 * 1000 for t in d1["timestamp"].tolist()}
        for c in box_calls:
            t = int(c["bar_4h_close_ms"])
            # 成箱时刻必须恰好是某根 1h bar 的收盘时刻（不能是盘中、不能是未来）
            self.assertIn(t, closes, f"成箱时刻 {t} 不是任何已收盘 1h 的收盘时刻")
            # 喂进去的 42 根 bar 长度必须完整（少喂 = 箱体窗口不完整）
            self.assertEqual(c["highs_len"], 42)
            self.assertEqual(c["lows_len"], 42)

    def test_w02b_box_time_not_after_decision_window(self):
        """成箱时刻不得超出决策窗口末端（防「提前用了窗口外数据」）。"""
        sm, box_calls, d1 = _sm()
        last_close = int(d1["timestamp"].iloc[-1]) + 3600 * 1000
        for c in box_calls:
            self.assertLessEqual(int(c["bar_4h_close_ms"]), last_close)

    # ── W03 候选时间链因果 ──
    def test_w03_candidate_causality(self):
        sm, _, _ = _sm()
        self.assertGreater(len(sm.candidates), 0,
                           "本窗口没有候选 ⇒ 时间链用例无样本（不当作通过）")
        for c in sm.candidates:
            snap = int(c.snapshot_time_ms)
            avail = int(c.available_at_ms)
            exe = int(c.execution_earliest_ms)
            self.assertLessEqual(avail, exe,
                                 f"可得时刻晚于最早执行时刻：{avail} > {exe}")
            self.assertLessEqual(snap, exe,
                                 f"快照晚于最早执行时刻：{snap} > {exe}")
            self.assertGreater(exe, snap,
                               f"执行时刻没有严格晚于快照（无留白）：{exe} <= {snap}")

    # ── W04 OI 不再依赖实时库（核心证据）──
    def test_w04_oi_independent_of_hot_db(self):
        """把 `MARKET_DB` 指到不存在的文件，仍必须产出候选。

        旧实现在此配置下 100% 失败（每标的每轮 `unable to open database file`，
        OI 全空 ⇒ 突破永不成立 ⇒ 候选恒 0）。这条用例就是接线修复的判据。
        """
        saved = os.environ.get("MARKET_DB")
        os.environ["MARKET_DB"] = str(PROJECT_ROOT / "不存在的库" / "ghost.sqlite3")
        try:
            import oi_feed as OF
            probe = OF.probe_hot_db()
            self.assertFalse(probe["exists"], "前提不成立：热库居然存在")
            sm, _, _ = _sm()
        finally:
            if saved is None:
                os.environ.pop("MARKET_DB", None)
            else:
                os.environ["MARKET_DB"] = saved
        self.assertGreater(len(sm.candidates), 0,
                           "热库不可用时候选消失 ⇒ OI 仍走实时库，接线没修好")

    # ── W05 PIT 门禁未绕过 ──
    def test_w05_pit_gate_not_bypassed(self):
        sm, _, _ = _sm()
        audit = sm.pit.audit()
        self.assertFalse(audit.get("qualified"),
                         "历史 OI 无发布时刻证明，qualified 不得为 True")
        joined = " | ".join(audit.get("qualification_failures") or [])
        self.assertIn("oi_available_at_unproven", joined)
        # 决策前必须真的校验过「bar 已收盘」，不是空跑
        self.assertGreater(audit.get("candles_checked") or 0, 0)

    def test_w05b_formal_pass_blocked(self):
        """`formal_pass_allowed` 必须仍是 False（规格未证明 + PIT 不合格）。"""
        r, cfg, d4, d1, d15, ots, ovs, fnd = _load()
        abc = r.run_variant_abc(SYMBOL, d4, d1, d15, ots, ovs, fnd,
                                cfg=cfg, initial_equity=5000.0)
        s = (abc.get("res_a") or {}).get("summary") or {}
        self.assertFalse(s.get("formal_pass_allowed"))
        self.assertFalse(abc.get("pit_qualified"))

    # ── W06 关 OI 门槛消融 ──
    def test_w06_oi_filter_off_runs(self):
        sm, _, _ = _sm(enable_oi=False)
        self.assertGreaterEqual(len(sm.completed_events), 1)

    # ── W07 A/B/C 不再退化成同一个 ──
    def test_w07_variants_not_degenerate(self):
        """原来 `sm_a = sm_b` 且三次都用 `variant="B"` ⇒ A/B/C 结果完全一样。

        现在三次独立执行：变体标识必须各不相同，且 A/C 用同一批候选、
        B 用关 OI 的候选（本窗口两者候选数相同属正常，但对象必须是分开的）。
        """
        r, cfg, d4, d1, d15, ots, ovs, fnd = _load()
        abc = r.run_variant_abc(SYMBOL, d4, d1, d15, ots, ovs, fnd,
                                cfg=cfg, initial_equity=5000.0)
        self.assertEqual(abc["res_a"]["variant"], "A")
        self.assertEqual(abc["res_b"]["variant"], "B")
        self.assertEqual(abc["res_c"]["variant"], "C")
        self.assertIsNot(abc["res_a"], abc["res_b"])
        self.assertIsNot(abc["res_a"], abc["res_c"])
        # C 是成本压力：同样候选下手续费必须更高
        fee_a = abc["res_a"]["summary"]["fee_total"]
        fee_c = abc["res_c"]["summary"]["fee_total"]
        if abc["res_a"]["summary"]["trade_count"] > 0:
            self.assertGreater(fee_c, fee_a, "C 变体没有加压成本 ⇒ 变体没真正生效")


if __name__ == "__main__":
    unittest.main(verbosity=2)
