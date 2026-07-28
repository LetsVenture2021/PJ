#!/usr/bin/env python3
"""Shared configuration and Responses API orchestration for PJ."""
import contextvars
import copy
import json
import os
import re
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from openai import OpenAI

import skills

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.json"
STATE_PATH = BASE_DIR / "state.json"
MCP_PATH = BASE_DIR / "mcp_servers.json"
COMPUTER_USE_MODELS = {"computer-use-preview"}
MAX_LOCAL_TOOL_ROUNDS = 12
MAX_DELEGATION_DETAIL_LENGTH = 6000
MAX_DELEGATION_CITATIONS = 25

ADVANCED_DELEGATION_TOOL = {
    "type": "function",
    "name": "delegate_advanced_task",
    "description": (
        "Delegate a complex research or multi-tool task to PJ's full text "
        "Responses runtime, then return a concise voice-safe summary."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "prompt": {
                "type": "string",
                "description": "The complete task and relevant conversational context.",
            }
        },
        "required": ["prompt"],
        "additionalProperties": False,
    },
}

_ENV_REF = re.compile(r"\$(?:\{([A-Za-z_][A-Za-z0-9_]*)\}|([A-Za-z_][A-Za-z0-9_]*))")
_URL_IN_TEXT = re.compile(r"https?://[^\s<>'\"]+")
_delegation_active = contextvars.ContextVar("pj_delegation_active", default=False)


def _get(value, key, default=None):
    if isinstance(value, dict):
        return value.get(key, default)
    return getattr(value, key, default)


def load_config(base_dir=BASE_DIR):
    base_dir = Path(base_dir)
    with (base_dir / "config.json").open() as f:
        cfg = json.load(f)
    instruction_files = cfg.get("instruction_files")
    if instruction_files is None:
        instruction_files = [cfg.get("instructions_file")]
    if (
        not isinstance(instruction_files, list)
        or not instruction_files
        or any(not isinstance(item, str) or not item.strip() for item in instruction_files)
    ):
        raise ValueError(
            "config.json must define instruction_files or legacy instructions_file"
        )
    instruction_files = list(dict.fromkeys(item.strip() for item in instruction_files))
    instruction_parts = []
    for filename in instruction_files:
        path = (base_dir / filename).resolve()
        try:
            path.relative_to(base_dir.resolve())
        except ValueError as exc:
            raise ValueError("instruction files must remain within the project") from exc
        instruction_parts.append(path.read_text())
    cfg["instructions"] = "\n\n".join(instruction_parts)
    cfg["instructions_source"] = instruction_files[0]
    cfg["instruction_files"] = instruction_files
    cfg.setdefault("instructions_file", instruction_files[0])

    vector_store_ids = cfg.get("vector_store_ids")
    if vector_store_ids is None:
        vector_store_ids = [cfg["vector_store_id"]] if cfg.get("vector_store_id") else []
    if (
        not isinstance(vector_store_ids, list)
        or any(not isinstance(item, str) or not item.strip() for item in vector_store_ids)
    ):
        raise ValueError("vector_store_ids must be a list of non-empty strings")
    vector_store_ids = list(dict.fromkeys(item.strip() for item in vector_store_ids))
    cfg["vector_store_ids"] = vector_store_ids
    if vector_store_ids:
        cfg.setdefault("vector_store_id", vector_store_ids[0])
    return cfg


def load_instructions(base_dir=BASE_DIR):
    cfg = load_config(base_dir)
    return cfg, cfg["instructions"]


def load_mcp_servers(base_dir=BASE_DIR):
    path = Path(base_dir) / "mcp_servers.json"
    if not path.exists():
        return []
    with path.open() as f:
        servers = json.load(f)
    if not isinstance(servers, list):
        raise ValueError("mcp_servers.json must contain a list")
    return servers


def _expand_secret_value(value, environ):
    missing = []

    def replace(match):
        name = match.group(1) or match.group(2)
        expanded = environ.get(name)
        if expanded is None:
            missing.append(name)
            return match.group(0)
        return expanded

    return _ENV_REF.sub(replace, value), missing


def _public_url(value):
    if not isinstance(value, str):
        return value
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return value.split("?", 1)[0].split("#", 1)[0]
    if not parsed.scheme or not parsed.hostname:
        return value.split("?", 1)[0]
    host = parsed.hostname
    if port:
        host = f"{host}:{port}"
    return urlunsplit((parsed.scheme, host, parsed.path, "", ""))


def sanitize_text_urls(value):
    if not isinstance(value, str):
        return value
    return _URL_IN_TEXT.sub(
        lambda match: _public_url(
            match.group(0).rstrip(".,);]")
        ),
        value,
    )


def prepare_mcp_servers(servers=None, *, environ=None, base_dir=BASE_DIR):
    servers = load_mcp_servers(base_dir) if servers is None else servers
    environ = os.environ if environ is None else environ
    prepared = []
    for raw in servers:
        if not isinstance(raw, dict):
            continue
        item = {
            "label": raw.get("label"),
            "url": raw.get("url"),
            "enabled": bool(raw.get("enabled", True)),
            "require_approval": raw.get("require_approval", "always"),
            "allowed_tools": raw.get("allowed_tools"),
            "headers": {},
            "missing_secrets": [],
        }
        for name, value in raw.get("headers", {}).items():
            if not isinstance(name, str) or not isinstance(value, str):
                continue
            expanded, missing = _expand_secret_value(value, environ)
            item["missing_secrets"].extend(missing)
            if not missing:
                item["headers"][name] = expanded
        prepared.append(item)
    return prepared


def build_tools(cfg, *, mcp_servers=None, environ=None):
    tools = []
    if cfg.get("code_interpreter_enabled", True):
        tools.append({"type": "code_interpreter", "container": {"type": "auto"}})
    if cfg.get("web_search_enabled", True):
        tools.append({"type": "web_search"})
    if cfg.get("image_generation_enabled", True):
        tools.append({"type": "image_generation"})
    vector_store_ids = cfg.get("vector_store_ids")
    if vector_store_ids is None:
        vector_store_ids = [cfg["vector_store_id"]] if cfg.get("vector_store_id") else []
    if vector_store_ids:
        tools.append({
            "type": "file_search",
            "vector_store_ids": list(vector_store_ids),
        })
    if cfg.get("computer_use_enabled") and cfg.get("model") in COMPUTER_USE_MODELS:
        tools.append({
            "type": "computer_use_preview",
            "display_width": 1280,
            "display_height": 800,
            "environment": "browser",
        })

    prepared = prepare_mcp_servers(mcp_servers, environ=environ)
    for server in prepared:
        if not server["enabled"] or server["missing_secrets"]:
            continue
        entry = {
            "type": "mcp",
            "server_label": server["label"],
            "server_url": server["url"],
            "require_approval": server["require_approval"],
        }
        if server["headers"]:
            entry["headers"] = server["headers"]
        if server["allowed_tools"]:
            entry["allowed_tools"] = server["allowed_tools"]
        tools.append(entry)

    tools.extend(copy.deepcopy(skills.TOOL_SCHEMAS))
    if cfg.get("tool_search_enabled", True) and len(tools) > 8:
        for tool in tools:
            if tool.get("type") == "function":
                tool["defer_loading"] = True
        tools.append({"type": "tool_search"})
    return tools


def capability_manifest(cfg=None, *, mcp_servers=None, environ=None):
    cfg = load_config() if cfg is None else cfg
    prepared = prepare_mcp_servers(mcp_servers, environ=environ)
    import imageops

    image_status = imageops.get_image_capability_status()
    image_tool_states = {
        "generate_image_asset": (
            "active"
            if image_status["generation"] == "active"
            else "disabled"
            if image_status["generation"] == "disabled"
            else "degraded"
        ),
        "edit_image_asset": "unavailable",
        "create_image_variation": "unavailable",
        "create_controlled_image": "active",
        "register_vector_image": "active",
        "get_image_asset": "active",
        "delete_image_asset": "active",
        "record_image_feedback": "active",
        "get_image_capability_status": "active",
    }
    functions = [
        {
            "name": tool["name"],
            "status": image_tool_states.get(tool["name"], "active"),
            "configured": True,
        }
        for tool in skills.TOOL_SCHEMAS
        if isinstance(tool, dict) and tool.get("type") == "function"
    ]
    mcp = []
    for server in prepared:
        if not server["enabled"]:
            status = "disabled"
        elif server["missing_secrets"]:
            status = "degraded"
        else:
            status = "configured"
        mcp.append({
            "label": server["label"],
            "url": _public_url(server["url"]),
            "status": status,
            "configured": True,
            "enabled": server["enabled"],
            "authentication": (
                "missing_environment_secret"
                if server["missing_secrets"] else
                ("configured" if server["headers"] else "not_required")
            ),
            "require_approval": server["require_approval"],
            "runtime_enabled": (
                server["enabled"]
                and not server["missing_secrets"]
            ),
            "approval_flow": (
                "explicit_owner_confirmation"
                if server["require_approval"] != "never"
                else "not_required"
            ),
        })

    computer_status = "disabled"
    if cfg.get("computer_use_enabled"):
        computer_status = (
            "active" if cfg.get("model") in COMPUTER_USE_MODELS else "unavailable"
        )
    native = {
        "web_search": {
            "status": "active" if cfg.get("web_search_enabled", True) else "disabled",
            "configured": True,
        },
        "file_search": {
            "status": "active" if (
                cfg.get("vector_store_ids") or cfg.get("vector_store_id")
            ) else "unavailable",
            "configured": bool(
                cfg.get("vector_store_ids") or cfg.get("vector_store_id")
            ),
        },
        "tool_search": {
            "status": "active" if cfg.get("tool_search_enabled", True) else "disabled",
            "configured": True,
        },
        "code_interpreter": {
            "status": "active" if cfg.get("code_interpreter_enabled", True) else "disabled",
            "configured": True,
        },
        "image_generation": {
            "status": "active" if cfg.get("image_generation_enabled", True) else "disabled",
            "configured": True,
        },
        "computer_use": {
            "status": computer_status,
            "configured": bool(cfg.get("computer_use_enabled")),
        },
    }
    return {
        "status_values": [
            "configured", "active", "disabled", "degraded", "unavailable"
        ],
        "model": {"id": cfg["model"], "status": "active"},
        "imageops": image_status,
        "instructions": {
            "source": cfg.get("instructions_source", cfg.get("instructions_file")),
            "status": "active",
        },
        "native": native,
        "local_functions": {
            "status": "active",
            "count": len(functions),
            "tools": functions,
        },
        "mcp_servers": mcp,
        "disabled_capabilities": [
            name for name, value in native.items()
            if value["status"] in ("disabled", "unavailable")
        ] + [
            f"mcp:{server['label']}" for server in mcp
            if server["status"] == "disabled"
        ],
    }


def load_state(path=STATE_PATH):
    path = Path(path)
    if path.exists():
        with path.open() as f:
            return json.load(f)
    return {"previous_response_id": None}


def save_state(state, path=STATE_PATH):
    with Path(path).open("w") as f:
        json.dump(state, f)


def dispatch_local_function(name, arguments):
    if not isinstance(arguments, dict):
        raise ValueError("Function arguments must be an object")
    return skills.dispatch(name, arguments)


def dispatch_approved_local_function(name, arguments):
    if not isinstance(arguments, dict):
        raise ValueError("Function arguments must be an object")
    return skills.dispatch(name, arguments, approval_granted=True)


def _parsed_arguments(raw_arguments):
    if isinstance(raw_arguments, dict):
        return raw_arguments
    try:
        return json.loads(raw_arguments or "{}")
    except (json.JSONDecodeError, TypeError) as exc:
        return {"_invalid_json": str(exc)}


def _function_calls(response):
    calls = []
    for item in _get(response, "output", []) or []:
        if _get(item, "type") != "function_call":
            continue
        calls.append({
            "name": _get(item, "name"),
            "call_id": _get(item, "call_id") or _get(item, "id"),
            "arguments": _parsed_arguments(_get(item, "arguments", "{}")),
        })
    return calls


def _mcp_approval_requests(response):
    approvals = []
    for item in _get(response, "output", []) or []:
        if _get(item, "type") != "mcp_approval_request":
            continue
        approvals.append({
            "provider_item_id": _get(item, "id"),
            "name": _get(item, "name"),
            "server_label": _get(item, "server_label"),
            "arguments": _parsed_arguments(_get(item, "arguments", "{}")),
        })
    return approvals


def _citation_metadata(response):
    citations = []
    seen = set()
    for item in _get(response, "output", []) or []:
        for content in _get(item, "content", []) or []:
            for annotation in _get(content, "annotations", []) or []:
                data = {
                    key: _get(annotation, key)
                    for key in (
                        "type", "title", "url", "filename", "file_id",
                        "start_index", "end_index",
                    )
                    if _get(annotation, key) is not None
                }
                if "url" in data:
                    data["url"] = _public_url(data["url"])
                marker = json.dumps(data, sort_keys=True, default=str)
                if data and marker not in seen:
                    seen.add(marker)
                    citations.append(data)
    return citations


def _source_metadata(response):
    sources = []
    for item in _get(response, "output", []) or []:
        item_type = _get(item, "type")
        if item_type == "file_search_call":
            for result in _get(item, "results", []) or []:
                text = _get(result, "text")
                sources.append({
                    key: value
                    for key, value in {
                        "type": "file_search",
                        "file_id": _get(result, "file_id"),
                        "filename": _get(result, "filename"),
                        "score": _get(result, "score"),
                        "text": text[:500] if isinstance(text, str) else None,
                    }.items()
                    if value is not None
                })
        elif item_type == "web_search_call":
            action = _get(item, "action")
            source = {
                "type": "web_search",
                "action": _get(action, "type"),
                "query": _get(action, "query"),
                "url": _get(action, "url"),
            }
            if source["url"] is not None:
                source["url"] = _public_url(source["url"])
            sources.append({
                key: value for key, value in source.items() if value is not None
            })
    return sources


def _native_tool_result(item):
    item_type = _get(item, "type")
    if item_type not in {
        "web_search_call",
        "file_search_call",
        "mcp_call",
        "code_interpreter_call",
        "image_generation_call",
        "computer_call",
    }:
        return None
    result = {
        "type": "tool.result",
        "tool_type": item_type,
        "call_id": _get(item, "id") or _get(item, "call_id"),
        "name": _get(item, "name") or _get(item, "server_label"),
        "status": _get(item, "status"),
    }
    output = _get(item, "output")
    if isinstance(output, str):
        result["output"] = output[:2000]
        result["output_truncated"] = len(output) > 2000
    return result


def _stream_error(event):
    error = _get(event, "error")
    return sanitize_text_urls(
        _get(error, "message")
        or _get(event, "message")
        or str(error or event)
    )


def requested_deliverable_format(message):
    """Map explicit file requests to the exact artifact format required."""
    if not isinstance(message, str):
        return None
    text = message.casefold()
    checks = (
        ("pptx", (r"\bpower\s*point\b", r"\bpowerpoint\b", r"\.pptx\b", r"\bpptx\b")),
        ("pdf", (r"\bportable document format\b", r"\.pdf\b", r"\bpdf\b")),
        (
            "xlsx",
            (
                r"\bexcel (?:file|spreadsheet|workbook)\b",
                r"\.xlsx\b",
                r"\bxlsx\b",
            ),
        ),
        ("docx", (r"\bword document\b", r"\.docx\b", r"\bdocx\b")),
        ("rtf", (r"\brich text format\b", r"\.rtf\b", r"\brtf\b")),
        ("html", (r"\bhtml file\b", r"\.html\b")),
        (
            "md",
            (
                r"\bmarkdown (?:file|document)\b",
                r"\.md\b",
            ),
        ),
    )
    for format_name, patterns in checks:
        if any(re.search(pattern, text) for pattern in patterns):
            return format_name
    return None


_SERVER_PATH_PATTERN = re.compile(
    r"""(?<![A-Za-z0-9:])
    (?:
        file://(?:/[^\s"'<>}\]]+)+
        |(?:[A-Za-z]:[\\/]|\\\\)[^\s"'<>}\]]+
        |/(?:[^/\s"'<>}\]]+/)+[^/\s"'<>}\]]+
    )""",
    re.X,
)
_URI_PATTERN = re.compile(r"\bhttps?://[^\s\"'<>}\]]+")
_SERVER_PATH_FIELDS = {
    "canonical_path",
    "cwd",
    "directory",
    "html_fallback",
    "local_path",
    "output_path",
    "path",
    "source_path",
    "workspace_path",
}


def redact_server_paths(value):
    """Remove absolute server paths while retaining repository-relative evidence."""
    if isinstance(value, dict):
        cleaned = {}
        for key, item in value.items():
            normalized_key = str(key).casefold()
            if (
                normalized_key in _SERVER_PATH_FIELDS
                or normalized_key.endswith(("_path", "_paths"))
            ):
                continue
            cleaned[key] = redact_server_paths(item)
        return cleaned
    if isinstance(value, (list, tuple)):
        return [redact_server_paths(item) for item in value]
    if isinstance(value, str):
        uris: dict[str, str] = {}

        def preserve_uri(match: re.Match) -> str:
            placeholder = f"\x00PJ_URI_{len(uris)}\x00"
            uris[placeholder] = match.group(0)
            return placeholder

        redacted = _SERVER_PATH_PATTERN.sub(
            "[server path redacted]",
            _URI_PATTERN.sub(preserve_uri, value),
        )
        for placeholder, uri in uris.items():
            redacted = redacted.replace(placeholder, uri)
        return redacted
    return value


def _verified_artifact_from_result(result):
    if not isinstance(result, dict):
        return None
    candidate = result.get("artifact")
    if not isinstance(candidate, dict) or candidate.get("status") != "ready":
        return None
    artifact_id = candidate.get("artifact_id")
    if not isinstance(artifact_id, str):
        return None
    import docops

    verified = docops.resolve_export_artifact(artifact_id)
    return verified if verified.get("status") == "ready" else None


def _tool_call_key(call_id, tool_type, name):
    if call_id:
        return str(call_id)
    return f"{tool_type or 'tool'}:{name or 'unnamed'}"


class ResponsesOrchestrator:
    def __init__(
            self,
            client,
            cfg,
            *,
            dispatcher=dispatch_local_function,
            approved_dispatcher=dispatch_approved_local_function,
            approval_handler=None,
            tool_executor=None,
            response_checkpoint=None,
            expose_local_paths=False):
        self.client = client
        self.cfg = cfg
        self.dispatcher = dispatcher
        self.approved_dispatcher = approved_dispatcher
        self.approval_handler = approval_handler
        self.tool_executor = tool_executor
        self.response_checkpoint = response_checkpoint
        self.expose_local_paths = bool(expose_local_paths)

    def _request_kwargs(
            self,
            input_value,
            previous_response_id,
            text_format,
            first,
            idempotency_key=None):
        kwargs = {
            "model": self.cfg["model"],
            "input": input_value,
            "tools": build_tools(self.cfg),
            "stream": True,
            "instructions": self.cfg["instructions"],
        }
        if previous_response_id:
            kwargs["previous_response_id"] = previous_response_id
        if self.cfg.get("reasoning_effort"):
            kwargs["reasoning"] = {"effort": self.cfg["reasoning_effort"]}
        if text_format:
            kwargs["text"] = {"format": text_format}
        if idempotency_key:
            kwargs["extra_headers"] = {"Idempotency-Key": idempotency_key}
        return kwargs

    def _dispatch_call(self, call: dict, *, approved: bool):
        if self.tool_executor is not None:
            return self.tool_executor(call, approved)
        dispatcher = self.approved_dispatcher if approved else self.dispatcher
        return dispatcher(call["name"], call["arguments"])

    def _checkpoint_response(self, operation_key, response_id):
        if (
            operation_key
            and response_id
            and self.response_checkpoint is not None
            and not self.response_checkpoint(operation_key, response_id)
        ):
            raise RuntimeError(
                "Provider idempotency key returned a different response ID"
            )

    def stream_turn(
            self,
            message,
            *,
            previous_response_id=None,
            text_format=None,
            required_deliverable_format=None,
            ready_artifact_ids=(),
            idempotency_key_prefix=None):
        input_value = message
        first = True
        round_number = 0
        all_citations = []
        all_sources = []
        tool_calls_emitted = set()
        tool_results_emitted = set()
        ready_artifacts = {}
        for artifact_id in ready_artifact_ids or ():
            import docops

            artifact = docops.resolve_export_artifact(artifact_id)
            if artifact.get("status") != "ready":
                raise RuntimeError(
                    "Persisted deliverable artifact failed integrity validation"
                )
            ready_artifacts[artifact_id] = artifact
        required_format = (
            required_deliverable_format or requested_deliverable_format(message)
        )
        if required_format not in {
            None,
            "md",
            "html",
            "pdf",
            "docx",
            "rtf",
            "pptx",
            "xlsx",
        }:
            raise ValueError("unsupported required deliverable format")
        repair_attempts = 0

        while True:
            operation_key = (
                f"{idempotency_key_prefix}:round:{round_number}"
                if idempotency_key_prefix else None
            )
            kwargs = self._request_kwargs(
                input_value,
                previous_response_id,
                text_format,
                first,
                operation_key,
            )
            final_response = None
            round_text = []
            pending_stream_results = {}
            for event in self.client.responses.create(**kwargs):
                event_type = _get(event, "type", "")
                if event_type == "response.created":
                    created_response = _get(event, "response")
                    self._checkpoint_response(
                        operation_key,
                        _get(created_response, "id")
                        or _get(event, "response_id")
                        or _get(event, "id"),
                    )
                elif event_type == "response.output_text.delta":
                    delta = _get(event, "delta", "")
                    round_text.append(delta)
                    if not required_format:
                        yield {"type": "text.delta", "delta": delta}
                elif event_type == "response.output_item.added":
                    item = _get(event, "item")
                    item_type = _get(item, "type")
                    if item_type in {
                        "function_call",
                        "mcp_call",
                        "web_search_call",
                        "file_search_call",
                        "code_interpreter_call",
                        "image_generation_call",
                        "computer_call",
                    }:
                        call_id = _get(item, "call_id") or _get(item, "id")
                        name = _get(item, "name") or _get(item, "server_label")
                        key = _tool_call_key(call_id, item_type, name)
                        if key in tool_calls_emitted:
                            continue
                        tool_calls_emitted.add(key)
                        yield {
                            "type": "tool.call",
                            "call_id": call_id,
                            "name": name,
                            "tool_type": item_type,
                        }
                elif event_type == "response.function_call_arguments.delta":
                    call_id = _get(event, "call_id") or _get(event, "item_id")
                    key = _tool_call_key(
                        call_id, "function_call", _get(event, "name")
                    )
                    if key not in tool_calls_emitted:
                        tool_calls_emitted.add(key)
                        yield {
                            "type": "tool.call",
                            "call_id": call_id,
                            "name": _get(event, "name"),
                            "tool_type": "function_call",
                        }
                    yield {
                        "type": "tool.call.delta",
                        "call_id": call_id,
                        "delta": _get(event, "delta", ""),
                    }
                elif event_type.startswith("response.mcp_call"):
                    call_id = (
                        _get(event, "item_id")
                        or _get(event, "call_id")
                        or _get(event, "id")
                    )
                    name = _get(event, "name") or _get(event, "server_label")
                    key = _tool_call_key(call_id, "mcp_call", name)
                    if key not in tool_calls_emitted:
                        tool_calls_emitted.add(key)
                        yield {
                            "type": "tool.call",
                            "call_id": call_id,
                            "tool_type": "mcp_call",
                            "source_event": event_type,
                            "name": name,
                        }
                    if (
                        event_type.endswith((".completed", ".failed"))
                        and key not in tool_results_emitted
                    ):
                        pending_stream_results[key] = {
                            "type": "tool.result",
                            "call_id": call_id,
                            "tool_type": "mcp_call",
                            "source_event": event_type,
                            "name": name,
                            "status": (
                                "failed" if event_type.endswith(".failed")
                                else "completed"
                            ),
                        }
                elif any(
                    event_type.startswith(f"response.{tool_type}_call")
                    for tool_type in (
                        "web_search", "file_search", "code_interpreter",
                        "image_generation", "computer",
                    )
                ):
                    tool_type = event_type.split(".")[1]
                    call_id = (
                        _get(event, "item_id")
                        or _get(event, "call_id")
                        or _get(event, "id")
                    )
                    key = _tool_call_key(call_id, tool_type, None)
                    if key not in tool_calls_emitted:
                        tool_calls_emitted.add(key)
                        yield {
                            "type": "tool.call",
                            "tool_type": tool_type,
                            "source_event": event_type,
                            "call_id": call_id,
                        }
                    if (
                        event_type.endswith((".completed", ".failed"))
                        and key not in tool_results_emitted
                    ):
                        pending_stream_results[key] = {
                            "type": "tool.result",
                            "tool_type": tool_type,
                            "source_event": event_type,
                            "call_id": call_id,
                            "status": (
                                "failed" if event_type.endswith(".failed")
                                else "completed"
                            ),
                        }
                elif event_type in (
                    "response.error", "response.failed", "response.incomplete",
                    "error",
                ):
                    raise RuntimeError(_stream_error(event))
                elif event_type == "response.completed":
                    final_response = _get(event, "response")

            if final_response is None:
                raise RuntimeError("Responses stream ended without response.completed")

            response_id = _get(final_response, "id")
            self._checkpoint_response(operation_key, response_id)
            citations = _citation_metadata(final_response)
            for citation in citations:
                all_citations.append(citation)
                yield {"type": "citation", "citation": citation}
            sources = _source_metadata(final_response)
            for source in sources:
                all_sources.append(source)
                yield {"type": "source", "source": source}
            for item in _get(final_response, "output", []) or []:
                native_result = _native_tool_result(item)
                if native_result:
                    key = _tool_call_key(
                        native_result.get("call_id"),
                        native_result.get("tool_type"),
                        native_result.get("name"),
                    )
                    if key not in tool_calls_emitted:
                        tool_calls_emitted.add(key)
                        yield {
                            "type": "tool.call",
                            "call_id": native_result.get("call_id"),
                            "tool_type": native_result.get("tool_type"),
                            "name": native_result.get("name"),
                        }
                    if key not in tool_results_emitted:
                        tool_results_emitted.add(key)
                        pending_stream_results.pop(key, None)
                        yield native_result
            for key, result in pending_stream_results.items():
                if key not in tool_results_emitted:
                    tool_results_emitted.add(key)
                    yield result

            mcp_approvals = _mcp_approval_requests(final_response)
            if mcp_approvals:
                approval_outputs = []
                for approval in mcp_approvals:
                    approval_event = {
                        "type": "approval.required",
                        "approval_kind": "mcp",
                        "name": approval["name"],
                        "server_label": approval["server_label"],
                        "arguments": approval["arguments"],
                        "_response_id": response_id,
                        "_provider_item_id": approval["provider_item_id"],
                        "_artifact_ids": list(ready_artifacts),
                    }
                    yield approval_event
                    if self.approval_handler is None:
                        return
                    approved = bool(self.approval_handler({
                        key: value
                        for key, value in approval_event.items()
                        if not key.startswith("_")
                    }))
                    yield {
                        "type": "approval.resolved",
                        "approval_kind": "mcp",
                        "name": approval["name"],
                        "approved": approved,
                    }
                    approval_outputs.append({
                        "type": "mcp_approval_response",
                        "approval_request_id": approval["provider_item_id"],
                        "approve": approved,
                    })
                round_number += 1
                if round_number > MAX_LOCAL_TOOL_ROUNDS:
                    raise RuntimeError("Tool continuation limit exceeded")
                input_value = approval_outputs
                previous_response_id = response_id
                first = False
                continue

            calls = _function_calls(final_response)
            if not calls:
                output_text = _get(final_response, "output_text", None)
                if output_text is None:
                    output_text = "".join(round_text)
                if required_format and not any(
                    artifact.get("format") == required_format
                    for artifact in ready_artifacts.values()
                ):
                    if repair_attempts < 1:
                        repair_attempts += 1
                        round_number += 1
                        if round_number > MAX_LOCAL_TOOL_ROUNDS:
                            raise RuntimeError("Deliverable repair limit exceeded")
                        input_value = (
                            "The requested deliverable is incomplete. Do not "
                            "claim completion or substitute another format. "
                            f"Create and export a validated .{required_format} "
                            "artifact now, using the governed document tools. "
                            "Finish only after export_document returns an "
                            "artifact with status ready."
                        )
                        previous_response_id = response_id
                        first = False
                        continue
                    failure_text = (
                        f"The requested .{required_format} deliverable is "
                        "incomplete because no validated artifact was produced."
                    )
                    yield {
                        "type": "deliverable.incomplete",
                        "requested_format": required_format,
                        "reason": "validated_artifact_missing",
                    }
                    yield {"type": "text.delta", "delta": failure_text}
                    yield {
                        "type": "completion",
                        "text": failure_text,
                        "citations": all_citations,
                        "sources": all_sources,
                        "artifacts": list(ready_artifacts.values()),
                        "deliverable": {
                            "status": "incomplete",
                            "requested_format": required_format,
                        },
                        "_response_id": response_id,
                    }
                    return
                if required_format and output_text:
                    yield {"type": "text.delta", "delta": output_text}
                completion = {
                    "type": "completion",
                    "text": output_text,
                    "citations": all_citations,
                    "sources": all_sources,
                    "artifacts": list(ready_artifacts.values()),
                    "_response_id": response_id,
                }
                if required_format:
                    completion["deliverable"] = {
                        "status": "ready",
                        "requested_format": required_format,
                    }
                if text_format:
                    try:
                        completion["structured_output"] = json.loads(output_text)
                    except json.JSONDecodeError as exc:
                        raise RuntimeError(
                            f"Structured response was not valid JSON: {exc}"
                        ) from exc
                yield completion
                return

            approval_calls = [
                call for call in calls
                if skills.tool_policy_mode(call["name"]) == "approval"
            ]
            if approval_calls:
                if len(calls) != 1:
                    raise RuntimeError(
                        "Responses requiring approval must contain exactly one "
                        "local function call"
                    )
                call = approval_calls[0]
                call_key = _tool_call_key(
                    call["call_id"], "function_call", call["name"]
                )
                if call_key not in tool_calls_emitted:
                    tool_calls_emitted.add(call_key)
                    yield {
                        "type": "tool.call",
                        "call_id": call["call_id"],
                        "name": call["name"],
                        "arguments": call["arguments"],
                        "tool_type": "function_call",
                    }
                approval_event = {
                    "type": "approval.required",
                    "approval_kind": "local_function",
                    "name": call["name"],
                    "arguments": call["arguments"],
                    "_response_id": response_id,
                    "_provider_item_id": call["call_id"],
                    "_artifact_ids": list(ready_artifacts),
                }
                yield approval_event
                if self.approval_handler is None:
                    return
                approved = bool(self.approval_handler({
                    key: value
                    for key, value in approval_event.items()
                    if not key.startswith("_")
                }))
                if approved:
                    result = self._dispatch_call(call, approved=True)
                else:
                    result = {"error": "The owner rejected this tool call."}
                yield {
                    "type": "approval.resolved",
                    "approval_kind": "local_function",
                    "name": call["name"],
                    "approved": approved,
                }
                public_result = (
                    result
                    if self.expose_local_paths
                    else redact_server_paths(result)
                )
                artifact = _verified_artifact_from_result(result)
                if artifact and artifact["artifact_id"] not in ready_artifacts:
                    ready_artifacts[artifact["artifact_id"]] = artifact
                    yield {"type": "artifact.ready", **artifact}
                if call_key not in tool_results_emitted:
                    tool_results_emitted.add(call_key)
                    yield {
                        "type": "tool.result",
                        "call_id": call["call_id"],
                        "name": call["name"],
                        "result": public_result,
                    }
                round_number += 1
                if round_number > MAX_LOCAL_TOOL_ROUNDS:
                    raise RuntimeError("Local function recursion limit exceeded")
                input_value = [{
                    "type": "function_call_output",
                    "call_id": call["call_id"],
                    "output": json.dumps(public_result, default=str),
                }]
                previous_response_id = response_id
                first = False
                continue

            round_number += 1
            if round_number > MAX_LOCAL_TOOL_ROUNDS:
                raise RuntimeError("Local function recursion limit exceeded")
            tool_outputs = []
            for call in calls:
                call_key = _tool_call_key(
                    call["call_id"], "function_call", call["name"]
                )
                if call_key not in tool_calls_emitted:
                    tool_calls_emitted.add(call_key)
                    yield {
                        "type": "tool.call",
                        "call_id": call["call_id"],
                        "name": call["name"],
                        "arguments": call["arguments"],
                        "tool_type": "function_call",
                    }
                result = self._dispatch_call(call, approved=False)
                public_result = (
                    result
                    if self.expose_local_paths
                    else redact_server_paths(result)
                )
                artifact = _verified_artifact_from_result(result)
                if artifact and artifact["artifact_id"] not in ready_artifacts:
                    ready_artifacts[artifact["artifact_id"]] = artifact
                    yield {"type": "artifact.ready", **artifact}
                if call_key not in tool_results_emitted:
                    tool_results_emitted.add(call_key)
                    yield {
                        "type": "tool.result",
                        "call_id": call["call_id"],
                        "name": call["name"],
                        "result": public_result,
                    }
                tool_outputs.append({
                    "type": "function_call_output",
                    "call_id": call["call_id"],
                    "output": json.dumps(public_result, default=str),
                })
            input_value = tool_outputs
            previous_response_id = response_id
            first = False


def terminal_approval_handler(event):
    target = event.get("server_label") or event.get("name") or "tool"
    arguments = json.dumps(event.get("arguments") or {}, default=str)[:1000]
    answer = input(
        f"\nApproval required for {target} with arguments {arguments}. "
        "Type 'approve' to continue: "
    )
    return answer.strip().lower() == "approve"


def send_message(
        client,
        cfg,
        state,
        message,
        text_format=None,
        *,
        echo=True,
        approval_handler=terminal_approval_handler):
    orchestrator = ResponsesOrchestrator(
        client,
        cfg,
        approval_handler=approval_handler,
        expose_local_paths=True,
    )
    completion = None
    for event in orchestrator.stream_turn(
        message,
        previous_response_id=state.get("previous_response_id"),
        text_format=text_format,
    ):
        if event["type"] == "text.delta" and echo:
            print(event["delta"], end="", flush=True)
        elif event["type"] == "tool.call" and event.get("name") and echo:
            print(f"\n🔧 PJ is calling {event['name']}...", flush=True)
        elif event["type"] == "tool.result" and echo:
            print(f"   ✅ {json.dumps(event['result'], default=str)}", flush=True)
        elif event["type"] == "approval.required" and echo:
            target = event.get("server_label") or event.get("name") or "tool"
            print(f"\n🔐 Owner approval required for {target}.", flush=True)
        elif event["type"] == "approval.resolved" and echo:
            decision = "approved" if event.get("approved") else "rejected"
            print(f"   Approval {decision}.", flush=True)
        elif event["type"] == "completion":
            completion = event
    if echo:
        print()
    if completion is None:
        raise RuntimeError("Responses turn did not complete")
    state["previous_response_id"] = completion["_response_id"]
    save_state(state)
    return completion["text"]


def send_structured_message(
        client, cfg, state, message, schema_name, json_schema, strict=True):
    raw = send_message(
        client,
        cfg,
        state,
        message,
        text_format={
            "type": "json_schema",
            "name": schema_name,
            "schema": json_schema,
            "strict": strict,
        },
    )
    return json.loads(raw)


def _voice_safe_summary(text, limit=700):
    compact = " ".join((text or "").split())
    if len(compact) <= limit:
        return compact
    return compact[:limit].rsplit(" ", 1)[0] + "."


def delegate_advanced_task(prompt, *, client=None, cfg=None):
    if not isinstance(prompt, str) or not prompt.strip():
        return {"error": "prompt must be a non-empty string"}
    if _delegation_active.get():
        return {"error": "Recursive advanced delegation is not allowed"}

    token = _delegation_active.set(True)
    try:
        cfg = load_config() if cfg is None else cfg
        client = OpenAI() if client is None else client
        completion = None
        approval = None
        for event in ResponsesOrchestrator(client, cfg).stream_turn(prompt.strip()):
            if event["type"] == "completion":
                completion = event
            elif event["type"] == "approval.required":
                approval = {
                    key: value
                    for key, value in event.items()
                    if not key.startswith("_")
                }
        if approval:
            return {
                "error": (
                    "This delegated task requires explicit owner approval in "
                    "Full Power mode."
                ),
                "approval": approval,
            }
        if completion is None:
            raise RuntimeError("Delegated Responses turn did not complete")
        detailed_text = completion["text"]
        truncated = len(detailed_text) > MAX_DELEGATION_DETAIL_LENGTH
        if truncated:
            detailed_text = detailed_text[:MAX_DELEGATION_DETAIL_LENGTH]
        return {
            "summary": _voice_safe_summary(completion["text"]),
            "details": {
                "text": detailed_text,
                "text_truncated": truncated,
                "citations": completion["citations"][:MAX_DELEGATION_CITATIONS],
                "sources": completion["sources"][:MAX_DELEGATION_CITATIONS],
            },
        }
    finally:
        _delegation_active.reset(token)


def dispatch_realtime_function(name, arguments, *, client=None, cfg=None):
    if name == ADVANCED_DELEGATION_TOOL["name"]:
        return delegate_advanced_task(
            arguments.get("prompt") if isinstance(arguments, dict) else None,
            client=client,
            cfg=cfg,
        )
    from realtime_config import realtime_tool_schemas

    allowed = {
        tool.get("name")
        for tool in realtime_tool_schemas()
        if isinstance(tool, dict) and tool.get("type") == "function"
    }
    if name not in allowed:
        raise ValueError(f"Tool '{name}' is not available in Realtime mode.")
    return redact_server_paths(dispatch_local_function(name, arguments))
