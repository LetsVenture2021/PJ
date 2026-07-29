#!/usr/bin/env python3
"""Read-only Google Cloud MCP server using stdio and Google REST APIs."""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from runtime_config import GoogleCloudSettings, load_runtime_config

SERVER_NAME = "pj-google-cloud"
SERVER_VERSION = "1.0.0"
PROTOCOL_VERSION = "2025-06-18"
MAX_RESULTS = 100
PROJECT_RE = re.compile(r"^(?:[a-z][a-z0-9-]{4,61}[a-z0-9]|[0-9]{6,30})$")
SERVICE_RE = re.compile(r"^[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?$")
LOCATION_RE = re.compile(r"^[a-z0-9-]{1,63}$")


def _schema(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required or [],
        "additionalProperties": False,
    }


TOOLS = [
    {
        "name": "gcp_search_projects",
        "description": "Search accessible Google Cloud projects and return bounded metadata.",
        "inputSchema": _schema(
            {
                "query": {"type": "string", "maxLength": 300, "default": ""},
                "limit": {"type": "integer", "minimum": 1, "maximum": MAX_RESULTS, "default": 20},
            }
        ),
    },
    {
        "name": "gcp_get_project",
        "description": "Get metadata for one accessible Google Cloud project.",
        "inputSchema": _schema(
            {"project": {"type": "string", "minLength": 6, "maxLength": 63}}, ["project"]
        ),
    },
    {
        "name": "gcp_list_cloud_run_services",
        "description": "List Cloud Run services in one project and location.",
        "inputSchema": _schema(
            {
                "project": {"type": "string", "minLength": 6, "maxLength": 63},
                "location": {"type": "string", "minLength": 1, "maxLength": 63},
                "limit": {"type": "integer", "minimum": 1, "maximum": MAX_RESULTS, "default": 20},
            }
        ),
    },
    {
        "name": "gcp_get_cloud_run_service",
        "description": "Get metadata for one Cloud Run service.",
        "inputSchema": _schema(
            {
                "service": {"type": "string", "minLength": 1, "maxLength": 63},
                "project": {"type": "string", "minLength": 6, "maxLength": 63},
                "location": {"type": "string", "minLength": 1, "maxLength": 63},
            },
            ["service"],
        ),
    },
]


def _settings() -> GoogleCloudSettings:
    return load_runtime_config(validate_required=False).google_cloud


def _access_token(settings: GoogleCloudSettings) -> str:
    token = os.environ.get("GOOGLE_CLOUD_ACCESS_TOKEN", "").strip()
    if token:
        return token
    request = urllib.request.Request(
        settings.metadata_token_url,
        headers={"Metadata-Flavor": "Google", "User-Agent": f"{SERVER_NAME}/{SERVER_VERSION}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=settings.timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, json.JSONDecodeError, OSError) as exc:
        raise RuntimeError(
            "Google Cloud authentication failed; run on Google Cloud with an attached "
            "service account or set a short-lived GOOGLE_CLOUD_ACCESS_TOKEN"
        ) from exc
    token = payload.get("access_token") if isinstance(payload, dict) else None
    if not isinstance(token, str) or not token:
        raise RuntimeError("Google metadata server did not return an access token")
    return token


def _request(url: str, settings: GoogleCloudSettings) -> Any:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {_access_token(settings)}",
            "User-Agent": f"{SERVER_NAME}/{SERVER_VERSION}",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=settings.timeout_seconds) as response:
            body = response.read().decode("utf-8")
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:2000]
        raise RuntimeError(f"Google Cloud HTTP {exc.code}: {body or exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Google Cloud connection failed: {exc.reason}") from exc


def _limit(value: Any) -> int:
    if isinstance(value, bool):
        return 20
    try:
        return min(max(int(value), 1), MAX_RESULTS)
    except (TypeError, ValueError):
        return 20


def _identifier(value: Any, field: str, pattern: re.Pattern[str]) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value.strip()):
        raise ValueError(f"{field} has an invalid Google Cloud resource identifier")
    return value.strip()


def _project(args: dict[str, Any], settings: GoogleCloudSettings) -> str:
    value = args.get("project") or settings.project
    if not value:
        raise ValueError("project is required when google_cloud.project is not configured")
    return _identifier(value, "project", PROJECT_RE)


def _location(args: dict[str, Any], settings: GoogleCloudSettings) -> str:
    value = args.get("location") or settings.location
    return _identifier(value, "location", LOCATION_RE)


def call_tool(name: str, args: dict[str, Any]) -> Any:
    if not isinstance(args, dict):
        raise ValueError("arguments must be an object")
    settings = _settings()
    if name == "gcp_search_projects":
        query = args.get("query", "")
        if not isinstance(query, str) or len(query) > 300:
            raise ValueError("query must be a string of at most 300 characters")
        params = {"pageSize": _limit(args.get("limit", 20))}
        if query.strip():
            params["query"] = query.strip()
        return _request(
            f"{settings.resource_manager_api}/projects:search?{urllib.parse.urlencode(params)}",
            settings,
        )
    project = _project(args, settings)
    if name == "gcp_get_project":
        return _request(f"{settings.resource_manager_api}/projects/{project}", settings)
    location = _location(args, settings)
    parent = f"projects/{project}/locations/{location}"
    if name == "gcp_list_cloud_run_services":
        params = urllib.parse.urlencode({"pageSize": _limit(args.get("limit", 20))})
        return _request(f"{settings.cloud_run_api}/{parent}/services?{params}", settings)
    if name == "gcp_get_cloud_run_service":
        service = _identifier(args.get("service"), "service", SERVICE_RE)
        return _request(f"{settings.cloud_run_api}/{parent}/services/{service}", settings)
    raise ValueError(f"unknown tool: {name}")


def _result(request_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def handle(message: dict[str, Any]) -> dict[str, Any] | None:
    request_id = message.get("id")
    if request_id is None:
        return None
    method = message.get("method")
    if method == "initialize":
        return _result(
            request_id,
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            },
        )
    if method == "ping":
        return _result(request_id, {})
    if method == "tools/list":
        return _result(request_id, {"tools": TOOLS})
    if method == "tools/call":
        params = message.get("params") or {}
        try:
            if not isinstance(params, dict):
                raise ValueError("params must be an object")
            value = call_tool(params.get("name", ""), params.get("arguments") or {})
            return _result(
                request_id,
                {
                    "content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False)}],
                    "structuredContent": value,
                    "isError": False,
                },
            )
        except (ValueError, RuntimeError) as exc:
            return _result(
                request_id,
                {"content": [{"type": "text", "text": str(exc)}], "isError": True},
            )
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": -32601, "message": "Method not found"},
    }


def serve() -> None:
    for raw_line in sys.stdin:
        if not raw_line.strip():
            continue
        try:
            message = json.loads(raw_line)
            if not isinstance(message, dict):
                raise ValueError("message must be an object")
            response = handle(message)
        except (json.JSONDecodeError, ValueError) as exc:
            response = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": f"Parse error: {exc}"},
            }
        if response is not None:
            sys.stdout.write(json.dumps(response, separators=(",", ":")) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    serve()
