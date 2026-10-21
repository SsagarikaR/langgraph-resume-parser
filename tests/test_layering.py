"""The packages depend in one direction only, so none can import its way into a cycle:

api → services → repositories / graph → tools → knowledge → core
"""

import ast
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[1] / "app"

# Packages each package must never import (directly)
FORBIDDEN = {
    "core": {"api", "services", "repositories", "graph", "tools", "knowledge"},
    "knowledge": {"api", "services", "repositories", "graph", "tools"},
    "tools": {"api", "services", "repositories", "graph"},
    "graph": {"api", "services"},
    "services": {"api"},
}


def imported_packages(path: Path) -> set[str]:
    packages = set()
    for node in ast.walk(ast.parse(path.read_text())):
        names = [node.module or ""] if isinstance(node, ast.ImportFrom) else []
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        packages |= {name.split(".")[1] for name in names if name.startswith("app.") and name.count(".") >= 1}
    return packages


@pytest.mark.parametrize("package", sorted(FORBIDDEN))
def test_package_imports_only_lower_layers(package):
    for path in (APP / package).rglob("*.py"):
        bad = imported_packages(path) & FORBIDDEN[package]
        assert not bad, f"{path.relative_to(APP.parent)} imports app.{', app.'.join(sorted(bad))}"
