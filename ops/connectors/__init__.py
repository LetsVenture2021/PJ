"""Transport-neutral contracts for safe third-party connectors."""

from .builtins import builtin_manifests
from .registry import ConnectorRegistry

__all__ = ["ConnectorRegistry", "builtin_manifests"]
