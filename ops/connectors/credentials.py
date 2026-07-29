"""Opaque credential storage adapters; public methods never return token values."""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from typing import Mapping


class MissingSecretError(RuntimeError):
    pass


class CredentialProvider(ABC):
    @abstractmethod
    def exists(self, handle: str) -> bool: ...

    @abstractmethod
    def _resolve_for_connector(self, handle: str) -> str:
        """Resolve internally for an adapter call. Never expose via tools or APIs."""

    @abstractmethod
    def rotate(self, handle: str, value: str) -> None: ...

    @abstractmethod
    def revoke(self, handle: str) -> None: ...


class EnvironmentCredentialProvider(CredentialProvider):
    """Maps opaque handles to environment variable names."""

    def __init__(self, references: Mapping[str, str], environ: Mapping[str, str] | None = None):
        self._references = dict(references)
        self._environ = os.environ if environ is None else environ

    def exists(self, handle: str) -> bool:
        name = self._references.get(handle)
        return bool(name and self._environ.get(name))

    def _resolve_for_connector(self, handle: str) -> str:
        name = self._references.get(handle)
        value = self._environ.get(name, "") if name else ""
        if not value:
            raise MissingSecretError("Connector credential is unavailable")
        return value

    def rotate(self, handle: str, value: str) -> None:
        raise NotImplementedError("Environment secrets must be rotated by the operator")

    def revoke(self, handle: str) -> None:
        self._references.pop(handle, None)


class MemoryCredentialProvider(CredentialProvider):
    """Mockable provider for tests; never use as durable production storage."""

    def __init__(self) -> None:
        self._values: dict[str, str] = {}

    def exists(self, handle: str) -> bool:
        return handle in self._values

    def _resolve_for_connector(self, handle: str) -> str:
        try:
            return self._values[handle]
        except KeyError as exc:
            raise MissingSecretError("Connector credential is unavailable") from exc

    def rotate(self, handle: str, value: str) -> None:
        self._values[handle] = value

    def revoke(self, handle: str) -> None:
        self._values.pop(handle, None)
