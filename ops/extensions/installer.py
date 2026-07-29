"""Offline-only preview and installation orchestration."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ops.extensions.manifest import permission_diff, policy_entries
from ops.extensions.models import InstallPreview, PackageIdentity
from ops.extensions.package import verify_package
from ops.extensions.state import ExtensionState


class LocalInstaller:
    """Installer deliberately accepts local archives only; remote installation is unsupported."""

    def __init__(self, state: ExtensionState, trusted_publishers: dict[str, bytes]):
        self.state = state
        self.trusted_publishers = trusted_publishers

    def preview(
        self,
        archive: str | Path,
        *,
        expected_digest: str | None = None,
        previous: dict[str, Any] | None = None,
        mapping: dict[str, str] | None = None,
    ) -> InstallPreview:
        manifest, digest = verify_package(
            archive, trusted_publishers=self.trusted_publishers, expected_digest=expected_digest
        )
        return InstallPreview(
            PackageIdentity(
                manifest["identifier"], manifest["publisher"], manifest["version"], digest
            ),
            manifest,
            permission_diff(previous, manifest),
            policy_entries(manifest, mapping),
            Path(archive),
        )

    def install(
        self, preview: InstallPreview, *, approve_permission_broadening: bool = False
    ) -> None:
        broadened = any(preview.permission_diff.values())
        if broadened and not approve_permission_broadening:
            from ops.extensions.models import ExtensionError

            raise ExtensionError("new permissions require fresh approval")
        self.state.record_install(
            preview.manifest,
            preview.identity.digest,
            approved_broadening=approve_permission_broadening,
        )
