from __future__ import annotations

import unittest
from datetime import timedelta

from ops.connectors.actions import ActionRunner, ApprovalRequired, ConnectorAction
from ops.connectors.builtins import builtin_manifests
from ops.connectors.credentials import MemoryCredentialProvider
from ops.connectors.health import evaluate_health, health_to_public_dict
from ops.connectors.models import ActionPreview, CredentialRecord, HealthReason, utc_now
from ops.connectors.oauth import OAuthCoordinator, OAuthError, OAuthTokenSet, TokenMetadata
from ops.connectors.registry import ConnectorRegistry, RegistryError


class FakeOAuth:
    authorization_endpoint = "https://provider.example/authorize"

    def exchange_code(self, code, verifier, redirect_uri):
        return OAuthTokenSet(
            "access-secret", "refresh-secret", TokenMetadata("1", "Person", ("read",), 3600)
        )

    def refresh(self, credential_handle):
        return TokenMetadata("1", "Person", ("read",), 3600)

    def revoke(self, credential_handle):
        return None


class FakeAction(ConnectorAction):
    calls = 0

    def validate(self, arguments):
        if "value" not in arguments:
            raise ValueError("value required")

    def preview(self, arguments):
        return ActionPreview("notes", "create_note", "Create note", ("one note",), 0, "USD", True)

    def execute(self, arguments, idempotency_key):
        self.calls += 1
        return "external-1"

    def verify(self, external_reference):
        return True


class ConnectorTests(unittest.TestCase):
    def test_oauth_rejects_state_nonce_and_redirect_attacks(self):
        credentials = MemoryCredentialProvider()
        oauth = OAuthCoordinator(FakeOAuth(), credentials, {"https://app.example/callback"})
        with self.assertRaises(OAuthError):
            oauth.begin("client", "https://evil.example/callback", ("read",))
        _, pending = oauth.begin("client", "https://app.example/callback", ("read",))
        with self.assertRaises(OAuthError):
            oauth.complete("code", pending.state, "wrong", pending.redirect_uri)
        _, pending = oauth.begin("client", "https://app.example/callback", ("read",))
        record = oauth.complete("code", pending.state, pending.nonce, pending.redirect_uri)
        self.assertTrue(credentials.exists(record.credential_handle))
        self.assertNotIn("secret", repr(record))

    def test_expiry_missing_secret_and_scope_degrade_without_secret_output(self):
        provider = MemoryCredentialProvider()
        record = CredentialRecord(
            "opaque", "email", "1", "Account", ("mail.read",), utc_now() - timedelta(seconds=1)
        )
        health = evaluate_health(record, provider, {"mail.read"})
        self.assertEqual(health.reason_code, HealthReason.MISSING_SECRET)
        self.assertNotIn("credential_handle", health_to_public_dict(health))
        provider.rotate("opaque", "super-secret-token")
        health = evaluate_health(record, provider, {"mail.read"})
        self.assertEqual(health.reason_code, HealthReason.EXPIRED)
        self.assertNotIn("super-secret-token", repr(health_to_public_dict(health)))

    def test_action_requires_approval_and_deduplicates_key(self):
        runner, action = ActionRunner(), FakeAction()
        with self.assertRaises(ApprovalRequired):
            runner.run("notes", "create_note", action, {"value": 1}, idempotency_key="key")
        first = runner.run(
            "notes", "create_note", action, {"value": 1}, idempotency_key="key", approved=True
        )
        second = runner.run(
            "notes", "create_note", action, {"value": 2}, idempotency_key="key", approved=True
        )
        self.assertEqual(first, second)
        self.assertEqual(action.calls, 1)

    def test_policy_omission_fails_closed(self):
        registry = ConnectorRegistry()
        for manifest in builtin_manifests():
            registry.register(manifest)
        with self.assertRaises(RegistryError):
            registry.validate_policy({})
        registry.validate_policy(
            {
                f"connector.{m.connector_id}.{a.name}": "approval"
                for m in registry.all()
                for a in m.actions
            }
        )


if __name__ == "__main__":
    unittest.main()
