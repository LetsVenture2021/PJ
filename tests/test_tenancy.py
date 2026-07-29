from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ops.tenancy.models import (
    AdminRole,
    FederationRequest,
    GovernedResource,
    SCIMUser,
    TenantContext,
    ValidatedIdentity,
)
from ops.tenancy.repository import TenantRepository
from ops.tenancy.service import GovernanceService


def policy(retention: int = 30):
    return {
        "connectors": {},
        "models": {},
        "retention": {"days": retention},
        "external_sharing": False,
        "tool_approvals": {},
        "budgets": {},
        "export": {},
        "automation": {},
        "regional_controls": {},
    }


class IdentityAdapter:
    def validate_id_token(self, token, request):
        return ValidatedIdentity(
            "user", request.expected_issuer, request.expected_audience, "user@example.test"
        )

    validate_response = validate_id_token


class ScimAdapter:
    def parse_user(self, payload):
        return SCIMUser(str(payload["id"]), bool(payload["active"]), str(payload["email"]))


class MemoryBackend:
    name = "cache"

    def __init__(self):
        self.present = True

    def export(self, context, destination):
        result = destination / "entry.txt"
        result.write_text(context.tenant_id)
        return [result]

    def delete(self, context):
        self.present = False
        return 1

    def verify_deleted(self, context):
        return not self.present


class TenancyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.repo = TenantRepository(root / "state.sqlite3")
        self.a = TenantContext("tenant-a", "bootstrap", "req-a")
        self.b = TenantContext("tenant-b", "bootstrap", "req-b")
        self.repo.create_tenant(self.a, "A")
        self.repo.create_tenant(self.b, "B")
        self.admin_a = TenantContext("tenant-a", "alice", "req-admin-a")
        self.admin_b = TenantContext("tenant-b", "alice", "req-admin-b")
        for bootstrap, admin in ((self.a, self.admin_a), (self.b, self.admin_b)):
            self.repo.put_user(bootstrap, "alice", "alice@example.test", active=True)
            self.repo.set_roles(bootstrap, "alice", {AdminRole.OWNER})
        self.backend = MemoryBackend()
        self.service = GovernanceService(self.repo, root / "data", [self.backend])

    def tearDown(self):
        self.temp.cleanup()

    def test_repository_policy_receipts_and_audit_are_tenant_isolated(self):
        first = self.repo.publish_policy(self.admin_a, policy(10), expected_version=0)
        self.repo.publish_policy(self.admin_b, policy(90), expected_version=0)
        receipt = self.repo.record_receipt(self.admin_a, "run-1", "allowed")
        self.assertEqual(receipt["policy_hash"], first["policy_hash"])
        self.assertNotEqual(
            self.repo.current_policy(self.admin_b)["policy_hash"], first["policy_hash"]
        )
        self.repo.audit(
            self.admin_a,
            resource_type="job",
            resource_id="run-1",
            action="execute",
            policy_decision="allow",
            status="ok",
        )
        self.assertEqual(len(self.repo.list_audit(self.admin_a)), 1)
        self.assertEqual(self.repo.list_audit(self.admin_b), [])
        self.assertNotIn("body", self.repo.list_audit(self.admin_a)[0])

    def test_policy_race_role_downgrade_and_deprovisioning(self):
        self.repo.publish_policy(self.admin_a, policy(), expected_version=0)
        with self.assertRaises(RuntimeError):
            self.repo.publish_policy(self.admin_a, policy(1), expected_version=0)
        self.repo.set_roles(self.admin_a, "alice", {AdminRole.MEMBER})
        with self.assertRaises(PermissionError):
            self.service.authorize(
                self.admin_a, "manage_policy", reauthenticated_at=datetime.now(timezone.utc)
            )
        self.repo.set_roles(self.a, "alice", {AdminRole.OWNER})
        self.repo.replace_grants(self.a, "alice", {("artifact", "one", "read")})
        self.service.provision_scim(
            self.a, ScimAdapter(), {"id": "alice", "active": False, "email": "alice@example.test"}
        )
        self.assertEqual(self.repo.roles(self.admin_a, "alice"), set())

    def test_legal_hold_cannot_be_bypassed_by_normal_deletion(self):
        self.repo.schedule_retention(
            self.admin_a, GovernedResource.ARTIFACTS, "artifact-1", "2020-01-01T00:00:00Z"
        )
        self.repo.set_legal_hold(
            self.admin_a, GovernedResource.ARTIFACTS, "artifact-1", "litigation"
        )
        with self.assertRaises(PermissionError):
            self.repo.mark_deleted(self.admin_a, GovernedResource.ARTIFACTS, "artifact-1")
        self.repo.set_legal_hold(self.admin_a, GovernedResource.ARTIFACTS, "artifact-1", None)
        self.repo.mark_deleted(self.admin_a, GovernedResource.ARTIFACTS, "artifact-1")

    def test_federation_reauthentication_paths_export_and_verified_deletion(self):
        request = FederationRequest("https://issuer.test", "pj", "state", "nonce", 1)
        self.assertEqual(
            self.service.authenticate_oidc(IdentityAdapter(), "token", request).subject, "user"
        )
        old = datetime.now(timezone.utc) - timedelta(minutes=6)
        with self.assertRaises(PermissionError):
            self.service.authorize(self.admin_a, "export", reauthenticated_at=old)
        now = datetime.now(timezone.utc)
        exported = self.service.export_tenant(
            self.admin_a, Path(self.temp.name) / "export", reauthenticated_at=now
        )
        self.assertIn("cache/entry.txt", exported["manifest"])
        a_path = self.service.tenant_path(self.admin_a, "artifacts", "one")
        b_path = self.service.tenant_path(self.admin_b, "artifacts", "one")
        self.assertNotEqual(a_path, b_path)
        self.assertTrue(
            all(self.service.delete_tenant(self.admin_a, reauthenticated_at=now).values())
        )


if __name__ == "__main__":
    unittest.main()
