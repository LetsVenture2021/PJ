import json
import tempfile
import unittest
from pathlib import Path

from runtime_config import ConfigError, load_mcp_config, load_runtime_config


class TestRuntimeConfig(unittest.TestCase):
    def test_mcp_url_can_be_supplied_by_environment_reference(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "mcp.json"
            path.write_text(json.dumps([{"label": "tenant", "url": "${TENANT_MCP_URL}"}]))

            servers = load_mcp_config(path, environ={})

        self.assertEqual(servers[0]["url"], "${TENANT_MCP_URL}")

    def _project(self, root: Path) -> None:
        (root / "instructions.txt").write_text("Project instructions")
        (root / "config.json").write_text(
            json.dumps(
                {
                    "name": "PJ",
                    "model": "base-model",
                    "instructions_file": "instructions.txt",
                    "profiles": {
                        "staging": {"model": "staging-model"},
                        "prod": {"reasoning_effort": "high"},
                    },
                }
            )
        )
        (root / "mcp_servers.json").write_text(
            json.dumps(
                [
                    {
                        "label": "docs",
                        "url": "https://example.test/mcp",
                        "enabled": True,
                    }
                ]
            )
        )
        (root / "tool_policy.json").write_text(
            json.dumps(
                {
                    "default": "allow",
                    "tools": {"dangerous": "approval"},
                }
            )
        )
        (root / "wrangler.toml.example").write_text(
            '[vars]\nPJ_ALLOWED_ORIGINS = "http://localhost:3001"\n'
            '[env.staging.vars]\nPJ_ALLOWED_ORIGINS = "https://staging.example.test"\n'
        )

    def test_dev_loads_all_legacy_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._project(root)
            config = load_runtime_config(root, environ={})

        self.assertEqual(config.profile, "dev")
        self.assertEqual(config.assistant["model"], "base-model")
        self.assertEqual(config.assistant["instructions"], "Project instructions")
        self.assertEqual(config.mcp_servers[0]["label"], "docs")
        self.assertEqual(config.tool_policy["tools"]["dangerous"], "approval")
        self.assertEqual(config.worker["PJ_ALLOWED_ORIGINS"], "http://localhost:3001")

    def test_profile_and_typed_environment_overrides_are_applied(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._project(root)
            config = load_runtime_config(
                root,
                environ={
                    "PJ_PROFILE": "staging",
                    "OPENAI_API_KEY": "test-key",
                    "PJ_MODEL": "environment-model",
                    "PJ_REALTIME_VOICE": "cedar",
                    "PJ_CONFIG__ASSISTANT__TOOL_SEARCH_ENABLED": "false",
                    "PJ_ALLOWED_ORIGINS": "https://override.example.test",
                },
            )

        self.assertEqual(config.profile, "staging")
        self.assertEqual(config.assistant["model"], "environment-model")
        self.assertFalse(config.assistant["tool_search_enabled"])
        self.assertEqual(config.realtime["voice"], "cedar")
        self.assertEqual(config.worker["PJ_ALLOWED_ORIGINS"], "https://override.example.test")

    def test_missing_profile_environment_fails_fast(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._project(root)
            with self.assertRaisesRegex(
                ConfigError, "OPENAI_API_KEY.*PJ_OWNER_EMAILS.*PJ_TOOL_BRIDGE_TOKEN"
            ):
                load_runtime_config(root, environ={"PJ_PROFILE": "prod"})

    def test_invalid_profile_has_clear_error(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._project(root)
            with self.assertRaisesRegex(ConfigError, "Invalid PJ profile"):
                load_runtime_config(root, environ={"PJ_PROFILE": "qa"})

    def test_invalid_existing_config_fails_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._project(root)
            (root / "tool_policy.json").write_text(
                json.dumps(
                    {
                        "default": "sometimes",
                        "tools": {},
                    }
                )
            )
            with self.assertRaisesRegex(ConfigError, "allow, deny, or approval"):
                load_runtime_config(root, environ={})

    def test_production_collaboration_fails_closed_without_identity_and_tenant(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._project(root)
            environment = {
                "PJ_PROFILE": "prod",
                "OPENAI_API_KEY": "test",
                "PJ_OWNER_EMAILS": "owner@test",
                "PJ_TOOL_BRIDGE_TOKEN": "test",
                "PJ_CONFIG__COLLABORATION__ENABLED": "true",
            }
            with self.assertRaisesRegex(ConfigError, "identity and tenant"):
                load_runtime_config(root, environ=environment)


if __name__ == "__main__":
    unittest.main()
