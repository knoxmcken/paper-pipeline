import shutil

import pytest

from paperpipe import extract


def _minimal_pdf(text: str = "Hello paperpipe") -> bytes:
    """A valid one-page PDF, built by hand so tests need no PDF authoring tool."""
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


def test_page_count_counts_form_feeds():
    assert extract.page_count("a\fb\fc\f") == 3
    assert extract.page_count("a\fb") == 2
    assert extract.page_count("") == 0


def test_extract_headings_finds_numbered_sections():
    text = "\n".join(
        [
            "Some title page",
            "1 Introduction",
            "body text goes here",
            "2.1 Method Overview",
            "2.2 Data",
            "References",
            "A really long line that should be ignored because it exceeds the heading length limit for sure",
        ]
    )
    headings = extract.extract_headings(text)
    assert "1 Introduction" in headings
    assert "2.1 Method Overview" in headings
    assert "2.2 Data" in headings


def test_available_backend_is_installed_on_this_host():
    assert extract.available_backend() in ("pdftotext", "mutool", None)


@pytest.mark.skipif(shutil.which("pdftotext") is None, reason="pdftotext not installed")
def test_pdf_to_text_and_extract_round_trip(tmp_path):
    pdf = tmp_path / "in.pdf"
    pdf.write_bytes(_minimal_pdf("Hello paperpipe"))
    text = extract.pdf_to_text(pdf)
    assert "paperpipe" in text
    info = extract.extract(pdf, tmp_path / "text")
    assert info["page_count"] == 1
    assert info["text_chars"] > 0
    assert (tmp_path / "text" / "in.txt").read_text().strip() != ""
