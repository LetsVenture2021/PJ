#!/usr/bin/env python3
"""Unified, profile-aware runtime configuration for PJ."""

from __future__ import annotations

import copy
import json
import os
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


BASE_DIR = Path(__file__).resolve().parent
PROFILES = {"dev", "staging", "prod"}
PROFILE_REQUIRED_ENV = {
    "dev": (),
    "staging": ("OPENAI_API_KEY",),
    "prod": ("OPENAI_API_KEY", "PJ_OWNER_EMAILS", "PJ_TOOL_BRIDGE_TOKEN"),
}
POLICY_MODES = {"allow", "deny", "approval"}
WORKER_ENV_NAMES = (
    "PJ_ALLOWED_ORIGINS",
    "CF_ACCESS_TEAM_DOMAIN",
    "CF_ACCESS_AUD",
    "PJ_TOOL_BRIDGE_URL",
    "PJ_TOOL_SCHEMAS_URL",
    "PJ_RESPONSES_BRIDGE_URL",
    "CF_ACCESS_CERT_CACHE_TTL_MS",
)


class ConfigError(ValueError):
    """Raised when runtime configuration cannot be loaded or validated."""


@dataclass(frozen=True)
class RuntimeConfig:
    profile: str
    assistant: dict[str, Any]
    mcp_servers: list[dict[str, Any]]
    tool_policy: dict[str, Any]
    realtime: dict[str, Any]
    worker: dict[str, Any]
    conversation_routing: ConversationRoutingSettings
    providers: dict[str, Any]
    execution_modes: dict[str, Any]
    collaboration: dict[str, Any]
    sources: dict[str, Path]
 

@dataclass(frozen=True)
class ConversationRoutingSettings:
    enabled_routes: tuple[str, ...]
    timeout_budgets_ms: dict[str, int]
    maximum_estimated_spend_usd: float
    safe_fallback_order: tuple[str, ...]


EXECUTION_MODE_FIELDS = {"capability", "latency", "tools", "spend", "privacy"}


def _validate_provider_routing(sections: dict[str, Any]) -> None:
    providers = sections.get("providers")
    modes = sections.get("execution_modes")
    if not isinstance(providers, dict) or not providers:
        raise ConfigError("providers must be a non-empty object")
    for name, provider in providers.items():
        if not isinstance(provider, dict):
            raise ConfigError(f"providers.{name} must be an object")
        models = provider.get("models")
        if not isinstance(models, dict) or not models:
            raise ConfigError(f"providers.{name}.models must be a non-empty object")
        if provider.get("required") and not provider.get("available", False):
            raise ConfigError(f"required provider {name!r} is unavailable")
    if not isinstance(modes, dict) or not modes:
        raise ConfigError("execution_modes must be a non-empty object")
    for mode, policy in modes.items():
        if not isinstance(policy, dict) or not EXECUTION_MODE_FIELDS <= policy.keys():
            raise ConfigError(
                f"execution_modes.{mode} must define capability, latency, tools, spend, and privacy"
            )
        fallbacks = policy.get("fallbacks", [])
        if not isinstance(fallbacks, list):
            raise ConfigError(f"execution_modes.{mode}.fallbacks must be a list")
        for fallback in fallbacks:
            if not isinstance(fallback, dict):
                raise ConfigError(f"execution_modes.{mode} fallback must be an object")
            provider = providers.get(fallback.get("provider"))
            if provider is None or fallback.get("model") not in provider.get("models", {}):
                raise ConfigError(f"execution_modes.{mode} has an unknown provider/model fallback")
            if policy["privacy"] == "local" and not provider.get("local", False):
                raise ConfigError(
                    f"execution_modes.{mode} cannot fall back to a non-local provider"
                )


def _read_json(path: Path, *, default: Any = None) -> Any:
    if not path.is_file():
        if default is not None:
            return copy.deepcopy(default)
        raise ConfigError(f"Required configuration file not found: {path}")
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise ConfigError(
            f"Invalid JSON in {path}: line {exc.lineno}, column {exc.colno}: {exc.msg}"
        ) from exc


def _read_json_env(name: str, environ: Mapping[str, str], expected_type: type) -> Any:
    raw = environ.get(name)
    if raw is None:
        return None
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{name} must contain valid JSON: {exc.msg}") from exc
    if not isinstance(value, expected_type):
        raise ConfigError(f"{name} must contain a JSON {expected_type.__name__}")
    return value


def _deep_merge(target: dict[str, Any], updates: Mapping[str, Any]) -> None:
    for key, value in updates.items():
        if isinstance(value, Mapping) and isinstance(target.get(key), dict):
            _deep_merge(target[key], value)
        else:
            target[key] = copy.deepcopy(value)


def _parse_env_value(raw: str) -> Any:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def _apply_nested_env_overrides(sections: dict[str, Any], environ: Mapping[str, str]) -> None:
    prefix = "PJ_CONFIG__"
    for name in sorted(environ):
        if not name.startswith(prefix):
            continue
        path = [part.lower() for part in name[len(prefix) :].split("__") if part]
        if len(path) < 2 or path[0] not in sections:
            raise ConfigError(
                f"{name} must target a section and field, for example PJ_CONFIG__ASSISTANT__MODEL"
            )
        current = sections[path[0]]
        for part in path[1:-1]:
            if not isinstance(current, dict):
                raise ConfigError(f"{name} traverses a non-object configuration value")
            current = current.setdefault(part, {})
        if not isinstance(current, dict):
            raise ConfigError(f"{name} targets a non-object configuration value")
        current[path[-1]] = _parse_env_value(environ[name])


def _select_profile(profile: str | None, environ: Mapping[str, str]) -> str:
    selected = (profile or environ.get("PJ_PROFILE") or "dev").strip().lower()
    if selected not in PROFILES:
        choices = ", ".join(sorted(PROFILES))
        raise ConfigError(f"Invalid PJ profile {selected!r}; expected one of: {choices}")
    return selected


def _apply_profile_overlay(config: dict[str, Any], profile: str, *, source: Path) -> dict[str, Any]:
    profiles = config.pop("profiles", {})
    if not isinstance(profiles, dict):
        raise ConfigError(f"{source}: profiles must be an object")
    overlay = profiles.get(profile, {})
    if not isinstance(overlay, dict):
        raise ConfigError(f"{source}: profiles.{profile} must be an object")
    _deep_merge(config, overlay)
    return config


def _normalize_assistant(config: Any, base_dir: Path, source: Path) -> dict[str, Any]:
    if not isinstance(config, dict):
        raise ConfigError(f"{source} must contain a JSON object")
    for key in ("name", "model"):
        if not isinstance(config.get(key), str) or not config[key].strip():
            raise ConfigError(f"{source} must define a non-empty {key}")

    instruction_files = config.get("instruction_files")
    if instruction_files is None:
        instruction_files = [config.get("instructions_file")]
    if (
        not isinstance(instruction_files, list)
        or not instruction_files
        or any(not isinstance(item, str) or not item.strip() for item in instruction_files)
    ):
        raise ConfigError(f"{source} must define instruction_files or legacy instructions_file")
    instruction_files = list(dict.fromkeys(item.strip() for item in instruction_files))
    instruction_parts = []
    resolved_base = base_dir.resolve()
    for filename in instruction_files:
        path = (base_dir / filename).resolve()
        try:
            path.relative_to(resolved_base)
        except ValueError as exc:
            raise ConfigError("Instruction files must remain within the project") from exc
        if not path.is_file():
            raise ConfigError(f"Instruction file not found: {path}")
        instruction_parts.append(path.read_text())
    config["instructions"] = "\n\n".join(instruction_parts)
    config["instructions_source"] = instruction_files[0]
    config["instruction_files"] = instruction_files
    config.setdefault("instructions_file", instruction_files[0])

    vector_store_ids = config.get("vector_store_ids")
    if vector_store_ids is None:
        vector_store_ids = [config["vector_store_id"]] if config.get("vector_store_id") else []
    if not isinstance(vector_store_ids, list) or any(
        not isinstance(item, str) or not item.strip() for item in vector_store_ids
    ):
        raise ConfigError("vector_store_ids must be a list of non-empty strings")
    vector_store_ids = list(dict.fromkeys(item.strip() for item in vector_store_ids))
    config["vector_store_ids"] = vector_store_ids
    if vector_store_ids:
        config.setdefault("vector_store_id", vector_store_ids[0])
    return config


def _normalize_mcp_servers(servers: Any, source: Path) -> list[dict[str, Any]]:
    if not isinstance(servers, list):
        raise ConfigError(f"{source} must contain a JSON list")
    labels: set[str] = set()
    normalized = []
    for index, server in enumerate(servers):
        if not isinstance(server, dict):
            raise ConfigError(f"{source}: server {index} must be an object")
        label = server.get("label")
        url = server.get("url")
        if not isinstance(label, str) or not label.strip():
            raise ConfigError(f"{source}: server {index} must define a non-empty label")
        if label in labels:
            raise ConfigError(f"{source}: duplicate MCP server label {label!r}")
        url_is_environment_reference = isinstance(url, str) and bool(
            re.fullmatch(r"\$(?:[A-Za-z_][A-Za-z0-9_]*|\{[A-Za-z_][A-Za-z0-9_]*\})", url)
        )
        if not isinstance(url, str) or not (
            url.startswith(("http://", "https://")) or url_is_environment_reference
        ):
            raise ConfigError(f"{source}: MCP server {label!r} must define an HTTP(S) URL")
        labels.add(label)
        normalized.append(copy.deepcopy(server))
    return normalized


def load_tool_policy(
    path: str | Path | None = None, *, environ: Mapping[str, str] | None = None
) -> dict[str, Any]:
    environ = os.environ if environ is None else environ
    source = Path(path or environ.get("PJ_TOOL_POLICY_PATH") or (BASE_DIR / "tool_policy.json"))
    policy = _read_json(source, default={"default": "allow", "tools": {}})
    override = _read_json_env("PJ_TOOL_POLICY_JSON", environ, dict)
    if override is not None:
        _deep_merge(policy, override)
    if not isinstance(policy, dict):
        raise ConfigError(f"{source} must contain a JSON object")
    default_mode = policy.get("default", "allow")
    tools = policy.get("tools", {})
    if default_mode not in POLICY_MODES:
        raise ConfigError(f"{source}: default must be allow, deny, or approval")
    if not isinstance(tools, dict):
        raise ConfigError(f"{source}: tools must be an object")
    for name, mode in tools.items():
        if not isinstance(name, str) or mode not in POLICY_MODES:
            raise ConfigError(
                f"{source}: tool policy entries must map names to allow, deny, or approval"
            )
    result = {"default": default_mode, "tools": copy.deepcopy(tools)}
    for name in _split_csv(environ.get("PJ_DENY_TOOLS", "")):
        result["tools"][name] = "deny"
    for name in _split_csv(environ.get("PJ_APPROVAL_TOOLS", "")):
        if result["tools"].get(name) != "deny":
            result["tools"][name] = "approval"
    return result


def load_mcp_config(
    path: str | Path | None = None, *, environ: Mapping[str, str] | None = None
) -> list[dict[str, Any]]:
    environ = os.environ if environ is None else environ
    source = Path(path or environ.get("PJ_MCP_SERVERS_PATH") or (BASE_DIR / "mcp_servers.json"))
    servers = _read_json(source, default=[])
    override = _read_json_env("PJ_MCP_SERVERS_JSON", environ, list)
    if override is not None:
        servers = override
    return _normalize_mcp_servers(servers, source)


def _split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _load_worker_config(path: Path, profile: str) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        with path.open("rb") as handle:
            manifest = tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"Invalid TOML in {path}: {exc}") from exc
    worker = copy.deepcopy(manifest.get("vars", {}))
    if not isinstance(worker, dict):
        raise ConfigError(f"{path}: vars must be a TOML table")
    profile_vars = (
        manifest.get("env", {}).get(profile, {}).get("vars", {})
        if isinstance(manifest.get("env", {}), dict)
        else {}
    )
    if not isinstance(profile_vars, dict):
        raise ConfigError(f"{path}: env.{profile}.vars must be a TOML table")
    _deep_merge(worker, profile_vars)
    return worker


def _validate_required_env(profile: str, environ: Mapping[str, str], *, extra: str = "") -> None:
    required = list(PROFILE_REQUIRED_ENV[profile])
    required.extend(_split_csv(extra))
    missing = sorted(set(name for name in required if not str(environ.get(name, "")).strip()))
    if missing:
        raise ConfigError(
            f"Profile {profile!r} requires environment variable(s): " + ", ".join(missing)
        )


def load_runtime_config(
    base_dir: str | Path = BASE_DIR,
    *,
    environ: Mapping[str, str] | None = None,
    profile: str | None = None,
    validate_required: bool = True,
) -> RuntimeConfig:
    """Load all PJ configuration sources and apply profile/environment overrides."""
    base_dir = Path(base_dir)
    environ = os.environ if environ is None else environ
    selected = _select_profile(profile, environ)

    assistant_path = Path(environ.get("PJ_ASSISTANT_CONFIG_PATH", base_dir / "config.json"))
    mcp_path = Path(environ.get("PJ_MCP_SERVERS_PATH", base_dir / "mcp_servers.json"))
    policy_path = Path(environ.get("PJ_TOOL_POLICY_PATH", base_dir / "tool_policy.json"))
    worker_path = Path(
        environ.get(
            "PJ_WRANGLER_CONFIG_PATH",
            base_dir
            / (
                "wrangler.toml"
                if (base_dir / "wrangler.toml").is_file()
                else "wrangler.toml.example"
            ),
        )
    )

    assistant = _read_json(assistant_path)
    if not isinstance(assistant, dict):
        raise ConfigError(f"{assistant_path} must contain a JSON object")
    assistant = _apply_profile_overlay(copy.deepcopy(assistant), selected, source=assistant_path)
    mcp_servers = load_mcp_config(mcp_path, environ=environ)
    tool_policy = load_tool_policy(policy_path, environ=environ)
    sections: dict[str, Any] = {
        "assistant": assistant,
        "mcp_servers": mcp_servers,
        "tool_policy": tool_policy,
        "realtime": {
            "model": "gpt-realtime-2.1",
            "voice": "marin",
        },
        "worker": _load_worker_config(worker_path, selected),
        "conversation_routing": assistant.pop(
            "conversation_routing",
            {
                "enabled_routes": ["realtime", "responses", "local", "hosted", "delegated"],
                "timeout_budgets_ms": {
                    "realtime": 12000,
                    "responses": 30000,
                    "local": 5000,
                    "hosted": 30000,
                    "delegated": 300000,
                },
                "maximum_estimated_spend_usd": 5.0,
                "safe_fallback_order": ["local", "responses", "realtime"],
            },
        ),
        "providers": assistant.pop(
            "providers",
            {
                "openai": {
                    "available": bool(environ.get("OPENAI_API_KEY")) or selected == "dev",
                    "required": selected in {"staging", "prod"},
                    "local": False,
                    "models": {"default": assistant["model"]},
                }
            },
        ),
        "execution_modes": assistant.pop(
            "execution_modes",
            {
                "quick": {
                    "capability": "standard",
                    "latency": "low",
                    "tools": "limited",
                    "spend": "low",
                    "privacy": "standard",
                    "fallbacks": [{"provider": "openai", "model": "default"}],
                },
                "balanced": {
                    "capability": "high",
                    "latency": "medium",
                    "tools": "standard",
                    "spend": "medium",
                    "privacy": "standard",
                    "fallbacks": [{"provider": "openai", "model": "default"}],
                },
                "deep": {
                    "capability": "highest",
                    "latency": "high",
                    "tools": "all",
                    "spend": "high",
                    "privacy": "standard",
                    "fallbacks": [{"provider": "openai", "model": "default"}],
                },
                "local_private": {
                    "capability": "standard",
                    "latency": "medium",
                    "tools": "local",
                    "spend": "none",
                    "privacy": "local",
                    "fallbacks": [],
                },
            },
        ),
        "collaboration": {
            "enabled": False,
            "identity_provider": "",
            "tenant_store": "",
        },
    }

    if environ.get("PJ_MODEL"):
        sections["assistant"]["model"] = environ["PJ_MODEL"]
    if environ.get("PJ_VECTOR_STORE_IDS") is not None:
        sections["assistant"]["vector_store_ids"] = _split_csv(environ["PJ_VECTOR_STORE_IDS"])
    if environ.get("PJ_REALTIME_MODEL"):
        sections["realtime"]["model"] = environ["PJ_REALTIME_MODEL"]
    if environ.get("PJ_REALTIME_VOICE"):
        sections["realtime"]["voice"] = environ["PJ_REALTIME_VOICE"]
    for name in WORKER_ENV_NAMES:
        if environ.get(name) is not None:
            sections["worker"][name] = environ[name]

    overrides = _read_json_env("PJ_CONFIG_OVERRIDES", environ, dict)
    if overrides is not None:
        _deep_merge(sections, overrides)
    _apply_nested_env_overrides(sections, environ)

    if validate_required:
        _validate_required_env(selected, environ, extra=environ.get("PJ_REQUIRED_ENV", ""))
    assistant = _normalize_assistant(sections["assistant"], base_dir, assistant_path)
    mcp_servers = _normalize_mcp_servers(sections["mcp_servers"], mcp_path)
    tool_policy = sections["tool_policy"]
    if (
        not isinstance(tool_policy, dict)
        or tool_policy.get("default") not in POLICY_MODES
        or not isinstance(tool_policy.get("tools"), dict)
        or any(mode not in POLICY_MODES for mode in tool_policy["tools"].values())
    ):
        raise ConfigError("tool_policy overrides must use allow, deny, or approval")
    realtime = sections["realtime"]
    if not isinstance(realtime, dict) or any(
        not isinstance(realtime.get(key), str) or not realtime[key].strip()
        for key in ("model", "voice")
    ):
        raise ConfigError("realtime.model and realtime.voice must be non-empty strings")
    worker = sections["worker"]
    if not isinstance(worker, dict):
        raise ConfigError("worker configuration must be an object")
    routing = sections["conversation_routing"]
    routes = {"realtime", "responses", "local", "hosted", "delegated"}
    if not isinstance(routing, dict):
        raise ConfigError("conversation_routing must be an object")
    enabled = routing.get("enabled_routes")
    fallbacks = routing.get("safe_fallback_order")
    budgets = routing.get("timeout_budgets_ms")
    spend = routing.get("maximum_estimated_spend_usd")
    if not isinstance(enabled, list) or not enabled or not set(enabled) <= routes:
        raise ConfigError("conversation_routing.enabled_routes contains invalid routes")
    if not isinstance(fallbacks, list) or not fallbacks or not set(fallbacks) <= set(enabled):
        raise ConfigError("conversation_routing.safe_fallback_order must contain enabled routes")
    if (
        not isinstance(budgets, dict)
        or not set(enabled) <= set(budgets)
        or any(not isinstance(value, int) or value <= 0 for value in budgets.values())
    ):
        raise ConfigError("conversation_routing.timeout_budgets_ms must contain positive integers")
    if not isinstance(spend, (int, float)) or isinstance(spend, bool) or spend <= 0:
        raise ConfigError("conversation_routing.maximum_estimated_spend_usd must be positive")
    if selected == "prod" and ("responses" not in enabled or "responses" not in fallbacks):
        raise ConfigError(
            "production conversation routing requires Responses as an enabled safe fallback"
        )
    routing_settings = ConversationRoutingSettings(
        tuple(enabled), copy.deepcopy(budgets), float(spend), tuple(fallbacks)
    )
    _validate_provider_routing(sections)
    collaboration = sections["collaboration"]
    if not isinstance(collaboration, dict) or not isinstance(collaboration.get("enabled"), bool):
        raise ConfigError("collaboration.enabled must be a boolean")
    if collaboration["enabled"] and selected == "prod":
        missing = [
            field
            for field in ("identity_provider", "tenant_store")
            if not isinstance(collaboration.get(field), str) or not collaboration[field].strip()
        ]
        if missing:
            raise ConfigError(
                "Production collaboration requires identity and tenant configuration: "
                + ", ".join(missing)
            )

    return RuntimeConfig(
        profile=selected,
        assistant=assistant,
        mcp_servers=mcp_servers,
        tool_policy=tool_policy,
        realtime=copy.deepcopy(realtime),
        worker=copy.deepcopy(worker),
        conversation_routing=routing_settings,
        providers=copy.deepcopy(sections["providers"]),
        execution_modes=copy.deepcopy(sections["execution_modes"]),
        collaboration=copy.deepcopy(collaboration),
        sources={
            "assistant": assistant_path,
            "mcp_servers": mcp_path,
            "tool_policy": policy_path,
            "worker": worker_path,
        },
    )
