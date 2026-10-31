import unittest
import sys
import os
from typing import List, Dict, Any

# 强制加载当前隔离工作树源码
sys.path.insert(0, "/tmp/gm50_fix_wt/框架/backend")
sys.path.insert(0, "/tmp/gm50_fix_wt/框架/backend/tests")

from backtest_data_gate import _check_oi, PASS, UNPROVEN

class TestOiPitBoundaryAndMultiTimeframe50(unittest.TestCase):
    """50 号工单新增：时间边界、时间单位及多周期 OI PIT 严格因果测试"""

    def setUp(self):
        self.policy = {
            "oi_min_coverage_ratio": 0.95,
            "oi_pit_required": True,
            "oi_max_staleness_ms": 300_000
        }

    def test_01_multi_tf_15m_oi_bucket_calculation(self):
        """1. 15m 周期 OI 桶（900,000ms）自动计算 bucket_end 并拦截未闭合"""
        sig_bars = [{"open_time": 1000_000 + i * 3600_000} for i in range(10)]
        win_start = 1000_000
        win_end = 1000_000 + 10 * 3600_000
        # 构造 15m OI 数据，步长为 900_000
        # 桶未闭合：在 ts + 500_000 提前到达 (< ts + 900_000)
        rows_unclosed = [{"ts": win_start + i * 900_000, "oi": 100.0, "available_at": win_start + i * 900_000 + 500_000} for i in range(40)]
        res_u = _check_oi(rows_unclosed, sig_bars, "1h", win_start, win_end, self.policy)
        pit_u = [c for c in res_u if c["id"] == "OI_PIT"][0]
        self.assertEqual(pit_u["status"], UNPROVEN)
        self.assertIn("前视未闭合违规", pit_u["detail"])

        # 桶已闭合：在 ts + 900_000 闭合时刻到达
        rows_closed = [{"ts": win_start + i * 900_000, "oi": 100.0, "available_at": win_start + i * 900_000 + 900_000} for i in range(40)]
        res_c = _check_oi(rows_closed, sig_bars, "1h", win_start, win_end, self.policy)
        pit_c = [c for c in res_c if c["id"] == "OI_PIT"][0]
        self.assertEqual(pit_c["status"], PASS)

    def test_02_multi_tf_1h_oi_bucket_calculation(self):
        """2. 1h 周期 OI 桶（3,600,000ms）因果计算与闭合验证"""
        sig_bars = [{"open_time": 1000_000 + i * 3600_000} for i in range(10)]
        win_start = 1000_000
        win_end = 1000_000 + 10 * 3600_000
        rows_1h = [{"ts": win_start + i * 3600_000, "oi": 100.0, "available_at": win_start + (i + 1) * 3600_000} for i in range(10)]
        res = _check_oi(rows_1h, sig_bars, "1h", win_start, win_end, self.policy)
        pit = [c for c in res if c["id"] == "OI_PIT"][0]
        self.assertEqual(pit["status"], PASS)

    def test_03_explicit_close_time_field_priority(self):
        """3. 数据行显式提供 close_time 时，以显式字段优先，不依赖推断"""
        sig_bars = [{"open_time": 1000_000 + i * 3600_000} for i in range(10)]
        win_start = 1000_000
        win_end = 1000_000 + 10 * 3600_000
        # 即使 ts 间隔是 300_000，但行内明确标注 close_time 为 ts + 60_000
        rows = [{
            "ts": win_start + i * 300_000,
            "close_time": win_start + i * 300_000 + 60_000,
            "oi": 100.0,
            "available_at": win_start + i * 300_000 + 60_000
        } for i in range(120)]
        res = _check_oi(rows, sig_bars, "1h", win_start, win_end, self.policy)
        pit = [c for c in res if c["id"] == "OI_PIT"][0]
        self.assertEqual(pit["status"], PASS)

    def test_04_policy_semantic_bucket_end_override(self):
        """4. policy 显式指定 oi_timestamp_semantic='bucket_end' 时支持快照点 semantics"""
        sig_bars = [{"open_time": 1000_000 + i * 3600_000} for i in range(10)]
        win_start = 1000_000
        win_end = 1000_000 + 10 * 3600_000
        pol = dict(self.policy)
        pol["oi_timestamp_semantic"] = "bucket_end"
        rows = [{"ts": win_start + i * 300_000, "oi": 100.0, "available_at": win_start + i * 300_000} for i in range(120)]
        res = _check_oi(rows, sig_bars, "1h", win_start, win_end, pol)
        pit = [c for c in res if c["id"] == "OI_PIT"][0]
        self.assertEqual(pit["status"], PASS)

    def test_05_sub_millisecond_boundary_exact_match(self):
        """5. 毫秒级因果精准边界：avail == bucket_end 通过，avail == bucket_end - 1 拦截"""
        sig_bars = [{"open_time": 1000_000 + i * 3600_000} for i in range(10)]
        win_start = 1000_000
        win_end = 1000_000 + 10 * 3600_000

        # 精确相差 1 毫秒（提前 1ms）
        rows_minus_1 = [{"ts": win_start + i * 300_000, "oi": 100.0, "available_at": win_start + (i + 1) * 300_000 - 1} for i in range(120)]
        res_m1 = _check_oi(rows_minus_1, sig_bars, "1h", win_start, win_end, self.policy)
        pit_m1 = [c for c in res_m1 if c["id"] == "OI_PIT"][0]
        self.assertEqual(pit_m1["status"], UNPROVEN)
        self.assertIn("前视未闭合违规", pit_m1["detail"])

        # 精确等于 bucket_end (闭合时刻到达)
        rows_exact = [{"ts": win_start + i * 300_000, "oi": 100.0, "available_at": win_start + (i + 1) * 300_000} for i in range(120)]
        res_ex = _check_oi(rows_exact, sig_bars, "1h", win_start, win_end, self.policy)
        pit_ex = [c for c in res_ex if c["id"] == "OI_PIT"][0]
        self.assertEqual(pit_ex["status"], PASS)

    def test_06_window_end_tolerance_boundary(self):
        """6. 回测窗口结束时间边界：在容差范围内放行，超出容差判定为未来违规"""
        sig_bars = [{"open_time": 1000_000 + i * 3600_000} for i in range(10)]
        win_start = 1000_000
        win_end = 1000_000 + 10 * 3600_000 # 37_000_000
        # end_tol = max(300_000, 3600_000) = 3600_000. max_eval_time = 37_000_000 + 3600_000 = 40_600_000

        # 构造一个到达时间刚好在 max_eval_time + 1ms 的数据
        rows_over = [{"ts": win_start + i * 300_000, "oi": 100.0, "available_at": win_start + (i + 1) * 300_000} for i in range(120)]
        rows_over[-1]["available_at"] = 40_600_001 # 超出容差 1ms
        res_ov = _check_oi(rows_over, sig_bars, "1h", win_start, win_end, self.policy)
        pit_ov = [c for c in res_ov if c["id"] == "OI_PIT"][0]
        self.assertEqual(pit_ov["status"], UNPROVEN)
        self.assertIn("未来数据违规", pit_ov["detail"])

    def test_07_empty_signals_graceful(self):
        """7. 信号列表为空时安全返回 OI_NO_BARS，不崩溃"""
        rows = [{"ts": 1000_000, "oi": 100.0, "available_at": 1300_000}]
        res = _check_oi(rows, [], "1h", 1000_000, 2000_000, self.policy)
        self.assertEqual(res[0]["id"], "OI_NO_BARS")
        self.assertEqual(res[0]["status"], UNPROVEN)

    def test_08_disordered_rows_sorted_automatically(self):
        """8. 输入乱序持仓量数据自动按时间戳正确排序，不影响因果计算"""
        sig_bars = [{"open_time": 1000_000 + i * 3600_000} for i in range(10)]
        win_start = 1000_000
        win_end = 1000_000 + 10 * 3600_000
        rows = [{"ts": win_start + i * 300_000, "oi": 100.0, "available_at": win_start + (i + 1) * 300_000} for i in range(120)]
        rows_scrambled = list(reversed(rows))
        res = _check_oi(rows_scrambled, sig_bars, "1h", win_start, win_end, self.policy)
        pit = [c for c in res if c["id"] == "OI_PIT"][0]
        self.assertEqual(pit["status"], PASS)

    def test_09_all_available_at_missing_stays_unproven(self):
        """9. 全部数据点缺失到达时间严格保持 UNPROVEN，不报异常"""
        sig_bars = [{"open_time": 1000_000 + i * 3600_000} for i in range(10)]
        win_start = 1000_000
        win_end = 1000_000 + 10 * 3600_000
        rows_no_avail = [{"ts": win_start + i * 300_000, "oi": 100.0} for i in range(120)]
        res = _check_oi(rows_no_avail, sig_bars, "1h", win_start, win_end, self.policy)
        pit = [c for c in res if c["id"] == "OI_PIT"][0]
        self.assertEqual(pit["status"], UNPROVEN)
        self.assertIn("缺失有效到达时间", pit["detail"])

    def test_10_mixed_taint_and_gapfill_rejected(self):
        """10. 混合 gapfill 及 taint 污染数据 100% 拦截为 UNPROVEN"""
        sig_bars = [{"open_time": 1000_000 + i * 3600_000} for i in range(10)]
        win_start = 1000_000
        win_end = 1000_000 + 10 * 3600_000
        rows = [{"ts": win_start + i * 300_000, "oi": 100.0, "available_at": win_start + (i + 1) * 300_000} for i in range(120)]
        rows[50]["source"] = "archive_gapfill_fix"
        res = _check_oi(rows, sig_bars, "1h", win_start, win_end, self.policy)
        pit = [c for c in res if c["id"] == "OI_PIT"][0]
        self.assertEqual(pit["status"], UNPROVEN)
        self.assertIn("受回填/推断污染", pit["detail"])

if __name__ == "__main__":
    unittest.main()
