"""pw_fetcher.py must not import playwright at module scope (issue #4 acceptance
criteria): http-only clubs must never pay for the browser-automation stack.
"""

import ast
from pathlib import Path

import pytest

from jazz_agent.adapters.pw_fetcher import PlaywrightFetcher

PW_FETCHER_PATH = (
    Path(__file__).resolve().parent.parent / "src" / "jazz_agent" / "adapters" / "pw_fetcher.py"
)


def test_playwright_import_is_not_at_module_scope() -> None:
    tree = ast.parse(PW_FETCHER_PATH.read_text())

    for node in tree.body:  # top-level statements only; imports inside get() are fine
        if isinstance(node, ast.Import):
            assert not any(alias.name.startswith("playwright") for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert not (node.module and node.module.startswith("playwright"))


def test_get_rejects_non_js_render_mode() -> None:
    fetcher = PlaywrightFetcher()

    with pytest.raises(ValueError, match="render_mode"):
        fetcher.get("https://example.com", render_mode="http")
