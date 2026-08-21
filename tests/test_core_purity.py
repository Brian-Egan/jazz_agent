"""core/ must import nothing outside the stdlib (issue #2 acceptance criteria)."""

import ast
import sys
from pathlib import Path

CORE_DIR = Path(__file__).resolve().parent.parent / "src" / "jazz_agent" / "core"

_ALLOWED = set(sys.stdlib_module_names) | {"jazz_agent", "__future__"}


def _imported_top_level_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.add(node.module.split(".")[0])
    return modules


def test_core_imports_only_stdlib() -> None:
    offenders = {
        str(path.relative_to(CORE_DIR)): modules
        for path in CORE_DIR.rglob("*.py")
        if (modules := _imported_top_level_modules(path) - _ALLOWED)
    }
    assert not offenders, f"core/ modules importing outside the stdlib: {offenders}"
