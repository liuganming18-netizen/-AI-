# -*- coding: utf-8 -*-
"""GM-56-04：数据门禁模式必须能由请求显式指定（回归测试）。

守护的缺陷
──────────
`backtest_gate_wiring.resolve_mode()` 的优先级是
`spec["data_gate_mode"]` > 环境变量 > 默认 `isolated_research`，
但 `event_trend_bridge.validate_run_spec()` 的白名单会把该键丢掉，
且生产代码里没有任何地方设置 `BACKTEST_DATA_GATE_MODE`。
后果：接线层永远吃到默认值 ⇒ `trusted` 分支**不可达** ⇒ 写侧门禁
虽然「接上了」，却没有任何「拒绝」出口（拒绝代码是死代码）。

本测试只锁两件事：
  1. 传了就真进规格、非法值必须报错；
  2. **不传时规格与改动前逐字节相同** —— 不许为了修这个缝隙
     悄悄改变老调用方的 `spec_sha256`，也不许把默认模式改掉。

不涉及：判据阈值、PIT 证据、策略参数。一行都不碰。
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import event_trend_bridge as ETB          # noqa: E402

BASE = {"strategy_id": "WX_EVENT_TREND_V0_1", "symbols": ["BTCUSDT"],
        "exchange": "binance", "market": "um",
        "window_start": "2026-09-21", "window_end": "2026-09-23"}

#: 修复前 `validate_run_spec` 的产出键集合（逐字节基线见 `test_no_key_is_byte_identical`）
BASELINE_KEYS = ["exchange", "exec_timeframe", "initial_equity", "market",
                 "note", "params", "signal_timeframe", "strategy_id",
                 "symbols", "window_end", "window_start"]


class GateModePassthrough56_04(unittest.TestCase):

    def test_no_key_is_byte_identical(self):
        """不传门禁键 ⇒ 键集合与修复前一致，且 JSON 逐字节相同。"""
        clean, errs = ETB.validate_run_spec(dict(BASE))
        self.assertEqual(errs, [])
        self.assertEqual(sorted(clean), BASELINE_KEYS)
        self.assertNotIn("data_gate_mode", clean)
        self.assertNotIn("data_gate_policy", clean)

    def test_trusted_is_carried(self):
        clean, errs = ETB.validate_run_spec({**BASE, "data_gate_mode": " TRUSTED "})
        self.assertEqual(errs, [])
        self.assertEqual(clean["data_gate_mode"], "trusted")

    def test_isolated_is_carried(self):
        clean, errs = ETB.validate_run_spec({**BASE, "data_gate_mode": "isolated_research"})
        self.assertEqual(errs, [])
        self.assertEqual(clean["data_gate_mode"], "isolated_research")

    def test_invalid_mode_rejected_not_downgraded(self):
        """非法值必须报错且不写进规格——静默降级成默认等于把请求吃掉。"""
        clean, errs = ETB.validate_run_spec({**BASE, "data_gate_mode": "always_pass"})
        self.assertTrue(errs)
        self.assertNotIn("data_gate_mode", clean)

    def test_policy_dict_carried(self):
        clean, errs = ETB.validate_run_spec(
            {**BASE, "data_gate_policy": {"oi_pit_required": True}})
        self.assertEqual(errs, [])
        self.assertEqual(clean["data_gate_policy"], {"oi_pit_required": True})

    def test_policy_non_dict_rejected(self):
        clean, errs = ETB.validate_run_spec({**BASE, "data_gate_policy": "oi_pit_required"})
        self.assertTrue(errs)
        self.assertNotIn("data_gate_policy", clean)

    def test_policy_is_copied_not_aliased(self):
        """透传的是副本：调用方后续改自己的 dict 不能影响已落盘规格。"""
        src = {"oi_pit_required": True}
        clean, _ = ETB.validate_run_spec({**BASE, "data_gate_policy": src})
        src["oi_pit_required"] = False
        self.assertTrue(clean["data_gate_policy"]["oi_pit_required"])

    def test_resolve_mode_reads_spec_first(self):
        """规格里的模式必须压过环境变量（否则透传等于没透）。"""
        import backtest_gate_wiring as W
        self.assertEqual(
            W.resolve_mode({"data_gate_mode": "trusted"},
                           env={"BACKTEST_DATA_GATE_MODE": "isolated_research"}),
            "trusted")

    def test_default_mode_unchanged(self):
        """默认仍是 isolated_research——本单不授权把默认改成 trusted。"""
        import backtest_gate_wiring as W
        self.assertEqual(W.DEFAULT_MODE, "isolated_research")
        self.assertEqual(W.resolve_mode({}, env={}), "isolated_research")


if __name__ == "__main__":
    unittest.main(verbosity=2)
