import hashlib
import json
import platform
import tempfile
import unittest
import zipfile
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from ops.extensions.installer import LocalInstaller
from ops.extensions.manifest import parse_manifest, policy_entries, realtime_tools
from ops.extensions.models import ExtensionError
from ops.extensions.package import extract_verified, verify_package
from ops.extensions.sandbox import require_sandbox
from ops.extensions.state import ExtensionState


def manifest(payload=b"{}"):
    return {
        "schema_version": "1.0",
        "identifier": "example.reviewed.connector",
        "publisher": "example.test",
        "version": "1.0.0",
        "minimum_pj_protocol": 1,
        "entry_points": {"connector": "contract.json"},
        "tools": [
            {"name": "fetch_local", "contract": "connector-transport-v1", "approval": "none"},
            {"name": "change_remote", "contract": "approval-action-v1", "approval": "required"},
        ],
        "permissions": ["connector", "approval_action"],
        "network_domains": ["api.example.com"],
        "filesystem_scope": [],
        "secrets": [],
        "costs": {"model": "free", "maximum_usd_per_call": 0},
        "approval_modes": ["per_call"],
        "data_retention": {"mode": "none", "maximum_days": 0},
        "files": {"contract.json": hashlib.sha256(payload).hexdigest()},
    }


def package(path, value, private, payload=b"{}", extras=None):
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("manifest.json", json.dumps(value))
        archive.writestr("signature.ed25519", private.sign(canonical))
        archive.writestr("contract.json", payload)
        for name, data in extras or []:
            archive.writestr(name, data)


class ExtensionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.private = Ed25519PrivateKey.generate()
        self.public = self.private.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )
        self.archive = self.root / "package.pjx"
        package(self.archive, manifest(), self.private)

    def tearDown(self):
        self.temp.cleanup()

    def test_offline_install_preview_activation_rollback_and_tombstone(self):
        state = ExtensionState(self.root / "state.json")
        installer = LocalInstaller(state, {"example.test": self.public})
        preview = installer.preview(
            self.archive,
            expected_digest=hashlib.sha256(self.archive.read_bytes()).hexdigest(),
            mapping={"fetch_local": "allow"},
        )
        self.assertEqual(
            preview.policy_entries, {"fetch_local": "allow", "change_remote": "approval"}
        )
        with self.assertRaises(ExtensionError):
            installer.install(preview)
        installer.install(preview, approve_permission_broadening=True)
        state.activate(preview.identity.identifier)
        state.disable(preview.identity.identifier)
        upgraded = manifest()
        upgraded["version"] = "1.1.0"
        second = self.root / "second.pjx"
        package(second, upgraded, self.private)
        installer.install(
            installer.preview(second, previous=manifest()), approve_permission_broadening=False
        )
        state.activate(preview.identity.identifier)
        state.rollback(preview.identity.identifier)
        state.uninstall(preview.identity.identifier)
        self.assertTrue(
            json.loads((self.root / "state.json").read_text())["packages"][
                preview.identity.identifier
            ]["tombstone"]
        )

    def test_extract_only_after_signature_hash_and_publisher_checks(self):
        value, digest = verify_package(
            self.archive, trusted_publishers={"example.test": self.public}
        )
        target = self.root / digest
        extract_verified(self.archive, target, value)
        self.assertEqual((target / "contract.json").read_bytes(), b"{}")
        with self.assertRaisesRegex(ExtensionError, "revoked"):
            verify_package(
                self.archive,
                trusted_publishers={"example.test": self.public},
                revoked_publishers={"example.test"},
            )
        other = (
            Ed25519PrivateKey.generate()
            .public_key()
            .public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        )
        with self.assertRaisesRegex(ExtensionError, "signature"):
            verify_package(self.archive, trusted_publishers={"example.test": other})

    def test_malicious_or_undeclared_archives_fail(self):
        for name in ("../escape.json", "credentials.json", "run.exe"):
            bad = self.root / (name.replace("/", "_") + ".pjx")
            package(bad, manifest(), self.private, extras=[(name, b"x")])
            with self.assertRaises(ExtensionError):
                verify_package(bad, trusted_publishers={"example.test": self.public})
        bad_hash = manifest()
        bad_hash["files"]["contract.json"] = "0" * 64
        package(self.root / "hash.pjx", bad_hash, self.private)
        with self.assertRaisesRegex(ExtensionError, "hash"):
            verify_package(self.root / "hash.pjx", trusted_publishers={"example.test": self.public})

    def test_protocol_permission_policy_and_realtime_fail_closed(self):
        future = manifest()
        future["minimum_pj_protocol"] = 999
        with self.assertRaisesRegex(ExtensionError, "incompatible"):
            parse_manifest(json.dumps(future))
        omitted = manifest()
        omitted["permissions"].remove("approval_action")
        with self.assertRaisesRegex(ExtensionError, "omitted"):
            parse_manifest(json.dumps(omitted))
        parsed = parse_manifest(json.dumps(manifest()))
        policy = policy_entries(parsed)
        self.assertEqual(policy["fetch_local"], "deny")
        self.assertEqual(
            realtime_tools(parsed, {"fetch_local": "allow", "change_remote": "allow"}),
            ["fetch_local"],
        )

    def test_dependency_confusion_and_sandbox_escape_fail_closed(self):
        value = manifest()
        value["dependencies"] = {"reviewed.lib": "https://public.invalid/lib"}
        with self.assertRaisesRegex(ExtensionError, "signed local package"):
            parse_manifest(json.dumps(value))
        if platform.system() != "Darwin":
            with self.assertRaisesRegex(ExtensionError, "sandbox"):
                require_sandbox(code_bearing=True, explicitly_enabled=True)

    def test_metadata_only_evaluation(self):
        state = ExtensionState(self.root / "state.json")
        state.evaluate("example.reviewed.connector", "pass", {"duration_ms": 2, "status": "ok"})
        with self.assertRaisesRegex(ExtensionError, "metadata-only"):
            state.evaluate("x", "fail", {"result": "secret"})


if __name__ == "__main__":
    unittest.main()
