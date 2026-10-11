"""Mechanical proof that the MCP server can write only feedback (issue #14
acceptance criteria): "Verify no code path reaches club config, playlists,
matches, or the pipeline." Static analysis over the actual source, not a
runtime sample -- if a write sneaks in anywhere else in mcp/, this fails.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

MCP_DIR = Path(__file__).resolve().parent.parent / "src" / "jazz_agent" / "mcp"

_WRITE_KEYWORDS = re.compile(r"\b(INSERT INTO|UPDATE|DELETE FROM)\b", re.IGNORECASE)

# The one file, and the one table, allowed to appear after a write keyword.
_ALLOWED_WRITE_FILE = "tools_feedback.py"
_ALLOWED_WRITE_TARGET = "feedback"


def test_no_sql_write_keyword_outside_the_feedback_write_path() -> None:
    offenders = {}
    for path in MCP_DIR.rglob("*.py"):
        text = path.read_text()
        matches = _WRITE_KEYWORDS.findall(text)
        if not matches:
            continue
        if path.name != _ALLOWED_WRITE_FILE:
            offenders[path.name] = matches

    assert not offenders, f"SQL write keyword outside {_ALLOWED_WRITE_FILE}: {offenders}"


def test_the_one_allowed_write_path_only_targets_the_feedback_table() -> None:
    text = (MCP_DIR / _ALLOWED_WRITE_FILE).read_text()
    # PgFeedbackRepo.record_feedback is the only writer used here, and it is
    # itself scoped to the feedback table (verified separately by
    # tests/test_pg_feedback_repo.py). This just proves nothing else in the
    # file writes anywhere else.
    write_calls = re.findall(
        r"\.record_feedback\(|\.upsert_\w+\(|\.add_tracks\(|\.remove_track\(", text
    )
    assert write_calls == [".record_feedback("] or all(
        c == ".record_feedback(" for c in write_calls
    )


def test_mcp_module_never_imports_pipeline() -> None:
    offenders = {}
    for path in MCP_DIR.rglob("*.py"):
        tree = ast.parse(path.read_text(), filename=str(path))
        imported_pipeline = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_pipeline.extend(
                    alias.name
                    for alias in node.names
                    if alias.name.startswith("jazz_agent.pipeline")
                )
            elif (
                isinstance(node, ast.ImportFrom)
                and node.module
                and node.module.startswith("jazz_agent.pipeline")
            ):
                imported_pipeline.append(node.module)
        if imported_pipeline:
            offenders[path.name] = imported_pipeline

    assert not offenders, f"mcp/ imports pipeline/, giving it a path to trigger a run: {offenders}"


def test_mcp_module_never_imports_write_capable_repo_adapters_other_than_feedback() -> None:
    """ArtistRepo/ClubRepo/PlaylistRepo/ShowRepo/RunRepo/GraphRepo implementations
    can all write beyond feedback (that's their job, for the pipeline). The MCP
    server must never construct one -- PgRunRepo is read-only in practice here
    (get_run_health only calls recent_runs), but even so it's excluded from
    write capability by never being asked to write; this test just proves the
    only *_repo.py import outside PgFeedbackRepo is the read-only run repo."""
    allowed_repo_imports = {"feedback_repo", "run_repo", "venue_group_repo"}
    offenders = {}
    for path in MCP_DIR.rglob("*.py"):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.ImportFrom)
                and node.module
                and node.module.startswith("jazz_agent.adapters.pg.")
                and node.module.endswith("_repo")
            ):
                repo_module = node.module.rsplit(".", 1)[-1]
                if repo_module not in allowed_repo_imports:
                    offenders.setdefault(path.name, []).append(node.module)

    assert not offenders, f"mcp/ imports a write-capable repo beyond feedback/run: {offenders}"


# --- ADR-021: the one widening (issue #31) ---------------------------------------------------

# Every repository method the mcp/ package may call. Anything else is a new capability and has
# to be argued for in an ADR, not slipped in.
_ALLOWED_REPO_CALLS = {
    "record_feedback",  # feedback
    "recent_runs",  # read-only
    "add_member",  # venue_group_members
    "deactivate_member",  # venue_group_members (stamps, never deletes)
    "active_members",  # read-only
}
_REPO_CALL_PATTERN = re.compile(r"\b(?:repo|Pg\w+Repo\([^)]*\))\.(\w+)\(")


def test_mcp_only_calls_the_allowed_repository_methods() -> None:
    called = set()
    for path in MCP_DIR.rglob("*.py"):
        called |= set(_REPO_CALL_PATTERN.findall(path.read_text()))

    assert called <= _ALLOWED_REPO_CALLS, f"unexpected repository calls from mcp/: {called}"
    assert {"record_feedback", "add_member", "deactivate_member"} <= called


def test_the_mcp_package_has_no_way_to_create_a_playlist_or_touch_stints() -> None:
    forbidden = (
        "upsert_rolling_playlist",
        "link_spotify_playlist",
        "open_stint",
        "close_stint",
        "create_playlist",
        "replace_playlist_items",
    )
    for path in MCP_DIR.rglob("*.py"):
        text = path.read_text()
        for name in forbidden:
            assert name not in text, (path.name, name)
