import copy
import tempfile
import unittest
from pathlib import Path

from scripts.validate_wrangler_config import (
    ConfigValidationError,
    REQUIRED_ROUTES,
    REQUIRED_VARS,
    validate_file,
    validate_manifest,
)


def valid_manifest() -> dict:
    return {
        "name": "pj-realtime-backend",
        "main": "pj_realtime_backend_worker.js",
        "compatibility_date": "2026-07-28",
        "routes": [
            {
                "pattern": pattern,
                "zone_name": pattern.split("/", 1)[0],
            }
            for pattern in sorted(REQUIRED_ROUTES)
        ],
        "vars": {key: f"example-{key.lower()}" for key in REQUIRED_VARS},
    }


class TestWranglerConfig(unittest.TestCase):
    def test_repository_example_is_valid(self):
        validate_file(Path(__file__).parents[1] / "wrangler.toml.example")

    def test_invalid_toml_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "wrangler.toml.example"
            path.write_text('name = "unterminated\n')

            with self.assertRaisesRegex(ConfigValidationError, "unable to parse TOML"):
                validate_file(path)

    def test_missing_required_variable_is_rejected(self):
        manifest = valid_manifest()
        del manifest["vars"]["CF_ACCESS_AUD"]

        with self.assertRaisesRegex(ConfigValidationError, "CF_ACCESS_AUD"):
            validate_manifest(manifest)

    def test_route_pattern_changes_are_rejected(self):
        manifest = valid_manifest()
        manifest["routes"][0]["pattern"] = "pj-assistant.ai/*"

        with self.assertRaisesRegex(ConfigValidationError, "patterns invalid"):
            validate_manifest(manifest)

    def test_secret_keys_are_rejected(self):
        manifest = copy.deepcopy(valid_manifest())
        manifest["vars"]["OPENAI_API_KEY"] = "must-not-be-committed"

        with self.assertRaisesRegex(ConfigValidationError, "must not contain secret keys"):
            validate_manifest(manifest)


if __name__ == "__main__":
    unittest.main()
