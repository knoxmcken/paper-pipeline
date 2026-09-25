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


def test_parse_outline_indents_nested_entries_and_unescapes():
    output = "\n".join(
        [
            '|\t"Introduction"\t#page=1&view=Fit',
            '-\t"Method"\t#page=2&view=Fit',
            '|\t\t"The \\"Q\\" Data"\t#page=3&view=Fit',
            '+\t"Results  and\\nDiscussion"\t#page=4',
            "not an outline line",
        ]
    )
    assert extract.parse_outline(output) == [
        "Introduction",
        "Method",
        '  The "Q" Data',
        "Results and Discussion",
    ]


def test_parse_outline_respects_limit_and_empty_output():
    output = "\n".join(f'|\t"S{i}"\t#page=1' for i in range(10))
    assert len(extract.parse_outline(output, limit=3)) == 3
    assert extract.parse_outline("") == []


def test_pdf_outline_is_empty_without_mutool(tmp_path, monkeypatch):
    pdf = tmp_path / "in.pdf"
    pdf.write_bytes(minimal_pdf(outline=[("Introduction", [])]))
    monkeypatch.setattr(extract, "_which", lambda *names: None)
    assert extract.pdf_outline(pdf) == []


@pytest.mark.skipif(shutil.which("mutool") is None, reason="mutool not installed")
def test_pdf_outline_reads_embedded_outline(tmp_path):
    pdf = tmp_path / "in.pdf"
    pdf.write_bytes(
        minimal_pdf(outline=[("Introduction", []), ("Method", ["Data (v2)"]), ("Results", [])])
    )
    assert extract.pdf_outline(pdf) == ["Introduction", "Method", "  Data (v2)", "Results"]


@pytest.mark.skipif(shutil.which("mutool") is None, reason="mutool not installed")
def test_pdf_outline_is_empty_when_pdf_has_none(tmp_path):
    pdf = tmp_path / "in.pdf"
    pdf.write_bytes(minimal_pdf())
    assert extract.pdf_outline(pdf) == []


@pytest.mark.skipif(extract.available_backend() is None, reason="no PDF text backend")
def test_extract_prefers_outline_and_records_method(tmp_path, monkeypatch):
    pdf = tmp_path / "in.pdf"
    pdf.write_bytes(minimal_pdf("1 Introduction"))
    monkeypatch.setattr(extract, "pdf_outline", lambda path: ["Overview", "  Details"])
    info = extract.extract(pdf, tmp_path / "text")
    assert info["headings"] == ["Overview", "  Details"]
    assert info["headings_method"] == "outline"


@pytest.mark.skipif(extract.available_backend() is None, reason="no PDF text backend")
def test_extract_falls_back_to_regex_without_outline(tmp_path, monkeypatch):
    pdf = tmp_path / "in.pdf"
    pdf.write_bytes(minimal_pdf("1 Introduction"))
    monkeypatch.setattr(extract, "pdf_outline", lambda path: [])
    info = extract.extract(pdf, tmp_path / "text")
    assert info["headings"] == ["1 Introduction"]
    assert info["headings_method"] == "regex"
