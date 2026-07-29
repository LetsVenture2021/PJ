"""Provider-neutral OAuth authorization-code flow with PKCE safeguards."""

from __future__ import annotations

import base64
import hashlib
import secrets
from dataclasses import dataclass
from datetime import timedelta
from typing import Protocol
from urllib.parse import urlencode

from .credentials import CredentialProvider
from .models import AuthorizationState, CredentialRecord, utc_now


class OAuthError(RuntimeError):
    pass


@dataclass(frozen=True)
class TokenMetadata:
    account_id: str
    account_label: str
    scopes: tuple[str, ...]
    expires_in: int | None


@dataclass(frozen=True)
class OAuthTokenSet:
    """Provider-internal exchange result, immediately placed in a secret store."""

    access_token: str
    refresh_token: str | None
    metadata: TokenMetadata


class OAuthProvider(Protocol):
    authorization_endpoint: str

    def exchange_code(self, code: str, verifier: str, redirect_uri: str) -> OAuthTokenSet: ...
    def refresh(self, credential_handle: str) -> TokenMetadata: ...
    def revoke(self, credential_handle: str) -> None: ...


class OAuthCoordinator:
    def __init__(
        self,
        provider: OAuthProvider,
        credentials: CredentialProvider,
        redirect_allowlist: set[str],
        ttl_seconds: int = 600,
    ):
        self.provider = provider
        self.credentials = credentials
        self.redirect_allowlist = frozenset(redirect_allowlist)
        self.ttl = timedelta(seconds=ttl_seconds)
        self._pending: dict[str, AuthorizationState] = {}

    def begin(
        self, client_id: str, redirect_uri: str, scopes: tuple[str, ...]
    ) -> tuple[str, AuthorizationState]:
        if redirect_uri not in self.redirect_allowlist:
            raise OAuthError("Redirect URI is not exactly allowlisted")
        state, nonce, verifier = (secrets.token_urlsafe(32) for _ in range(3))
        auth = AuthorizationState(state, nonce, verifier, redirect_uri)
        self._pending[state] = auth
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .rstrip(b"=")
            .decode()
        )
        query = urlencode(
            {
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "scope": " ".join(scopes),
                "response_type": "code",
                "state": state,
                "nonce": nonce,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
        )
        return f"{self.provider.authorization_endpoint}?{query}", auth

    def complete(self, code: str, state: str, nonce: str, redirect_uri: str) -> CredentialRecord:
        pending = self._pending.pop(state, None)
        if pending is None or not secrets.compare_digest(pending.nonce, nonce):
            raise OAuthError("Invalid OAuth state or nonce")
        if redirect_uri != pending.redirect_uri or redirect_uri not in self.redirect_allowlist:
            raise OAuthError("Redirect URI mismatch")
        if utc_now() - pending.created_at > self.ttl:
            raise OAuthError("OAuth authorization attempt expired")
        token_set = self.provider.exchange_code(code, pending.code_verifier, redirect_uri)
        handle = "oauth_" + secrets.token_urlsafe(24)
        self.credentials.rotate(handle, token_set.refresh_token or token_set.access_token)
        metadata = token_set.metadata
        expiry = utc_now() + timedelta(seconds=metadata.expires_in) if metadata.expires_in else None
        return CredentialRecord(
            handle, "oauth", metadata.account_id, metadata.account_label, metadata.scopes, expiry
        )
