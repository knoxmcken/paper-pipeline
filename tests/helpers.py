"""Shared test helpers.

Kept in one module rather than imported from another test module: tests that
import each other break as soon as the import layout changes (which is exactly
how this repo's first CI run went red - ``tests`` was not a package).
"""

from __future__ import annotations

from typing import Dict, Optional


def make_paper(**overrides) -> Dict[str, object]:
    """A canonical paper record, matching the columns of the ``papers`` table."""
    base = {
        "arxiv_id": "2401.00001",
        "version": "v1",
        "title": "T",
        "abstract": "A",
        "authors": ["A. Author"],
        "primary_category": "cs.CL",
        "categories": ["cs.CL"],
        "published": "2024-01-01T00:00:00Z",
        "updated": "2024-01-01T00:00:00Z",
        "doi": None,
        "journal_ref": None,
        "comment": None,
        "abs_url": "http://arxiv.org/abs/2401.00001",
        "pdf_url": "http://arxiv.org/pdf/2401.00001",
        "pdf_path": None,
        "pdf_sha256": None,
        "pdf_bytes": None,
        "text_path": None,
        "text_chars": None,
        "page_count": None,
        "headings": None,
        "fetched_at": "2024-01-02T00:00:00Z",
        "extracted_at": None,
    }
    base.update(overrides)
    return base


def minimal_pdf(text: str = "Hello paperpipe") -> bytes:
    """A valid one-page PDF, built by hand so tests need no PDF authoring tool.

    ``mutool convert`` cannot turn text into a PDF, and we do not want a
    reportlab-style dependency just to have something for pdftotext to chew on.
    """
    content = f"BT /F1 12 Tf 20 100 Td ({text}) Tj ET".encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref_at = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_at}\n%%EOF\n"
    ).encode()
    return bytes(out)


def write_pdf(path, text: Optional[str] = None):
    """Write ``minimal_pdf`` to ``path``, creating parent directories."""
    from pathlib import Path

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(minimal_pdf(text) if text else minimal_pdf())
    return path
