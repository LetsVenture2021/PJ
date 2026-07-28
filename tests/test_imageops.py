import hashlib
import io
import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from PIL import Image

import docops
import imageops
import skills


class TestImageOps(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.old_image_db = imageops.DB_PATH
        self.old_asset_root = imageops.ASSET_ROOT
        self.old_doc_db = docops._DB_PATH
        self.old_exports = docops.EXPORTS_DIR
        imageops.DB_PATH = root / "pj.sqlite3"
        docops._DB_PATH = imageops.DB_PATH
        docops.EXPORTS_DIR = root / "exports"
        docops.EXPORTS_DIR.mkdir()
        imageops.ASSET_ROOT = docops.EXPORTS_DIR / "image-assets"

    def tearDown(self):
        imageops.DB_PATH = self.old_image_db
        imageops.ASSET_ROOT = self.old_asset_root
        docops._DB_PATH = self.old_doc_db
        docops.EXPORTS_DIR = self.old_exports
        self.temp.cleanup()

    @staticmethod
    def png_bytes(color="red"):
        stream = io.BytesIO()
        Image.new("RGB", (12, 8), color).save(stream, format="PNG")
        return stream.getvalue()

    def test_raster_asset_is_immutable_downloadable_and_idempotent(self):
        data = self.png_bytes()
        first = imageops.register_image_bytes(
            data,
            idempotency_key="raster-1",
        )
        repeated = imageops.register_image_bytes(
            data,
            idempotency_key="raster-1",
        )

        self.assertEqual(first["asset_id"], repeated["asset_id"])
        self.assertEqual(first["artifact"]["status"], "ready")
        self.assertEqual(first["artifact"]["sha256"], hashlib.sha256(data).hexdigest())
        self.assertNotIn("path", first)
        resolved = docops.resolve_export_artifact(first["artifact_id"])
        self.assertEqual(resolved["status"], "ready")

        with self.assertRaisesRegex(imageops.ImageOpsError, "different image"):
            imageops.register_image_bytes(
                self.png_bytes("blue"),
                idempotency_key="raster-1",
            )

    def test_controlled_svg_is_safe_and_downloadable(self):
        result = imageops.create_controlled_svg(
            width=640,
            height=360,
            title="Governed visual",
            idempotency_key="svg-1",
        )
        self.assertEqual(result["mime_type"], "image/svg+xml")
        self.assertEqual(result["width"], 640)
        self.assertEqual(result["height"], 360)
        self.assertEqual(result["artifact"]["status"], "ready")

        with self.assertRaisesRegex(imageops.ImageOpsError, "disallowed"):
            imageops.register_controlled_svg(
                '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10">'
                '<script>alert(1)</script></svg>'
            )

    def test_paid_generation_fails_closed_without_network(self):
        with patch.dict(
            "os.environ",
            {
                "PJ_IMAGE_GENERATION_ENABLED": "false",
                "PJ_IMAGE_BUDGET_USD": "0",
            },
            clear=False,
        ):
            with self.assertRaisesRegex(imageops.ImageOpsError, "disabled"):
                imageops.generate_image(
                    "A safe diagram",
                    idempotency_key="generation-1",
                )

    def test_tombstone_preserves_lineage_and_blocks_resolution(self):
        asset = imageops.register_image_bytes(self.png_bytes())
        deleted = imageops.delete_image_asset(asset["asset_id"])
        self.assertEqual(deleted["status"], "tombstoned")
        with self.assertRaisesRegex(imageops.ImageOpsError, "not found"):
            imageops.get_image_asset(asset["asset_id"])
        self.assertEqual(
            docops.resolve_export_artifact(asset["artifact_id"])["status"],
            "tombstoned",
        )

    def test_training_manifest_requires_exact_verified_29_chunks(self):
        root = Path(self.temp.name) / "package"
        root.mkdir()
        chunks = []
        for index in range(29):
            filename = f"chunk-{index + 1:02d}.md"
            data = f"chunk {index + 1}".encode()
            (root / filename).write_bytes(data)
            chunks.append(
                {
                    "filename": filename,
                    "byte_size": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                }
            )
        manifest = root / "manifest.json"
        manifest.write_text(json.dumps({
            "schema_version": "1.0",
            "package_version": "test-1",
            "chunks": chunks,
        }))

        result = imageops.inspect_training_package(str(manifest))
        self.assertEqual(result["status"], "verified")
        self.assertEqual(result["chunk_count"], 29)

        chunks.pop()
        manifest.write_text(json.dumps({
            "schema_version": "1.0",
            "package_version": "test-1",
            "chunks": chunks,
        }))
        with self.assertRaisesRegex(imageops.ImageOpsError, "exactly 29"):
            imageops.inspect_training_package(str(manifest))

    def test_status_and_tools_report_explicit_degradation(self):
        with patch.dict("os.environ", {}, clear=True):
            status = imageops.get_image_capability_status()
        self.assertEqual(status["generation"], "disabled")
        self.assertEqual(status["training"], "training_unavailable")
        self.assertEqual(
            status["operations"]["edit"],
            "adapter_unavailable",
        )
        tool_names = {tool["name"] for tool in imageops.IMAGEOPS_SCHEMAS}
        self.assertTrue(
            {
                "generate_image_asset",
                "edit_image_asset",
                "create_image_variation",
                "create_controlled_image",
                "register_vector_image",
                "get_image_asset",
                "delete_image_asset",
                "record_image_feedback",
                "get_image_capability_status",
            }.issubset(tool_names)
        )
        self.assertTrue(tool_names.issubset(skills.DISPATCH_TABLE))

    def test_provider_calls_are_budgeted_and_idempotent(self):
        class FakeProvider:
            name = "fake"

            def __init__(self):
                self.generate_calls = 0
                self.edit_calls = 0
                self.variation_calls = 0

            def generate(self, prompt, *, size, quality):
                self.generate_calls += 1
                return imageops.ProviderImage(self_png)

            def edit(self, image, prompt, *, size, quality):
                self.edit_calls += 1
                return imageops.ProviderImage(self_blue)

            def variation(self, image, *, size):
                self.variation_calls += 1
                return imageops.ProviderImage(self_green)

        self_png = self.png_bytes()
        self_blue = self.png_bytes("blue")
        self_green = self.png_bytes("green")
        provider = FakeProvider()
        env = {
            "PJ_IMAGE_GENERATION_ENABLED": "true",
            "PJ_IMAGE_BUDGET_USD": "5",
            "PJ_IMAGE_ESTIMATED_CALL_USD": "0.25",
        }
        with patch.dict("os.environ", env, clear=False), patch.object(
            imageops, "_provider", return_value=provider
        ):
            generated = imageops.generate_image(
                "Governed image",
                idempotency_key="provider-generate",
            )
            repeated = imageops.generate_image(
                "Governed image",
                idempotency_key="provider-generate",
            )
            edited = imageops.edit_image(
                generated["asset_id"],
                "Make it blue",
                idempotency_key="provider-edit",
            )
            varied = imageops.create_image_variation(
                generated["asset_id"],
                idempotency_key="provider-variation",
            )
            with self.assertRaisesRegex(
                imageops.ImageOpsError, "different image request"
            ):
                imageops.generate_image(
                    "Different prompt",
                    idempotency_key="provider-generate",
                )

        self.assertEqual(generated["asset_id"], repeated["asset_id"])
        self.assertEqual(edited["parent_asset_id"], generated["asset_id"])
        self.assertEqual(varied["parent_asset_id"], generated["asset_id"])
        self.assertEqual(provider.generate_calls, 1)
        self.assertEqual(provider.edit_calls, 1)
        self.assertEqual(provider.variation_calls, 1)
        with imageops._db() as conn:
            committed = conn.execute(
                "SELECT COUNT(*) FROM imageops_budget_reservations "
                "WHERE status='committed'"
            ).fetchone()[0]
        self.assertEqual(committed, 3)

    def test_provider_result_is_reused_after_asset_storage_failure(self):
        class FakeProvider:
            name = "fake"

            def __init__(self):
                self.generate_calls = 0

            def generate(self, _prompt, *, size, quality):
                self.generate_calls += 1
                return imageops.ProviderImage(
                    self_png,
                    provider_asset_id="provider-asset-1",
                )

        self_png = self.png_bytes()
        provider = FakeProvider()
        env = {
            "PJ_IMAGE_GENERATION_ENABLED": "true",
            "PJ_IMAGE_BUDGET_USD": "5",
            "PJ_IMAGE_ESTIMATED_CALL_USD": "0.25",
        }
        with patch.dict("os.environ", env, clear=False), patch.object(
            imageops, "_provider", return_value=provider
        ):
            with patch.object(
                imageops,
                "register_image_bytes",
                side_effect=RuntimeError("storage unavailable"),
            ):
                with self.assertRaisesRegex(RuntimeError, "storage unavailable"):
                    imageops.generate_image(
                        "Retry-safe image",
                        idempotency_key="provider-storage-retry",
                    )
            with imageops._db() as conn:
                conn.execute(
                    "UPDATE imageops_idempotency SET status='storing', "
                    "updated_at='2000-01-01T00:00:00+00:00' "
                    "WHERE idempotency_key='provider-storage-retry'"
                )
            result = imageops.generate_image(
                "Retry-safe image",
                idempotency_key="provider-storage-retry",
            )

        self.assertEqual(result["operation"], "generate")
        self.assertEqual(provider.generate_calls, 1)
        self.assertFalse(
            imageops._provider_staging_path(
                "provider-storage-retry"
            ).exists()
        )
        with imageops._db() as conn:
            state, provider_asset_id = conn.execute(
                "SELECT status, provider_asset_id FROM imageops_idempotency "
                "WHERE idempotency_key='provider-storage-retry'"
            ).fetchone()
        self.assertEqual(state, "ready")
        self.assertEqual(provider_asset_id, "provider-asset-1")

    def test_concurrent_paid_request_invokes_provider_once(self):
        entered = threading.Event()
        release = threading.Event()

        class SlowProvider:
            name = "fake"

            def __init__(self):
                self.generate_calls = 0

            def generate(self, _prompt, *, size, quality):
                self.generate_calls += 1
                entered.set()
                self.assert_released()
                return imageops.ProviderImage(self_png)

            @staticmethod
            def assert_released():
                if not release.wait(timeout=5):
                    raise AssertionError("provider test release timed out")

        self_png = self.png_bytes()
        provider = SlowProvider()
        env = {
            "PJ_IMAGE_GENERATION_ENABLED": "true",
            "PJ_IMAGE_BUDGET_USD": "5",
            "PJ_IMAGE_ESTIMATED_CALL_USD": "0.25",
        }
        with patch.dict("os.environ", env, clear=False), patch.object(
            imageops, "_provider", return_value=provider
        ), ThreadPoolExecutor(max_workers=2) as pool:
            future = pool.submit(
                imageops.generate_image,
                "Concurrent image",
                idempotency_key="provider-concurrent",
            )
            self.assertTrue(entered.wait(timeout=5))
            with self.assertRaisesRegex(
                imageops.ImageOpsError,
                "already in progress",
            ):
                imageops.generate_image(
                    "Concurrent image",
                    idempotency_key="provider-concurrent",
                )
            release.set()
            first = future.result(timeout=5)
            repeated = imageops.generate_image(
                "Concurrent image",
                idempotency_key="provider-concurrent",
            )

        self.assertEqual(first["asset_id"], repeated["asset_id"])
        self.assertEqual(provider.generate_calls, 1)

    def test_concurrent_local_registration_is_serialized(self):
        entered = threading.Event()
        release = threading.Event()
        original_write = imageops._atomic_write

        def slow_write(path, data):
            entered.set()
            if not release.wait(timeout=5):
                raise AssertionError("storage test release timed out")
            original_write(path, data)

        data = self.png_bytes()
        with patch.object(
            imageops, "_atomic_write", side_effect=slow_write
        ), ThreadPoolExecutor(max_workers=2) as pool:
            future = pool.submit(
                imageops.register_image_bytes,
                data,
                idempotency_key="local-concurrent",
            )
            self.assertTrue(entered.wait(timeout=5))
            with self.assertRaisesRegex(
                imageops.ImageOpsError,
                "already in progress",
            ):
                imageops.register_image_bytes(
                    data,
                    idempotency_key="local-concurrent",
                )
            release.set()
            first = future.result(timeout=5)

        repeated = imageops.register_image_bytes(
            data,
            idempotency_key="local-concurrent",
        )
        self.assertEqual(first["asset_id"], repeated["asset_id"])

    def test_edit_and_variation_validate_options_before_provider_work(self):
        with self.assertRaisesRegex(imageops.ImageOpsError, "unsupported image size"):
            imageops.edit_image(
                "IMG-missing",
                "Edit it",
                size="invalid",
                idempotency_key="edit-invalid-size",
            )
        with self.assertRaisesRegex(imageops.ImageOpsError, "unsupported image quality"):
            imageops.edit_image(
                "IMG-missing",
                "Edit it",
                quality="invalid",
                idempotency_key="edit-invalid-quality",
            )
        with self.assertRaisesRegex(imageops.ImageOpsError, "unsupported image size"):
            imageops.create_image_variation(
                "IMG-missing",
                size="invalid",
                idempotency_key="variation-invalid-size",
            )


if __name__ == "__main__":
    unittest.main()
