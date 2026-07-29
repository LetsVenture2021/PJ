"""Loopback WorkflowOps authoring UI and JSON validation endpoints."""

from __future__ import annotations

import json

from flask import Blueprint, jsonify, render_template, request

from .compiler import WorkflowCompiler
from .models import WorkflowDefinition, WorkflowError


def create_workflow_blueprint(available_tools: set[str] | None = None) -> Blueprint:
    blueprint = Blueprint("workflowops", __name__, template_folder="templates")
    tools = available_tools or set()

    @blueprint.get("/workflows")
    def editor():
        return render_template("workflowops.html")

    @blueprint.post("/api/workflows/dry-run")
    def dry_run():
        try:
            payload = request.get_json(force=True)
            compiled = WorkflowCompiler(tools).compile(
                WorkflowDefinition.from_dict(payload.get("workflow"))
            )
            return jsonify(WorkflowCompiler(tools).dry_run(compiled, payload.get("inputs", {})))
        except WorkflowError as exc:
            return jsonify({"error": str(exc)}), 400
        except (AttributeError, json.JSONDecodeError):
            return jsonify({"error": "Invalid workflow payload."}), 400

    return blueprint
