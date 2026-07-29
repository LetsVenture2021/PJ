#!/usr/bin/env python3
"""
realtime_server.py — signaling server that connects PJ to voice.

Provides:
  POST /session    - browser WebRTC signaling (SDP offer in, SDP answer out)
  POST /execute-tool - executes local PJ tools for browser function calls

The legacy local-only POST /webhook SIP handler is unsupported and must not be
exposed publicly because webhook signatures are not verified.
"""

import codecs
import hashlib
import hmac
import ipaddress
import json
import logging
import os
import re
import secrets
import shutil
import sqlite3
import zipfile
from pathlib import Path
from pathlib import PurePosixPath
from time import perf_counter

import requests
from flask import (
    Flask,
    g,
    request,
    Response,
    send_file,
    send_from_directory,
    session,
    stream_with_context,
)
from flask_cors import CORS
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from openai import OpenAI
from werkzeug.exceptions import RequestEntityTooLarge

import chatlog
import docops
import promptops
import skills
from ops.docs import formats as upload_formats
from ops.productivity.ui import blueprint as productivity_blueprint
from ops.docs import uploads as document_uploads
from pj_contract import CONTRACT_VERSION, PROTOCOL_VERSION
from ops.shared.logging import (
    bind_log_context,
    clear_log_context,
    configure_logging,
    get_logger,
    new_correlation_id,
    set_log_context,
)
from ops.realtime.payload_validation import (
    RealtimePayloadValidationError,
    validate_inbound_payload,
    validate_outbound_event,
    validate_outbound_payload,
)
from realtime_config import realtime_session_config, realtime_tool_schemas
from responses_runtime import (
    capability_manifest,
    dispatch_realtime_function,
    load_config,
    redact_server_paths,
    requested_deliverable_format,
    ResponsesOrchestrator,
    sanitize_text_urls,
)

BASE_DIR = Path(__file__).resolve().parents[2]
MAX_ERROR_DETAIL_LENGTH = 320
MAX_SESSION_TITLE_LENGTH = 120
MAX_MESSAGE_LENGTH = 20000
SESSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{8,128}$")
ARTIFACT_ID_PATTERN = re.compile(r"^ART-[a-f0-9]{32}$")
SCHEMA_NAME_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
ARTIFACT_ID_PATTERN = re.compile(r"^ART-[a-f0-9]{32}$")
OPENAI_CLIENT_FACTORY = OpenAI
DEFAULT_MAX_UPLOAD_FILE_BYTES = 90 * 1024 * 1024
DEFAULT_MAX_UPLOAD_TOTAL_BYTES = 100 * 1024 * 1024
UPLOAD_MULTIPART_OVERHEAD_BYTES = 1024 * 1024
UPLOAD_CHUNK_BYTES = 1024 * 1024
UPLOAD_ID_PATTERN = re.compile(r"^UPL-[a-f0-9]{32}$")
ALLOWED_UPLOAD_TYPES = {
    ".csv": {"text/csv", "application/vnd.ms-excel"},
    ".doc": {"application/msword"},
    ".docx": {"application/vnd.openxmlformats-officedocument.wordprocessingml.document"},
    ".json": {"application/json", "text/json"},
    ".md": {"text/markdown", "text/plain"},
    ".markdown": {"text/markdown", "text/plain"},
    ".odp": {"application/vnd.oasis.opendocument.presentation"},
    ".ods": {"application/vnd.oasis.opendocument.spreadsheet"},
    ".odt": {"application/vnd.oasis.opendocument.text"},
    ".pdf": {"application/pdf"},
    ".ppt": {"application/vnd.ms-powerpoint"},
    ".pptx": {"application/vnd.openxmlformats-officedocument.presentationml.presentation"},
    ".rtf": {"application/rtf", "text/rtf"},
    ".tsv": {"text/tab-separated-values", "text/plain"},
    ".txt": {"text/plain"},
    ".xls": {"application/vnd.ms-excel"},
    ".xlsx": {"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"},
    ".xml": {"application/xml", "text/xml"},
    ".yaml": {"application/yaml", "text/yaml", "text/plain"},
    ".yml": {"application/yaml", "text/yaml", "text/plain"},
}
TEXT_UPLOAD_EXTENSIONS = {
    ".csv",
    ".json",
    ".md",
    ".markdown",
    ".tsv",
    ".txt",
    ".xml",
    ".yaml",
    ".yml",
}

configure_logging()
_LOGGER = get_logger("realtime.server")


class DurableExecutionOutcomeUnknown(RuntimeError):
    """Raised when replaying a tool or provider effect would be unsafe."""


app = Flask(__name__, static_folder=str(BASE_DIR / "assets"), static_url_path="/assets")
app.register_blueprint(productivity_blueprint)
app.secret_key = os.getenv("PJ_LOCAL_WEB_SESSION_SECRET") or secrets.token_hex(32)
app.config.update(
    LOCAL_WEB_OWNER_SESSION_ENABLED=(os.getenv("PJ_LOCAL_WEB_OWNER_SESSION_ENABLED") == "1"),
    MAX_UPLOAD_FILE_BYTES=DEFAULT_MAX_UPLOAD_FILE_BYTES,
    MAX_UPLOAD_TOTAL_BYTES=DEFAULT_MAX_UPLOAD_TOTAL_BYTES,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_NAME="pj_local_web_session",
    SESSION_COOKIE_SAMESITE="Strict",
)
app.config.setdefault("UPLOAD_SCANNER", None)
CORS(app)  # allow local browser origins when running on localhost


@app.before_request
def _start_request_logging():
    req_id = _request_id()
    if request.path in {"/upload/files", "/upload/folder"}:
        request.max_content_length = (
            int(app.config["MAX_UPLOAD_TOTAL_BYTES"]) + UPLOAD_MULTIPART_OVERHEAD_BYTES
        )
    route_values = request.view_args or {}
    session_id = (
        route_values.get("session_id")
        or request.args.get("session_id")
        or request.headers.get("x-pj-session-id")
        or ""
    )
    if not session_id and request.is_json:
        payload = request.get_json(silent=True)
        if isinstance(payload, dict):
            session_id = payload.get("session_id") or ""
    if not SESSION_ID_PATTERN.fullmatch(str(session_id)):
        session_id = None
    set_log_context(request_id=req_id, session_id=session_id)
    g.request_started_at = perf_counter()
    _LOGGER.info(
        "http.request.started",
        extra={"http_method": request.method, "http_path": request.path},
    )
    return _validate_protocol_request(req_id)


@app.after_request
def _finish_request_logging(response):
    req_id = _request_id()
    response.headers.setdefault("x-request-id", req_id)
    started_at = getattr(g, "request_started_at", perf_counter())
    level = logging.WARNING if response.status_code >= 500 else logging.INFO
    _LOGGER.log(
        level,
        "http.request.completed",
        extra={
            "duration_ms": round((perf_counter() - started_at) * 1000, 3),
            "http_method": request.method,
            "http_path": request.path,
            "http_status": response.status_code,
        },
    )
    return response


@app.teardown_request
def _clear_request_logging(_error=None):
    clear_log_context()


@app.errorhandler(RequestEntityTooLarge)
def _handle_request_too_large(_error):
    if request.path in {"/upload/files", "/upload/folder"}:
        session_id = request.headers.get("x-pj-session-id")
        if not SESSION_ID_PATTERN.fullmatch(str(session_id or "")):
            session_id = None
        _upload_audit(
            "upload.rejected",
            req_id=_request_id(),
            session_id=session_id,
            upload_id=None,
            error_code="upload_too_large",
        )
        return _error_response(
            "upload_too_large",
            "The total upload exceeds the configured size limit.",
            413,
            _request_id(),
        )
    return _error_response(
        "request_too_large",
        "The request exceeds the configured size limit.",
        413,
        _request_id(),
    )


def _tool_policy_sha256():
    path = BASE_DIR / "tool_policy.json"
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def _request_id():
    cached = getattr(g, "request_id", None)
    if cached:
        return cached
    req_id = new_correlation_id(request.headers.get("x-pj-client-request-id"))
    g.request_id = req_id
    return req_id


def _trim_detail(detail):
    if detail is None:
        return None
    compact = sanitize_text_urls(" ".join(str(redact_server_paths(str(detail))).split()).strip())
    if not compact:
        return None
    if len(compact) <= MAX_ERROR_DETAIL_LENGTH:
        return compact
    return compact[:MAX_ERROR_DETAIL_LENGTH] + "..."


def _json_response(payload, status=200, req_id=None, outbound_schema=None):
    req_id = req_id or _request_id()
    if outbound_schema:
        try:
            validate_outbound_payload(outbound_schema, payload)
        except RealtimePayloadValidationError as exc:
            _LOGGER.error(
                "realtime.outbound_payload.invalid",
                extra={"error_code": "invalid_outbound_payload"},
            )
            payload = {
                "ok": False,
                "error": {
                    "code": "invalid_outbound_payload",
                    "message": "Server response did not match the realtime contract.",
                    "request_id": req_id,
                    "detail": _trim_detail(exc.detail),
                },
            }
            status = 502
    body = json.dumps({**payload, "version": PROTOCOL_VERSION})
    resp = Response(body, status=status, mimetype="application/json")
    resp.headers["x-request-id"] = req_id
    resp.headers["x-pj-contract-version"] = CONTRACT_VERSION
    resp.headers["x-pj-protocol-version"] = str(PROTOCOL_VERSION)
    return resp


def _error_response(code, message, status, req_id, detail=None):
    return _json_response(
        {
            "ok": False,
            "error": {
                "code": code,
                "message": message,
                "request_id": req_id,
                "detail": _trim_detail(detail),
            },
        },
        status=status,
        req_id=req_id,
    )


def _uses_pj_protocol():
    return request.path in {
        "/health",
        "/session",
        "/token",
        "/execute-tool",
        "/tool-schemas",
        "/upload/files",
        "/upload/folder",
    } or request.path.startswith("/responses/")


def _validate_protocol_request(req_id):
    if not _uses_pj_protocol():
        return None
    received = []
    header_version = request.headers.get("x-pj-protocol-version")
    if header_version is not None:
        received.append(("header", header_version))
    if request.is_json:
        payload = request.get_json(silent=True)
        if isinstance(payload, dict) and "version" in payload:
            received.append(("message", payload["version"]))
    unsupported = [
        {"source": source, "version": version}
        for source, version in received
        if str(version) != str(PROTOCOL_VERSION)
    ]
    if not unsupported:
        return None
    return _error_response(
        "unsupported_protocol_version",
        "The PJ realtime protocol version is not supported.",
        426,
        req_id,
        detail=json.dumps(
            {
                "received": unsupported,
                "supported": [PROTOCOL_VERSION],
            }
        ),
    )


def _is_loopback_request():
    try:
        return ipaddress.ip_address(request.remote_addr or "").is_loopback
    except ValueError:
        return False


def _same_origin_browser_request():
    expected_origin = request.host_url.rstrip("/")
    origin = (request.headers.get("Origin") or "").rstrip("/")
    if origin:
        return hmac.compare_digest(origin, expected_origin)
    referer = request.headers.get("Referer") or ""
    return referer == expected_origin or referer.startswith(f"{expected_origin}/")


def _local_web_session_authorized():
    return (
        app.config["LOCAL_WEB_OWNER_SESSION_ENABLED"]
        and _is_loopback_request()
        and session.get("local_owner") is True
        and _same_origin_browser_request()
    )


def _check_bridge_auth(req_id, *, required=False):
    # `required` is accepted for call-site compatibility; the local loopback
    # owner session bypass below applies uniformly regardless of the flag.
    if _local_web_session_authorized():
        return None
    expected = (os.getenv("PJ_TOOL_BRIDGE_TOKEN") or "").strip()
    if not expected:
        return _error_response(
            "bridge_auth_not_configured",
            "Bridge authorization is not configured.",
            503,
            req_id,
        )
    provided = request.headers.get("Authorization") or ""
    if hmac.compare_digest(
        provided.encode("utf-8"),
        f"Bearer {expected}".encode("utf-8"),
    ):
        return None
    return _error_response(
        "bridge_auth_required",
        "Bridge authorization failed.",
        401,
        req_id,
    )


class UploadValidationError(ValueError):
    def __init__(self, code, message, status, detail=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.detail = detail


def _sanitize_upload_path(raw_path, *, folder_mode):
    if not isinstance(raw_path, str):
        raise UploadValidationError("unsafe_upload_path", "Upload paths must be strings.", 400)
    value = raw_path.strip()
    if (
        not value
        or "\x00" in value
        or "\\" in value
        or value.startswith("/")
        or re.match(r"^[A-Za-z]:", value)
    ):
        raise UploadValidationError(
            "unsafe_upload_path", "Upload path is absolute or malformed.", 400
        )
    parts = value.split("/")
    if (
        any(part in {"", ".", ".."} for part in parts)
        or any(len(part.encode("utf-8")) > 255 for part in parts)
        or any(re.search(r'[\x00-\x1f<>:"|?*]', part) for part in parts)
    ):
        raise UploadValidationError(
            "unsafe_upload_path", "Upload path contains an unsafe component.", 400
        )
    if not folder_mode and len(parts) != 1:
        raise UploadValidationError(
            "unsafe_upload_path", "File uploads cannot include directory paths.", 400
        )
    normalized = PurePosixPath(*parts).as_posix()
    if len(normalized.encode("utf-8")) > 1024:
        raise UploadValidationError("unsafe_upload_path", "Upload path is too long.", 400)
    return normalized


def _validate_upload_type(relative_path, content_type):
    """Accept broadly, parse narrowly: refuse only credential-shaped names here.

    Formats outside the legacy allowlist are accepted and classified by
    ops.docs.formats; their handling tier decides whether they are ever parsed.
    The legacy document extensions keep their strict MIME pairing.
    """
    name = Path(relative_path).name
    if upload_formats.rejected_secret_name(name):
        raise UploadValidationError(
            "upload_rejected_probable_secret",
            f"'{name}' looks like a credential file and was refused.",
            415,
        )
    extension = Path(relative_path).suffix.lower()
    mime = str(content_type or "application/octet-stream").split(";", 1)[0].strip().lower()
    allowed_types = ALLOWED_UPLOAD_TYPES.get(extension)
    if allowed_types and mime not in allowed_types and mime != "application/octet-stream":
        raise UploadValidationError(
            "disallowed_content_type",
            f"Content type '{mime}' is not allowed for {extension} files.",
            415,
        )
    return mime


def _validate_upload_signature(path, extension, classification=None):
    with path.open("rb") as handle:
        sample = handle.read(4096)
    dangerous_signatures = (
        b"MZ",
        b"\x7fELF",
        b"\xfe\xed\xfa\xce",
        b"\xfe\xed\xfa\xcf",
        b"\xce\xfa\xed\xfe",
        b"\xcf\xfa\xed\xfe",
    )
    if any(sample.startswith(signature) for signature in dangerous_signatures):
        raise UploadValidationError("executable_content", "Executable content is not allowed.", 415)
    if classification is not None and classification.rejection:
        raise UploadValidationError(
            classification.rejection,
            "The file content does not match its declared format.",
            415,
        )
    if (
        classification is not None
        and classification.spec.handling == "extract"
        and not classification.spec.magic
        and extension not in TEXT_UPLOAD_EXTENSIONS
    ):
        # Text-like extract families (source code, notebooks, HTML) accept
        # shebangs but must still be genuine text. Binary-container extract
        # families (spreadsheets, OOXML) declare magic bytes and are exempt.
        if b"\x00" in sample:
            raise UploadValidationError(
                "invalid_text_content",
                "Text document contains binary content.",
                415,
            )
    if extension in TEXT_UPLOAD_EXTENSIONS:
        prefix = sample.lstrip().lower()
        if prefix.startswith(b"#!") or prefix.startswith(b"<?php"):
            raise UploadValidationError("script_content", "Script content is not allowed.", 415)
        decoder = codecs.getincrementaldecoder("utf-8")()
        try:
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(UPLOAD_CHUNK_BYTES), b""):
                    if b"\x00" in chunk:
                        raise UploadValidationError(
                            "invalid_text_content",
                            "Text document contains binary content.",
                            415,
                        )
                    decoder.decode(chunk, final=False)
            decoder.decode(b"", final=True)
        except UnicodeDecodeError as exc:
            raise UploadValidationError(
                "invalid_text_content", "Text documents must use UTF-8 encoding.", 415
            ) from exc
        if extension == ".json":
            try:
                json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise UploadValidationError(
                    "invalid_document_content",
                    "JSON uploads must contain valid JSON.",
                    415,
                ) from exc
        return

    sample_lower = sample.lower()
    if extension == ".pdf" and not sample.startswith(b"%PDF-"):
        raise UploadValidationError(
            "invalid_document_content", "The file does not contain a valid PDF header.", 415
        )
    if extension == ".rtf" and not sample_lower.startswith(b"{\\rtf"):
        raise UploadValidationError(
            "invalid_document_content", "The file does not contain a valid RTF header.", 415
        )
    if extension in {".doc", ".xls", ".ppt"} and not sample.startswith(
        b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
    ):
        raise UploadValidationError(
            "invalid_document_content",
            "The file does not contain a valid legacy Office container.",
            415,
        )
    if extension in {".docx", ".xlsx", ".pptx", ".odt", ".ods", ".odp"}:
        try:
            with zipfile.ZipFile(path) as archive:
                names = archive.namelist()
                if len(names) > 10000:
                    raise UploadValidationError(
                        "invalid_document_content",
                        "The document container contains too many entries.",
                        415,
                    )
                name_set = set(names)
                expected_prefix = {
                    ".docx": "word/",
                    ".xlsx": "xl/",
                    ".pptx": "ppt/",
                }.get(extension)
                if expected_prefix and (
                    "[Content_Types].xml" not in name_set
                    or not any(name.startswith(expected_prefix) for name in names)
                ):
                    raise UploadValidationError(
                        "invalid_document_content",
                        "The file does not match its Office document extension.",
                        415,
                    )
                expected_mime = {
                    ".odt": "application/vnd.oasis.opendocument.text",
                    ".ods": "application/vnd.oasis.opendocument.spreadsheet",
                    ".odp": "application/vnd.oasis.opendocument.presentation",
                }.get(extension)
                if expected_mime:
                    mime_info = archive.getinfo("mimetype")
                    if mime_info.file_size > 256:
                        raise UploadValidationError(
                            "invalid_document_content",
                            "The OpenDocument MIME entry is invalid.",
                            415,
                        )
                    actual_mime = archive.read(mime_info).decode("ascii")
                    if actual_mime != expected_mime:
                        raise UploadValidationError(
                            "invalid_document_content",
                            "The file does not match its OpenDocument extension.",
                            415,
                        )
        except (KeyError, UnicodeDecodeError, zipfile.BadZipFile) as exc:
            raise UploadValidationError(
                "invalid_document_content",
                "The file does not contain a valid document container.",
                415,
            ) from exc


def _scan_uploaded_file(path, metadata):
    scanner = app.config.get("UPLOAD_SCANNER")
    if scanner is None:
        return
    if not callable(scanner):
        raise RuntimeError("UPLOAD_SCANNER must be callable")
    try:
        result = scanner(path, dict(metadata))
    except Exception as exc:
        raise RuntimeError("upload scanner failed") from exc
    if result is False or (isinstance(result, dict) and result.get("ok") is False):
        detail = result.get("detail") if isinstance(result, dict) else None
        raise UploadValidationError(
            "upload_scan_rejected", "The upload was rejected by the content scanner.", 422, detail
        )


def _upload_audit(event, *, req_id, session_id, upload_id, **extra):
    _LOGGER.info(
        event,
        extra={
            "request_id": req_id,
            "session_id": session_id,
            "upload_id": upload_id,
            **extra,
        },
    )


def _upload_rejection(
    code, message, status, req_id, *, session_id=None, upload_id=None, detail=None
):
    _upload_audit(
        "upload.rejected",
        req_id=req_id,
        session_id=session_id,
        upload_id=upload_id,
        error_code=code,
    )
    return _error_response(code, message, status, req_id, detail=detail)


def _function_tool_schemas():
    tools = []
    for schema in realtime_tool_schemas():
        if not isinstance(schema, dict):
            continue
        if schema.get("type") != "function":
            continue
        if not isinstance(schema.get("name"), str) or not schema["name"].strip():
            continue
        tools.append(schema)
    return tools


def _inbound_schema_error(schema_name, exc):
    if exc.validator in {"additionalProperties", "required"} and not exc.path:
        return (
            "invalid_request_body",
            "Request body does not match the endpoint contract.",
        )
    if schema_name == "session.create":
        if exc.path and exc.path[0] == "title":
            return (
                "invalid_session_title",
                f"title must be a string up to {MAX_SESSION_TITLE_LENGTH} characters.",
            )
        return "invalid_session_channel", "channel must be web or realtime."
    if schema_name == "realtime.message":
        return "invalid_realtime_message", "Realtime message payload is invalid."
    if schema_name == "responses.turn":
        if exc.path and exc.path[0] == "structured_output":
            return "invalid_structured_output", "structured_output must be an object."
        return (
            "invalid_message",
            f"message must be a non-empty string up to {MAX_MESSAGE_LENGTH} characters.",
        )
    if schema_name == "approval.decision":
        return "invalid_approval_decision", "approve must be a boolean."
    return "invalid_realtime_payload", f"Request body does not match the {schema_name} schema."


def _validated_json(req_id, *, schema_name=None, allowed=None, required=()):
    if not request.is_json:
        return None, _error_response(
            "invalid_content_type",
            "Expected Content-Type: application/json.",
            415,
            req_id,
        )
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return None, _error_response(
            "invalid_json",
            "Expected a JSON object.",
            400,
            req_id,
        )
    payload = dict(payload)
    payload.pop("version", None)
    if schema_name:
        try:
            validate_inbound_payload(schema_name, payload)
        except RealtimePayloadValidationError as exc:
            code, message = _inbound_schema_error(schema_name, exc)
            return None, _error_response(
                code,
                message,
                400,
                req_id,
                detail=exc.detail,
            )
        return payload, None
    allowed = set(allowed or ())
    extras = sorted(set(payload) - set(allowed))
    missing = sorted(set(required) - set(payload))
    if extras or missing:
        detail = {
            "unexpected_fields": extras,
            "missing_fields": missing,
        }
        return None, _error_response(
            "invalid_request_body",
            "Request body does not match the endpoint contract.",
            400,
            req_id,
            detail=json.dumps(detail),
        )
    return payload, None


def _validated_limit(req_id, default=20, maximum=100):
    extras = sorted(set(request.args) - {"limit"})
    if extras:
        return None, _error_response(
            "invalid_query",
            "Unexpected query parameters.",
            400,
            req_id,
            detail=", ".join(extras),
        )
    raw = request.args.get("limit", str(default))
    try:
        limit = int(raw)
    except ValueError:
        limit = 0
    if limit < 1 or limit > maximum:
        return None, _error_response(
            "invalid_limit",
            f"limit must be between 1 and {maximum}.",
            400,
            req_id,
        )
    return limit, None


def _session_detail_with_artifacts(sid):
    detail = chatlog.session_detail(sid)
    if not detail:
        return None
    artifact_ids = detail.pop("artifact_ids", [])
    detail["artifacts"] = []
    for artifact_id in artifact_ids:
        artifact = docops.resolve_export_artifact(artifact_id)
        if artifact.get("status") == "ready":
            detail["artifacts"].append(artifact)
    return detail


def _validate_and_link_artifacts(sid, artifact_ids, artifact_hashes):
    verified = []
    for artifact_id in artifact_ids or ():
        artifact = docops.resolve_export_artifact(artifact_id)
        if artifact.get("status") != "ready" or artifact.get("sha256") != artifact_hashes.get(
            artifact_id
        ):
            raise RuntimeError("A persisted tool artifact failed integrity validation")
        if not chatlog.link_session_artifact(sid, artifact_id):
            raise RuntimeError("A tool artifact could not be linked to the session")
        verified.append(artifact)
    return verified


def _result_with_linked_artifacts(sid, result):
    public_result = redact_server_paths(result)
    artifact_ids = []
    artifact_hashes = {}
    candidate = result.get("artifact") if isinstance(result, dict) else None
    if isinstance(candidate, dict) and candidate.get("status") == "ready":
        artifact_id = str(candidate.get("artifact_id") or "")
        artifact = docops.resolve_export_artifact(artifact_id)
        if artifact.get("status") != "ready":
            raise RuntimeError("The tool produced an invalid artifact")
        if not chatlog.link_session_artifact(sid, artifact_id):
            raise RuntimeError("The tool artifact could not be linked to the session")
        artifact_ids.append(artifact_id)
        artifact_hashes[artifact_id] = artifact["sha256"]
    return public_result, artifact_ids, artifact_hashes


def _execute_durable_tool(
    session, *, execution_key, approval_id, name, arguments, approval_granted
):
    started_at = perf_counter()
    log_fields = {
        "approval_granted": approval_granted,
        "tool_call_id": execution_key,
        "tool_name": name,
    }
    _LOGGER.info("tool.execution.started", extra=log_fields)
    try:
        reservation = chatlog.reserve_tool_execution(
            session["id"],
            execution_key,
            name,
            arguments,
            approval_id=approval_id,
        )
    except Exception:
        _LOGGER.exception(
            "tool.execution.failed",
            extra={
                **log_fields,
                "duration_ms": round((perf_counter() - started_at) * 1000, 3),
            },
        )
        raise
    state = reservation.get("state")
    if state == "completed":
        _validate_and_link_artifacts(
            session["id"],
            reservation.get("artifact_ids") or [],
            reservation.get("artifact_hashes") or {},
        )
        _LOGGER.info(
            "tool.execution.replayed",
            extra={
                **log_fields,
                "duration_ms": round((perf_counter() - started_at) * 1000, 3),
            },
        )
        return (
            reservation["result"],
            list(reservation.get("artifact_ids") or []),
            dict(reservation.get("artifact_hashes") or {}),
        )
    if state != "reserved":
        _LOGGER.error(
            "tool.execution.failed",
            extra={
                **log_fields,
                "duration_ms": round((perf_counter() - started_at) * 1000, 3),
                "failure_reason": "outcome_unknown",
            },
        )
        raise DurableExecutionOutcomeUnknown(
            "A prior tool execution did not durably record a safe outcome"
        )
    token = reservation["execution_token"]
    try:
        result = skills.dispatch(
            name,
            arguments,
            approval_granted=approval_granted,
        )
        public_result, artifact_ids, artifact_hashes = _result_with_linked_artifacts(
            session["id"], result
        )
        if not chatlog.complete_tool_execution(
            session["id"],
            execution_key,
            token,
            public_result,
            artifact_ids,
            artifact_hashes,
        ):
            raise DurableExecutionOutcomeUnknown(
                "The tool outcome could not be committed exactly once"
            )
        _LOGGER.info(
            "tool.execution.completed",
            extra={
                **log_fields,
                "duration_ms": round((perf_counter() - started_at) * 1000, 3),
            },
        )
        return public_result, artifact_ids, artifact_hashes
    except DurableExecutionOutcomeUnknown:
        chatlog.mark_tool_execution_unknown(session["id"], execution_key, token)
        _LOGGER.exception(
            "tool.execution.failed",
            extra={
                **log_fields,
                "duration_ms": round((perf_counter() - started_at) * 1000, 3),
            },
        )
        raise
    except Exception as exc:
        chatlog.mark_tool_execution_unknown(session["id"], execution_key, token)
        _LOGGER.exception(
            "tool.execution.failed",
            extra={
                **log_fields,
                "duration_ms": round((perf_counter() - started_at) * 1000, 3),
            },
        )
        raise DurableExecutionOutcomeUnknown(
            "The tool may have executed, but its outcome was not durably recorded"
        ) from exc


def _approval_idempotency_prefix(session_id, approval_id):
    digest = hashlib.sha256(f"{session_id}\0{approval_id}".encode("utf-8")).hexdigest()
    return f"pj-approval-{digest[:32]}"


def _validated_session(session_id, req_id):
    if not SESSION_ID_PATTERN.fullmatch(session_id):
        return None, _error_response(
            "session_not_found",
            "Session was not found.",
            404,
            req_id,
        )
    session = chatlog.get_session(session_id)
    if not session:
        return None, _error_response(
            "session_not_found",
            "Session was not found.",
            404,
            req_id,
        )
    return session, None


def _validated_structured_output(value, req_id):
    if value is None:
        return None, None
    if not isinstance(value, dict) or set(value) - {"name", "schema", "strict"}:
        return None, _error_response(
            "invalid_structured_output",
            "structured_output must contain only name, schema, and strict.",
            400,
            req_id,
        )
    name = value.get("name")
    schema = value.get("schema")
    strict = value.get("strict", True)
    if not isinstance(name, str) or not SCHEMA_NAME_PATTERN.fullmatch(name):
        return None, _error_response(
            "invalid_structured_output",
            "structured_output.name is invalid.",
            400,
            req_id,
        )
    if not isinstance(schema, dict) or not isinstance(strict, bool):
        return None, _error_response(
            "invalid_structured_output",
            "structured_output.schema must be an object and strict must be boolean.",
            400,
            req_id,
        )
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        return None, _error_response(
            "invalid_structured_output_schema",
            "structured_output.schema is not a valid JSON Schema.",
            400,
            req_id,
            detail=exc.message,
        )
    return {
        "type": "json_schema",
        "name": name,
        "schema": schema,
        "strict": strict,
    }, None


def _sse(event):
    validate_outbound_event(event)
    message = {**event, "version": PROTOCOL_VERSION}
    event_type = message.get("type", "message")
    return f"event: {event_type}\ndata: {json.dumps(message, default=str)}\n\n"


@app.route("/", methods=["GET"])
def web_client():
    """Serve the PJ web client."""
    if not app.config["LOCAL_WEB_OWNER_SESSION_ENABLED"] or not _is_loopback_request():
        return _error_response(
            "local_web_only",
            "The built-in web client is available only from the local host.",
            403,
            _request_id(),
        )
    session.clear()
    session["local_owner"] = True
    response = send_from_directory(BASE_DIR, "webrtc_client.html")
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/health", methods=["GET"])
def health():
    req_id = _request_id()
    return _json_response(
        {
            "ok": True,
            "service": "pj-realtime-server",
            "contract_version": CONTRACT_VERSION,
            "protocol_version": PROTOCOL_VERSION,
            "tool_count": len(_function_tool_schemas()),
            "prompt_perfecting_version": promptops.PROMPT_PERFECTING_VERSION,
            "tool_policy_sha256": _tool_policy_sha256(),
            "bridge_auth_enabled": bool((os.getenv("PJ_TOOL_BRIDGE_TOKEN") or "").strip()),
            "endpoints": [
                "/session",
                "/token",
                "/execute-tool",
                "/tool-schemas",
                "/responses/capabilities",
                "/responses/prompt-perfect",
                "/responses/sessions",
                "/responses/sessions/search",
                "/responses/sessions/<id>/resume",
                "/responses/sessions/<id>/turns",
                "/responses/sessions/<id>/realtime-messages",
                "/responses/sessions/<id>/approvals/<id>",
                "/responses/artifacts/<artifact-id>",
                "/upload/files",
                "/upload/folder",
                "/health",
            ],
        },
        status=200,
        req_id=req_id,
    )


@app.route("/tool-schemas", methods=["GET"])
def tool_schemas():
    req_id = _request_id()
    auth_error = _check_bridge_auth(req_id)
    if auth_error:
        return auth_error
    tools = _function_tool_schemas()
    cfg = load_config()
    instructions = cfg["instructions"]
    tool_manifest = json.dumps(
        tools, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()
    return _json_response(
        {
            "ok": True,
            "contract_version": CONTRACT_VERSION,
            "count": len(tools),
            "tools": tools,
            "tool_manifest_sha256": hashlib.sha256(tool_manifest).hexdigest(),
            "instructions": instructions,
            "instructions_sha256": hashlib.sha256(instructions.encode()).hexdigest(),
            "instruction_files": cfg["instruction_files"],
            "prompt_perfecting_version": promptops.PROMPT_PERFECTING_VERSION,
            "tool_policy_sha256": _tool_policy_sha256(),
        },
        status=200,
        req_id=req_id,
    )


@app.route("/responses/capabilities", methods=["GET"])
def responses_capabilities():
    req_id = _request_id()
    auth_error = _check_bridge_auth(req_id)
    if auth_error:
        return auth_error
    return _json_response(
        {"ok": True, "capabilities": capability_manifest(load_config())},
        req_id=req_id,
    )


@app.route("/responses/prompt-perfect", methods=["POST"])
def prompt_perfect():
    req_id = _request_id()
    auth_error = _check_bridge_auth(req_id, required=True)
    if auth_error:
        return auth_error
    payload, error = _validated_json(
        req_id,
        allowed={"prompt", "surface"},
        required={"prompt", "surface"},
    )
    if error:
        return error
    prompt = payload["prompt"]
    surface = payload["surface"]
    if (
        not isinstance(prompt, str)
        or not prompt.strip()
        or len(prompt) > MAX_MESSAGE_LENGTH
        or surface not in {"full_power", "full_power_voice"}
    ):
        return _error_response(
            "invalid_prompt_perfecting_request",
            "prompt and surface are invalid.",
            400,
            req_id,
        )
    if "OPENAI_API_KEY" not in os.environ:
        return _error_response(
            "missing_openai_api_key",
            "OPENAI_API_KEY is not set.",
            500,
            req_id,
        )
    try:
        result = promptops.perfect_prompt(
            OPENAI_CLIENT_FACTORY(),
            load_config(),
            prompt,
            surface=surface,
        )
    except promptops.PromptPerfectingError as exc:
        return _error_response(exc.code, str(exc), 422, req_id)
    return _json_response(
        {"ok": True, "prompt": promptops.public_result(result)},
        req_id=req_id,
    )


@app.route("/responses/sessions", methods=["POST"])
def create_responses_session():
    req_id = _request_id()
    auth_error = _check_bridge_auth(req_id)
    if auth_error:
        return auth_error
    payload, error = _validated_json(req_id, schema_name="session.create")
    if error:
        return error
    title = payload.get("title", "")
    if not isinstance(title, str) or len(title.strip()) > MAX_SESSION_TITLE_LENGTH:
        return _error_response(
            "invalid_session_title",
            f"title must be a string up to {MAX_SESSION_TITLE_LENGTH} characters.",
            400,
            req_id,
        )
    channel = payload.get("channel", "web")
    if channel not in {"web", "realtime"}:
        return _error_response(
            "invalid_session_channel",
            "channel must be web or realtime.",
            400,
            req_id,
        )
    session = chatlog.new_session(title.strip(), channel=channel)
    bind_log_context(session_id=session["id"])
    session.pop("last_response_id", None)
    return _json_response(
        {"ok": True, "session": session},
        201,
        req_id,
        outbound_schema="session.response",
    )


@app.route("/responses/sessions", methods=["GET"])
def list_responses_sessions():
    req_id = _request_id()
    auth_error = _check_bridge_auth(req_id)
    if auth_error:
        return auth_error
    limit, error = _validated_limit(req_id)
    if error:
        return error
    sessions = chatlog.list_sessions(limit)
    return _json_response(
        {"ok": True, "count": len(sessions), "sessions": sessions},
        req_id=req_id,
    )


@app.route("/responses/sessions/search", methods=["GET"])
def search_responses_sessions():
    req_id = _request_id()
    auth_error = _check_bridge_auth(req_id)
    if auth_error:
        return auth_error
    extras = sorted(set(request.args) - {"q", "limit"})
    query = request.args.get("q", "")
    try:
        limit = int(request.args.get("limit", "20"))
    except ValueError:
        limit = 0
    if extras or not query.strip() or len(query) > 200 or not 1 <= limit <= 100:
        return _error_response(
            "invalid_query",
            "q is required (maximum 200 characters) and limit must be 1-100.",
            400,
            req_id,
            detail=", ".join(extras) if extras else None,
        )
    matches = chatlog.search(query.strip(), limit)
    return _json_response(
        {"ok": True, "count": len(matches), "matches": matches},
        req_id=req_id,
    )


@app.route("/responses/sessions/<session_id>/resume", methods=["POST"])
def resume_responses_session(session_id):
    req_id = _request_id()
    auth_error = _check_bridge_auth(req_id)
    if auth_error:
        return auth_error
    payload, error = _validated_json(req_id, schema_name="session.resume")
    if error:
        return error
    session, error = _validated_session(session_id, req_id)
    if error:
        return error
    return _json_response(
        {"ok": True, "session": _session_detail_with_artifacts(session["id"])},
        req_id=req_id,
        outbound_schema="session.response",
    )


@app.route("/responses/sessions/<session_id>/artifacts", methods=["GET"])
def list_responses_session_artifacts(session_id):
    req_id = _request_id()
    auth_error = _check_bridge_auth(req_id, required=True)
    if auth_error:
        return auth_error
    if request.args:
        return _error_response("invalid_query", "Unexpected query parameters.", 400, req_id)
    session, error = _validated_session(session_id, req_id)
    if error:
        return error
    detail = _session_detail_with_artifacts(session["id"])
    artifacts = detail["artifacts"]
    return _json_response(
        {"ok": True, "count": len(artifacts), "artifacts": artifacts},
        req_id=req_id,
    )


def _handle_document_upload(*, folder_mode):
    req_id = _request_id()
    auth_error = _check_bridge_auth(req_id, required=True)
    if auth_error:
        audit_session_id = request.headers.get("x-pj-session-id")
        if not SESSION_ID_PATTERN.fullmatch(str(audit_session_id or "")):
            audit_session_id = None
        _upload_audit(
            "upload.rejected",
            req_id=req_id,
            session_id=audit_session_id,
            upload_id=None,
            error_code="upload_auth_failed",
        )
        return auth_error

    max_file_size = int(app.config["MAX_UPLOAD_FILE_BYTES"])
    max_total_size = int(app.config["MAX_UPLOAD_TOTAL_BYTES"])
    if max_file_size <= 0 or max_total_size <= 0:
        return _upload_rejection(
            "invalid_upload_configuration",
            "Upload size limits are not configured correctly.",
            500,
            req_id,
        )
    if (
        request.content_length is not None
        and request.content_length > max_total_size + UPLOAD_MULTIPART_OVERHEAD_BYTES
    ):
        return _upload_rejection(
            "upload_too_large",
            "The total upload exceeds the configured size limit.",
            413,
            req_id,
            session_id=request.headers.get("x-pj-session-id"),
        )

    session_id = str(request.form.get("session_id") or "").strip()
    if not session_id:
        session_id = f"upload_{secrets.token_hex(12)}"
    if not SESSION_ID_PATTERN.fullmatch(session_id):
        return _upload_rejection(
            "invalid_upload_session",
            "session_id must contain 8-128 letters, numbers, underscores, or hyphens.",
            400,
            req_id,
        )
    bind_log_context(session_id=session_id)

    files = request.files.getlist("files")
    paths = request.form.getlist("paths")
    if not files:
        return _upload_rejection(
            "missing_upload_files",
            "At least one file is required.",
            400,
            req_id,
            session_id=session_id,
        )
    if folder_mode and len(paths) != len(files):
        return _upload_rejection(
            "missing_folder_paths",
            "Folder uploads require one relative path for every file.",
            400,
            req_id,
            session_id=session_id,
        )
    if paths and len(paths) != len(files):
        return _upload_rejection(
            "invalid_upload_paths",
            "The number of upload paths must match the number of files.",
            400,
            req_id,
            session_id=session_id,
        )

    upload_id = f"UPL-{secrets.token_hex(16)}"
    upload_root = document_uploads.UPLOADS_DIR
    staging_dir = upload_root / ".staging" / upload_id
    final_dir = upload_root / session_id / upload_id
    prepared = []
    seen_paths = set()
    skipped = []
    # Folder uploads and multi-file batches skip individually unacceptable
    # files and continue; single-file document uploads keep returning the
    # file's own rejection directly.
    multi_file = folder_mode or len(files) > 1
    try:
        for index, storage in enumerate(files):
            raw_path = paths[index] if paths else storage.filename
            relative_path = _sanitize_upload_path(raw_path, folder_mode=folder_mode)
            if relative_path in seen_paths:
                raise UploadValidationError(
                    "duplicate_upload_path",
                    "Each file in an upload must have a unique path.",
                    400,
                    relative_path,
                )
            seen_paths.add(relative_path)
            try:
                mime = _validate_upload_type(relative_path, storage.mimetype)
            except UploadValidationError as exc:
                if not multi_file:
                    raise
                skipped.append({"path": relative_path, "code": exc.code, "message": exc.message})
                continue
            prepared.append(
                {
                    "storage": storage,
                    "relative_path": relative_path,
                    "name": PurePosixPath(relative_path).name,
                    "mime": mime,
                }
            )

        upload_root.mkdir(parents=True, exist_ok=True)
        staging_dir.mkdir(parents=True)
        total_size = 0
        staged_items = []
        for item in prepared:
            destination = staging_dir.joinpath(*PurePosixPath(item["relative_path"]).parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            digest = hashlib.sha256()
            file_size = 0
            try:
                with destination.open("xb") as target:
                    while True:
                        chunk = item["storage"].stream.read(UPLOAD_CHUNK_BYTES)
                        if not chunk:
                            break
                        file_size += len(chunk)
                        total_size += len(chunk)
                        if file_size > max_file_size:
                            raise UploadValidationError(
                                "file_too_large",
                                f"'{item['name']}' exceeds the per-file size limit.",
                                413,
                            )
                        if total_size > max_total_size:
                            raise UploadValidationError(
                                "upload_too_large",
                                "The total upload exceeds the configured size limit.",
                                413,
                            )
                        digest.update(chunk)
                        target.write(chunk)
                    target.flush()
                    os.fsync(target.fileno())
                destination.chmod(0o600)
                if file_size == 0:
                    raise UploadValidationError(
                        "empty_upload_file", f"'{item['name']}' is empty.", 400
                    )
                with destination.open("rb") as handle:
                    head = handle.read(4096)
                classification = upload_formats.classify(item["relative_path"], head, file_size)
                _validate_upload_signature(
                    destination,
                    Path(item["relative_path"]).suffix.lower(),
                    classification,
                )
            except UploadValidationError as exc:
                # The whole-batch size ceiling stays fatal; anything else only
                # drops this file when the batch has other files to keep.
                if not multi_file or exc.code == "upload_too_large":
                    raise
                destination.unlink(missing_ok=True)
                total_size -= file_size
                skipped.append(
                    {"path": item["relative_path"], "code": exc.code, "message": exc.message}
                )
                continue
            staged_item = {
                "relative_path": item["relative_path"],
                "name": item["name"],
                "mime": item["mime"],
                "size": file_size,
                "sha256": digest.hexdigest(),
                "classification": classification.public(),
            }
            _scan_uploaded_file(destination, staged_item)
            staged_items.append(staged_item)

        if not staged_items:
            raise UploadValidationError(
                "no_acceptable_files",
                "No files in the upload were acceptable.",
                415,
            )

        final_dir.parent.mkdir(parents=True, exist_ok=True)
        if final_dir.exists():
            raise RuntimeError("generated upload destination already exists")
        staging_dir.replace(final_dir)
        indexed_files = []
        for item in staged_items:
            saved_path = (
                PurePosixPath("uploads")
                / session_id
                / upload_id
                / PurePosixPath(item["relative_path"])
            ).as_posix()
            indexed_files.append(
                {
                    **item,
                    "saved_path": saved_path,
                    "path": final_dir.joinpath(*PurePosixPath(item["relative_path"]).parts),
                }
            )
        registered = document_uploads.register_uploaded_documents(
            upload_id, session_id, indexed_files
        )
        classification_by_path = {
            item["relative_path"]: item["classification"] for item in staged_items
        }
        for document in registered["documents"]:
            relative = document["saved_path"].split(f"{upload_id}/", 1)[-1]
            if relative in classification_by_path:
                document["classification"] = classification_by_path[relative]
    except UploadValidationError as exc:
        shutil.rmtree(staging_dir, ignore_errors=True)
        shutil.rmtree(final_dir, ignore_errors=True)
        return _upload_rejection(
            exc.code,
            exc.message,
            exc.status,
            req_id,
            session_id=session_id,
            upload_id=upload_id,
            detail=exc.detail,
        )
    except (OSError, RuntimeError, ValueError, sqlite3.Error) as exc:
        shutil.rmtree(staging_dir, ignore_errors=True)
        shutil.rmtree(final_dir, ignore_errors=True)
        _LOGGER.exception(
            "upload.failed",
            extra={"session_id": session_id, "upload_id": upload_id},
        )
        return _error_response(
            "upload_failed",
            "The upload could not be persisted and registered.",
            500,
            req_id,
            detail=exc,
        )

    _upload_audit(
        "upload.accepted",
        req_id=req_id,
        session_id=session_id,
        upload_id=upload_id,
        file_count=registered["count"],
        skipped_count=len(skipped),
        total_size=total_size,
        mode="folder" if folder_mode else "files",
    )
    return _json_response(
        {
            "ok": True,
            "upload_id": upload_id,
            "session_id": session_id,
            "mode": "folder" if folder_mode else "files",
            "count": registered["count"],
            "total_size": total_size,
            "files": registered["documents"],
            "skipped": skipped,
        },
        status=201,
        req_id=req_id,
    )


@app.route("/upload/files", methods=["POST"])
def upload_files():
    return _handle_document_upload(folder_mode=False)


@app.route("/upload/folder", methods=["POST"])
def upload_folder():
    return _handle_document_upload(folder_mode=True)


@app.route(
    "/responses/sessions/<session_id>/realtime-messages",
    methods=["POST"],
)
def record_realtime_message(session_id):
    req_id = _request_id()
    auth_error = _check_bridge_auth(req_id, required=True)
    if auth_error:
        return auth_error
    session, error = _validated_session(session_id, req_id)
    if error:
        return error
    payload, error = _validated_json(req_id, schema_name="realtime.message")
    if error:
        return error
    metadata = payload.get("metadata") or {}
    if not isinstance(metadata, dict):
        return _error_response(
            "invalid_realtime_message",
            "metadata must be an object.",
            400,
            req_id,
        )
    metadata_allowed = {
        "prompt_perfecting_version",
        "refined_prompt",
        "refined_sha256",
        "changed",
        "refined_prompt_truncated",
    }
    if set(metadata) - metadata_allowed:
        return _error_response(
            "invalid_realtime_message",
            "metadata contains unsupported fields.",
            400,
            req_id,
        )
    for key in ("changed", "refined_prompt_truncated"):
        if key in metadata and not isinstance(metadata[key], bool):
            return _error_response(
                "invalid_realtime_message",
                f"metadata.{key} must be a boolean.",
                400,
                req_id,
            )
    if "prompt_perfecting_version" in metadata and (
        not isinstance(metadata["prompt_perfecting_version"], str)
        or len(metadata["prompt_perfecting_version"]) > 100
    ):
        return _error_response(
            "invalid_realtime_message",
            "metadata.prompt_perfecting_version is invalid.",
            400,
            req_id,
        )
    if (
        (
            metadata.get("refined_prompt") is not None
            and not isinstance(metadata["refined_prompt"], str)
        )
        or len(str(metadata.get("refined_prompt") or "")) > 4000
        or (
            metadata.get("refined_sha256") is not None
            and not re.fullmatch(r"[a-f0-9]{64}", str(metadata["refined_sha256"]))
        )
    ):
        return _error_response(
            "invalid_realtime_message",
            "prompt metadata is invalid or exceeds its persistence limit.",
            400,
            req_id,
        )
    try:
        message = chatlog.record_external_turn(
            session,
            payload["role"],
            payload["content"],
            external_id=payload["external_id"],
            source=payload["source"],
            response_id=payload.get("response_id"),
            status=payload["status"],
            playback_ms=payload.get("playback_ms"),
            metadata=metadata,
        )
    except ValueError as exc:
        return _error_response("invalid_realtime_message", str(exc), 400, req_id)
    return _json_response(
        {"ok": True, "message": message},
        req_id=req_id,
        outbound_schema="realtime.message.response",
    )


@app.route("/responses/artifacts/<artifact_id>", methods=["GET"])
def download_responses_artifact(artifact_id):
    req_id = _request_id()
    auth_error = _check_bridge_auth(req_id, required=True)
    if auth_error:
        return auth_error
    if request.args or not ARTIFACT_ID_PATTERN.fullmatch(artifact_id):
        return _error_response("artifact_not_found", "Artifact was not found.", 404, req_id)
    artifact, snapshot = docops.open_export_artifact_snapshot(artifact_id)
    if artifact.get("status") == "not_found":
        return _error_response("artifact_not_found", "Artifact was not found.", 404, req_id)
    if artifact.get("status") != "ready":
        return _error_response(
            "artifact_unavailable",
            "Artifact failed its integrity check.",
            409,
            req_id,
            detail=artifact.get("error"),
        )
    response = send_file(
        snapshot,
        mimetype=artifact["mime_type"].split(";", 1)[0],
        as_attachment=True,
        download_name=artifact["filename"],
        conditional=False,
        etag=False,
        max_age=0,
    )
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["ETag"] = f'"sha256-{artifact["sha256"]}"'
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["x-request-id"] = req_id
    response.headers["x-pj-contract-version"] = CONTRACT_VERSION
    response.headers["x-pj-protocol-version"] = str(PROTOCOL_VERSION)
    response.call_on_close(snapshot.close)
    return response


@app.route("/responses/sessions/<session_id>/turns", methods=["POST"])
def stream_responses_turn(session_id):
    req_id = _request_id()
    auth_error = _check_bridge_auth(req_id)
    if auth_error:
        return auth_error
    session, error = _validated_session(session_id, req_id)
    if error:
        return error
    payload, error = _validated_json(req_id, schema_name="responses.turn")
    if error:
        return error
    message = payload["message"]
    if not isinstance(message, str) or not message.strip() or len(message) > MAX_MESSAGE_LENGTH:
        return _error_response(
            "invalid_message",
            f"message must be a non-empty string up to {MAX_MESSAGE_LENGTH} characters.",
            400,
            req_id,
        )
    text_format, error = _validated_structured_output(payload.get("structured_output"), req_id)
    if error:
        return error
    if "OPENAI_API_KEY" not in os.environ:
        return _error_response(
            "missing_openai_api_key",
            "OPENAI_API_KEY is not set.",
            500,
            req_id,
        )

    message = message.strip()
    if chatlog.list_pending_approvals(session["id"]):
        return _error_response(
            "session_approval_pending",
            "Resolve the pending owner approval before starting another turn.",
            409,
            req_id,
        )
    turn_token = chatlog.claim_session_turn(session["id"])
    if not turn_token:
        return _error_response(
            "session_turn_in_progress",
            "Another turn is already in progress for this session.",
            409,
            req_id,
        )
    chatlog.record_turn(session, "user", message)
    try:
        perfected = promptops.perfect_prompt(
            OPENAI_CLIENT_FACTORY(),
            load_config(),
            message,
            surface="full_power",
        )
    except promptops.PromptPerfectingError as exc:
        chatlog.release_session_turn(session["id"], turn_token)
        return _error_response(exc.code, str(exc), 422, req_id)
    model_message = perfected["refined_prompt"]
    input_value = model_message
    if not session.get("last_response_id"):
        prior_history = chatlog.history(session["id"], 21)[:-1]
        if prior_history:
            context_lines = [
                ("PJ" if item["role"] == "assistant" else "User") + ": " + item["content"]
                for item in prior_history
                if item["role"] in {"user", "assistant"} and item["content"]
            ]
            if context_lines:
                input_value = (
                    "Recent authoritative conversation transcript:\n"
                    + "\n".join(context_lines)
                    + "\n\nCurrent refined request:\n"
                    + model_message
                )
    prompt_event = {
        "type": "prompt.perfected",
        **promptops.public_result(perfected),
    }

    response = Response(
        stream_with_context(
            _stream_session_response(
                session,
                turn_token,
                input_value,
                previous_response_id=session.get("last_response_id"),
                text_format=text_format,
                req_id=req_id,
                prelude=(prompt_event,),
                required_deliverable_format=requested_deliverable_format(message),
            )
        ),
        status=200,
        mimetype="text/event-stream",
    )
    response.headers["Cache-Control"] = "no-cache"
    response.headers["X-Accel-Buffering"] = "no"
    response.headers["x-request-id"] = req_id
    response.headers["x-pj-contract-version"] = CONTRACT_VERSION
    response.headers["x-pj-protocol-version"] = str(PROTOCOL_VERSION)
    return response


def _stream_session_response(
    session,
    turn_token,
    input_value,
    *,
    previous_response_id,
    text_format,
    req_id,
    required_deliverable_format=None,
    ready_artifact_ids=(),
    prelude=(),
    approval_execution=None,
):
    set_log_context(request_id=req_id, session_id=session["id"])
    try:
        deliverable_format = required_deliverable_format or requested_deliverable_format(
            input_value
        )
        yield _sse(
            {
                "type": "session",
                "session_id": session["id"],
                "request_id": req_id,
            }
        )
        for event in prelude:
            yield _sse(event)
        idempotency_key_prefix = None
        tool_executor = None
        response_checkpoint = None
        if approval_execution:
            approval_id = approval_execution["approval_id"]
            idempotency_key_prefix = _approval_idempotency_prefix(session["id"], approval_id)

            def checkpoint(operation_key, provider_response_id):
                if not chatlog.record_provider_response_checkpoint(
                    session["id"], operation_key, provider_response_id
                ):
                    raise DurableExecutionOutcomeUnknown(
                        "The provider returned conflicting response IDs for one idempotency key"
                    )
                return True

            def execute_follow_on(call, approved):
                if approved:
                    raise DurableExecutionOutcomeUnknown(
                        "A follow-on approved tool lacked a separate approval"
                    )
                call_id = str(call.get("call_id") or "").strip()
                if not call_id:
                    raise DurableExecutionOutcomeUnknown(
                        "A follow-on tool call lacked a durable provider ID"
                    )
                result, _, _ = _execute_durable_tool(
                    session,
                    execution_key=f"provider:{approval_id}:{call_id}",
                    approval_id=approval_id,
                    name=call["name"],
                    arguments=call["arguments"],
                    approval_granted=False,
                )
                return result

            response_checkpoint = checkpoint
            tool_executor = execute_follow_on
        orchestrator = ResponsesOrchestrator(
            OPENAI_CLIENT_FACTORY(),
            load_config(),
            tool_executor=tool_executor,
            response_checkpoint=response_checkpoint,
        )
        for event in orchestrator.stream_turn(
            input_value,
            previous_response_id=previous_response_id,
            text_format=text_format,
            required_deliverable_format=deliverable_format,
            ready_artifact_ids=ready_artifact_ids,
            idempotency_key_prefix=idempotency_key_prefix,
        ):
            public_event = dict(event)
            response_id = public_event.pop("_response_id", None)
            provider_item_id = public_event.pop("_provider_item_id", None)
            artifact_ids = public_event.pop("_artifact_ids", [])
            if public_event["type"] == "artifact.ready":
                artifact_id = public_event.get("artifact_id", "")
                artifact = docops.resolve_export_artifact(artifact_id)
                if artifact.get("status") != "ready":
                    raise RuntimeError("Generated artifact failed server-side integrity validation")
                if not chatlog.link_session_artifact(session["id"], artifact_id):
                    raise RuntimeError("Artifact could not be linked to the session")
                public_event = {"type": "artifact.ready", **artifact}
            if public_event["type"] == "approval.required":
                if not response_id or not provider_item_id:
                    raise RuntimeError("Approval request did not include provider continuity")
                pending = chatlog.pause_session_turn_for_approval(
                    session,
                    turn_token,
                    approval_kind=public_event["approval_kind"],
                    provider_response_id=response_id,
                    provider_item_id=provider_item_id,
                    tool_name=public_event.get("name") or "",
                    server_label=public_event.get("server_label") or "",
                    arguments=public_event.get("arguments") or {},
                    text_format=text_format,
                    deliverable_format=deliverable_format,
                    artifact_ids=artifact_ids,
                    artifact_hashes={
                        artifact_id: docops.resolve_export_artifact(artifact_id).get("sha256", "")
                        for artifact_id in artifact_ids
                    },
                    completed_approval_id=(
                        approval_execution["approval_id"] if approval_execution else None
                    ),
                    completed_approval_decision=(
                        approval_execution["approve"] if approval_execution else None
                    ),
                )
                if not pending:
                    raise RuntimeError("Session turn lease expired before approval was stored")
                public_event.update(
                    {
                        "approval_id": pending["approval_id"],
                        "expires_at": pending["expires_at"],
                        "session_id": session["id"],
                    }
                )
                if approval_execution:
                    yield _sse(
                        {
                            "type": "approval.resolved",
                            "approval_id": approval_execution["approval_id"],
                            "approval_kind": approval_execution["approval_kind"],
                            "name": approval_execution["name"],
                            "approved": approval_execution["approved"],
                        }
                    )
                yield _sse(public_event)
                return
            if public_event["type"] == "artifact.ready":
                artifact = docops.resolve_export_artifact(public_event.get("artifact_id", ""))
                if artifact.get("status") != "ready":
                    raise RuntimeError("Document artifact failed integrity validation")
                if not chatlog.link_session_artifact(session["id"], artifact["artifact_id"]):
                    raise RuntimeError("Document artifact could not be linked to chat")
                public_event = {"type": "artifact.ready", **artifact}
            if public_event["type"] == "completion":
                stored = chatlog.finish_session_turn(
                    session,
                    turn_token,
                    public_event.get("text", ""),
                    response_id,
                    completed_approval_id=(
                        approval_execution["approval_id"] if approval_execution else None
                    ),
                    completed_approval_decision=(
                        approval_execution["approve"] if approval_execution else None
                    ),
                )
                if not stored:
                    raise RuntimeError("Session turn lease expired before completion")
                if approval_execution:
                    yield _sse(
                        {
                            "type": "approval.resolved",
                            "approval_id": approval_execution["approval_id"],
                            "approval_kind": approval_execution["approval_kind"],
                            "name": approval_execution["name"],
                            "approved": approval_execution["approved"],
                        }
                    )
                public_event["session_id"] = session["id"]
            yield _sse(public_event)
    except RealtimePayloadValidationError as exc:
        _LOGGER.error(
            "responses.turn.invalid_outbound_payload",
            extra={"error_code": "invalid_outbound_payload"},
        )
        yield _sse(
            {
                "type": "error",
                "error": {
                    "code": "invalid_outbound_payload",
                    "message": "Realtime stream event did not match the server contract.",
                    "request_id": req_id,
                    "detail": _trim_detail(exc.detail),
                },
            }
        )
    except DurableExecutionOutcomeUnknown as exc:
        _LOGGER.exception(
            "responses.turn.failed",
            extra={"error_code": "approval_execution_outcome_unknown"},
        )
        if approval_execution:
            chatlog.mark_pending_approval_execution_unknown(
                session["id"],
                approval_execution["approval_id"],
                approval_execution["approve"],
            )
        yield _sse(
            {
                "type": "error",
                "error": {
                    "code": "approval_execution_outcome_unknown",
                    "message": ("The action outcome is unknown and will not be replayed."),
                    "request_id": req_id,
                    "detail": _trim_detail(exc),
                },
            }
        )
    except Exception as exc:
        _LOGGER.exception(
            "responses.turn.failed",
            extra={"error_code": "responses_turn_failed"},
        )
        yield _sse(
            {
                "type": "error",
                "error": {
                    "code": "responses_turn_failed",
                    "message": "Responses turn failed.",
                    "request_id": req_id,
                    "detail": _trim_detail(exc),
                },
            }
        )
    finally:
        chatlog.release_session_turn(session["id"], turn_token)
        clear_log_context()


@app.route(
    "/responses/sessions/<session_id>/approvals/<approval_id>",
    methods=["POST"],
)
def resolve_responses_approval(session_id, approval_id):
    req_id = _request_id()
    auth_error = _check_bridge_auth(req_id)
    if auth_error:
        return auth_error
    session, error = _validated_session(session_id, req_id)
    if error:
        return error
    if not SESSION_ID_PATTERN.fullmatch(approval_id):
        return _error_response("approval_not_found", "Approval was not found.", 404, req_id)
    payload, error = _validated_json(req_id, schema_name="approval.decision")
    if error:
        return error
    approve = payload["approve"]
    if not isinstance(approve, bool):
        return _error_response(
            "invalid_approval_decision",
            "approve must be a boolean.",
            400,
            req_id,
        )
    if "OPENAI_API_KEY" not in os.environ:
        return _error_response(
            "missing_openai_api_key",
            "OPENAI_API_KEY is not set.",
            500,
            req_id,
        )
    pending = chatlog.get_pending_approval(session["id"], approval_id)
    if not pending:
        return _error_response("approval_not_found", "Approval was not found.", 404, req_id)
    turn_token = chatlog.claim_session_turn(session["id"], pending_approval_id=approval_id)
    if not turn_token:
        return _error_response(
            "session_turn_in_progress",
            "Another turn is already in progress for this session.",
            409,
            req_id,
        )
    pending = chatlog.begin_pending_approval_execution(session["id"], approval_id, approve)
    if not pending:
        chatlog.release_session_turn(session["id"], turn_token)
        return _error_response(
            "approval_already_resolved",
            "Approval was already resolved or expired.",
            409,
            req_id,
        )

    try:
        _validate_and_link_artifacts(
            session["id"],
            pending.get("artifact_ids") or [],
            pending.get("artifact_hashes") or {},
        )
    except Exception as exc:
        if not pending.get("execution_result_recorded"):
            chatlog.retry_pending_approval(session["id"], approval_id)
        chatlog.release_session_turn(session["id"], turn_token)
        return _error_response(
            "approval_artifact_validation_failed",
            "Approval artifacts could not be validated; retry is available.",
            409,
            req_id,
            detail=exc,
        )

    prelude = [
        {
            "type": "approval.executing",
            "approval_id": approval_id,
            "approval_kind": pending["approval_kind"],
            "name": pending["name"],
            "approved": approve,
        }
    ]
    ready_artifact_ids = list(pending.get("artifact_ids") or ())
    ready_artifact_hashes = dict(pending.get("artifact_hashes") or {})
    if pending["approval_kind"] == "mcp":
        continuation = [
            {
                "type": "mcp_approval_response",
                "approval_request_id": pending["provider_item_id"],
                "approve": approve,
            }
        ]
    else:
        if pending.get("execution_result_recorded"):
            public_result = pending["execution_result"]
            produced_artifact_ids = []
            produced_artifact_hashes = {}
        else:
            if approve:
                try:
                    (
                        public_result,
                        produced_artifact_ids,
                        produced_artifact_hashes,
                    ) = _execute_durable_tool(
                        session,
                        execution_key=f"approval:{approval_id}",
                        approval_id=approval_id,
                        name=pending["name"],
                        arguments=pending["arguments"],
                        approval_granted=True,
                    )
                except DurableExecutionOutcomeUnknown as exc:
                    chatlog.mark_pending_approval_execution_unknown(
                        session["id"], approval_id, approve
                    )
                    chatlog.release_session_turn(session["id"], turn_token)
                    return _error_response(
                        "approval_execution_outcome_unknown",
                        "The approved action outcome is unknown and will not be replayed.",
                        409,
                        req_id,
                        detail=exc,
                    )
            else:
                public_result = {"error": "The owner rejected this tool call."}
                produced_artifact_ids = []
                produced_artifact_hashes = {}
        for artifact_id in produced_artifact_ids:
            if artifact_id not in ready_artifact_ids:
                ready_artifact_ids.append(artifact_id)
                ready_artifact_hashes[artifact_id] = produced_artifact_hashes[artifact_id]
                artifact = docops.resolve_export_artifact(artifact_id)
                prelude.append({"type": "artifact.ready", **artifact})
        if not chatlog.store_pending_approval_execution(
            session["id"],
            approval_id,
            approve,
            public_result,
            ready_artifact_ids,
            ready_artifact_hashes,
        ):
            chatlog.release_session_turn(session["id"], turn_token)
            return _error_response(
                "approval_state_conflict",
                "Approval execution could not be persisted; the durable result can be retried.",
                409,
                req_id,
            )
        prelude.append(
            {
                "type": "tool.result",
                "call_id": pending["provider_item_id"],
                "name": pending["name"],
                "result": public_result,
            }
        )
        continuation = [
            {
                "type": "function_call_output",
                "call_id": pending["provider_item_id"],
                "output": json.dumps(public_result, default=str),
            }
        ]

    response = Response(
        stream_with_context(
            _stream_session_response(
                session,
                turn_token,
                continuation,
                previous_response_id=pending["provider_response_id"],
                text_format=pending["text_format"],
                req_id=req_id,
                required_deliverable_format=pending.get("deliverable_format"),
                ready_artifact_ids=ready_artifact_ids,
                prelude=prelude,
                approval_execution={
                    "approval_id": approval_id,
                    "approval_kind": pending["approval_kind"],
                    "name": pending["name"],
                    "approved": approve,
                    "approve": approve,
                },
            )
        ),
        status=200,
        mimetype="text/event-stream",
    )
    response.headers["Cache-Control"] = "no-cache"
    response.headers["X-Accel-Buffering"] = "no"
    response.headers["x-request-id"] = req_id
    response.headers["x-pj-contract-version"] = CONTRACT_VERSION
    response.headers["x-pj-protocol-version"] = str(PROTOCOL_VERSION)
    return response


@app.route("/session", methods=["POST"])
def webrtc_session():
    """Browser WebRTC signaling endpoint: exchanges SDP for an SDP answer."""
    req_id = _request_id()
    extras = sorted(set(request.args) - {"session_id", "voice_mode"})
    session_id = request.args.get("session_id", "")
    voice_mode = request.args.get("voice_mode", "fast")
    if (
        extras
        or voice_mode not in {"fast", "full_power"}
        or (
            session_id
            and (
                not SESSION_ID_PATTERN.fullmatch(session_id) or not chatlog.get_session(session_id)
            )
        )
    ):
        return _error_response(
            "invalid_realtime_session",
            "session_id or voice_mode is invalid.",
            400,
            req_id,
            detail=", ".join(extras) if extras else None,
        )
    if "OPENAI_API_KEY" not in os.environ:
        return _error_response(
            "missing_openai_api_key",
            "OPENAI_API_KEY is not set.",
            500,
            req_id,
        )

    content_type = request.content_type or ""
    if content_type and "application/sdp" not in content_type:
        return _error_response(
            "invalid_content_type",
            "Expected Content-Type: application/sdp for /session.",
            415,
            req_id,
            detail=content_type,
        )

    sdp_offer = (request.get_data(as_text=True) or "").strip()
    if not sdp_offer:
        return _error_response(
            "missing_sdp_offer",
            "Missing SDP offer body.",
            400,
            req_id,
        )

    session_cfg = realtime_session_config(
        "You are speaking with the user live over voice. If they speak in "
        "another language, respond in that same language unless asked to "
        "translate into a specific target language.",
        voice_mode=voice_mode,
    )

    try:
        resp = requests.post(
            "https://api.openai.com/v1/realtime/calls",
            headers={"Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}"},
            files={
                "sdp": (None, sdp_offer),
                "session": (None, json.dumps(session_cfg)),
            },
            timeout=35,
        )
    except requests.Timeout as exc:
        return _error_response(
            "openai_timeout",
            "OpenAI realtime signaling timed out.",
            504,
            req_id,
            detail=exc,
        )
    except requests.RequestException as exc:
        return _error_response(
            "openai_request_failed",
            "OpenAI realtime request failed.",
            502,
            req_id,
            detail=exc,
        )

    if resp.status_code >= 400:
        return _error_response(
            "openai_realtime_failed",
            f"Realtime signaling failed ({resp.status_code}).",
            resp.status_code,
            req_id,
            detail=resp.text,
        )

    sdp_response = Response(resp.text, status=resp.status_code, mimetype="application/sdp")
    sdp_response.headers["x-request-id"] = req_id
    sdp_response.headers["x-pj-contract-version"] = CONTRACT_VERSION
    sdp_response.headers["x-pj-protocol-version"] = str(PROTOCOL_VERSION)
    if session_id:
        sdp_response.headers["x-pj-session-id"] = session_id
    return sdp_response


@app.route("/token", methods=["POST"])
def mint_realtime_token():
    """Mint a realtime client secret for browser fallback signaling."""
    req_id = _request_id()
    if "OPENAI_API_KEY" not in os.environ:
        return _error_response(
            "missing_openai_api_key",
            "OPENAI_API_KEY is not set.",
            500,
            req_id,
        )

    payload = request.get_json(force=True, silent=True)
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        return _error_response(
            "invalid_json",
            "Expected JSON object for /token.",
            400,
            req_id,
        )
    payload = dict(payload)
    payload.pop("version", None)
    extras = sorted(set(payload) - {"session_id", "voice_mode"})
    session_id = payload.get("session_id", "")
    voice_mode = payload.get("voice_mode", "fast")
    if (
        extras
        or voice_mode not in {"fast", "full_power"}
        or (
            session_id
            and (
                not isinstance(session_id, str)
                or not SESSION_ID_PATTERN.fullmatch(session_id)
                or not chatlog.get_session(session_id)
            )
        )
    ):
        return _error_response(
            "invalid_realtime_session",
            "session_id or voice_mode is invalid.",
            400,
            req_id,
            detail=", ".join(extras) if extras else None,
        )

    session_cfg = realtime_session_config(
        "You are speaking with the user live over voice. If they speak in "
        "another language, respond in that same language unless asked to "
        "translate into a specific target language.",
        voice_mode=voice_mode,
    )

    try:
        resp = requests.post(
            "https://api.openai.com/v1/realtime/client_secrets",
            headers={
                "Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}",
                "Content-Type": "application/json",
            },
            json={"session": session_cfg},
            timeout=20,
        )
    except requests.Timeout as exc:
        return _error_response(
            "openai_timeout",
            "OpenAI client secret minting timed out.",
            504,
            req_id,
            detail=exc,
        )
    except requests.RequestException as exc:
        return _error_response(
            "openai_request_failed",
            "OpenAI client secret request failed.",
            502,
            req_id,
            detail=exc,
        )

    if resp.status_code >= 400:
        return _error_response(
            "openai_client_secret_failed",
            f"Client secret minting failed ({resp.status_code}).",
            resp.status_code,
            req_id,
            detail=resp.text,
        )

    try:
        raw = resp.json()
    except ValueError:
        return _error_response(
            "invalid_client_secret_payload",
            "OpenAI returned non-JSON client secret payload.",
            502,
            req_id,
            detail=resp.text,
        )

    value = (
        ((raw.get("client_secret") or {}).get("value")) if isinstance(raw, dict) else None
    ) or (raw.get("value") if isinstance(raw, dict) else None)
    if not value:
        return _error_response(
            "invalid_client_secret_payload",
            "OpenAI client secret payload did not include a token value.",
            502,
            req_id,
            detail=raw,
        )

    return _json_response(
        {
            "ok": True,
            "session_id": session_id or None,
            "client_secret": {
                "value": value,
                "expires_at": ((raw.get("client_secret") or {}).get("expires_at"))
                if isinstance(raw, dict)
                else None,
            },
            "tool_count": len(_function_tool_schemas()),
        },
        status=200,
        req_id=req_id,
    )


@app.route("/execute-tool", methods=["POST"])
def execute_tool():
    """Run a local skill on behalf of the browser client."""
    req_id = _request_id()
    auth_error = _check_bridge_auth(req_id)
    if auth_error:
        return auth_error
    payload = request.get_json(force=True, silent=True)
    if payload is None:
        return _error_response(
            "invalid_json",
            "Expected JSON body for /execute-tool.",
            400,
            req_id,
        )
    if not isinstance(payload, dict):
        return _error_response(
            "invalid_json",
            "Expected a JSON object for /execute-tool.",
            400,
            req_id,
        )
    payload = dict(payload)
    payload.pop("version", None)

    name = payload.get("name")
    if not isinstance(name, str) or not name.strip():
        return _error_response(
            "invalid_tool_name",
            "Tool name is required.",
            400,
            req_id,
        )

    session_id = str(payload.get("session_id") or "").strip()
    if session_id and not chatlog.get_session(session_id):
        return _error_response(
            "session_not_found",
            "The requested chat session was not found.",
            404,
            req_id,
        )
    if session_id:
        bind_log_context(session_id=session_id)

    arguments = payload.get("arguments") or {}
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments or "{}")
        except json.JSONDecodeError as exc:
            return _error_response(
                "invalid_tool_arguments",
                "Tool arguments must be valid JSON.",
                400,
                req_id,
                detail=exc,
            )
    elif not isinstance(arguments, dict):
        return _error_response(
            "invalid_tool_arguments",
            "Tool arguments must be an object.",
            400,
            req_id,
        )

    try:
        result = dispatch_realtime_function(name, arguments, session_id=session_id or None)
    except ValueError as exc:
        return _error_response(
            "tool_dispatch_error",
            str(exc),
            400,
            req_id,
        )
    except Exception as exc:
        return _error_response(
            "tool_execution_error",
            "Tool execution failed.",
            500,
            req_id,
            detail=exc,
        )

    artifact = result.get("artifact") if isinstance(result, dict) else None
    if session_id and isinstance(artifact, dict) and artifact.get("status") == "ready":
        if not chatlog.link_session_artifact(session_id, str(artifact.get("artifact_id") or "")):
            return _error_response(
                "artifact_link_failed",
                "The tool artifact could not be linked to the chat session.",
                500,
                req_id,
            )
    return _json_response(redact_server_paths(result), status=200, req_id=req_id)


@app.route("/webhook", methods=["POST"])
def sip_webhook():
    """Legacy local-only SIP handler; unsupported for public deployment."""
    req_id = _request_id()
    event = request.get_json(force=True, silent=True) or {}
    event_type = event.get("type")

    if event_type != "realtime.call.incoming":
        return _json_response({"ok": True, "ignored_event_type": event_type}, req_id=req_id)

    call_id = event.get("data", {}).get("call_id")
    if not call_id:
        return _error_response(
            "missing_call_id",
            "Webhook payload missing data.call_id.",
            400,
            req_id,
        )
    if "OPENAI_API_KEY" not in os.environ:
        return _error_response(
            "missing_openai_api_key",
            "OPENAI_API_KEY is not set.",
            500,
            req_id,
        )

    session_cfg = realtime_session_config(
        "You are answering an inbound phone call on PJ's line. Greet the "
        "caller briefly, identify yourself as PJ, and help them."
    )
    try:
        requests.post(
            f"https://api.openai.com/v1/realtime/calls/{call_id}/accept",
            headers={
                "Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}",
                "Content-Type": "application/json",
            },
            json=session_cfg,
            timeout=30,
        )
    except requests.RequestException as exc:
        return _error_response(
            "openai_accept_failed",
            "Failed to accept inbound realtime call.",
            502,
            req_id,
            detail=exc,
        )

    return _json_response({"ok": True}, status=200, req_id=req_id)


def run():
    if "OPENAI_API_KEY" not in os.environ:
        raise SystemExit("OPENAI_API_KEY not set - source ~/.env first")
    bind_host = os.getenv("PJ_REALTIME_BIND_HOST", "127.0.0.1")
    try:
        app.config["LOCAL_WEB_OWNER_SESSION_ENABLED"] = ipaddress.ip_address(bind_host).is_loopback
    except ValueError:
        app.config["LOCAL_WEB_OWNER_SESSION_ENABLED"] = False
    _LOGGER.info(
        "realtime.server.started",
        extra={"bind_host": bind_host, "port": 3001},
    )
    app.run(host=bind_host, port=3001)


if __name__ == "__main__":
    run()
