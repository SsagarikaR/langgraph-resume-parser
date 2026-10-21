"""Text extraction from untrusted PDFs: a capped, time-limited child process."""

import io

import pytest
from pypdf import PdfWriter

from app.utils import pdf
from app.utils.pdf import MAX_PAGES, UnreadablePdfError, extract_text_from_pdf


def pdf_with_pages(count: int) -> bytes:
    writer = PdfWriter()
    for _ in range(count):
        writer.add_blank_page(width=72, height=72)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def test_pdf_page_limit():
    with pytest.raises(UnreadablePdfError, match="more than"):
        extract_text_from_pdf(io.BytesIO(pdf_with_pages(MAX_PAGES + 1)))


def test_pdf_reading_that_takes_too_long_is_stopped():
    with pytest.raises(UnreadablePdfError, match="took longer than"):
        extract_text_from_pdf(io.BytesIO(pdf_with_pages(1)), timeout=0.001)


def test_any_failure_inside_the_pdf_reader_is_an_unreadable_pdf():
    with pytest.raises(UnreadablePdfError, match="Could not read the PDF"):
        extract_text_from_pdf(io.BytesIO(b"%PDF-1.7 \x00\xff garbage"))


def test_pdf_text_is_extracted_in_process_too():
    # The child process is not measured by coverage; the same code is exercised here directly
    assert pdf._extract(pdf_with_pages(1)) == ""
    with pytest.raises(UnreadablePdfError, match="more than"):
        pdf._extract(pdf_with_pages(MAX_PAGES + 1))


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (b"", (False, "Could not read the PDF")),  # pypdf raises on an empty file
        (b"%PDF-1.7 \x00\xff garbage", (False, "Could not read the PDF")),
    ],
)
def test_the_reader_process_answers_with_a_message_safe_to_show(data, expected, monkeypatch):
    sent = []
    capped = []

    class Pipe:
        def send(self, value):
            sent.append(value)

        def close(self):
            pass

    # This runs the child's code in the test process: the real cap would limit pytest itself for
    # the rest of the run (it cannot be raised again), so later thread starts would fail
    monkeypatch.setattr(pdf, "_cap_memory", lambda: capped.append(True))
    pdf._extract_into(data, Pipe())  # type: ignore[arg-type]
    assert capped == [True]  # the child does cap its memory first
    ok, message = sent[0]
    assert (ok, message.startswith(expected[1])) == (expected[0], True)
