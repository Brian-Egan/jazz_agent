"""allowed_emails_check unit tests (issue #14 acceptance criteria: unauthenticated
and non-allow-listed requests are rejected). Full end-to-end OAuth (an actual
Google login) needs a real Google Developer app and a browser -- neither of
which happens here; the allow-list logic this project owns is what's
unit-tested directly, the same treatment as scripts/spotify_auth.py (issue #6).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from jazz_agent.mcp.server import allowed_emails_check


@dataclass
class _FakeToken:
    claims: dict[str, Any]


@dataclass
class _FakeAuthContext:
    token: _FakeToken | None


def test_unauthenticated_request_is_rejected() -> None:
    check = allowed_emails_check(("you@example.com",))

    assert check(_FakeAuthContext(token=None)) is False


def test_non_allow_listed_account_is_rejected() -> None:
    check = allowed_emails_check(("you@example.com",))

    assert (
        check(_FakeAuthContext(token=_FakeToken(claims={"email": "someone-else@example.com"})))
        is False
    )


def test_allow_listed_account_is_accepted() -> None:
    check = allowed_emails_check(("you@example.com",))

    assert check(_FakeAuthContext(token=_FakeToken(claims={"email": "you@example.com"}))) is True


def test_allow_list_check_is_case_insensitive() -> None:
    check = allowed_emails_check(("You@Example.com",))

    assert check(_FakeAuthContext(token=_FakeToken(claims={"email": "you@example.com"}))) is True


def test_token_with_no_email_claim_is_rejected() -> None:
    check = allowed_emails_check(("you@example.com",))

    assert check(_FakeAuthContext(token=_FakeToken(claims={}))) is False


def test_every_registered_tool_carries_the_auth_check() -> None:
    """Static proof that every @mcp.tool() registration in server.py passes
    auth=check -- nothing is accidentally left open."""
    import ast
    from pathlib import Path

    source = (
        Path(__file__).resolve().parent.parent / "src" / "jazz_agent" / "mcp" / "server.py"
    ).read_text()
    tree = ast.parse(source)

    tool_decorators_missing_auth = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call):
                continue
            func = decorator.func
            is_tool_call = (isinstance(func, ast.Attribute) and func.attr == "tool") or (
                isinstance(func, ast.Name) and func.id == "tool"
            )
            if not is_tool_call:
                continue
            has_auth_kwarg = any(kw.arg == "auth" for kw in decorator.keywords)
            if not has_auth_kwarg:
                tool_decorators_missing_auth.append(node.name)

    assert not tool_decorators_missing_auth, (
        f"tools registered without auth=check: {tool_decorators_missing_auth}"
    )
