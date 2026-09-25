import json

import pytest

from paperpipe import db, export, index
from tests.helpers import make_paper


@pytest.fixture()
def conn(tmp_path):
    c = db.connect(tmp_path / "papers.db")
    db.init_db(c)
    yield c
    c.close()


def test_index_is_derived_from_db(conn, tmp_path):
    db.upsert_papers(conn, [make_paper(title="Beta"), make_paper(arxiv_id="2401.00002", title="alpha")])
    path = tmp_path / "index.json"
    payload = index.build(conn, path)
    assert payload["count"] == 2
    assert [p["title"] for p in payload["papers"]] == ["alpha", "Beta"]  # case-insensitive sort
    assert json.loads(path.read_text())["count"] == 2
    assert index.check(conn, path) is True


def test_index_check_detects_drift(conn, tmp_path):
    db.upsert_papers(conn, [make_paper()])
    path = tmp_path / "index.json"
    index.build(conn, path)
    db.upsert_papers(conn, [make_paper(arxiv_id="2401.00009", title="new")])
    assert index.check(conn, path) is False
    assert index.check(conn, tmp_path / "missing.json") is False


def test_markdown_export_groups_by_category(conn, tmp_path):
    db.upsert_papers(
        conn,
        [
            make_paper(title="One", headings=["1 Introduction"]),
            make_paper(arxiv_id="2401.2", title="Two", primary_category="cs.AI"),
        ],
    )
    path = export.write_markdown(conn, tmp_path / "exports" / "papers.md")
    body = path.read_text()
    assert body.startswith("# Paper Index")
    assert "## cs.AI" in body and "## cs.CL" in body
    assert "**Authors:** A. Author" in body
    assert "**Sections:** 1 Introduction" in body


def test_csv_export_has_one_row_per_paper(conn, tmp_path):
    db.upsert_papers(conn, [make_paper()])
    path = export.build_csv(conn, tmp_path / "papers.csv")
    lines = path.read_text().strip().splitlines()
    assert len(lines) == 2 and lines[0].startswith("arxiv_id,title")


TRICKY = make_paper(
    arxiv_id="doi:10.1000/xyz.1",
    title='Tricky <title> & "quotes"\x07 with a bell',
    authors=["Ada Lovelace", "Grace Hopper"],
    published="2023-06-01",
    abs_url=None,
    doi="10.1000/xyz.1",
    page_count=12,
)


def test_render_csv_has_the_download_columns():
    import csv
    import io

    rows = list(csv.DictReader(io.StringIO(export.render_csv([make_paper(), TRICKY]))))
    assert len(rows) == 2
    arxiv, tricky = rows
    assert arxiv["arxiv"] == "2401.00001" and arxiv["year"] == "2024" and arxiv["doi"] == ""
    assert tricky["arxiv"] == "" and tricky["doi"] == "10.1000/xyz.1"
    assert tricky["url"] == "https://doi.org/10.1000/xyz.1"
    assert tricky["authors"] == "Ada Lovelace; Grace Hopper"


def test_render_xlsx_is_a_valid_workbook_with_one_row_per_paper():
    import io

    openpyxl = pytest.importorskip("openpyxl")
    book = openpyxl.load_workbook(io.BytesIO(export.render_xlsx([make_paper(), TRICKY])))
    sheet = book.active
    assert sheet.title == "papers"
    rows = list(sheet.iter_rows(values_only=True))
    assert list(rows[0]) == export.TABLE_COLUMNS
    assert len(rows) == 3
    tricky = dict(zip(export.TABLE_COLUMNS, rows[2]))
    assert tricky["title"] == 'Tricky <title> & "quotes" with a bell'  # control char dropped
    assert tricky["page_count"] == 12 and tricky["metadata_only"] is True
    assert tricky["doi"] == "10.1000/xyz.1" and tricky["arxiv"] is None


def test_render_xlsx_handles_an_empty_list_and_is_deterministic():
    import io

    openpyxl = pytest.importorskip("openpyxl")
    assert export.render_xlsx([make_paper()]) == export.render_xlsx([make_paper()])
    rows = list(openpyxl.load_workbook(io.BytesIO(export.render_xlsx([]))).active.iter_rows())
    assert len(rows) == 1


def test_xlsx_column_names():
    assert [export._xlsx_column(i) for i in (0, 25, 26, 27, 701, 702)] == [
        "A", "Z", "AA", "AB", "ZZ", "AAA"
    ]


def test_render_markdown_from_a_list():
    body = export.render_markdown([TRICKY], title="Mine", category=False)
    assert body.startswith("# Mine") and "**1 papers**" in body
