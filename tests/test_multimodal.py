"""Offline security and lifecycle tests for multimodal primitives."""

import json
import tempfile
import unittest
from pathlib import Path

from ops.docs.visual_extraction import extract_visual_regions
from ops.multimodal.budgets import BudgetExceeded, CaptureBudget
from ops.multimodal.models import Grounding, MediaReference, OcrBlock, Region
from ops.multimodal.redaction import candidate, confirm
from ops.multimodal.store import MediaStore, RetentionMode
from ops.shared.providers.vision import CallableVisionProvider, VisionResult


HASH = "a" * 64


class MultimodalModelTests(unittest.TestCase):
    def test_region_coordinates_are_normalized_and_bounded(self):
        self.assertEqual(Region(0.1, 0.2, 0.3, 0.4).width, 0.3)
        for values in ((-0.1, 0, 0.2, 0.2), (0.9, 0, 0.2, 0.2), (0, 0, 0, 0.2)):
            with self.subTest(values=values), self.assertRaises(ValueError):
                Region(*values)

    def test_automated_redaction_requires_confirmation(self):
        proposal = candidate(Region(0, 0, 0.2, 0.2), HASH, "frame-1", "possible face")
        self.assertFalse(proposal.confirmed)
        self.assertTrue(confirm(proposal).confirmed)


class MediaStoreTests(unittest.TestCase):
    def test_ephemeral_capture_persists_metadata_only(self):
        malicious = b"<svg><script>alert('secret screen')</script></svg>"
        with tempfile.TemporaryDirectory() as temp:
            store = MediaStore(Path(temp))
            receipt = store.ingest(malicious, "image/svg+xml", RetentionMode.EPHEMERAL)
            self.assertIsNone(receipt.artifact_id)
            self.assertEqual(list(store.artifacts.iterdir()), [])
            receipt_text = next(store.receipts.iterdir()).read_text()
            self.assertNotIn("script", receipt_text)
            self.assertEqual(json.loads(receipt_text)["byte_size"], len(malicious))

    def test_retained_media_is_content_addressed_and_reused(self):
        with tempfile.TemporaryDirectory() as temp:
            store = MediaStore(Path(temp))
            first = store.ingest(b"same frame", "image/jpeg", RetentionMode.RETAIN)
            second = store.ingest(b"same frame", "image/jpeg", RetentionMode.RETAIN)
            self.assertEqual(first.artifact_id, second.artifact_id)
            self.assertEqual(len(list(store.artifacts.iterdir())), 1)


class VisionBoundaryTests(unittest.TestCase):
    def test_mocked_vision_ocr_is_data_not_markup(self):
        source = MediaReference("MEDIA-test", HASH, "image/png", 100, 10, 10)
        grounding = Grounding(HASH, frame_id="frame-1", region=Region(0, 0, 1, 1), confidence=0.8)
        malicious_ocr = "<img src=x onerror=alert(1)>"
        provider = CallableVisionProvider(
            lambda request: VisionResult(ocr=(OcrBlock(malicious_ocr, grounding, "safe text"),))
        )
        result = extract_visual_regions(source, provider, CaptureBudget(max_bytes=100))
        self.assertEqual(result.ocr[0].text, malicious_ocr)
        self.assertEqual(result.ocr[0].correction_proposal, "safe text")

    def test_oversize_and_cost_limits_fail_before_provider(self):
        source = MediaReference("MEDIA-test", HASH, "image/png", 101, 10, 10)
        calls = []
        provider = CallableVisionProvider(lambda request: calls.append(request) or VisionResult())
        with self.assertRaises(BudgetExceeded):
            extract_visual_regions(source, provider, CaptureBudget(max_bytes=100))
        self.assertEqual(calls, [])
        with self.assertRaises(BudgetExceeded):
            extract_visual_regions(
                MediaReference("MEDIA-test", HASH, "image/png", 1, 10, 10),
                provider,
                CaptureBudget(max_bytes=100, max_spend_micros=2),
                estimated_spend_micros=3,
            )
        self.assertEqual(calls, [])

    def test_mismatched_grounding_hash_is_rejected(self):
        source = MediaReference("MEDIA-test", HASH, "application/pdf", 1, page_count=1)
        bad = Grounding("b" * 64, page=1)
        provider = CallableVisionProvider(lambda request: VisionResult(ocr=(OcrBlock("x", bad),)))
        with self.assertRaisesRegex(ValueError, "source_hash_mismatch"):
            extract_visual_regions(source, provider, CaptureBudget(max_bytes=1))


if __name__ == "__main__":
    unittest.main()
