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
