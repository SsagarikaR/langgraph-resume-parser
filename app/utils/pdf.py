"""Text extraction from uploaded PDFs, isolated in a child process.

Uploads are untrusted, and a crafted PDF can make a parser spin or allocate without end, which no
check on the file up front can rule out. Each extraction therefore runs in its own process with a
memory cap, and is killed if it outlasts PDF_TIMEOUT_SECONDS: a hostile file costs one short-lived
process, never a worker thread of the API.
"""

import io
import multiprocessing
from multiprocessing.connection import Connection
from pathlib import Path
from typing import BinaryIO

from pypdf import PdfReader
from pypdf.errors import PyPdfError

from app.core.config import get_settings

# Resumes and JDs are a few pages; a cap stops a crafted many-page PDF from tying up a worker
MAX_PAGES = 50
# Address space of the extracting process: far more than a resume needs, far less than the API has
MEMORY_LIMIT_BYTES = 1024 * 1024 * 1024
# spawn, not fork: forking a multi-threaded server can copy a lock another thread holds
_CONTEXT = multiprocessing.get_context("spawn")


class UnreadablePdfError(Exception):
    """The file starts like a PDF but cannot be used: corrupt, truncated, encrypted, too long, or
    too costly to read."""


def extract_text_from_pdf(pdf: str | Path | BinaryIO, timeout: float | None = None) -> str:
    """Extract raw text from a PDF file path or an in-memory stream, in a child process killed
    after `timeout` seconds (default PDF_TIMEOUT_SECONDS)."""
    data = Path(pdf).read_bytes() if isinstance(pdf, str | Path) else pdf.read()
    timeout = timeout if timeout is not None else get_settings().pdf_timeout_seconds
    receiver, sender = _CONTEXT.Pipe(duplex=False)
    process = _CONTEXT.Process(target=_extract_into, args=(data, sender), daemon=True)
    process.start()
    sender.close()  # the child holds the only writing end: its exit ends the pipe
    try:
        if not receiver.poll(timeout):
            raise UnreadablePdfError(f"Reading the PDF took longer than {timeout:g} s")
        try:
            ok, value = receiver.recv()
        except EOFError:  # the child died without answering, e.g. over its memory limit
            raise UnreadablePdfError("Could not read the PDF (the reader ran out of resources)") from None
    finally:
        receiver.close()
        if process.is_alive():
            process.kill()
        process.join()
    if not ok:
        raise UnreadablePdfError(value)
    return value


def _cap_memory() -> None:
    """Cap this process's address space. Only ever call it in the child: the limit cannot be
    raised again, so in the API (or a test run) every later thread start would fail."""
    try:
        import resource

        resource.setrlimit(resource.RLIMIT_AS, (MEMORY_LIMIT_BYTES, MEMORY_LIMIT_BYTES))
    except (ImportError, ValueError, OSError):  # not on this platform: the timeout still applies
        pass


def _extract_into(data: bytes, sender: Connection) -> None:
    """Child process: send back (True, text) or (False, a message safe to show the client)."""
    _cap_memory()
    try:
        sender.send((True, _extract(data)))
    except UnreadablePdfError as e:
        sender.send((False, str(e)))
    except Exception as e:  # a crafted file can make pypdf raise anything
        sender.send((False, f"Could not read the PDF ({type(e).__name__})"))
    finally:
        sender.close()


def _extract(data: bytes) -> str:
    try:
        reader = PdfReader(io.BytesIO(data))
        if len(reader.pages) > MAX_PAGES:
            raise UnreadablePdfError(f"The PDF has more than {MAX_PAGES} pages")
        pages = [page.extract_text() or "" for page in reader.pages]
    except PyPdfError as e:
        raise UnreadablePdfError(f"Could not read the PDF ({type(e).__name__})") from e
    return "\n".join(pages).strip()
