"""Provider-neutral visual understanding boundary."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, Sequence

from ops.multimodal.models import ChartObservation, MediaReference, OcrBlock


@dataclass(frozen=True, slots=True)
class VisionRequest:
    media: MediaReference
    task: str
    max_output_tokens: int


@dataclass(frozen=True, slots=True)
class VisionResult:
    ocr: Sequence[OcrBlock] = ()
    observations: Sequence[ChartObservation] = ()


class VisionProvider(Protocol):
    def analyze(self, request: VisionRequest) -> VisionResult:
        """Analyze referenced media without exposing a vendor SDK to domain code."""


@dataclass(frozen=True, slots=True)
class CallableVisionProvider:
    """Adapter for an injected provider callable; useful for offline tests."""

    client: Any

    def analyze(self, request: VisionRequest) -> VisionResult:
        result = self.client(request)
        if not isinstance(result, VisionResult):
            raise TypeError("vision provider returned an invalid normalized result")
        return result
