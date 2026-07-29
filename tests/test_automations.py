from __future__ import annotations

import hashlib
import hmac
import unittest
from datetime import datetime, time, timezone

from ops.jobs.controls import KillSwitches
from ops.jobs.events import Connector, EventRejected, EventVerifier
from ops.jobs.models import MissedRunPolicy, QuietHours, Schedule, TriggerKind
from ops.jobs.notifications import NotificationPolicy
from ops.jobs.scheduler import candidate_runs
from ops.workflows.models import ApprovalPolicy, Step, Workflow, WorkflowKind
from ops.workflows.simulation import simulate


class AutomationTests(unittest.TestCase):
    def test_catch_up_is_bounded_and_utc_deterministic(self) -> None:
        schedule = Schedule(
            TriggerKind.RECURRING,
            "America/New_York",
            at=datetime(2024, 3, 10, 6, tzinfo=timezone.utc),
            interval_seconds=3600,
            missed_run_policy=MissedRunPolicy.CATCH_UP,
            max_catch_up=2,
        )
        runs = candidate_runs(
            schedule,
            last_check=datetime(2024, 3, 10, 5, tzinfo=timezone.utc),
            now=datetime(2024, 3, 10, 10, tzinfo=timezone.utc),
        )
        self.assertEqual([r.hour for r in runs], [9, 10])

    def test_event_signature_replay_and_schema(self) -> None:
        connector = Connector("example", b"secret", lambda value: value == {"ok": True})
        body, timestamp = b'{"ok": true}', 1000
        signature = hmac.new(b"secret", b"1000." + body, hashlib.sha256).hexdigest()
        verifier = EventVerifier()
        self.assertEqual(
            verifier.verify(
                connector, body, event_id="e1", timestamp=timestamp, signature=signature, now=1000
            ),
            {"ok": True},
        )
        with self.assertRaises(EventRejected):
            verifier.verify(
                connector, body, event_id="e1", timestamp=timestamp, signature=signature, now=1000
            )

    def test_sensitive_step_approval_and_simulation(self) -> None:
        workflow = Workflow(
            "publish",
            1,
            "owner",
            WorkflowKind.ACTION,
            (Step("post", "public", approval=ApprovalPolicy.NONE),),
            budget=2,
        )
        result = simulate(workflow, {})
        self.assertEqual(result.approvals, ("post",))
        self.assertEqual(result.external_effects, ("public",))

    def test_kill_switch_cancels_and_blocks(self) -> None:
        switches = KillSwitches()
        token = switches.register("r1", connector="c", project="p", workflow="w")
        self.assertIsNotNone(token)
        switches.activate("workflow", "w")
        assert token is not None
        self.assertTrue(token.requested.is_set())
        self.assertIsNone(switches.register("r2", connector="c", project="p", workflow="w"))

    def test_notification_controls(self) -> None:
        policy = NotificationPolicy(daily_cap=1, quiet_hours=QuietHours(time(22), time(7)))
        now = datetime(2025, 1, 1, 12, tzinfo=timezone.utc)
        self.assertTrue(policy.decide("a", category="failure", now=now).send)
        self.assertEqual(policy.decide("a", category="failure", now=now).reason, "duplicate")
        self.assertEqual(policy.decide("b", category="failure", now=now).reason, "daily_cap")


if __name__ == "__main__":
    unittest.main()
