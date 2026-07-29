"""Bounded retry policy for provider I/O."""
import time
from dataclasses import dataclass
from typing import Any

from .interfaces import HttpProvider, HttpResponse
from .logging import get_logger


_LOGGER = get_logger("retry")


@dataclass(frozen=True)
class RetryPolicy:
    attempts: int = 4
    backoff_seconds: float = 0.5

    def normalized_attempts(self) -> int:
        return max(1, int(self.attempts or 1))


def get_with_retry(
        provider: HttpProvider,
        url: str,
        *,
        policy: RetryPolicy,
        sleep=time.sleep,
        **kwargs: Any) -> HttpResponse:
    attempts = policy.normalized_attempts()
    last_error = ""
    for attempt in range(attempts):
        try:
            response = provider.get(url, **kwargs)
        except provider.request_errors as exc:
            last_error = str(exc)
        else:
            if response.status_code < 400:
                return response
            last_error = f"HTTP {response.status_code}: {response.text[:300]}"
            if response.status_code != 429 and response.status_code < 500:
                # A response owns a pooled connection until its body is consumed
                # or it is closed.  This branch is not retried, but it still has
                # to release that connection before surfacing the status error.
                response.close()
                raise RuntimeError(last_error)
            response.close()
        if attempt + 1 < attempts:
            delay = policy.backoff_seconds * (2 ** attempt)
            _LOGGER.debug("Retrying provider request in %.3f seconds", delay)
            sleep(delay)
    raise RuntimeError(
        f"request failed after {attempts} attempts: {last_error}"
    )
