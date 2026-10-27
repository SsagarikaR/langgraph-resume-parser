#!/usr/bin/env python3
"""Drop requirement lines whose environment marker does not match this interpreter.

requirements/*.txt are locked with `uv pip compile --universal`, so a package needed at
different versions on different Python versions appears as two lines (same name, markers that
never both match). pip-audit's --disable-pip static parser cannot handle two lines for one
package name, even when their markers are mutually exclusive, so CI audits a filtered copy
instead of the lockfile itself. Usage: filter_requirements_for_audit.py IN.txt OUT.txt
"""

import sys
from pathlib import Path

from packaging.requirements import Requirement


def matches(line: str) -> bool:
    name_and_marker = line.split("\\", 1)[0].strip()
    marker = Requirement(name_and_marker).marker
    return marker is None or marker.evaluate()


def filter_requirements(text: str) -> str:
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip() or line.startswith(("#", " ", "\t")):
            out.append(line)
            i += 1
            continue
        block = [line]
        i += 1
        while i < len(lines) and lines[i].startswith((" ", "\t")):
            block.append(lines[i])
            i += 1
        if matches(line):
            out.extend(block)
    return "".join(out)


def main() -> None:
    src, dst = sys.argv[1], sys.argv[2]
    Path(dst).write_text(filter_requirements(Path(src).read_text()))


if __name__ == "__main__":
    main()
