import json
import tempfile
import unittest
import sqlite3
from pathlib import Path

import backtest_chart
import live_api


class KlineEventMappingTests(unittest.TestCase):
    def test_maps_only_persisted_times_and_prices(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dossier = {
                "event_id": "evt-1", "symbol": "BTCUSDT", "created_at_ms": 50,
                "current_state": "INVALIDATED", "ended_at_ms": 900,
                "box": {"timeframe": "4h", "formation_time_ms": 100,
                        "high_h": 110, "low_l": 90},
                "sweep_time_ms": 200, "sweep_low_s": 88,
                "retest_time_ms": 300, "retest_low_lr": 95,
                "breakout_time_ms": 400, "breakout_close_price": 112,
                "history": [
                    {"transition_id": "tr-box", "to_state": "BOX",
                     "trigger_bar_close_ms": 150, "available_at_ms": 160},
                    {"transition_id": "tr-1", "to_state": "SWEEP", "available_at_ms": 201},
                    {"transition_id": "tr-2", "to_state": "RETEST", "available_at_ms": 301},
                    {"transition_id": "tr-3", "to_state": "CANDIDATE", "available_at_ms": 401},
                    {"transition_id": "tr-4", "to_state": "INVALIDATED",
                     "trigger_bar_close_ms": 800, "available_at_ms": 801, "reason": "test"},
                ],
            }
            (root / "events.jsonl").write_text(json.dumps(dossier) + "\n", encoding="utf-8")
            (root / "candidates.jsonl").write_text(json.dumps({
                "candidate_id": "cand-1", "snapshot_time_ms": 400,
                "available_at_ms": 401, "breakout_close": 112,
            }) + "\n", encoding="utf-8")
            events = backtest_chart.events_from_run_dir(root, "BTCUSDT", "run-1")
            by_type = {e["event_type"]: e for e in events}
            self.assertEqual(by_type["box"]["price_upper"], 110)
            self.assertEqual(by_type["box"]["range_end_time"], 900)
            self.assertEqual(by_type["box"]["event_time"], 100)
            self.assertEqual(by_type["box"]["range_start_time"], 150)
            self.assertEqual(by_type["box"]["formation_time"], 100)
            self.assertEqual(by_type["box"]["confirmed_at"], 150)
            self.assertEqual(by_type["box"]["available_at"], 160)
            self.assertEqual(by_type["sweep_low"]["event_time"], 200)
            self.assertEqual(by_type["retest"]["available_at"], 301)
            self.assertEqual(by_type["breakout"]["available_at"], 401)
            self.assertEqual(by_type["signal_invalidated"]["event_time"], 800)
            self.assertEqual(by_type["signal_invalidated"]["reason"], "test")
            self.assertFalse(any(e["event_time"] == 0 for e in events))

    def test_dossier_creation_time_is_not_a_box_formation_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "events.jsonl").write_text(json.dumps({
                "event_id": "evt-no-formation", "symbol": "BTCUSDT", "created_at_ms": 500,
                "box": {"timeframe": "4h", "high_h": 110, "low_l": 90},
            }) + "\n", encoding="utf-8")
            events = backtest_chart.events_from_run_dir(root, "BTCUSDT", "run-2")
            self.assertFalse(any(e["event_type"] == "box" for e in events))

    def test_backtest_entry_and_close_require_linked_identity_and_price(self):
        trades = [
            {"trade_id": "trd-real", "symbol": "BTCUSDT",
             "entry": {"t": 100, "price": 101.5},
             "exit": {"t": 200, "price": 110.0, "reason": "target"}},
            {"trade_id": None, "symbol": "BTCUSDT",
             "entry": {"t": 300, "price": 120}, "exit": {"t": 400, "price": 125}},
            {"trade_id": "trd-no-price", "symbol": "BTCUSDT",
             "entry": {"t": 500, "price": None}},
            {"trade_id": "trd-other", "symbol": "ETHUSDT",
             "entry": {"t": 300, "price": 10}},
        ]
        events = backtest_chart.trade_events_from_rows(trades, "BTCUSDT", "1h", "run-3")
        by_type = {e["event_type"]: e for e in events}
        self.assertEqual(by_type["entry"]["event_id"], "trd-real:entry")
        self.assertEqual(by_type["entry"]["event_time"], 100)
        self.assertEqual(by_type["entry"]["price"], 101.5)
        self.assertEqual(by_type["entry"]["source"], "trades.jsonl")
        self.assertEqual(by_type["close"]["event_time"], 200)
        self.assertEqual(by_type["close"]["price"], 110.0)
        self.assertEqual(by_type["close"]["reason"], "target")
        self.assertEqual({e["event_id"] for e in events}, {"trd-real:entry", "trd-real:close"})

    def test_paper_realized_query_filters_by_symbol_and_actual_event_time(self):
        con = sqlite3.connect(":memory:")
        con.row_factory = sqlite3.Row
        con.execute("CREATE TABLE paper_realized (id INTEGER PRIMARY KEY, account_id TEXT, position_id INTEGER, symbol TEXT, qty REAL, entry_price REAL, exit_price REAL, gross_pnl REAL, entry_fee REAL, exit_fee REAL, funding_paid REAL, net_pnl REAL, entry_ts INTEGER, exit_ts INTEGER, exit_reason TEXT, voided INTEGER)")
        con.executemany("INSERT INTO paper_realized VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", [
            (1, "paper43_3a", 1, "BTCUSDT", 1, 100, 110, 10, 0, 0, 0, 10, 150, 300, "target", 0),
            (2, "paper43_3a", 2, "ETHUSDT", 1, 100, 110, 10, 0, 0, 0, 10, 150, 180, "target", 0),
        ])
        block = live_api._trades_block("3a", con, 20, symbol="BTCUSDT", from_ms=100, to_ms=200)
        self.assertEqual([r["position_id"] for r in block["trades"]], [1])
        self.assertEqual(block["account_id"], "paper43_3a")
        con.close()


if __name__ == "__main__":
    unittest.main()
