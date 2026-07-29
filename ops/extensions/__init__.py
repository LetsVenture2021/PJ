"""Secure, provider-neutral PJ extension package APIs."""

from ops.extensions.manifest import parse_manifest, permission_diff, policy_entries
from ops.extensions.models import ExtensionError, InstallPreview, PackageIdentity
from ops.extensions.package import extract_verified, verify_package
from ops.extensions.state import ExtensionState

__all__ = [
    "ExtensionError",
    "ExtensionState",
    "InstallPreview",
    "PackageIdentity",
    "extract_verified",
    "parse_manifest",
    "permission_diff",
    "policy_entries",
    "verify_package",
]
