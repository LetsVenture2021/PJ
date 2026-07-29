"""OpenAI adapters used by prompting and realtime orchestration."""

import json
from dataclasses import dataclass
from typing import Any

from ..interfaces import HttpProvider


@dataclass(frozen=True)
class ProviderResponse:
    id: str
    status: str
    output_text: str = ""


def _normalize_response(response: Any, *, response_id: str = "") -> ProviderResponse:
    return ProviderResponse(
        id=str(getattr(response, "id", response_id)),
        status=str(getattr(response, "status", "unknown")),
        output_text=str(getattr(response, "output_text", "") or ""),
    )


def _authorization_header(api_key: str) -> str:
    return "Bearer " + api_key


@dataclass(frozen=True)
class OpenAIResponsesProvider:
    client: Any

    def create_response(self, **kwargs: Any):
        return self.client.responses.create(**kwargs)

    def retrieve_response(self, response_id: str) -> ProviderResponse:
        return _normalize_response(
            self.client.responses.retrieve(response_id), response_id=response_id
        )

    def cancel_response(self, response_id: str) -> ProviderResponse:
        return _normalize_response(
            self.client.responses.cancel(response_id), response_id=response_id
        )


@dataclass(frozen=True)
class OpenAIRealtimeProvider:
    http: HttpProvider

    def exchange_sdp(self, api_key: str, offer: str, session: dict, *, timeout: int):
        return self.http.post(
            "https://api.openai.com/v1/realtime/calls",
            headers={"Authorization": _authorization_header(api_key)},
            files={
                "sdp": (None, offer),
                "session": (None, json.dumps(session)),
            },
            timeout=timeout,
        )

    def mint_client_secret(self, api_key: str, session: dict, *, timeout: int):
        return self.http.post(
            "https://api.openai.com/v1/realtime/client_secrets",
            headers={
                "Authorization": _authorization_header(api_key),
                "Content-Type": "application/json",
            },
            json={"session": session},
            timeout=timeout,
        )

    def accept_call(self, api_key: str, call_id: str, session: dict, *, timeout: int):
        return self.http.post(
            f"https://api.openai.com/v1/realtime/calls/{call_id}/accept",
            headers={
                "Authorization": _authorization_header(api_key),
                "Content-Type": "application/json",
            },
            json=session,
            timeout=timeout,
        )
