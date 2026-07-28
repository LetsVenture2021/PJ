"""OpenAI adapters used by prompting and realtime orchestration."""
import json
from dataclasses import dataclass
from typing import Any

from ..interfaces import HttpProvider


def _authorization_header(api_key: str) -> str:
    return "Bearer " + api_key


@dataclass(frozen=True)
class OpenAIResponsesProvider:
    client: Any

    def create_response(self, **kwargs: Any):
        return self.client.responses.create(**kwargs)


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

    def accept_call(
            self,
            api_key: str,
            call_id: str,
            session: dict,
            *,
            timeout: int):
        return self.http.post(
            f"https://api.openai.com/v1/realtime/calls/{call_id}/accept",
            headers={
                "Authorization": _authorization_header(api_key),
                "Content-Type": "application/json",
            },
            json=session,
            timeout=timeout,
        )
