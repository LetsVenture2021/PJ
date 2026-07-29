"""Vision analysis for uploaded raster images.

Turns a registered upload into an ``input_image`` on a Responses call so PJ
can describe screenshots, photos, and diagrams. Uses ``detail: original`` for
OCR-grade fidelity. Read-only: the image is sent to the provider for analysis
and nothing is written besides the returned text.
"""

from __future__ import annotations

import base64
from pathlib import PurePosixPath

from ops.docs import uploads as document_uploads

RASTER_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
MAX_IMAGE_BYTES = 25 * 1024 * 1024
MIME_BY_EXTENSION = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
}


def analyze_uploaded_image(upload_id: str = "", question: str = "", saved_path: str = "") -> dict:
    record = document_uploads.get_uploaded_document(upload_id, saved_path)
    if record.get("error"):
        return record
    if "documents" in record:
        return {
            "error": "upload contains multiple files; pass saved_path",
            "documents": [d["saved_path"] for d in record["documents"]],
        }
    relative = PurePosixPath(record["saved_path"])
    extension = relative.suffix.lower()
    if extension not in RASTER_EXTENSIONS:
        return {"error": f"'{extension}' is not a supported image format"}
    path = document_uploads.UPLOADS_DIR.joinpath(*relative.parts[1:])
    if not path.is_file() or path.stat().st_size > MAX_IMAGE_BYTES:
        return {"error": "image is missing or exceeds the 25 MB analysis cap"}
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    from openai import OpenAI

    from ops.realtime.orchestration import load_config

    prompt = str(question or "").strip() or "Describe this image in detail."
    try:
        response = OpenAI().responses.create(
            model=load_config()["model"],
            input=[
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": prompt},
                        {
                            "type": "input_image",
                            "image_url": f"data:{MIME_BY_EXTENSION[extension]};base64,{encoded}",
                            "detail": "original",
                        },
                    ],
                }
            ],
        )
    except Exception as exc:
        return {"error": f"vision_analysis_failed: {str(exc)[:200]}"}
    return {"status": "analyzed", "name": record["name"], "analysis": response.output_text}


VISION_SCHEMAS = [
    {
        "type": "function",
        "name": "analyze_uploaded_image",
        "description": (
            "Look at an uploaded raster image (screenshot, photo, diagram) and "
            "answer a question about it. Use whenever the owner uploads an "
            "image and asks about its contents."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "upload_id": {"type": "string", "description": "UPL-... id"},
                "question": {"type": "string"},
                "saved_path": {"type": "string"},
            },
            "required": ["upload_id"],
        },
    }
]

VISION_DISPATCH = {
    "analyze_uploaded_image": lambda upload_id="", question="", saved_path="": (
        analyze_uploaded_image(upload_id, question, saved_path)
    )
}
