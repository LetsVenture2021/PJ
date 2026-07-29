"""Bounded normalized visual extraction for accepted PDFs and images."""

from __future__ import annotations

from dataclasses import dataclass

from ops.multimodal.budgets import CaptureBudget
from ops.multimodal.models import ChartObservation, MediaReference, OcrBlock
from ops.shared.providers.vision import VisionProvider, VisionRequest


@dataclass(frozen=True, slots=True)
class VisualExtraction:
    source: MediaReference
    ocr: tuple[OcrBlock, ...]
    observations: tuple[ChartObservation, ...]


def extract_visual_regions(
    source: MediaReference,
    provider: VisionProvider,
    budget: CaptureBudget,
    *,
    task: str = "Extract OCR, tables, charts, and diagrams with page/region grounding",
    estimated_spend_micros: int = 0,
    max_output_tokens: int = 2_000,
) -> VisualExtraction:
    """Analyze a previously validated artifact through the neutral provider boundary."""
    if source.media_type not in {"application/pdf", "image/jpeg", "image/png", "image/webp"}:
        raise ValueError("visual_extraction_media_type_rejected")
    if not 1 <= max_output_tokens <= 4_000:
        raise ValueError("visual_extraction_token_limit_invalid")
    budget.charge(byte_size=source.byte_size, spend_micros=estimated_spend_micros)
    result = provider.analyze(VisionRequest(source, task, max_output_tokens))
    for item in (*result.ocr, *result.observations):
        if item.grounding.source_sha256 != source.sha256:
            raise ValueError("vision_result_source_hash_mismatch")
    return VisualExtraction(source, tuple(result.ocr), tuple(result.observations))
