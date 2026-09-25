"""Shared test helpers.

Kept in one module rather than imported from another test module: tests that
import each other break as soon as the import layout changes (which is exactly
how this repo's first CI run went red - ``tests`` was not a package).
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple


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


def minimal_pdf(
    text: str = "Hello paperpipe", outline: Optional[List[Tuple[str, List[str]]]] = None
) -> bytes:
    """A valid one-page PDF, built by hand so tests need no PDF authoring tool.

    ``mutool convert`` cannot turn text into a PDF, and we do not want a
    reportlab-style dependency just to have something for pdftotext to chew on.
    ``outline`` embeds a document outline: ``[(title, [child titles]), ...]``.
    """
    content = f"BT /F1 12 Tf 20 100 Td ({text}) Tj ET".encode("ascii")
    catalog = b"<< /Type /Catalog /Pages 2 0 R >>"
    if outline:
        catalog = b"<< /Type /Catalog /Pages 2 0 R /Outlines 6 0 R >>"
    objects = [
        catalog,
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    if outline:
        objects += _outline_objects(outline, root=6)
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


def _pdf_string(text: str) -> bytes:
    escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    return b"(" + escaped.encode("ascii") + b")"


def _outline_objects(outline: List[Tuple[str, List[str]]], root: int) -> List[bytes]:
    """Outline dictionaries numbered from ``root``, all pointing at page object 3."""
    items: List[Dict[str, object]] = []  # flattened; each knows its parent and siblings

    def add(titles: List[Tuple[str, List[str]]], parent: int) -> List[int]:
        numbers = []
        for title, children in titles:
            item = {"title": title, "parent": parent, "number": root + 1 + len(items)}
            items.append(item)
            numbers.append(item["number"])
            item["kids"] = add([(c, []) for c in children], item["number"])
        for i, number in enumerate(numbers):
            item = next(it for it in items if it["number"] == number)
            item["prev"] = numbers[i - 1] if i else None
            item["next"] = numbers[i + 1] if i + 1 < len(numbers) else None
        return numbers

    top = add(outline, root)
    objects = [
        f"<< /Type /Outlines /First {top[0]} 0 R /Last {top[-1]} 0 R /Count {len(items)} >>".encode()
    ]
    for item in items:
        body = b"<< /Title " + _pdf_string(item["title"]) + f" /Parent {item['parent']} 0 R".encode()
        if item["prev"]:
            body += f" /Prev {item['prev']} 0 R".encode()
        if item["next"]:
            body += f" /Next {item['next']} 0 R".encode()
        kids = item["kids"]
        if kids:
            body += f" /First {kids[0]} 0 R /Last {kids[-1]} 0 R /Count {len(kids)}".encode()
        objects.append(body + b" /Dest [3 0 R /Fit] >>")
    return objects


def write_pdf(path, text: Optional[str] = None):
    """Write ``minimal_pdf`` to ``path``, creating parent directories."""
    from pathlib import Path

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(minimal_pdf(text) if text else minimal_pdf())
    return path
