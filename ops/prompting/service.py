"""Versioned prompt perfecting for PJ's text and Full Power surfaces."""

import hashlib
import json
import re

from openai import APIError
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from ops.shared.interfaces import ResponsesProvider
from ops.shared.providers import OpenAIResponsesProvider


PROMPT_PERFECTING_VERSION = "2.0"
DEFAULT_MAX_INPUT_CHARS = 20000
DEFAULT_MAX_OUTPUT_CHARS = 30000
_URL_RE = re.compile(r"https?://[^\s<>'\"]+")
_DATE_RE = re.compile(
    r"\b(?:\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{2,4}|"
    r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|"
    r"Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|"
    r"Nov(?:ember)?|Dec(?:ember)?)\s+\d{1,2}(?:,\s*\d{4})?)\b",
    re.IGNORECASE,
)
_QUANTITY_RE = re.compile(
    r"(?<![\w.])(?:[$€£]\s*)?\d+(?:,\d{3})*(?:\.\d+)?"
    r"(?:\s*(?:%|percent|seconds?|minutes?|hours?|days?|weeks?|months?|"
    r"years?|bytes?|KB|MB|GB|TB|px|items?|records?|files?|slides?))?",
    re.IGNORECASE,
)
_QUOTED_RE = re.compile(r"""(["'])(?:(?!\1).){1,500}\1""")
_FENCED_CODE_RE = re.compile(r"```[\s\S]*?```")
_INLINE_CODE_RE = re.compile(r"`[^`\n]+`")
_FLAG_RE = re.compile(r"(?<!\w)--[A-Za-z0-9][A-Za-z0-9-]*")
_IDENTIFIER_RE = re.compile(
    r"(?<!\w)(?:[A-Za-z0-9]+(?:[_:/.-][A-Za-z0-9]+)+|"
    r"(?=[A-Za-z0-9]*[A-Za-z])(?=[A-Za-z0-9]*\d)[A-Za-z0-9]+)(?!\w)"
)
_REPAIRABLE_LITERAL_CATEGORIES = frozenset({"URL", "quoted", "code", "identifier", "flag"})


class PromptPerfectingError(RuntimeError):
    """Raised when a required perfecting step cannot produce a safe prompt."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class PromptPerfectingSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    enabled: bool
    model: str
    timeout_seconds: int
    reasoning_effort: str
    surfaces: frozenset[str]
    max_input_chars: int
    max_output_chars: int


class PromptPerfectingPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    refined_prompt: str = Field(min_length=1, max_length=50000)
    intent_summary: str = Field(min_length=1, max_length=4000)
    constraints_preserved: list[str] = Field(max_length=25)

    @field_validator("refined_prompt", "intent_summary", mode="after")
    @classmethod
    def _non_whitespace(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("field may not contain only whitespace")
        return value

    @field_validator("constraints_preserved", mode="after")
    @classmethod
    def _valid_constraints(cls, value: list[str]) -> list[str]:
        if any(not item.strip() for item in value):
            raise ValueError("constraints may not contain empty strings")
        return value


_SCHEMA = PromptPerfectingPayload.model_json_schema()


def settings_from_config(cfg: dict) -> PromptPerfectingSettings:
    raw = cfg.get("prompt_perfecting") or {}
    if not isinstance(raw, dict):
        raise PromptPerfectingError(
            "invalid_prompt_perfecting_config",
            "prompt_perfecting configuration must be an object.",
        )
    surfaces = raw.get("surfaces", ["cli", "full_power", "full_power_voice"])
    if not isinstance(surfaces, list) or not all(
        isinstance(surface, str) and surface for surface in surfaces
    ):
        raise PromptPerfectingError(
            "invalid_prompt_perfecting_config",
            "prompt_perfecting.surfaces must be a string array.",
        )
    enabled = raw.get("enabled", False)
    model = raw.get("model") or cfg.get("model") or ""
    effort = raw.get("reasoning_effort") or "low"
    numbers = {
        "timeout_seconds": raw.get("timeout_seconds", 30),
        "max_input_chars": raw.get("max_input_chars", DEFAULT_MAX_INPUT_CHARS),
        "max_output_chars": raw.get("max_output_chars", DEFAULT_MAX_OUTPUT_CHARS),
    }
    if (
        not isinstance(enabled, bool)
        or not isinstance(model, str)
        or not isinstance(effort, str)
        or effort not in {"none", "minimal", "low", "medium", "high", "xhigh"}
        or any(isinstance(value, bool) or not isinstance(value, int) for value in numbers.values())
    ):
        raise PromptPerfectingError(
            "invalid_prompt_perfecting_config",
            "prompt_perfecting contains invalid field types or values.",
        )
    try:
        return PromptPerfectingSettings(
            enabled=enabled,
            model=model.strip(),
            timeout_seconds=max(5, min(numbers["timeout_seconds"], 120)),
            reasoning_effort=effort,
            surfaces=frozenset(surfaces),
            max_input_chars=max(100, min(numbers["max_input_chars"], 50000)),
            max_output_chars=max(100, min(numbers["max_output_chars"], 50000)),
        )
    except ValidationError as exc:
        raise PromptPerfectingError(
            "invalid_prompt_perfecting_config",
            "prompt_perfecting configuration is invalid.",
        ) from exc


def _value(value, key, default=None):
    if isinstance(value, dict):
        return value.get(key, default)
    return getattr(value, key, default)


def _response_text(response) -> str:
    output_text = _value(response, "output_text")
    if isinstance(output_text, str) and output_text.strip():
        return output_text
    for item in _value(response, "output", []) or []:
        if _value(item, "type") != "message":
            continue
        for part in _value(item, "content", []) or []:
            if _value(part, "type") in {"output_text", "text"}:
                text = _value(part, "text", "")
                if isinstance(text, str) and text.strip():
                    return text
    return ""


def _normalize_prompt(value: str) -> str:
    return value.strip().replace("\r\n", "\n").replace("\r", "\n")


def _validate_result(original: str, payload: dict, max_output_chars: int) -> dict:
    try:
        contract = PromptPerfectingPayload.model_validate(payload, strict=True)
    except ValidationError as exc:
        raise PromptPerfectingError(
            "invalid_prompt_perfecting_output",
            "Prompt perfecting returned an invalid structured result.",
        ) from exc
    refined = contract.refined_prompt
    if len(refined) > max_output_chars:
        raise PromptPerfectingError(
            "invalid_prompt_perfecting_output",
            "Prompt perfecting returned malformed or oversized fields.",
        )
    normalized = _normalize_prompt(refined)
    if original.casefold() in {"yes", "no", "approve", "reject", "stop"} and (
        normalized.casefold() != original.casefold()
    ):
        raise PromptPerfectingError(
            "prompt_intent_changed",
            "Prompt perfecting changed a control response.",
        )
    missing = _missing_preserved_literal_values(original, normalized)
    if missing:
        if set(missing).issubset(_REPAIRABLE_LITERAL_CATEGORIES):
            normalized = _repair_preserved_literals(original, normalized, missing)
            if len(normalized) > max_output_chars:
                raise PromptPerfectingError(
                    "invalid_prompt_perfecting_output",
                    "Prompt perfecting repair exceeded the configured output limit.",
                )
            missing = _missing_preserved_literal_values(original, normalized)
    if missing:
        categories = ", ".join(sorted(missing))
        raise PromptPerfectingError(
            "prompt_intent_changed",
            "Prompt perfecting did not preserve exact "
            f"{categories} literals from the original request.",
        )
    return {
        "refined_prompt": normalized,
        "intent_summary": contract.intent_summary.strip(),
        "constraints_preserved": [item.strip() for item in contract.constraints_preserved],
    }


def _literal_extractors():
    return {
        "URL": lambda text: {item.rstrip(".,);]") for item in _URL_RE.findall(text)},
        "date": lambda text: set(_DATE_RE.findall(text)),
        "quantity": lambda text: set(_QUANTITY_RE.findall(text)),
        "quoted": lambda text: {match.group(0) for match in _QUOTED_RE.finditer(text)},
        "code": lambda text: set(_FENCED_CODE_RE.findall(text) + _INLINE_CODE_RE.findall(text)),
        "flag": lambda text: set(_FLAG_RE.findall(text)),
        "identifier": lambda text: set(_IDENTIFIER_RE.findall(text)),
    }


def _preserved_literals(text: str) -> dict[str, set[str]]:
    return {
        category: {value for value in extract(text) if value}
        for category, extract in _literal_extractors().items()
    }


def _missing_preserved_literal_values(original: str, refined: str) -> dict[str, list[str]]:
    missing = {}
    for category, values in _preserved_literals(original).items():
        absent = sorted(value for value in values if value not in refined)
        if absent:
            missing[category] = absent
    return missing


def _missing_preserved_literals(original: str, refined: str) -> set[str]:
    return set(_missing_preserved_literal_values(original, refined))


def _repair_preserved_literals(
    original: str,
    refined: str,
    missing: dict[str, list[str]] | None = None,
) -> str:
    missing = missing or _missing_preserved_literal_values(original, refined)
    if not missing:
        return refined
    lines = [refined.rstrip(), "", "Immutable literals to preserve exactly:"]
    seen = set()
    for category in sorted(missing):
        for value in missing[category]:
            if value in seen:
                continue
            seen.add(value)
            lines.append(f"- {value}")
    return "\n".join(lines).strip()


def _unchanged_result(original: str, surface: str, summary: str) -> dict:
    digest = hashlib.sha256(original.encode()).hexdigest()
    return {
        "original_prompt": original,
        "refined_prompt": original,
        "changed": False,
        "version": PROMPT_PERFECTING_VERSION,
        "surface": surface,
        "intent_summary": summary,
        "constraints_preserved": [],
        "original_sha256": digest,
        "refined_sha256": digest,
    }


def perfect_prompt(
    client,
    cfg: dict,
    prompt: str,
    *,
    surface: str,
    required: bool = True,
    provider: ResponsesProvider | None = None,
) -> dict:
    settings = settings_from_config(cfg)
    original = _normalize_prompt(prompt if isinstance(prompt, str) else "")
    if not original:
        raise PromptPerfectingError("invalid_prompt", "Prompt must be a non-empty string.")
    if len(original) > settings.max_input_chars:
        raise PromptPerfectingError(
            "prompt_too_large",
            f"Prompt exceeds {settings.max_input_chars} characters.",
        )
    if not settings.enabled or surface not in settings.surfaces:
        if required:
            raise PromptPerfectingError(
                "prompt_perfecting_unavailable",
                f"Prompt perfecting is not enabled for {surface}.",
            )
        return _unchanged_result(
            original,
            surface,
            "Prompt perfecting disabled for this surface.",
        )
    try:
        if not settings.model:
            raise PromptPerfectingError(
                "invalid_prompt_perfecting_config",
                "Prompt perfecting model is not configured.",
            )
        instructions = (
            "Perfect the user's prompt into a complete brief with four parts, "
            "keeping only the parts the request actually needs:\n"
            "- Goal: state the result to produce, not a list of steps. Name the "
            "audience or purpose when it changes the output.\n"
            "- Context: carry over every source, file, tool, or fact the user "
            "referenced, and note where the executor should look. Never invent "
            "sources or details; mark essential-but-unstated dimensions as "
            "open-ended rather than guessing.\n"
            "- Output: specify format, length, structure, and level of detail. "
            "Request tables or headers when they would organize the result.\n"
            "- Boundaries: preserve every stated constraint verbatim - what must "
            "stay unchanged, what to avoid, approvals required before acting - "
            "and add a final self-check when the task is consequential.\n"
            "Preserve exact URL, code, identifier, quoted, date, and quantity "
            "literals character-for-character. Preserve the exact intent, "
            "requested format, named entities, facts, constraints, dates, "
            "quantities, permissions, scope, and uncertainty. Write in the "
            "user's first person. Do not answer the prompt. Do not invent "
            "requirements. Keep explicit approval, refusal, stop, or "
            "confirmation language unchanged. Return only the requested "
            "structured output."
        )
        try:
            provider = provider or OpenAIResponsesProvider(client)
            response = provider.create_response(
                model=settings.model,
                instructions=instructions,
                input=original,
                reasoning={"effort": settings.reasoning_effort},
                text={
                    "format": {
                        "type": "json_schema",
                        "name": "pj_prompt_perfecting",
                        "schema": _SCHEMA,
                        "strict": True,
                    }
                },
                max_output_tokens=4000,
                timeout=settings.timeout_seconds,
            )
        except APIError as exc:
            raise PromptPerfectingError(
                "prompt_perfecting_failed",
                "Prompt perfecting could not be completed.",
            ) from exc
        raw = _response_text(response)
        try:
            payload = json.loads(raw)
        except (TypeError, json.JSONDecodeError) as exc:
            raise PromptPerfectingError(
                "invalid_prompt_perfecting_output",
                "Prompt perfecting did not return valid structured JSON.",
            ) from exc
        result = _validate_result(
            original,
            payload,
            settings.max_output_chars,
        )
    except PromptPerfectingError:
        if required:
            raise
        return _unchanged_result(
            original,
            surface,
            "Prompt perfecting failed; the original prompt was retained.",
        )
    result.update(
        {
            "original_prompt": original,
            "changed": result["refined_prompt"] != original,
            "version": PROMPT_PERFECTING_VERSION,
            "surface": surface,
            "original_sha256": hashlib.sha256(original.encode()).hexdigest(),
            "refined_sha256": hashlib.sha256(result["refined_prompt"].encode()).hexdigest(),
        }
    )
    return result


def public_result(result: dict) -> dict:
    return {
        key: result[key]
        for key in (
            "refined_prompt",
            "changed",
            "version",
            "surface",
            "intent_summary",
            "constraints_preserved",
            "original_sha256",
            "refined_sha256",
        )
        if key in result
    }
