"""Apply migrations/*.sql in order against DATABASE_URL.

Each file is run with ``psql -v ON_ERROR_STOP=1 -f`` (DATA_MODEL.md section 6),
so a partial apply fails loudly instead of leaving the schema half-migrated.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from jazz_agent.config import load_config

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"


def main() -> int:
    config = load_config()
    migrations = sorted(MIGRATIONS_DIR.glob("*.sql"))
    if not migrations:
        print("No migrations found in migrations/.", file=sys.stderr)
        return 1

    for migration in migrations:
        print(f"Applying {migration.name}...")
        result = subprocess.run(
            ["psql", config.database_url, "-v", "ON_ERROR_STOP=1", "-f", str(migration)],
            check=False,
        )
        if result.returncode != 0:
            print(f"Failed applying {migration.name}", file=sys.stderr)
            return result.returncode

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
