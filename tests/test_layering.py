"""The packages depend in one direction only, so none can import its way into a cycle:

api → routes → middleware / services → repository / graph → tools → schemas → core
(config, llms, prompts, exceptions)

Packages joined by "/" above are siblings: neither may import the other.
"""

import ast
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[1] / "app"

# Ordered top-to-bottom; packages within the same level are siblings.
LAYERS = [
    {"api"},
    {"routes"},
    {"middleware", "services"},
    {"repository", "graph"},
    {"tools"},
    {"schemas"},
    {"core", "config", "llms", "prompts", "exceptions"},
]

# Packages each package must never import (directly): everything in a
# strictly-higher level.
FORBIDDEN = {package: set().union(*LAYERS[:level]) for level, packages in enumerate(LAYERS) for package in packages}
# repository has a read-only knowledge side (vector_repository, historically open to
# tools) and a transactional side (sql_repository, forbidden to tools like the rest of
# repository). Narrow tools' restriction to just the latter before the blanket expansion.
FORBIDDEN["tools"] = (FORBIDDEN["tools"] - {"repository"}) | {"repository.sql_repository"}

for _package, _forbidden in FORBIDDEN.items():
    if "repository" in _forbidden:
        _forbidden |= {"repository.sql_repository", "repository.vector_repository"}

# middleware and services are siblings that must not import each other.
FORBIDDEN["middleware"] |= {"services"}
FORBIDDEN["services"] |= {"middleware"}


def imported_packages(path: Path) -> set[str]:
    packages = set()
    for node in ast.walk(ast.parse(path.read_text())):
        names = [node.module or ""] if isinstance(node, ast.ImportFrom) else []
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        for name in names:
            if not name.startswith("app.") or name.count(".") < 1:
                continue
            parts = name.split(".")
            if parts[1] == "repository" and len(parts) >= 3 and parts[2] in {"sql_repository", "vector_repository"}:
                packages.add(f"repository.{parts[2]}")
            else:
                packages.add(parts[1])
    return packages


@pytest.mark.parametrize("package", sorted(FORBIDDEN))
def test_package_imports_only_lower_layers(package):
    for path in (APP / package).rglob("*.py"):
        bad = imported_packages(path) & FORBIDDEN[package]
        assert not bad, f"{path.relative_to(APP.parent)} imports app.{', app.'.join(sorted(bad))}"
