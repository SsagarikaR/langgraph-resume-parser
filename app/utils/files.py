import os
import re
from pathlib import Path


def safe_filename(name: str) -> str:
    """Strip anything that could form a path from a value used in a filename."""
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")[:60] or "candidate"


def write_private(path: Path, data: bytes) -> None:
    """Write a file only the app's own user can read (0600): it holds personal data.

    The mode is set when the file is created, so it is never readable by others, even briefly.
    """
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
