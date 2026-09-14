import shutil

import pytest

from paperpipe import extract
from tests.helpers import minimal_pdf


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
    pdf.write_bytes(minimal_pdf("Hello paperpipe"))
    text = extract.pdf_to_text(pdf)
    assert "paperpipe" in text
    info = extract.extract(pdf, tmp_path / "text")
    assert info["page_count"] == 1
    assert info["text_chars"] > 0
    assert (tmp_path / "text" / "in.txt").read_text().strip() != ""
