"""Tests for scripts/generate_project_instructions.py (issue #15 acceptance
criteria). scripts/ isn't a package, so the module is loaded by file path.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = REPO_ROOT / "scripts" / "generate_project_instructions.py"
DOCS_PATH = REPO_ROOT / "docs" / "claude-project-instructions.md"


def _load_script() -> Any:
    spec = importlib.util.spec_from_file_location("generate_project_instructions", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@dataclass
class _FakeTool:
    name: str
    description: str
    parameters: dict[str, Any]


def test_render_tool_inventory_formats_reads_and_writes_separately() -> None:
    script = _load_script()
    tools = [
        _FakeTool(
            name="search_shows",
            description="  Structured and fuzzy show lookup.\n  Second line.  ",
            parameters={"properties": {"query": {}, "club": {}}, "required": ["query"]},
        ),
        _FakeTool(
            name="record_feedback",
            description="Attach sentiment.",
            parameters={"properties": {"target_type": {}}, "required": ["target_type"]},
        ),
    ]

    rendered = script.render_tool_inventory(tools)

    assert (
        "`search_shows(query, club?)` -- Structured and fuzzy show lookup. Second line." in rendered
    )
    assert "`record_feedback(target_type)` -- Attach sentiment." in rendered
    reads_idx = rendered.index("**Reads**")
    writes_idx = rendered.index("**Writes**")
    assert reads_idx < rendered.index("search_shows") < writes_idx
    assert writes_idx < rendered.index("record_feedback")


def test_render_tool_inventory_is_sorted_by_name() -> None:
    script = _load_script()
    tools = [
        _FakeTool(
            name="zebra_tool", description="z", parameters={"properties": {}, "required": []}
        ),
        _FakeTool(
            name="alpha_tool", description="a", parameters={"properties": {}, "required": []}
        ),
    ]

    rendered = script.render_tool_inventory(tools)

    assert rendered.index("alpha_tool") < rendered.index("zebra_tool")


def test_adding_a_tool_changes_the_rendered_inventory() -> None:
    script = _load_script()
    before = [
        _FakeTool(
            name="search_shows", description="lookup", parameters={"properties": {}, "required": []}
        )
    ]
    after = [
        *before,
        _FakeTool(
            name="brand_new_tool",
            description="new thing",
            parameters={"properties": {}, "required": []},
        ),
    ]

    rendered_before = script.render_tool_inventory(before)
    rendered_after = script.render_tool_inventory(after)

    assert "brand_new_tool" not in rendered_before
    assert "brand_new_tool" in rendered_after


def test_main_is_idempotent_and_preserves_prose_outside_markers() -> None:
    script = _load_script()
    original = DOCS_PATH.read_text()

    exit_code = script.main()

    try:
        assert exit_code == 0
        first_run = DOCS_PATH.read_text()

        exit_code_2 = script.main()
        second_run = DOCS_PATH.read_text()

        assert exit_code_2 == 0
        assert first_run == second_run  # idempotent

        begin, end = script.BEGIN_MARKER, script.END_MARKER
        original_prose_before = original[: original.index(begin)]
        original_prose_after = original[original.index(end) + len(end) :]
        new_prose_before = second_run[: second_run.index(begin)]
        new_prose_after = second_run[second_run.index(end) + len(end) :]
        assert original_prose_before == new_prose_before
        assert original_prose_after == new_prose_after
    finally:
        DOCS_PATH.write_text(original)  # leave the real docs file untouched


def test_generated_inventory_contains_all_nine_real_tools() -> None:
    script = _load_script()
    original = DOCS_PATH.read_text()

    try:
        script.main()
        rendered = DOCS_PATH.read_text()
    finally:
        DOCS_PATH.write_text(original)

    for tool_name in [
        "search_shows",
        "whats_playing_at",
        "search_notes",
        "recent_feedback",
        "artist_profile",
        "artist_connections",
        "get_run_health",
        "get_listening_candidates",
        "record_feedback",
    ]:
        assert re.search(rf"`{tool_name}\(", rendered), (
            f"{tool_name} missing from generated inventory"
        )
