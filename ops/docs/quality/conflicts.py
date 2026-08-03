"""Git conflict-marker detection shared by document quality gates."""

from __future__ import annotations

import re


GIT_CONFLICT_MARKER_LABEL = "git conflict marker"

# Git defaults to seven marker characters, but merge-file also supports custom
# marker sizes. Keep '=' out of the standalone detector because repeated equals
# lines are common Markdown/ASCII dividers; start, end, and diff3 base markers
# are the reliable residue for both complete and partially resolved conflicts.
GIT_CONFLICT_MARKER_LINE = re.compile(r"^(?:<{5,}|>{5,}|\|{5,})(?:\s.*)?$", re.MULTILINE)


def git_conflict_marker_match(content: str) -> re.Match[str] | None:
    return GIT_CONFLICT_MARKER_LINE.search(content)


def has_git_conflict_marker(content: str) -> bool:
    return git_conflict_marker_match(content) is not None
