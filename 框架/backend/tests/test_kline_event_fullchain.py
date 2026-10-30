import json
import tempfile
import unittest
from pathlib import Path

import backtest_chart


class KlineEventMappingTests(unittest.TestCase):
    def test_maps_only_persisted_times_and_prices(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dossier = {
                "event_id": "evt-1", "symbol": "BTCUSDT", "created_at_ms": 100,
                "current_state": "INVALIDATED", "ended_at_ms": 900,
                "box": {"timeframe": "4h", "formation_time_ms": 100,
                        "high_h": 110, "low_l": 90},
                "sweep_time_ms": 200, "sweep_low_s": 88,
                "retest_time_ms": 300, "retest_low_lr": 95,
                "breakout_time_ms": 400, "breakout_close_price": 112,
                "history": [
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
            self.assertEqual(by_type["sweep_low"]["event_time"], 200)
            self.assertEqual(by_type["retest"]["available_at"], 301)
            self.assertEqual(by_type["breakout"]["available_at"], 401)
            self.assertEqual(by_type["signal_invalidated"]["event_time"], 800)
            self.assertEqual(by_type["signal_invalidated"]["reason"], "test")
            self.assertFalse(any(e["event_time"] == 0 for e in events))


if __name__ == "__main__":
    unittest.main()
