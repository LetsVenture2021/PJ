import io
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

import google_cloud_mcp_server as gcp  # noqa: E402
from runtime_config import GoogleCloudSettings  # noqa: E402


SETTINGS = GoogleCloudSettings(
    project="default-project",
    location="us-central1",
    timeout_seconds=12,
    resource_manager_api="https://cloudresourcemanager.googleapis.com/v3",
    cloud_run_api="https://run.googleapis.com/v2",
    metadata_token_url=(
        "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token"
    ),
)


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps(self.payload).encode()


class TestGoogleCloudMcpServer(unittest.TestCase):
    def test_initialize_and_tools(self):
        response = gcp.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
        self.assertEqual(response["result"]["serverInfo"]["name"], "pj-google-cloud")
        tools = gcp.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        names = {tool["name"] for tool in tools["result"]["tools"]}
        self.assertEqual(len(names), 4)
        self.assertIn("gcp_list_cloud_run_services", names)

    @patch("google_cloud_mcp_server._settings", return_value=SETTINGS)
    @patch("urllib.request.urlopen")
    def test_search_projects_is_bounded_and_authenticated(self, urlopen, _settings):
        urlopen.return_value = FakeResponse({"projects": [{"projectId": "test-project"}]})
        with patch.dict(os.environ, {"GOOGLE_CLOUD_ACCESS_TOKEN": "short-lived"}, clear=True):
            result = gcp.call_tool("gcp_search_projects", {"query": "state:ACTIVE", "limit": 999})
        self.assertEqual(result["projects"][0]["projectId"], "test-project")
        request = urlopen.call_args.args[0]
        self.assertIn("pageSize=100", request.full_url)
        self.assertEqual(request.headers["Authorization"], "Bearer short-lived")
        self.assertNotIn("short-lived", json.dumps(result))

    @patch("google_cloud_mcp_server._settings", return_value=SETTINGS)
    @patch("urllib.request.urlopen")
    def test_cloud_run_uses_configured_defaults(self, urlopen, _settings):
        urlopen.return_value = FakeResponse({"services": []})
        with patch.dict(os.environ, {"GOOGLE_CLOUD_ACCESS_TOKEN": "token"}, clear=True):
            gcp.call_tool("gcp_list_cloud_run_services", {})
        request = urlopen.call_args.args[0]
        self.assertIn("projects/default-project/locations/us-central1/services", request.full_url)

    @patch("urllib.request.urlopen")
    def test_metadata_service_account_authentication(self, urlopen):
        urlopen.side_effect = [
            FakeResponse({"access_token": "metadata-token", "expires_in": 3599}),
            FakeResponse({"name": "projects/123456"}),
        ]
        with (
            patch.dict(os.environ, {}, clear=True),
            patch("google_cloud_mcp_server._settings", return_value=SETTINGS),
        ):
            result = gcp.call_tool("gcp_get_project", {"project": "123456"})
        self.assertEqual(result["name"], "projects/123456")
        metadata_request = urlopen.call_args_list[0].args[0]
        api_request = urlopen.call_args_list[1].args[0]
        self.assertEqual(metadata_request.headers["Metadata-flavor"], "Google")
        self.assertEqual(api_request.headers["Authorization"], "Bearer metadata-token")

    def test_rejects_resource_path_injection(self):
        with patch("google_cloud_mcp_server._settings", return_value=SETTINGS):
            with self.assertRaisesRegex(ValueError, "invalid"):
                gcp.call_tool(
                    "gcp_get_cloud_run_service",
                    {"project": "safe-project/services/bad", "service": "service"},
                )

    def test_notification_has_no_response(self):
        self.assertIsNone(gcp.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}))

    def test_stdio_round_trip(self):
        incoming = io.StringIO(json.dumps({"jsonrpc": "2.0", "id": 7, "method": "ping"}) + "\n")
        outgoing = io.StringIO()
        with patch.object(sys, "stdin", incoming), patch.object(sys, "stdout", outgoing):
            gcp.serve()
        self.assertEqual(json.loads(outgoing.getvalue())["id"], 7)


if __name__ == "__main__":
    unittest.main()
