#!/usr/bin/env python3
"""Hugging Face MCP server for PJ.

A dependency-free Model Context Protocol server using stdio transport. It
exposes bounded, read-oriented Hugging Face Hub discovery plus opt-in inference.
Authentication is read from HF_TOKEN; secrets are never accepted as tool input.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

SERVER_NAME = "pj-hugging-face"
SERVER_VERSION = "1.0.0"
PROTOCOL_VERSION = "2025-06-18"
HF_HUB_API = os.getenv("HF_HUB_API", "https://huggingface.co/api").rstrip("/")
HF_INFERENCE_API = os.getenv(
    "HF_INFERENCE_API", "https://router.huggingface.co/hf-inference/models"
).rstrip("/")
DEFAULT_TIMEOUT = min(max(int(os.getenv("HF_MCP_TIMEOUT_SECONDS", "30")), 1), 120)
MAX_RESULTS = 50
MAX_INPUT_CHARS = 100_000


def _schema(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required or [],
        "additionalProperties": False,
    }


TOOLS = [
    {
        "name": "hf_search_models",
        "description": "Search Hugging Face Hub models. Returns bounded public metadata.",
        "inputSchema": _schema({
            "query": {"type": "string", "minLength": 1, "maxLength": 300},
            "limit": {"type": "integer", "minimum": 1, "maximum": MAX_RESULTS, "default": 10},
            "sort": {"type": "string", "enum": ["downloads", "likes", "lastModified"], "default": "downloads"},
        }, ["query"]),
    },
    {
        "name": "hf_get_model",
        "description": "Get metadata for one Hugging Face model repository.",
        "inputSchema": _schema({
            "model_id": {"type": "string", "minLength": 1, "maxLength": 300},
        }, ["model_id"]),
    },
    {
        "name": "hf_search_datasets",
        "description": "Search Hugging Face Hub datasets.",
        "inputSchema": _schema({
            "query": {"type": "string", "minLength": 1, "maxLength": 300},
            "limit": {"type": "integer", "minimum": 1, "maximum": MAX_RESULTS, "default": 10},
        }, ["query"]),
    },
    {
        "name": "hf_get_dataset",
        "description": "Get metadata for one Hugging Face dataset repository.",
        "inputSchema": _schema({
            "dataset_id": {"type": "string", "minLength": 1, "maxLength": 300},
        }, ["dataset_id"]),
    },
    {
        "name": "hf_search_spaces",
        "description": "Search Hugging Face Spaces.",
        "inputSchema": _schema({
            "query": {"type": "string", "minLength": 1, "maxLength": 300},
            "limit": {"type": "integer", "minimum": 1, "maximum": MAX_RESULTS, "default": 10},
        }, ["query"]),
    },
    {
        "name": "hf_get_space",
        "description": "Get metadata for one Hugging Face Space.",
        "inputSchema": _schema({
            "space_id": {"type": "string", "minLength": 1, "maxLength": 300},
        }, ["space_id"]),
    },
    {
        "name": "hf_inference",
        "description": "Run bounded inference through a Hugging Face model. Requires HF_TOKEN.",
        "inputSchema": _schema({
            "model_id": {"type": "string", "minLength": 1, "maxLength": 300},
            "inputs": {"description": "Model input: string, object, or array."},
            "parameters": {"type": "object", "default": {}},
        }, ["model_id", "inputs"]),
    },
]


def _token() -> str | None:
    value = os.getenv("HF_TOKEN", "").strip()
    return value or None


def _request(method: str, url: str, payload: Any = None, *, auth: bool = False) -> Any:
    headers = {"Accept": "application/json", "User-Agent": f"{SERVER_NAME}/{SERVER_VERSION}"}
    if auth:
        token = _token()
        if not token:
            raise RuntimeError("HF_TOKEN is required for this operation")
        headers["Authorization"] = f"Bearer {token}"
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=DEFAULT_TIMEOUT) as response:
            body = response.read().decode("utf-8")
            return json.loads(body) if body else None
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:2000]
        raise RuntimeError(f"Hugging Face HTTP {exc.code}: {body or exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Hugging Face connection failed: {exc.reason}") from exc


def _bounded_limit(value: Any) -> int:
    if isinstance(value, bool):
        return 10
    try:
        return min(max(int(value), 1), MAX_RESULTS)
    except (TypeError, ValueError):
        return 10


def _repo_id(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 300:
        raise ValueError(f"{field} must be a non-empty string of at most 300 characters")
    return value.strip()


def _search(kind: str, args: dict[str, Any]) -> Any:
    query = _repo_id(args.get("query"), "query")
    params = {"search": query, "limit": _bounded_limit(args.get("limit", 10))}
    if kind == "models":
        sort = args.get("sort", "downloads")
        if sort not in {"downloads", "likes", "lastModified"}:
            raise ValueError("sort must be downloads, likes, or lastModified")
        params.update({"sort": sort, "direction": -1})
    return _request("GET", f"{HF_HUB_API}/{kind}?{urllib.parse.urlencode(params)}")


def call_tool(name: str, args: dict[str, Any]) -> Any:
    if not isinstance(args, dict):
        raise ValueError("arguments must be an object")
    if name == "hf_search_models":
        return _search("models", args)
    if name == "hf_get_model":
        repo = urllib.parse.quote(_repo_id(args.get("model_id"), "model_id"), safe="/")
        return _request("GET", f"{HF_HUB_API}/models/{repo}", auth=bool(_token()))
    if name == "hf_search_datasets":
        return _search("datasets", args)
    if name == "hf_get_dataset":
        repo = urllib.parse.quote(_repo_id(args.get("dataset_id"), "dataset_id"), safe="/")
        return _request("GET", f"{HF_HUB_API}/datasets/{repo}", auth=bool(_token()))
    if name == "hf_search_spaces":
        return _search("spaces", args)
    if name == "hf_get_space":
        repo = urllib.parse.quote(_repo_id(args.get("space_id"), "space_id"), safe="/")
        return _request("GET", f"{HF_HUB_API}/spaces/{repo}", auth=bool(_token()))
    if name == "hf_inference":
        model = urllib.parse.quote(_repo_id(args.get("model_id"), "model_id"), safe="/")
        inputs = args.get("inputs")
        encoded = json.dumps(inputs)
        if len(encoded) > MAX_INPUT_CHARS:
            raise ValueError(f"inputs exceed {MAX_INPUT_CHARS} serialized characters")
        parameters = args.get("parameters", {})
        if not isinstance(parameters, dict):
            raise ValueError("parameters must be an object")
        return _request(
            "POST", f"{HF_INFERENCE_API}/{model}",
            {"inputs": inputs, "parameters": parameters}, auth=True,
        )
    raise ValueError(f"unknown tool: {name}")


def _result(request_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def handle(message: dict[str, Any]) -> dict[str, Any] | None:
    method = message.get("method")
    request_id = message.get("id")
    if request_id is None:
        return None
    if method == "initialize":
        return _result(request_id, {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        })
    if method == "ping":
        return _result(request_id, {})
    if method == "tools/list":
        return _result(request_id, {"tools": TOOLS})
    if method == "tools/call":
        params = message.get("params") or {}
        try:
            value = call_tool(params.get("name", ""), params.get("arguments") or {})
            return _result(request_id, {
                "content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False)}],
                "structuredContent": value,
                "isError": False,
            })
        except (ValueError, RuntimeError) as exc:
            return _result(request_id, {
                "content": [{"type": "text", "text": str(exc)}],
                "isError": True,
            })
    return _error(request_id, -32601, f"Method not found: {method}")


def serve() -> None:
    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
            if not isinstance(message, dict):
                raise ValueError("message must be an object")
            response = handle(message)
        except (json.JSONDecodeError, ValueError) as exc:
            response = _error(None, -32700, f"Parse error: {exc}")
        if response is not None:
            sys.stdout.write(json.dumps(response, separators=(",", ":")) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    serve()
