"""Interfaces between orchestration and infrastructure implementations."""
from collections.abc import Callable, Iterable, Mapping
from typing import Any, Protocol


class ResponsesProvider(Protocol):
    """Provider-neutral access to a streaming or non-streaming response."""

    def create_response(self, **kwargs: Any) -> Any:
        """Create a provider response using normalized orchestration arguments."""


class ToolDispatcher(Protocol):
    """Execute a named local tool with validated arguments."""

    def __call__(self, name: str, arguments: dict[str, Any]) -> Any:
        """Dispatch a local operation."""


class HttpResponse(Protocol):
    status_code: int
    text: str
    headers: Mapping[str, str]

    def close(self) -> None:
        """Release response resources."""


class HttpProvider(Protocol):
    """Minimal HTTP boundary used by provider adapters."""

    request_errors: tuple[type[BaseException], ...]
    timeout_errors: tuple[type[BaseException], ...]

    def get(self, url: str, **kwargs: Any) -> HttpResponse:
        """Issue an HTTP GET request."""

    def post(self, url: str, **kwargs: Any) -> HttpResponse:
        """Issue an HTTP POST request."""


RetryableResult = Callable[[HttpResponse], bool]
ResponseDescription = Callable[[HttpResponse], str]
ResponseStream = Iterable[Any]
