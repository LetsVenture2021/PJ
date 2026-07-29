import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from ops.jobs.models import Estimate, StepDefinition
from ops.jobs.repository import JobRepository
from ops.jobs.scheduler import CronSchedule, OneTimeSchedule
from ops.jobs.service import JobService


class Handler:
    def validate(self, payload):
        return dict(payload)

    def estimate(self, payload):
        return Estimate(2, "low")

    def plan(self, payload):
        return [StepDefinition("first"), StepDefinition("second", dependencies=("first",))]


class JobTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.repository = JobRepository(Path(self.directory.name) / "jobs.sqlite3")
        self.service = JobService(self.repository, {"test": Handler()})

    def tearDown(self):
        self.directory.cleanup()

    def test_atomic_lease_and_expired_recovery(self):
        job = self.service.create("test", {}, 2)
        first = self.repository.acquire_lease("one", ttl=2, now=10)
        self.assertEqual(job["id"], first["job_id"])
        self.assertIsNone(self.repository.acquire_lease("two", now=11))
        recovered = self.repository.acquire_lease("two", now=13)
        self.assertEqual(job["id"], recovered["job_id"])
        self.assertFalse(self.repository.heartbeat(job["id"], first["token"]))

    def test_repeated_effect_and_unknown_outcome(self):
        job = self.service.create("test", {}, 2)
        self.assertEqual(
            ("started", None), self.repository.claim_effect(job["id"], "stable-key", {"x": 1})
        )
        self.repository.complete_effect("stable-key", None, "outcome_unknown")
        self.assertEqual(
            ("outcome_unknown", None),
            self.repository.claim_effect(job["id"], "stable-key", {"x": 1}),
        )
        with self.assertRaises(ValueError):
            self.repository.claim_effect(job["id"], "stable-key", {"x": 2})

    def test_cancellation_and_budget(self):
        job = self.service.create("test", {}, 2)
        self.assertTrue(self.service.cancel(job["id"]))
        self.assertEqual("cancelled", self.repository.get_job(job["id"])["state"])
        with self.assertRaisesRegex(ValueError, "budget"):
            self.service.create("test", {}, 1)

    def test_schedule_boundaries_are_timezone_aware(self):
        now = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
        self.assertEqual(11, CronSchedule("0 11 * * *", "UTC").next_after(now).hour)
        self.assertEqual(now, OneTimeSchedule(now, "UTC").next_after(now.replace(hour=9)))
        self.assertIsNone(OneTimeSchedule(now, "UTC").next_after(now))


if __name__ == "__main__":
    unittest.main()
