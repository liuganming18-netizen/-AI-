#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""BT-UI-02 回归：盘上 `chart.json` 的窗口必须与本 run 自己的窗口一致（只读、零网络）。

对应一个真实问题：
`回测日志/event_trend_v0_1/runs/` 下有 83 个 run 的 `chart.json` 窗口被写成固定的
`2024-01-01 ~ 2024-06-30`，而它们真实窗口在 2021–2023。这些 run 的 89 笔成交里有
85 笔落在图外 —— 页面「有图」，但图上的 K 线不是这笔交易发生的那一段。

成因：旧读接口在缺 `chart.json` 时用写死窗口重建，并把结果 `write_chart()` 写回 run 目录。
BT-UI-01 已切断写回，但盘上那批错图还在。所以读取侧必须**拒收窗口不符的盘上图表**，
改按本 run 自己的窗口重建；且**绝不写盘**。

四条断言：
  1. 窗口不符的盘上图表被拒收，返回的图窗口 == 本 run 自己的窗口；
  2. 拒收原因写进 `derived_from`，页面上能看见「为什么换了一张图」；
  3. 窗口一致的盘上图表**照常原样返回**（不误伤 1578 个正常 run）；
  4. 全过程 run 目录字节不变（`chart.json` 的 sha256 前后相同）。
"""
import hashlib
import json
import os
import sys
import unittest
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.dirname(HERE)
FRAMEWORK = os.path.dirname(BACKEND)
for _p in (BACKEND, FRAMEWORK):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import backtest_api as A          # noqa: E402
import backtest_store as S        # noqa: E402

DEFAULT_ROOT = S.get_runs_root()


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _own_window(d: Path):
    """本 run 自己的窗口（与 `_run_own_window` 同源：inputs.json 优先）。"""
    try:
        inp = json.loads((d / "inputs.json").read_text(encoding="utf-8"))
    except Exception:                                      # noqa: BLE001
        return None, None
    tr = ((inp.get("data_summary") or {}).get("time_range_1h") or {})
    ws, we = tr.get("start_ms"), tr.get("end_ms")
    if isinstance(ws, int) and isinstance(we, int) and ws > 0 and we > ws:
        return ws, we
    return None, None


def _scan(pred, limit=4000):
    """在默认根里找第一个满足 pred 的 run 目录。"""
    if not DEFAULT_ROOT.is_dir():
        return None
    for i, d in enumerate(sorted(DEFAULT_ROOT.iterdir())):
        if i > limit:
            break
        if not d.is_dir() or not d.name.startswith("run_"):
            continue
        if not (d / "chart.json").is_file() or not (d / "inputs.json").is_file():
            continue
        ws, we = _own_window(d)
        if ws is None:
            continue
        try:
            cj = json.loads((d / "chart.json").read_text(encoding="utf-8"))
        except Exception:                                  # noqa: BLE001
            continue
        w = cj.get("window") or {}
        cws, cwe = w.get("start_ms"), w.get("end_ms")
        if not isinstance(cws, int) or not isinstance(cwe, int):
            continue
        if pred(ws, we, cws, cwe):
            return d, ws, we, cws, cwe
    return None, None, None, None, None


def _mismatched(ws, we, cws, cwe):
    return abs(cws - ws) > A._CHART_WINDOW_TOLERANCE_MS or abs(cwe - we) > A._CHART_WINDOW_TOLERANCE_MS


def _aligned(ws, we, cws, cwe):
    return not _mismatched(ws, we, cws, cwe)


@unittest.skipUnless(DEFAULT_ROOT.is_dir(), "默认运行根不存在（离线环境）")
class TestChartWindowGuard(unittest.TestCase):
    """① ② 窗口不符 ⇒ 拒收并按其本窗重建；③ 窗口一致 ⇒ 原样返回；④ 不写盘。"""

    def test_guard_rejects_mismatched_and_keeps_aligned(self):
        d, ws, we, cws, cwe = _scan(_mismatched)
        if d is None:
            self.skipTest("默认根里没有「盘上图表窗口与本 run 不符」的样本")
        # 直接对守卫函数断言，避免把整张 6MB 图搬进内存
        chart = {"window": {"start_ms": cws, "end_ms": cwe}}
        reason = A._chart_window_reject_reason(d.name, chart)
        self.assertIsNotNone(reason, "窗口不符的盘上图表必须被拒收")
        self.assertIn("不符", reason)
        # 对齐的不误伤
        ok = {"window": {"start_ms": ws, "end_ms": we}}
        self.assertIsNone(A._chart_window_reject_reason(d.name, ok))

    def test_mismatched_chart_is_rebuilt_on_own_window_and_not_written(self):
        d, ws, we, cws, cwe = _scan(_mismatched)
        if d is None:
            self.skipTest("默认根里没有「盘上图表窗口与本 run 不符」的样本")
        chart_file = d / "chart.json"
        before = _sha256(chart_file)

        body, code = A.get_chart(d.name)
        self.assertEqual(code, 200)
        chart = body.get("chart")
        self.assertIsNotNone(chart, f"应能按本 run 窗口重建：{body.get('missing_reason')}")
        got = chart.get("window") or {}
        self.assertEqual(int(got["start_ms"]), int(ws),
                         f"返回的图必须画在本 run 自己的窗口上（原盘上是 {cws}）")
        self.assertEqual(int(got["end_ms"]), int(we))
        # 拒收原因必须可见，不许悄悄换图
        self.assertIn("拒收", str(chart.get("derived_from") or ""))
        # ④ 读接口不得改写 run 目录
        self.assertEqual(_sha256(chart_file), before, "读接口不得改写盘上 chart.json")

    def test_aligned_chart_served_as_is(self):
        d, ws, we, cws, cwe = _scan(_aligned)
        if d is None:
            self.skipTest("默认根里没有「盘上图表窗口与本 run 一致」的样本")
        before = _sha256(d / "chart.json")
        body, code = A.get_chart(d.name)
        self.assertEqual(code, 200)
        chart = body.get("chart") or {}
        got = chart.get("window") or {}
        self.assertEqual(int(got["start_ms"]), int(cws),
                         "窗口一致的盘上图表必须原样返回，不能被重建顶掉")
        self.assertEqual(_sha256(d / "chart.json"), before)


@unittest.skipUnless(DEFAULT_ROOT.is_dir(), "默认运行根不存在（离线环境）")
class TestChartTradeStopBackfill(unittest.TestCase):
    """⑤ 图上成交点的初始止损不得是空的/0（账本里有真值就必须补上）。"""

    def _pick(self):
        if not DEFAULT_ROOT.is_dir():
            return None
        for i, d in enumerate(sorted(DEFAULT_ROOT.iterdir())):
            if i > 600:
                break
            if not d.is_dir() or not d.name.startswith("run_"):
                continue
            if not (d / "trades.jsonl").is_file():
                continue
            try:
                if not (d / "trades.jsonl").read_text(encoding="utf-8").strip():
                    continue
            except OSError:
                continue
            return d
        return None

    def test_initial_stop_backfilled_from_ledger(self):
        d = self._pick()
        if d is None:
            self.skipTest("默认根里没有带成交的 run")
        before = _sha256(d / "trades.jsonl")
        body, code = A.get_chart(d.name)
        self.assertEqual(code, 200)
        chart = body.get("chart") or {}
        for t in (chart.get("trades") or []):
            st = t.get("initial_stop")
            self.assertTrue(st, f"图上成交点缺初始止损：{d.name} / {t.get('trade_id')}")
            self.assertGreater(float(st), 0.0)
        # 只读：账本字节不变
        self.assertEqual(_sha256(d / "trades.jsonl"), before)


@unittest.skipUnless(DEFAULT_ROOT.is_dir(), "默认运行根不存在（离线环境）")
class TestBatchTradeIdCompleteness(unittest.TestCase):
    """⑥ `runs` 批次的逐笔交易号必须来自账本，不能整列是「—」。

    `runs/` 是项目根批量脚本跑的，它们的 `trades.jsonl` 里**没有 `trade_id` 字段**；
    旧兜底只从 `chart.json` 换，那 469 个没有 `chart.json` 的 run 就永远拿不到交易号。
    现在改为退到账本 `positions.jsonl` 的 `POSITION_CLOSED`（按平仓时刻+净盈亏对齐）。
    """

    def _runs_batch_dir(self):
        d = DEFAULT_ROOT
        return d if d.is_dir() else None

    def test_trade_id_from_ledger_when_trades_jsonl_lacks_it(self):
        bd = self._runs_batch_dir()
        if bd is None:
            self.skipTest("默认运行根不存在")
        import backtest_batches as B
        picked = 0
        for d in sorted(bd.iterdir()):
            if picked >= 5:
                break
            if not d.is_dir() or not d.name.startswith("run_"):
                continue
            tj = d / "trades.jsonl"
            if not tj.is_file() or tj.stat().st_size == 0:
                continue
            raw = B._read_jsonl(tj)
            if not raw or any(t.get("trade_id") for t in raw):
                continue                      # 本 run 自带交易号，不属本用例
            if not (d / "positions.jsonl").is_file():
                continue
            idx = B._ledger_trade_index(d)
            for t in raw:
                row = B._trade_row(d, bd.name, t, None, None, idx)
                self.assertTrue(str(row["trade_id"] or "").startswith("trd_"),
                                f"{d.name}: trades.jsonl 无 trade_id 时应从账本补出 trd_ 号，实得 {row['trade_id']}")
                self.assertIn("positions.jsonl", str(row["trade_id_source"]))
            picked += 1
        if picked == 0:
            self.skipTest("默认根里没有「trades.jsonl 不带 trade_id」的样本")


class TestMissingFeeIsNotZero(unittest.TestCase):
    """⑦ 「没记手续费」必须返回缺失，不能算成「0 手续费」。

    旧写法 `(entry_fee or 0) + (exit_fee or 0)` 把「账本没记」变成「真的 0」，
    页面显示「手续费 $0.00」，读起来像「这笔交易零成本、收益已验证」。
    """

    def _mk(self, fee_fields: bool) -> str:
        import tempfile
        d = tempfile.mkdtemp(prefix="bt_ui_02_fee_")
        trade = {
            "symbol": "TESTUSDT", "direction": 1,
            "entry_time": 1700000000000, "entry_price": 1.0,
            "exit_time": 1700086400000, "exit_price": 1.1,
            "exit_reason": "take_profit", "qty": 10.0,
            "gross_pnl": 1.0, "net_pnl": 1.0,
        }
        if fee_fields:
            trade.update({"entry_fee": 0.01, "exit_fee": 0.01, "fees": 0.02, "funding_fee": 0.0})
        with open(os.path.join(d, "trades.jsonl"), "w", encoding="utf-8") as f:
            f.write(json.dumps(trade) + "\n")
        with open(os.path.join(d, "positions.jsonl"), "w", encoding="utf-8") as f:
            f.write(json.dumps({"event_type": "POSITION_CLOSED", "event_time": 1700086400000,
                                "symbol": "TESTUSDT",
                                "payload": {"trade_id": "trd_test0001", "net_pnl": 1.0,
                                            "exit_reason": "take_profit"}}) + "\n")
        with open(os.path.join(d, "equity_curve.jsonl"), "w", encoding="utf-8") as f:
            for i, ts in enumerate((1700000000000, 1700086400000)):
                f.write(json.dumps({"timestamp": ts, "equity": 2000.0 + i * 1.0}) + "\n")
        return d

    def test_all_fees_missing_returns_none(self):
        import backtest_metrics as M
        d = self._mk(fee_fields=False)
        m, notes = M.compute_metrics(d, "run_test_no_fee")
        self.assertIsNone(m["total_fees"], "一笔都没记费用 ⇒ 合计必须是 None，不是 0")
        self.assertIsNone(m["total_entry_fee"])
        self.assertIsNone(m["total_exit_fee"])
        self.assertTrue(any("手续费" in n for n in notes), f"必须写明原因：{notes}")

    def test_fees_present_are_summed(self):
        import backtest_metrics as M
        d = self._mk(fee_fields=True)
        m, _ = M.compute_metrics(d, "run_test_with_fee")
        self.assertAlmostEqual(m["total_fees"], 0.02, places=9)
        self.assertAlmostEqual(m["total_funding_fee"], 0.0, places=9)


if __name__ == "__main__":
    unittest.main(verbosity=2)
