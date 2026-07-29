"""Portable JSON and Markdown research bundles."""

from __future__ import annotations

import hashlib
import json

from .models import ResearchBundle


def export_json(bundle: ResearchBundle) -> str:
    return json.dumps(bundle.to_dict(), indent=2, sort_keys=True, ensure_ascii=False)


def export_markdown(bundle: ResearchBundle) -> str:
    lines = [f"# {bundle.plan.title}", "", "## Plan"]
    lines.extend(f"- {q.text}" for q in bundle.plan.questions)
    lines += [
        "",
        "## Sources",
        "",
        "| ID | Title | Publisher | Identity | Accessed | Hash |",
        "|---|---|---|---|---|---|",
    ]
    lines.extend(
        f"| {s.id} | {s.title} | {s.publisher or ''} | {s.identity} | {s.accessed_at} | {s.content_hash} |"
        for s in bundle.sources
    )
    lines += ["", "## Claims"]
    for claim in bundle.claims:
        refs = ", ".join(f"{support.source_id}:{support.evidence_id}" for support in claim.supports)
        lines.append(f"- **{claim.verification}** {claim.text} [{refs}]")
    lines += [
        "",
        "## Conflicts",
        "```json",
        json.dumps(bundle.conflicts, indent=2),
        "```",
        "",
        "## Gaps",
        "```json",
        json.dumps(bundle.gaps, indent=2),
        "```",
        "",
        "## Timestamps",
        "```json",
        json.dumps(bundle.timestamps, indent=2),
        "```",
        "",
        "## Artifact hashes",
        "```json",
        json.dumps(bundle.artifact_hashes, indent=2),
        "```",
    ]
    return "\n".join(lines) + "\n"


def artifact_hash(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()
