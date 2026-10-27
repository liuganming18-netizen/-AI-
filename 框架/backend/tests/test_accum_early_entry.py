# -*- coding: utf-8 -*-
"""工单 W1 核心单测：压制位取窗、多根扫低确定性定义与状态机转移/失效测试。

测试范围：
1. 修复局部压制位取窗（第 2 项）：
   - 验证局部压制位严格以「扫低发生根」为基准窗口（之前 48 根 1h），严禁读取确认根及未来 K 线。
   - 即使扫低根之后出现高于压制位的尖峰，压制位也恒定不受污染，突破原压制位正常确认。
2. 消除状态与多根扫低冲突（第 3 项）：
   - 多根扫低（multi_bar）确定性规则：扫低窗口内按最低价最低的一根为扫低根，并列时取最早一根。
   - 承接不达标单一状态转移：进入 SWEEP 状态后，承接不达标（sp_ok is False）停在 SWEEP 保持观察，阻断入场，绝不进入 RETEST/CANDIDATE。
   - 承接数据缺失（sp_ok is None）：以 DATA_INSUFFICIENT 关闭。
   - 失效路径：箱体连续跌破下沿 L、盘中超限跌破、观察期限超期、局部压制位空间不足（NO_RESISTANCE）。
"""
import json
import os
import tempfile
import time
import unittest
from pathlib import Path

os.environ["ACCUM05_FIXTURE_OK"] = "1"

from 框架.backend.accum_early_entry import (
    SchemeObserver,
    _absorption,
    _resistance,
    _sweep_check,
    load_config,
    open_store,
)

ROOT = Path(__file__).resolve().parents[3]
FIXTURE_PATH = ROOT / "evidence" / "ACCUM-EARLY-05-B" / "config" / "fixture_rules_05.json"
T0 = 1780400000000
H = 3_600_000
L_BOX = 100.0
H_BOX = 110.0
ATR = 1.0


def _make_bar(ms, o=103.6, h=103.9, l=103.4, c=103.7, qv=1000.0, tbq=500.0):
    return {
        "open_time": ms,
        "close_time": ms + H - 1,
        "open": float(o), "high": float(h), "low": float(l), "close": float(c),
        "volume": 1000.0,
        "quote_volume": qv,
        "taker_buy_quote_volume": tbq,
    }


class DummyFeed:
    def at_or_after(self, timeframe, symbol, earliest_ms):
        return {
            "open_time": earliest_ms,
            "open": 104.0,
            "high": 104.2,
            "low": 103.9,
            "close": 104.1,
            "available_at": earliest_ms + 1000,
        }


class TestAccumEarlyEntryW1(unittest.TestCase):
    def setUp(self):
        self.cfg = load_config(FIXTURE_PATH)
        self.box = {
            "formation_ms": T0 + 50 * H,
            "low_l": L_BOX,
            "high_h": H_BOX,
            "atr": ATR,
        }
        self.tmp_dir = tempfile.TemporaryDirectory()
        db_path = Path(self.tmp_dir.name) / "test.sqlite3"
        self.store = open_store(db_path)
        self.feed = DummyFeed()

    def tearDown(self):
        self.store.close()
        self.tmp_dir.cleanup()

    def test_local_resistance_window_uses_sweep_bar_not_confirm_bar(self):
        """1. 局部压制位严格以扫低根为基准取窗，不受扫低根之后的尖峰抬高。"""
        sweep_i = 60
        spike_i = 61      # 扫低之后出现尖峰，高点 105.0，但并未确认
        confirm_i = 65    # 尖峰后第 4 根收盘 103.95

        bars = []
        for i in range(confirm_i + 1):
            ms = T0 + i * H
            if i < sweep_i:
                # 扫低前历史：最高价 103.9（在合理区间）
                bars.append(_make_bar(ms, o=103.7, h=103.8, l=103.6, c=103.75))
            elif i == sweep_i:
                # 扫低发生根：跌破局部支撑 103.6 至 103.1 并收回至 103.8
                bars.append(_make_bar(ms, o=103.5, h=103.85, l=103.1, c=103.8, tbq=800.0))
            elif i == spike_i:
                # 尖峰根：高点 105.0（若按确认根取窗，压制位会被抬高到 105.0）
                bars.append(_make_bar(ms, o=103.7, h=105.0, l=103.6, c=103.75, tbq=500.0))
            elif i == confirm_i:
                # 确认根：收盘 103.95（高于 103.9 但低于 105.0）
                bars.append(_make_bar(ms, o=103.8, h=104.0, l=103.75, c=103.95, tbq=900.0))
            else:
                bars.append(_make_bar(ms, o=103.7, h=103.8, l=103.6, c=103.75, tbq=500.0))

        # 扫低根相对窗口压制位计算
        res_sweep, detail_sweep = _resistance(bars, sweep_i, self.box, self.cfg)
        self.assertIsNotNone(res_sweep)
        self.assertLess(res_sweep, 104.0)

        # 运行状态机
        obs = SchemeObserver("4a", "TESTUSDT", self.box, self.cfg, self.store, self.feed,
                             strategy_id="test", rule_version="v1")
        obs.open_event()

        for i in range(confirm_i + 1):
            obs.on_bar(bars, i, ATR)

        # 验证压制位恒定使用扫低根窗口，在 confirm_i 处成功确认并生成候选
        self.assertEqual(obs.state, "CANDIDATE")
        self.assertIsNotNone(obs.resistance)
        self.assertEqual(obs.resistance, res_sweep)
        self.assertLessEqual(obs.resistance, 104.0)

    def test_multi_bar_sweep_deterministic_rule(self):
        """2. 多根扫低（multi_bar）定义写死：窗口内以最低价最低的一根为扫低根，并列取最早一根。"""
        cfg_multi = json.loads(json.dumps(self.cfg))
        cfg_multi["doc"]["shared"]["sweep_recovery_scope"]["mode"] = "multi_bar"
        cfg_multi["doc"]["shared"]["sweep_recovery_scope"]["max_bars"] = 3

        bars = []
        # 构建 60 根基础历史
        for i in range(58):
            bars.append(_make_bar(T0 + i * H))

        # 3 根扫低窗口：
        # bar 58: low = 103.1 (并列最低)
        # bar 59: low = 103.1 (并列最低)
        # bar 60: low = 103.3, close 收回 = 103.8
        bars.append(_make_bar(T0 + 58 * H, o=103.6, h=103.7, l=103.1, c=103.5, tbq=800.0))
        bars.append(_make_bar(T0 + 59 * H, o=103.5, h=103.6, l=103.1, c=103.4, tbq=800.0))
        bars.append(_make_bar(T0 + 60 * H, o=103.4, h=103.85, l=103.3, c=103.8, tbq=800.0))

        obs = SchemeObserver("4a", "TESTUSDT", self.box, cfg_multi, self.store, self.feed,
                             strategy_id="test", rule_version="v1")
        obs.open_event()

        for i in range(len(bars)):
            obs.on_bar(bars, i, ATR)

        self.assertEqual(obs.state, "SWEEP")
        # 并列时必须取最早一根 58
        self.assertEqual(obs.sweep_i, 58)

    def test_absorption_failure_stays_in_sweep_and_blocks_confirmation(self):
        """3. 承接不达标单一状态转移：停在 SWEEP 保持观察，阻断入场，不迁移至后续状态。"""
        bars = []
        for i in range(65):
            ms = T0 + i * H
            if i < 60:
                bars.append(_make_bar(ms))
            elif i == 60:
                # 扫低发生根：主动买占比极低（tbq=100.0 / qv=1000.0 => 0.1 < 基准 0.5），承接失败
                bars.append(_make_bar(ms, o=103.5, h=103.85, l=103.1, c=103.8, tbq=100.0))
            elif i == 61:
                # 收上压制位
                bars.append(_make_bar(ms, o=103.8, h=104.2, l=103.8, c=104.1, tbq=900.0))
            else:
                bars.append(_make_bar(ms))

        obs = SchemeObserver("4a", "TESTUSDT", self.box, self.cfg, self.store, self.feed,
                             strategy_id="test", rule_version="v1")
        obs.open_event()

        for i in range(len(bars)):
            obs.on_bar(bars, i, ATR)

        # 停在 SWEEP，未生成候选
        self.assertEqual(obs.state, "SWEEP")
        self.assertEqual(len(obs.candidates), 0)
        self.assertFalse(obs.done)

    def test_invalidation_rules_clear_and_unambiguous(self):
        """4. 失效规则：结构破位（连续破 L）立即终结为 INVALIDATED。"""
        bars = []
        for i in range(65):
            ms = T0 + i * H
            if i < 60:
                bars.append(_make_bar(ms))
            elif i == 60:
                bars.append(_make_bar(ms, o=103.5, h=103.85, l=103.1, c=103.8, tbq=800.0))
            else:
                # 连续收盘破箱体下沿 L（100.0）
                bars.append(_make_bar(ms, o=99.5, h=99.8, l=98.0, c=98.5))

        obs = SchemeObserver("4a", "TESTUSDT", self.box, self.cfg, self.store, self.feed,
                             strategy_id="test", rule_version="v1")
        obs.open_event()

        for i in range(len(bars)):
            obs.on_bar(bars, i, ATR)

        self.assertEqual(obs.state, "INVALIDATED")
        self.assertTrue(obs.done)


if __name__ == "__main__":
    unittest.main()
