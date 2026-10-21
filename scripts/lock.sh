#!/usr/bin/env bash
# Regenerate the hash-pinned lockfiles in requirements/ from pyproject.toml, which stays the one
# place dependencies are declared. Run after changing them (needs uv: pip install uv):
#   scripts/lock.sh              # keep current pins where still allowed
#   scripts/lock.sh --upgrade    # move every pin to the newest allowed version
# --universal: one lockfile for every Python (3.10+) and platform the images and CI use.
set -euo pipefail
cd "$(dirname "$0")/.."

compile() {
  uv pip compile --universal --generate-hashes --python-version 3.10 --quiet \
    --custom-compile-command "scripts/lock.sh" "$@"
}

compile pyproject.toml -o requirements/api.txt "${@}"                     # API image
sed -n '/^ui = \[/,/^\]/p' pyproject.toml | grep -o '"[^"]*"' | tr -d '"' \
  | compile - -o requirements/ui.txt "${@}"                                # UI image: Streamlit + requests only
compile pyproject.toml --extra dev -o requirements/dev.txt "${@}"         # CI and local development
