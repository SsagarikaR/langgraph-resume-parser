"""Turn a plain-text resume into a one-page PDF for an eval case, with no extra dependencies:

    python -m evals.text_to_pdf evals/fixtures/frontend_junior.txt data/resumes/frontend_junior.pdf

The PDF has real text (Helvetica, one line per line of input), so pypdf extracts it the way it
extracts a resume exported from a word processor.
"""

import sys
from pathlib import Path

LINE_HEIGHT = 13
TOP, LEFT = 800, 50


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def text_to_pdf(text: str) -> bytes:
    lines = text.splitlines()
    stream = f"BT /F1 10 Tf {LINE_HEIGHT} TL {LEFT} {TOP} Td "
    stream += " ".join(f"({_escape(line)}) Tj T*" for line in lines) + " ET"
    content = stream.encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    return bytes(out)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("usage: python -m evals.text_to_pdf <resume.txt> <resume.pdf>")
    Path(sys.argv[2]).write_bytes(text_to_pdf(Path(sys.argv[1]).read_text(encoding="utf-8")))
