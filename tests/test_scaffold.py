"""Smoke test for issue #1: the package installs and config falls back to
sane defaults for any variable that isn't actually set.
"""

import pytest

from jazz_agent.config import load_config


def test_package_imports() -> None:
    import jazz_agent

    assert jazz_agent is not None


def test_load_config_has_defaults_without_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # config.py auto-loads a real .env if the developer has one (a deliberate
    # fix for exactly this friction -- see git history), so this test can't
    # assume no .env exists. It clears just the keys it asserts defaults for.
    for key in ("DATABASE_URL", "RUN_HOUR_ET", "MCP_ALLOWED_EMAILS"):
        monkeypatch.delenv(key, raising=False)

    config = load_config()

    assert config.database_url.startswith("postgresql://")
    assert config.run_hour_et == 13
    assert config.mcp_allowed_emails == ()
