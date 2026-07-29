import unittest
from datetime import datetime, timedelta, timezone

from ops.sharing.authorization import AuthorizationContext, AuthorizationError, authorize
from ops.sharing.models import Permission, ResourceGrant, ShareLink
from ops.sharing.sanitization import sanitize_shared_resource
from ops.sharing.service import CollaborationStore, VersionConflict


class TestCollaborationSecurity(unittest.TestCase):
    def setUp(self):
        self.now = datetime.now(timezone.utc)
        self.grant = ResourceGrant(
            organization_id="tenant-a",
            resource_type="project",
            resource_id="project-1",
            principal_id="principal-a",
            permission=Permission.EDITOR,
            expires_at=self.now + timedelta(minutes=5),
        )

    def context(self, **changes):
        values = {
            "principal_id": "principal-a",
            "organization_id": "tenant-a",
            "resource_type": "project",
            "resource_id": "project-1",
            "required": Permission.VIEWER,
            "now": self.now,
        }
        values.update(changes)
        return AuthorizationContext(**values)

    def test_exact_grant_allows_and_tenant_or_resource_mismatch_denies(self):
        authorize(self.context(), grants=[self.grant])
        for changes in ({"organization_id": "tenant-b"}, {"resource_id": "project-2"}):
            with self.assertRaises(AuthorizationError):
                authorize(self.context(**changes), grants=[self.grant])

    def test_expired_and_revoked_grants_deny(self):
        with self.assertRaises(AuthorizationError):
            authorize(self.context(now=self.now + timedelta(minutes=6)), grants=[self.grant])
        revoked = ResourceGrant(
            organization_id="tenant-a",
            resource_type="project",
            resource_id="project-1",
            principal_id="principal-a",
            permission=Permission.EDITOR,
            revoked_at=self.now,
        )
        with self.assertRaises(AuthorizationError):
            authorize(self.context(), grants=[revoked])

    def test_links_are_hashed_scoped_revocable_and_never_powerful(self):
        link, token = ShareLink.issue(
            organization_id="tenant-a",
            resource_type="project",
            resource_id="project-1",
            permission=Permission.COMMENTER,
            expires_at=self.now + timedelta(minutes=5),
        )
        self.assertNotEqual(link.token_hash, token)
        authorize(self.context(principal_id=None, share_token=token), links=[link])
        for changes in (
            {"memory_access": True},
            {"connector_action": True},
            {"resource_id": "other"},
        ):
            with self.assertRaises(AuthorizationError):
                authorize(
                    self.context(principal_id=None, share_token=token, **changes), links=[link]
                )

    def test_share_representation_drops_all_private_state(self):
        shared = sanitize_shared_resource(
            {
                "id": "one",
                "title": "Safe",
                "content": "Public",
                "memory": "private",
                "projects": ["other"],
                "connector_credentials": "secret",
                "tool_arguments": {},
                "private_sources": "source",
                "machine_path": "/Users/private",
            }
        )
        self.assertEqual(
            shared, {"id": "one", "title": "Safe", "content": "Public", "shared": True}
        )

    def test_versions_conflict_and_deletion_removes_derivatives(self):
        store = CollaborationStore()
        store.save(
            organization_id="tenant-a",
            resource_type="project",
            resource_id="project-1",
            content={"id": "project-1", "title": "v1"},
            actor_principal_id="principal-a",
            expected_version=0,
        )
        with self.assertRaises(VersionConflict):
            store.save(
                organization_id="tenant-a",
                resource_type="project",
                resource_id="project-1",
                content={"title": "lost write"},
                actor_principal_id="principal-b",
                expected_version=0,
            )
        exported = store.export_resource("tenant-a", "project", "project-1")
        self.assertEqual(exported["versions"][0]["author_principal_id"], "principal-a")
        removed = []
        store.delete_resource(
            "tenant-a",
            "project",
            "project-1",
            remove_search_index=lambda *key: removed.append(("index", key)),
            remove_derived_previews=lambda *key: removed.append(("preview", key)),
        )
        self.assertEqual([item[0] for item in removed], ["index", "preview"])


if __name__ == "__main__":
    unittest.main()
