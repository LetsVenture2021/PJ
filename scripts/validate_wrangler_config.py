#!/usr/bin/env python3
"""Validate the checked-in, non-secret Cloudflare Worker configuration."""

from __future__ import annotations

import argparse
import datetime
import tomllib
from pathlib import Path
from typing import Any

REQUIRED_ROUTES = {
    "pj-assistant.ai/execute-tool",
    "pj-assistant.ai/conversations*",
    "pj-assistant.ai/health",
    "pj-assistant.ai/projects*",
    "pj-assistant.ai/responses/*",
    "pj-assistant.ai/session",
    "pj-assistant.ai/token",
    "pj-assistant.ai/tool-schemas",
    "pj-assistant.ai/upload/*",
}
REQUIRED_VARS = {
    "CF_ACCESS_AUD",
    "CF_ACCESS_TEAM_DOMAIN",
    "PJ_ALLOWED_ORIGINS",
    "PJ_TOOL_BRIDGE_URL",
    "PJ_TOOL_SCHEMAS_URL",
}
SECRET_VARS = {
    "OPENAI_API_KEY",
    "PJ_OWNER_EMAILS",
    "PJ_TOOL_BRIDGE_TOKEN",
}


class ConfigValidationError(ValueError):
    """Raised when the Wrangler example violates its deployment contract."""


def _require_nonempty_string(mapping: dict[str, Any], key: str, context: str) -> None:
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ConfigValidationError(f"{context}.{key} must be a non-empty string")


def validate_manifest(manifest: dict[str, Any]) -> None:
    """Validate required Wrangler keys, variables, and route patterns."""
    for key in ("name", "main"):
        _require_nonempty_string(manifest, key, "wrangler")

    compatibility_date = manifest.get("compatibility_date")
    if not isinstance(compatibility_date, str):
        raise ConfigValidationError("wrangler.compatibility_date must be a YYYY-MM-DD string")
    try:
        parsed_date = datetime.date.fromisoformat(compatibility_date)
    except ValueError as exc:
        raise ConfigValidationError(
            "wrangler.compatibility_date must be a YYYY-MM-DD string"
        ) from exc
    if parsed_date.isoformat() != compatibility_date:
        raise ConfigValidationError("wrangler.compatibility_date must be a YYYY-MM-DD string")

    variables = manifest.get("vars")
    if not isinstance(variables, dict):
        raise ConfigValidationError("wrangler.vars must be a TOML table")
    for key in sorted(REQUIRED_VARS):
        _require_nonempty_string(variables, key, "wrangler.vars")

    committed_secrets = sorted(SECRET_VARS.intersection(variables))
    if committed_secrets:
        raise ConfigValidationError(
            "wrangler.vars must not contain secret keys: " + ", ".join(committed_secrets)
        )

    routes = manifest.get("routes")
    if not isinstance(routes, list):
        raise ConfigValidationError("wrangler.routes must be an array")

    patterns: list[str] = []
    for index, route in enumerate(routes):
        if not isinstance(route, dict):
            raise ConfigValidationError(f"wrangler.routes[{index}] must be a table")
        _require_nonempty_string(route, "pattern", f"wrangler.routes[{index}]")
        _require_nonempty_string(route, "zone_name", f"wrangler.routes[{index}]")
        patterns.append(route["pattern"])

    actual_routes = set(patterns)
    missing_routes = sorted(REQUIRED_ROUTES - actual_routes)
    unexpected_routes = sorted(actual_routes - REQUIRED_ROUTES)
    duplicate_routes = sorted(pattern for pattern in actual_routes if patterns.count(pattern) > 1)
    route_errors = []
    if missing_routes:
        route_errors.append("missing: " + ", ".join(missing_routes))
    if unexpected_routes:
        route_errors.append("unexpected: " + ", ".join(unexpected_routes))
    if duplicate_routes:
        route_errors.append("duplicate: " + ", ".join(duplicate_routes))
    if route_errors:
        raise ConfigValidationError(
            "wrangler.routes patterns invalid (" + "; ".join(route_errors) + ")"
        )

    for index, route in enumerate(routes):
        route_host = route["pattern"].split("/", 1)[0]
        if route["zone_name"] != route_host:
            raise ConfigValidationError(
                f"wrangler.routes[{index}].zone_name must match its pattern host"
            )


def validate_file(path: Path) -> None:
    try:
        with path.open("rb") as config_file:
            manifest = tomllib.load(config_file)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigValidationError(f"{path}: unable to parse TOML: {exc}") from exc
    validate_manifest(manifest)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "config",
        nargs="?",
        type=Path,
        default=Path("wrangler.toml.example"),
    )
    args = parser.parse_args()

    try:
        validate_file(args.config)
    except ConfigValidationError as exc:
        parser.error(str(exc))
    print(f"{args.config}: valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
