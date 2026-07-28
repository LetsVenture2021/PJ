"""Requests-backed HTTP provider."""
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RequestsHttpProvider:
    requests_module: Any

    @property
    def request_errors(self) -> tuple[type[BaseException], ...]:
        return (self.requests_module.RequestException,)

    @property
    def timeout_errors(self) -> tuple[type[BaseException], ...]:
        return (self.requests_module.Timeout,)

    def get(self, url: str, **kwargs: Any):
        return self.requests_module.get(url, **kwargs)

    def post(self, url: str, **kwargs: Any):
        return self.requests_module.post(url, **kwargs)
