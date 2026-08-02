import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import realtime_server
from ops.artifacts import ArtifactFacade


class ArtifactRouteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.facade = ArtifactFacade(root / "artifacts.sqlite3")
        self.left = self._register(root / "left.md", "before")
        self.right = self._register(root / "right.md", "after")
        realtime_server.app.config.update(TESTING=True)
        self.client = realtime_server.app.test_client()
        self.auth = {"Authorization": "Bearer bridge-secret"}
        self.environment = patch.dict(
            "os.environ", {"PJ_TOOL_BRIDGE_TOKEN": "bridge-secret"}, clear=False
        )
        self.environment.start()

    def tearDown(self):
        self.environment.stop()
        self.temp.cleanup()

    def _register(self, path, content):
        path.write_text(content)
        return self.facade.register(
            path=path, domain="docs", source_version=path.name, session_id="session-test"
        )

    def test_compare_and_restore_routes_execute_artifact_operations(self):
        with (
            patch.object(realtime_server, "ARTIFACT_FACADE", self.facade),
            patch.object(
                realtime_server,
                "_validated_session",
                return_value=({"id": "session-test"}, None),
            ),
            patch.object(
                realtime_server.chatlog,
                "list_session_artifact_ids",
                return_value=[self.left.artifact_id, self.right.artifact_id],
            ),
            patch.object(realtime_server.chatlog, "link_session_artifact", Mock(return_value=True)),
        ):
            compared = self.client.post(
                "/responses/sessions/session-test/artifacts/compare",
                headers=self.auth,
                json={
                    "left_artifact_id": self.left.artifact_id,
                    "right_artifact_id": self.right.artifact_id,
                },
            )
            restored = self.client.post(
                f"/responses/sessions/session-test/artifacts/{self.left.artifact_id}/restore",
                headers=self.auth,
                json={},
            )

        self.assertEqual(compared.status_code, 200)
        self.assertTrue(compared.get_json()["ok"])
        self.assertEqual(compared.get_json()["comparison"]["kind"], "text_diff")
        self.assertEqual(restored.status_code, 201)
        self.assertTrue(restored.get_json()["ok"])
        artifact = restored.get_json()["artifact"]
        self.assertNotEqual(artifact["artifact_id"], self.left.artifact_id)
        self.assertTrue(artifact["download_url"].endswith("/download"))


if __name__ == "__main__":
    unittest.main()
