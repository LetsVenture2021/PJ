#!/usr/bin/env python3
"""
realtime_server.py — signaling/webhook server that connects PJ to voice.

Provides:
  POST /session    - browser WebRTC signaling (SDP offer in, SDP answer out)
  POST /execute-tool - executes local PJ tools for browser function calls
  POST /webhook    - SIP webhook for inbound phone calls
"""
import hashlib
import hmac
import ipaddress
import json
import os
import re
import secrets
from pathlib import Path
from uuid import uuid4

import requests
from flask import (
    Flask,
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

import chatlog
import docops
import promptops
import skills
from pj_contract import CONTRACT_VERSION
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

BASE_DIR = Path(__file__).resolve().parent
MAX_ERROR_DETAIL_LENGTH = 320
MAX_SESSION_TITLE_LENGTH = 120
MAX_MESSAGE_LENGTH = 20000
SESSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{8,128}$")
ARTIFACT_ID_PATTERN = re.compile(r"^ART-[a-f0-9]{32}$")
SCHEMA_NAME_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
ARTIFACT_ID_PATTERN = re.compile(r"^ART-[a-f0-9]{32}$")
OPENAI_CLIENT_FACTORY = OpenAI


class DurableExecutionOutcomeUnknown(RuntimeError):
    """Raised when replaying a tool or provider effect would be unsafe."""


app = Flask(__name__, static_folder=str(BASE_DIR / "assets"), static_url_path="/assets")
app.secret_key = (
    os.getenv("PJ_LOCAL_WEB_SESSION_SECRET") or secrets.token_hex(32)
)
app.config.update(
    LOCAL_WEB_OWNER_SESSION_ENABLED=(
        os.getenv("PJ_LOCAL_WEB_OWNER_SESSION_ENABLED") == "1"
    ),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_NAME="pj_local_web_session",
    SESSION_COOKIE_SAMESITE="Strict",
)
CORS(app)  # allow local browser origins when running on localhost


def _tool_policy_sha256():
    path = BASE_DIR / "tool_policy.json"
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def _request_id():
    return request.headers.get("x-pj-client-request-id") or str(uuid4())


def _trim_detail(detail):
    if detail is None:
        return None
    compact = sanitize_text_urls(
        " ".join(str(redact_server_paths(str(detail))).split()).strip()
    )
    if not compact:
        return None
    if len(compact) <= MAX_ERROR_DETAIL_LENGTH:
        return compact
    return compact[:MAX_ERROR_DETAIL_LENGTH] + "..."


def _json_response(payload, status=200, req_id=None):
    req_id = req_id or _request_id()
    body = json.dumps(payload)
    resp = Response(body, status=status, mimetype="application/json")
    resp.headers["x-request-id"] = req_id
    resp.headers["x-pj-contract-version"] = CONTRACT_VERSION
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
    return referer == expected_origin or referer.startswith(
        f"{expected_origin}/"
    )


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


def _validated_json(req_id, *, allowed, required=()):
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
        if (
            artifact.get("status") != "ready"
            or artifact.get("sha256") != artifact_hashes.get(artifact_id)
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
        session,
        *,
        execution_key,
        approval_id,
        name,
        arguments,
        approval_granted):
    reservation = chatlog.reserve_tool_execution(
        session["id"],
        execution_key,
        name,
        arguments,
        approval_id=approval_id,
    )
    state = reservation.get("state")
    if state == "completed":
        _validate_and_link_artifacts(
            session["id"],
            reservation.get("artifact_ids") or [],
            reservation.get("artifact_hashes") or {},
        )
        return (
            reservation["result"],
            list(reservation.get("artifact_ids") or []),
            dict(reservation.get("artifact_hashes") or {}),
        )
    if state != "reserved":
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
        public_result, artifact_ids, artifact_hashes = (
            _result_with_linked_artifacts(session["id"], result)
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
        return public_result, artifact_ids, artifact_hashes
    except DurableExecutionOutcomeUnknown:
        chatlog.mark_tool_execution_unknown(
            session["id"], execution_key, token
        )
        raise
    except Exception as exc:
        chatlog.mark_tool_execution_unknown(
            session["id"], execution_key, token
        )
        raise DurableExecutionOutcomeUnknown(
            "The tool may have executed, but its outcome was not durably recorded"
        ) from exc


def _approval_idempotency_prefix(session_id, approval_id):
    digest = hashlib.sha256(
        f"{session_id}\0{approval_id}".encode("utf-8")
    ).hexdigest()
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
    event_type = event.get("type", "message")
    return f"event: {event_type}\ndata: {json.dumps(event, default=str)}\n\n"


@app.route("/", methods=["GET"])
def web_client():
    """Serve the PJ web client."""
    if (
        not app.config["LOCAL_WEB_OWNER_SESSION_ENABLED"]
        or not _is_loopback_request()
    ):
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
                "/webhook",
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
            "instructions_sha256": hashlib.sha256(
                instructions.encode()
            ).hexdigest(),
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
    payload, error = _validated_json(
        req_id, allowed={"title", "channel"}
    )
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
    session.pop("last_response_id", None)
    return _json_response({"ok": True, "session": session}, 201, req_id)


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
    payload, error = _validated_json(req_id, allowed=set())
    if error:
        return error
    session, error = _validated_session(session_id, req_id)
    if error:
        return error
    return _json_response(
        {"ok": True, "session": _session_detail_with_artifacts(session["id"])},
        req_id=req_id,
    )


@app.route("/responses/sessions/<session_id>/artifacts", methods=["GET"])
def list_responses_session_artifacts(session_id):
    req_id = _request_id()
    auth_error = _check_bridge_auth(req_id, required=True)
    if auth_error:
        return auth_error
    if request.args:
        return _error_response(
            "invalid_query", "Unexpected query parameters.", 400, req_id
        )
    session, error = _validated_session(session_id, req_id)
    if error:
        return error
    detail = _session_detail_with_artifacts(session["id"])
    artifacts = detail["artifacts"]
    return _json_response(
        {"ok": True, "count": len(artifacts), "artifacts": artifacts},
        req_id=req_id,
    )


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
    payload, error = _validated_json(
        req_id,
        allowed={
            "external_id",
            "role",
            "content",
            "source",
            "response_id",
            "status",
            "playback_ms",
            "metadata",
        },
        required={"external_id", "role", "content", "source", "status"},
    )
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
    if (
        "prompt_perfecting_version" in metadata
        and (
            not isinstance(metadata["prompt_perfecting_version"], str)
            or len(metadata["prompt_perfecting_version"]) > 100
        )
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
            and not re.fullmatch(
                r"[a-f0-9]{64}", str(metadata["refined_sha256"])
            )
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
        return _error_response(
            "invalid_realtime_message", str(exc), 400, req_id
        )
    return _json_response({"ok": True, "message": message}, req_id=req_id)


@app.route("/responses/artifacts/<artifact_id>", methods=["GET"])
def download_responses_artifact(artifact_id):
    req_id = _request_id()
    auth_error = _check_bridge_auth(req_id, required=True)
    if auth_error:
        return auth_error
    if request.args or not ARTIFACT_ID_PATTERN.fullmatch(artifact_id):
        return _error_response(
            "artifact_not_found", "Artifact was not found.", 404, req_id
        )
    artifact, snapshot = docops.open_export_artifact_snapshot(artifact_id)
    if artifact.get("status") == "not_found":
        return _error_response(
            "artifact_not_found", "Artifact was not found.", 404, req_id
        )
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
    payload, error = _validated_json(
        req_id,
        allowed={"message", "structured_output"},
        required={"message"},
    )
    if error:
        return error
    message = payload["message"]
    if (
        not isinstance(message, str)
        or not message.strip()
        or len(message) > MAX_MESSAGE_LENGTH
    ):
        return _error_response(
            "invalid_message",
            f"message must be a non-empty string up to {MAX_MESSAGE_LENGTH} characters.",
            400,
            req_id,
        )
    text_format, error = _validated_structured_output(
        payload.get("structured_output"), req_id
    )
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
                (
                    "PJ" if item["role"] == "assistant" else "User"
                ) + ": " + item["content"]
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
        stream_with_context(_stream_session_response(
            session,
            turn_token,
            input_value,
            previous_response_id=session.get("last_response_id"),
            text_format=text_format,
            req_id=req_id,
            prelude=(prompt_event,),
            required_deliverable_format=requested_deliverable_format(message),
        )),
        status=200,
        mimetype="text/event-stream",
    )
    response.headers["Cache-Control"] = "no-cache"
    response.headers["X-Accel-Buffering"] = "no"
    response.headers["x-request-id"] = req_id
    response.headers["x-pj-contract-version"] = CONTRACT_VERSION
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
        approval_execution=None):
    deliverable_format = (
        required_deliverable_format
        or requested_deliverable_format(input_value)
    )
    yield _sse({
        "type": "session",
        "session_id": session["id"],
        "request_id": req_id,
    })
    for event in prelude:
        yield _sse(event)
    try:
        idempotency_key_prefix = None
        tool_executor = None
        response_checkpoint = None
        if approval_execution:
            approval_id = approval_execution["approval_id"]
            idempotency_key_prefix = _approval_idempotency_prefix(
                session["id"], approval_id
            )

            def checkpoint(operation_key, provider_response_id):
                if not chatlog.record_provider_response_checkpoint(
                    session["id"], operation_key, provider_response_id
                ):
                    raise DurableExecutionOutcomeUnknown(
                        "The provider returned conflicting response IDs for one "
                        "idempotency key"
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
                    raise RuntimeError(
                        "Generated artifact failed server-side integrity validation"
                    )
                if not chatlog.link_session_artifact(session["id"], artifact_id):
                    raise RuntimeError("Artifact could not be linked to the session")
                public_event = {"type": "artifact.ready", **artifact}
            if public_event["type"] == "approval.required":
                if not response_id or not provider_item_id:
                    raise RuntimeError(
                        "Approval request did not include provider continuity"
                    )
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
                        artifact_id: docops.resolve_export_artifact(
                            artifact_id
                        ).get("sha256", "")
                        for artifact_id in artifact_ids
                    },
                    completed_approval_id=(
                        approval_execution["approval_id"]
                        if approval_execution else None
                    ),
                    completed_approval_decision=(
                        approval_execution["approve"]
                        if approval_execution else None
                    ),
                )
                if not pending:
                    raise RuntimeError(
                        "Session turn lease expired before approval was stored"
                    )
                public_event.update({
                    "approval_id": pending["approval_id"],
                    "expires_at": pending["expires_at"],
                    "session_id": session["id"],
                })
                if approval_execution:
                    yield _sse({
                        "type": "approval.resolved",
                        "approval_id": approval_execution["approval_id"],
                        "approval_kind": approval_execution["approval_kind"],
                        "name": approval_execution["name"],
                        "approved": approval_execution["approved"],
                    })
                yield _sse(public_event)
                return
            if public_event["type"] == "artifact.ready":
                artifact = docops.resolve_export_artifact(
                    public_event.get("artifact_id", "")
                )
                if artifact.get("status") != "ready":
                    raise RuntimeError("Document artifact failed integrity validation")
                if not chatlog.link_session_artifact(
                    session["id"], artifact["artifact_id"]
                ):
                    raise RuntimeError("Document artifact could not be linked to chat")
                public_event = {"type": "artifact.ready", **artifact}
            if public_event["type"] == "completion":
                stored = chatlog.finish_session_turn(
                    session,
                    turn_token,
                    public_event.get("text", ""),
                    response_id,
                    completed_approval_id=(
                        approval_execution["approval_id"]
                        if approval_execution else None
                    ),
                    completed_approval_decision=(
                        approval_execution["approve"]
                        if approval_execution else None
                    ),
                )
                if not stored:
                    raise RuntimeError(
                        "Session turn lease expired before completion"
                    )
                if approval_execution:
                    yield _sse({
                        "type": "approval.resolved",
                        "approval_id": approval_execution["approval_id"],
                        "approval_kind": approval_execution["approval_kind"],
                        "name": approval_execution["name"],
                        "approved": approval_execution["approved"],
                    })
                public_event["session_id"] = session["id"]
            yield _sse(public_event)
    except DurableExecutionOutcomeUnknown as exc:
        if approval_execution:
            chatlog.mark_pending_approval_execution_unknown(
                session["id"],
                approval_execution["approval_id"],
                approval_execution["approve"],
            )
        yield _sse({
            "type": "error",
            "error": {
                "code": "approval_execution_outcome_unknown",
                "message": (
                    "The action outcome is unknown and will not be replayed."
                ),
                "request_id": req_id,
                "detail": _trim_detail(exc),
            },
        })
    except Exception as exc:
        yield _sse({
            "type": "error",
            "error": {
                "code": "responses_turn_failed",
                "message": "Responses turn failed.",
                "request_id": req_id,
                "detail": _trim_detail(exc),
            },
        })
    finally:
        chatlog.release_session_turn(session["id"], turn_token)


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
        return _error_response(
            "approval_not_found", "Approval was not found.", 404, req_id
        )
    payload, error = _validated_json(
        req_id, allowed={"approve"}, required={"approve"}
    )
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
        return _error_response(
            "approval_not_found", "Approval was not found.", 404, req_id
        )
    turn_token = chatlog.claim_session_turn(
        session["id"], pending_approval_id=approval_id
    )
    if not turn_token:
        return _error_response(
            "session_turn_in_progress",
            "Another turn is already in progress for this session.",
            409,
            req_id,
        )
    pending = chatlog.begin_pending_approval_execution(
        session["id"], approval_id, approve
    )
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

    prelude = [{
        "type": "approval.executing",
        "approval_id": approval_id,
        "approval_kind": pending["approval_kind"],
        "name": pending["name"],
        "approved": approve,
    }]
    ready_artifact_ids = list(pending.get("artifact_ids") or ())
    ready_artifact_hashes = dict(pending.get("artifact_hashes") or {})
    if pending["approval_kind"] == "mcp":
        continuation = [{
            "type": "mcp_approval_response",
            "approval_request_id": pending["provider_item_id"],
            "approve": approve,
        }]
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
                public_result = {
                    "error": "The owner rejected this tool call."
                }
                produced_artifact_ids = []
                produced_artifact_hashes = {}
        for artifact_id in produced_artifact_ids:
            if artifact_id not in ready_artifact_ids:
                ready_artifact_ids.append(artifact_id)
                ready_artifact_hashes[artifact_id] = (
                    produced_artifact_hashes[artifact_id]
                )
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
        prelude.append({
            "type": "tool.result",
            "call_id": pending["provider_item_id"],
            "name": pending["name"],
            "result": public_result,
        })
        continuation = [{
            "type": "function_call_output",
            "call_id": pending["provider_item_id"],
            "output": json.dumps(public_result, default=str),
        }]

    response = Response(
        stream_with_context(_stream_session_response(
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
        )),
        status=200,
        mimetype="text/event-stream",
    )
    response.headers["Cache-Control"] = "no-cache"
    response.headers["X-Accel-Buffering"] = "no"
    response.headers["x-request-id"] = req_id
    response.headers["x-pj-contract-version"] = CONTRACT_VERSION
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
                not SESSION_ID_PATTERN.fullmatch(session_id)
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
        ((raw.get("client_secret") or {}).get("value"))
        if isinstance(raw, dict)
        else None
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
                if isinstance(raw, dict) else None,
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
        result = dispatch_realtime_function(name, arguments)
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

    artifact = (
        result.get("artifact") if isinstance(result, dict) else None
    )
    if session_id and isinstance(artifact, dict) and artifact.get("status") == "ready":
        if not chatlog.link_session_artifact(
            session_id, str(artifact.get("artifact_id") or "")
        ):
            return _error_response(
                "artifact_link_failed",
                "The tool artifact could not be linked to the chat session.",
                500,
                req_id,
            )
    return _json_response(
        redact_server_paths(result), status=200, req_id=req_id
    )


@app.route("/webhook", methods=["POST"])
def sip_webhook():
    """Handle OpenAI realtime.call.incoming webhook events for SIP calls."""
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


if __name__ == "__main__":
    if "OPENAI_API_KEY" not in os.environ:
        raise SystemExit("OPENAI_API_KEY not set — source ~/.env first")
    bind_host = os.getenv("PJ_REALTIME_BIND_HOST", "127.0.0.1")
    try:
        app.config["LOCAL_WEB_OWNER_SESSION_ENABLED"] = (
            ipaddress.ip_address(bind_host).is_loopback
        )
    except ValueError:
        app.config["LOCAL_WEB_OWNER_SESSION_ENABLED"] = False
    app.run(host=bind_host, port=3001)
