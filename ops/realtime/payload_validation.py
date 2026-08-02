"""JSON Schema contracts for realtime boundary payloads."""

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

MAX_MESSAGE_LENGTH = 100_000


class RealtimePayloadValidationError(ValueError):
    """Raised when a realtime payload does not match its boundary schema."""

    def __init__(self, schema_name, detail, *, path=(), validator=None):
        super().__init__(detail)
        self.schema_name = schema_name
        self.detail = detail
        self.path = tuple(path)
        self.validator = validator


_STRING_OR_NULL = {"type": ["string", "null"]}
_ID_OR_NULL = {"type": ["string", "null"], "maxLength": 200}

INBOUND_SCHEMAS = {
    "session.create": {
        "type": "object",
        "properties": {
            "title": {"type": "string", "maxLength": 120},
            "channel": {"enum": ["web", "realtime"]},
        },
        "additionalProperties": False,
    },
    "session.resume": {
        "type": "object",
        "maxProperties": 0,
        "additionalProperties": False,
    },
    "realtime.message": {
        "type": "object",
        "properties": {
            "external_id": {"type": "string", "minLength": 1, "maxLength": 200},
            "role": {"enum": ["user", "assistant"]},
            "content": {
                "type": "string",
                "minLength": 1,
                "maxLength": MAX_MESSAGE_LENGTH,
            },
            "source": {
                "enum": ["typed", "input_audio", "output_audio", "output_text"],
            },
            "response_id": _ID_OR_NULL,
            "status": {"enum": ["completed", "interrupted", "failed"]},
            "playback_ms": {
                "type": ["integer", "null"],
                "minimum": 0,
                "maximum": 86400000,
            },
            "metadata": {
                "type": ["object", "null"],
                "properties": {
                    "prompt_perfecting_version": {
                        "type": "string",
                        "maxLength": 100,
                    },
                    "refined_prompt": {
                        "type": ["string", "null"],
                        "maxLength": 4000,
                    },
                    "refined_sha256": {
                        "type": ["string", "null"],
                        "pattern": "^[a-f0-9]{64}$",
                    },
                    "changed": {"type": "boolean"},
                    "refined_prompt_truncated": {"type": "boolean"},
                },
                "additionalProperties": False,
            },
        },
        "required": ["external_id", "role", "content", "source", "status"],
        "additionalProperties": False,
    },
    "responses.turn": {
        "type": "object",
        "properties": {
            "message": {
                "type": "string",
                "minLength": 1,
                "maxLength": MAX_MESSAGE_LENGTH,
            },
            "structured_output": {"type": "object"},
        },
        "required": ["message"],
        "additionalProperties": False,
    },
    "approval.decision": {
        "type": "object",
        "properties": {"approve": {"type": "boolean"}},
        "required": ["approve"],
        "additionalProperties": False,
    },
}

OUTBOUND_PAYLOAD_SCHEMAS = {
    "session.response": {
        "type": "object",
        "properties": {
            "ok": {"const": True},
            "session": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "minLength": 8, "maxLength": 128},
                },
                "required": ["id"],
            },
        },
        "required": ["ok", "session"],
    },
    "realtime.message.response": {
        "type": "object",
        "properties": {
            "ok": {"const": True},
            "message": {
                "type": "object",
                "properties": {
                    "external_id": {"type": "string", "minLength": 1},
                    "role": {"enum": ["user", "assistant"]},
                    "content": {"type": "string", "minLength": 1},
                    "source": {"type": "string", "minLength": 1},
                    "status": {"enum": ["completed", "interrupted", "failed"]},
                },
                "required": ["external_id", "role", "content", "source", "status"],
            },
        },
        "required": ["ok", "message"],
    },
}

_EVENT_BASE = {
    "type": "object",
    "properties": {"type": {"type": "string", "minLength": 1}},
    "required": ["type"],
}

OUTBOUND_EVENT_SCHEMAS = {
    "session": {
        **_EVENT_BASE,
        "properties": {
            "type": {"const": "session"},
            "session_id": {"type": "string", "minLength": 8},
            "request_id": {"type": "string", "minLength": 1},
        },
        "required": ["type", "session_id", "request_id"],
    },
    "prompt.perfected": {
        **_EVENT_BASE,
        "properties": {"type": {"const": "prompt.perfected"}},
    },
    "text.delta": {
        **_EVENT_BASE,
        "properties": {
            "type": {"const": "text.delta"},
            "delta": {"type": "string"},
        },
        "required": ["type", "delta"],
    },
    "tool.call": {
        **_EVENT_BASE,
        "properties": {
            "type": {"const": "tool.call"},
            "call_id": _STRING_OR_NULL,
            "name": _STRING_OR_NULL,
            "tool_type": {"type": "string", "minLength": 1},
        },
        "required": ["type", "call_id", "tool_type"],
    },
    "tool.call.delta": {
        **_EVENT_BASE,
        "properties": {
            "type": {"const": "tool.call.delta"},
            "call_id": _STRING_OR_NULL,
            "delta": {"type": "string"},
        },
        "required": ["type", "call_id", "delta"],
    },
    "tool.result": {
        **_EVENT_BASE,
        "properties": {
            "type": {"const": "tool.result"},
            "call_id": _STRING_OR_NULL,
        },
        "required": ["type", "call_id"],
    },
    "artifact.ready": {
        **_EVENT_BASE,
        "properties": {
            "type": {"const": "artifact.ready"},
            "artifact_id": {"type": "string", "pattern": "^ART-[a-f0-9]{32}$"},
            "filename": {"type": "string", "minLength": 1},
            "download_url": {"type": "string", "minLength": 1},
        },
        "required": ["type", "artifact_id", "filename", "download_url"],
    },
    "deliverable.incomplete": {
        **_EVENT_BASE,
        "properties": {
            "type": {"const": "deliverable.incomplete"},
            "requested_format": {"type": "string", "minLength": 1},
        },
        "required": ["type", "requested_format"],
    },
    "citation": {
        **_EVENT_BASE,
        "properties": {
            "type": {"const": "citation"},
            "citation": {"type": "object"},
        },
        "required": ["type", "citation"],
    },
    "source": {
        **_EVENT_BASE,
        "properties": {
            "type": {"const": "source"},
            "source": {"type": "object"},
        },
        "required": ["type", "source"],
    },
    "approval.required": {
        **_EVENT_BASE,
        "properties": {
            "type": {"const": "approval.required"},
            "approval_id": {"type": "string", "minLength": 8},
            "approval_kind": {"type": "string", "minLength": 1},
            "session_id": {"type": "string", "minLength": 8},
        },
        "required": ["type", "approval_id", "approval_kind", "session_id"],
    },
    "approval.executing": {
        **_EVENT_BASE,
        "properties": {
            "type": {"const": "approval.executing"},
            "approval_id": {"type": "string", "minLength": 8},
            "approval_kind": {"type": "string", "minLength": 1},
            "approved": {"type": "boolean"},
        },
        "required": ["type", "approval_id", "approval_kind", "approved"],
    },
    "approval.resolved": {
        **_EVENT_BASE,
        "properties": {
            "type": {"const": "approval.resolved"},
            "approval_id": {"type": "string", "minLength": 8},
            "approval_kind": {"type": "string", "minLength": 1},
            "approved": {"type": "boolean"},
        },
        "required": ["type", "approval_id", "approval_kind", "approved"],
    },
    "completion": {
        **_EVENT_BASE,
        "properties": {
            "type": {"const": "completion"},
            "text": {"type": "string"},
            "session_id": {"type": "string", "minLength": 8},
        },
        "required": ["type", "text", "session_id"],
    },
    "error": {
        **_EVENT_BASE,
        "properties": {
            "type": {"const": "error"},
            "error": {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "minLength": 1},
                    "message": {"type": "string", "minLength": 1},
                    "request_id": {"type": "string", "minLength": 1},
                    "detail": {"type": ["string", "null"]},
                },
                "required": ["code", "message", "request_id", "detail"],
            },
        },
        "required": ["type", "error"],
    },
}

_INBOUND_VALIDATORS = {
    name: Draft202012Validator(schema) for name, schema in INBOUND_SCHEMAS.items()
}
_OUTBOUND_PAYLOAD_VALIDATORS = {
    name: Draft202012Validator(schema) for name, schema in OUTBOUND_PAYLOAD_SCHEMAS.items()
}
_OUTBOUND_EVENT_VALIDATORS = {
    name: Draft202012Validator(schema) for name, schema in OUTBOUND_EVENT_SCHEMAS.items()
}


def _error_detail(error: ValidationError) -> str:
    path = "$"
    for part in error.absolute_path:
        path += f"[{part}]" if isinstance(part, int) else f".{part}"
    if error.validator == "required" and isinstance(error.instance, dict):
        missing = next(
            (name for name in error.validator_value if name not in error.instance),
            None,
        )
        if missing:
            path += f".{missing}"
    return f"{path}: {error.message}"


def _validate(validators, schema_name, payload):
    validator = validators.get(schema_name)
    if validator is None:
        raise RealtimePayloadValidationError(schema_name, "schema is not registered")
    error = next(iter(validator.iter_errors(payload)), None)
    if error is not None:
        raise RealtimePayloadValidationError(
            schema_name,
            _error_detail(error),
            path=error.absolute_path,
            validator=error.validator,
        )
    return payload


def validate_inbound_payload(schema_name, payload):
    return _validate(_INBOUND_VALIDATORS, schema_name, payload)


def validate_outbound_payload(schema_name, payload):
    return _validate(_OUTBOUND_PAYLOAD_VALIDATORS, schema_name, payload)


def validate_outbound_event(event):
    if not isinstance(event, dict):
        raise RealtimePayloadValidationError("event", "$: expected an object")
    event_type = event.get("type")
    if not isinstance(event_type, str) or not event_type:
        raise RealtimePayloadValidationError("event", "$.type: is required")
    return _validate(_OUTBOUND_EVENT_VALIDATORS, event_type, event)
