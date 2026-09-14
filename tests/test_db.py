import pytest

from paperpipe import db
from tests.helpers import make_paper


@pytest.fixture()
def conn(tmp_path):
    c = db.connect(tmp_path / "papers.db")
    db.init_db(c)
    yield c
    c.close()


def test_upsert_is_idempotent_and_preserves_pdf(conn):
    db.upsert_papers(conn, [make_paper()])
    db.update_pdf(conn, "2401.00001", {"path": "p.pdf", "sha256": "x", "bytes": 10})
    db.upsert_papers(conn, [make_paper(title="T2", pdf_path=None)])
    row = db.get_paper(conn, "2401.00001")
    assert row["title"] == "T2"
    assert row["pdf_path"] == "p.pdf"  # COALESCE keeps the stored pdf on re-upsert
    assert row["authors"] == ["A. Author"]


def test_missing_and_stats_and_search(conn):
    db.upsert_papers(conn, [make_paper(), make_paper(arxiv_id="2401.00002", title="Graphs")])
    db.update_extraction(
        conn, "2401.00002",
        {"text_path": "t.txt", "text_chars": 5, "page_count": 3, "headings": ["1 Intro"]},
        "2024-01-03T00:00:00Z",
    )
    assert [p["arxiv_id"] for p in db.papers_missing(conn, "text")] == ["2401.00001"]
    assert len(db.papers_missing(conn, "pdf")) == 2
    assert db.stats(conn) == {"papers": 2, "with_pdf": 0, "with_text": 1, "pages": 3}
    assert [p["arxiv_id"] for p in db.search(conn, "graph")] == ["2401.00002"]
    assert db.get_paper(conn, "2401.00002")["headings"] == ["1 Intro"]


def test_runs_are_recorded(conn):
    run_id = db.start_run(conn, "fetch", "{}", "2024-01-01T00:00:00Z")
    db.finish_run(conn, run_id, True, "3 metadata", "2024-01-01T00:01:00Z")
    row = conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
    assert row["kind"] == "fetch" and row["ok"] == 1 and row["message"] == "3 metadata"
