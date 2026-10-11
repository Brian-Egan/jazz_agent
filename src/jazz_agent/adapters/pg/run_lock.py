"""Postgres advisory-lock implementation of ports.repository.RunLock."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from psycopg_pool import ConnectionPool


class PgRunLock:
    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    @contextmanager
    def hold(self, name: str) -> Iterator[bool]:
        # Session-level lock: it lives on this pooled connection until released below, and is
        # dropped by Postgres if the process dies, so a crashed run can never wedge the next.
        with self._pool.connection() as conn:
            row = conn.execute("SELECT pg_try_advisory_lock(hashtext(%s))", (name,)).fetchone()
            acquired = bool(row and row[0])
            try:
                yield acquired
            finally:
                if acquired:
                    conn.execute("SELECT pg_advisory_unlock(hashtext(%s))", (name,))
