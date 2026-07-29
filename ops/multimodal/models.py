"""Normalized immutable models for grounded visual results.

Coordinates are fractions of the source dimensions.  Every derived result is
bound to a page/frame and the SHA-256 of its immutable source artifact.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


def _unit(value: float, name: str) -> float:
    value = float(value)
    if not 0.0 <= value <= 1.0:
        raise ValueError(f"{name} must be between 0 and 1")
    return value


def _hash(value: str) -> str:
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise ValueError("source_sha256 must be a lowercase SHA-256 hex digest")
    return value


@dataclass(frozen=True, slots=True)
class MediaReference:
    artifact_id: str
    sha256: str
    media_type: str
    byte_size: int
    width: int | None = None
    height: int | None = None
    page_count: int | None = None

    def __post_init__(self) -> None:
        _hash(self.sha256)
        if self.byte_size < 0:
            raise ValueError("byte_size cannot be negative")
        for name in ("width", "height", "page_count"):
            value = getattr(self, name)
            if value is not None and value <= 0:
                raise ValueError(f"{name} must be positive")


@dataclass(frozen=True, slots=True)
class Frame:
    frame_id: str
    source_sha256: str
    sequence: int
    captured_at_ms: int
    width: int
    height: int
    source: Literal["camera", "screen", "image", "pdf"]
    page: int | None = None

    def __post_init__(self) -> None:
        _hash(self.source_sha256)
        if min(self.sequence, self.captured_at_ms) < 0 or min(self.width, self.height) <= 0:
            raise ValueError("invalid frame dimensions, sequence, or timestamp")
        if self.page is not None and self.page < 1:
            raise ValueError("page numbers are one-based")


@dataclass(frozen=True, slots=True)
class Region:
    x: float
    y: float
    width: float
    height: float

    def __post_init__(self) -> None:
        for name in ("x", "y", "width", "height"):
            object.__setattr__(self, name, _unit(getattr(self, name), name))
        if self.width == 0 or self.height == 0:
            raise ValueError("region must have positive area")
        if self.x + self.width > 1.0 or self.y + self.height > 1.0:
            raise ValueError("region exceeds source bounds")


@dataclass(frozen=True, slots=True)
class Grounding:
    source_sha256: str
    frame_id: str | None = None
    page: int | None = None
    region: Region | None = None
    confidence: float = 1.0

    def __post_init__(self) -> None:
        _hash(self.source_sha256)
        object.__setattr__(self, "confidence", _unit(self.confidence, "confidence"))
        if self.frame_id is None and self.page is None:
            raise ValueError("grounding requires a frame_id or page")
        if self.page is not None and self.page < 1:
            raise ValueError("page numbers are one-based")


@dataclass(frozen=True, slots=True)
class OcrBlock:
    text: str
    grounding: Grounding
    correction_proposal: str | None = None


@dataclass(frozen=True, slots=True)
class ChartObservation:
    kind: Literal["chart", "diagram", "table"]
    summary: str
    grounding: Grounding
    data: tuple[tuple[str, str], ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class Citation:
    label: str
    grounding: Grounding
