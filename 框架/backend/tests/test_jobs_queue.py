"""持久任务队列契约测试（架构重构第 2 期）。

只测任务表本身，不启动 worker、不碰回测、不联网。
覆盖：投递幂等、原子领取、租约回收、重试上限、确定性失败不重试、取消、统计。
"""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from infrastructure.jobs.store import (
    CANCELED, FAILED, QUEUED, RUNNING, SUCCEEDED, JobsStore,
)


class JobsStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.store = JobsStore(Path(self._tmp.name) / "jobs.sqlite3")

    def tearDown(self) -> None:
        self.store.close()
        self._tmp.cleanup()

    def test_enqueue_then_claim(self) -> None:
        job_id, created = self.store.enqueue("backtest", {"run_id": "r1"})
        self.assertTrue(created)
        job = self.store.claim("w1")
        self.assertIsNotNone(job)
        self.assertEqual(job["job_id"], job_id)
        self.assertEqual(job["state"], RUNNING)
        self.assertEqual(job["payload"]["run_id"], "r1")
        self.assertEqual(job["attempts"], 1)

    def test_idempotency_key_returns_same_job(self) -> None:
        first, created1 = self.store.enqueue("backtest", {"run_id": "r1"},
                                             idempotency_key="backtest:r1")
        second, created2 = self.store.enqueue("backtest", {"run_id": "r1"},
                                              idempotency_key="backtest:r1")
        self.assertTrue(created1)
        self.assertFalse(created2)
        self.assertEqual(first, second)
        self.assertEqual(self.store.stats()[QUEUED], 1)

    def test_claim_returns_none_when_empty(self) -> None:
        self.assertIsNone(self.store.claim("w1"))

    def test_expired_lease_is_reclaimable(self) -> None:
        self.store.enqueue("backtest", {"run_id": "r1"})
        first = self.store.claim("w1", lease_ms=1)
        self.assertIsNotNone(first)
        time.sleep(0.01)
        second = self.store.claim("w2", lease_ms=60_000)
        self.assertIsNotNone(second)
        self.assertEqual(second["job_id"], first["job_id"])
        self.assertEqual(second["attempts"], 2)

    def test_active_lease_is_not_stolen(self) -> None:
        self.store.enqueue("backtest", {"run_id": "r1"})
        self.assertIsNotNone(self.store.claim("w1", lease_ms=60_000))
        self.assertIsNone(self.store.claim("w2", lease_ms=60_000))

    def test_heartbeat_keeps_lease(self) -> None:
        self.store.enqueue("backtest", {"run_id": "r1"})
        job = self.store.claim("w1", lease_ms=10)
        self.assertTrue(self.store.heartbeat(job["job_id"], "w1", lease_ms=60_000))
        time.sleep(0.02)
        self.assertIsNone(self.store.claim("w2", lease_ms=60_000))

    def test_heartbeat_from_wrong_owner_fails(self) -> None:
        self.store.enqueue("backtest", {"run_id": "r1"})
        job = self.store.claim("w1")
        self.assertFalse(self.store.heartbeat(job["job_id"], "w2"))

    def test_success_is_terminal(self) -> None:
        self.store.enqueue("backtest", {"run_id": "r1"})
        job = self.store.claim("w1")
        self.assertTrue(self.store.finish(job["job_id"], "w1", SUCCEEDED,
                                          result_ref="r1"))
        got = self.store.get(job["job_id"])
        self.assertEqual(got["state"], SUCCEEDED)
        self.assertEqual(got["result_ref"], "r1")
        self.assertIsNone(self.store.claim("w1"))

    def test_retryable_failure_requeues_with_backoff(self) -> None:
        self.store.enqueue("backtest", {"run_id": "r1"}, max_attempts=2)
        job = self.store.claim("w1")
        self.store.finish(job["job_id"], "w1", FAILED, error="boom")
        got = self.store.get(job["job_id"])
        self.assertEqual(got["state"], QUEUED)
        # 退避期内不可领取
        self.assertIsNone(self.store.claim("w1"))

    def test_failure_exhausts_attempts(self) -> None:
        self.store.enqueue("backtest", {"run_id": "r1"}, max_attempts=1)
        job = self.store.claim("w1")
        self.store.finish(job["job_id"], "w1", FAILED, error="boom")
        got = self.store.get(job["job_id"])
        self.assertEqual(got["state"], FAILED)
        self.assertEqual(got["error"]["message"], "boom")

    def test_deterministic_failure_does_not_retry(self) -> None:
        self.store.enqueue("backtest", {"run_id": "r1"}, max_attempts=5)
        job = self.store.claim("w1")
        self.store.finish(job["job_id"], "w1", FAILED, error="参数非法", retry=False)
        got = self.store.get(job["job_id"])
        self.assertEqual(got["state"], FAILED)
        self.assertIsNone(self.store.claim("w1"))

    def test_cancel_queued(self) -> None:
        job_id, _ = self.store.enqueue("backtest", {"run_id": "r1"})
        self.assertTrue(self.store.cancel(job_id))
        self.assertEqual(self.store.get(job_id)["state"], CANCELED)
        self.assertIsNone(self.store.claim("w1"))

    def test_cancel_running_prevents_overwrite(self) -> None:
        self.store.enqueue("backtest", {"run_id": "r1"})
        job = self.store.claim("w1")
        self.assertTrue(self.store.cancel(job["job_id"]))
        self.store.finish(job["job_id"], "w1", SUCCEEDED, result_ref="r1")
        self.assertEqual(self.store.get(job["job_id"])["state"], CANCELED)

    def test_priority_order(self) -> None:
        low, _ = self.store.enqueue("backtest", {"run_id": "low"}, priority=0)
        high, _ = self.store.enqueue("backtest", {"run_id": "high"}, priority=10)
        self.assertEqual(self.store.claim("w1")["job_id"], high)
        self.assertEqual(self.store.claim("w1")["job_id"], low)

    def test_stats_counts_all_states(self) -> None:
        self.store.enqueue("backtest", {"run_id": "a"})
        self.store.enqueue("backtest", {"run_id": "b"})
        job = self.store.claim("w1")
        self.store.finish(job["job_id"], "w1", SUCCEEDED)
        stats = self.store.stats()
        self.assertEqual(stats[QUEUED], 1)
        self.assertEqual(stats[SUCCEEDED], 1)
        self.assertEqual(stats["total"], 2)

    def test_find_by_run(self) -> None:
        job_id, _ = self.store.enqueue("backtest", {"run_id": "r9"})
        found = self.store.find_by_run("r9")
        self.assertIsNotNone(found)
        self.assertEqual(found["job_id"], job_id)
        self.assertIsNone(self.store.find_by_run("nope"))

    def test_progress_is_visible(self) -> None:
        self.store.enqueue("backtest", {"run_id": "r1"})
        job = self.store.claim("w1")
        self.store.progress(job["job_id"], "w1", {"pct": 42})
        self.assertEqual(self.store.get(job["job_id"])["progress"]["pct"], 42)


if __name__ == "__main__":
    unittest.main()
