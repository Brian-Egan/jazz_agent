"""Postgres connection pool. Every repository shares one of these."""

from __future__ import annotations

from psycopg_pool import ConnectionPool


def make_pool(database_url: str, min_size: int = 1, max_size: int = 5) -> ConnectionPool:
    return ConnectionPool(conninfo=database_url, min_size=min_size, max_size=max_size, open=True)
