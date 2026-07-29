import tempfile
import unittest
from pathlib import Path

from ops.preferences import Preference, PreferenceScope, PreferenceSource, PreferenceStore
from ops.preferences.service import ConsentStatus
from ops.routing import Capabilities, RouteRequest, Router
from ops.shared.cost_ledger import CostLedger, CostRecord


class PreferenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = PreferenceStore(Path(self.temp.name) / "preferences.sqlite")

    def tearDown(self):
        self.temp.cleanup()

    def test_precedence_isolation_defaults_and_sensitive_inference(self):
        self.store.set(
            Preference(
                "tone",
                "global",
                PreferenceScope.GLOBAL,
                PreferenceSource.EXPLICIT,
                ConsentStatus.APPROVED,
                1,
            )
        )
        self.store.set(
            Preference(
                "tone",
                "project",
                PreferenceScope.PROJECT,
                PreferenceSource.EXPLICIT,
                ConsentStatus.APPROVED,
                1,
                project_id="a",
            )
        )
        self.assertEqual(self.store.effective("tone", project_id="a"), "project")
        self.assertEqual(self.store.effective("tone", project_id="b"), "global")
        self.assertEqual(
            self.store.effective("tone", project_id="a", use_defaults=True, default="default"),
            "default",
        )
        with self.assertRaises(ValueError):
            self.store.propose(
                "health", True, scope=PreferenceScope.PROJECT, project_id="a", sensitive=True
            )


class RoutingTests(unittest.TestCase):
    def test_deterministic_route_and_private_refusal(self):
        capability = Capabilities(3, "low", 0.2, frozenset({"text"}), 1000, frozenset({"search"}))
        modes = {"quick": {"privacy": "standard"}, "private": {"privacy": "local"}}
        router = Router({("provider", "profile"): capability}, modes)
        request = RouteRequest("quick", required_tools=frozenset({"search"}))
        self.assertEqual(router.route(request), router.route(request))
        self.assertEqual(
            router.route(request).audit_metadata()["decision_code"], "ROUTE_CAPABILITY_MATCH"
        )
        with self.assertRaisesRegex(RuntimeError, "private_fallback_refused"):
            router.route(RouteRequest("private"))


class CostLedgerTests(unittest.TestCase):
    def test_reservation_exhaustion_and_settlement(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = CostLedger(Path(directory) / "cost.sqlite")
            record = CostRecord(
                "s", "p", "j", "research", 1.0, 1.0, None, "usd", {"price_version": "2026-01"}
            )
            ledger.reserve(record, limit=1.0)
            with self.assertRaisesRegex(ValueError, "cost_exhausted"):
                ledger.reserve(
                    CostRecord("s", "p", "j2", "media", 1, 1, None, "USD", {}), limit=1.0
                )
            ledger.settle("s", "p", "j", "research", 0.8)
            self.assertTrue(ledger.estimate_card("research", 1)["approval_required"])
            self.assertEqual(ledger.outcome_card("research", 0.8)["actual_cost"], 0.8)


if __name__ == "__main__":
    unittest.main()
