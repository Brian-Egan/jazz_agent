"""Smoke test for issue #1: the package installs and config loads without a .env."""

from jazz_agent.config import load_config


def test_package_imports() -> None:
    import jazz_agent

    assert jazz_agent is not None


def test_load_config_has_defaults_without_env() -> None:
    config = load_config()

    assert config.database_url.startswith("postgresql://")
    assert config.run_hour_et == 13
    assert config.mcp_allowed_emails == ()
