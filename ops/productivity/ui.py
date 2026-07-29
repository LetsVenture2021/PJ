"""Small private-runtime views for evidence and approval review."""

from __future__ import annotations

from flask import Blueprint, render_template_string

blueprint = Blueprint("productivity", __name__, url_prefix="/productivity")

_PAGE = """<!doctype html><title>{{ title }}</title><main><h1>{{ title }}</h1><p>{{ description }}</p><p>Source references and timestamps are shown beside every extracted item.</p></main>"""


@blueprint.get("/<view>")
def view(view: str):
    pages = {
        "daily-brief": ("Daily brief", "Ranked commitments and deadlines"),
        "meeting-packet": ("Meeting packet", "Agenda, attendees, and related evidence"),
        "transcript-evidence": (
            "Transcript evidence",
            "Timestamped segments and uncertain speaker labels",
        ),
        "draft-review": (
            "Draft review",
            "Sender, recipients, calendar, time zone, effects, and reversibility",
        ),
        "approval": ("Approval", "Externally visible action awaiting owner approval"),
    }
    title, description = pages.get(view, ("Not found", "Unknown productivity view"))
    return render_template_string(_PAGE, title=title, description=description), (
        200 if view in pages else 404
    )
