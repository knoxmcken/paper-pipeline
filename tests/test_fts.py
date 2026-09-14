import pytest

from paperpipe import db
from tests.helpers import make_paper


@pytest.fixture()
def conn(tmp_path):
    c = db.connect(tmp_path / "papers.db")
    db.init_db(c)
    yield c
    c.close()


def _seed_text(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return str(path)


def test_index_fulltext_splits_pages_and_search_ranks_hits(conn, tmp_path):
    db.upsert_papers(conn, [make_paper(title="Prompt Injection Survey")])
    text_path = _seed_text(
        tmp_path, "p1.txt",
        "intro page, nothing relevant here\fprompt injection against CTF agents is discussed here"
    )
    n = db.index_fulltext(conn, "2401.00001", text_path)
    assert n == 2  # one row per non-blank page

    hits = db.search_fulltext(conn, "prompt injection")
    assert len(hits) == 1
    assert hits[0]["arxiv_id"] == "2401.00001"
    assert hits[0]["page"] == 2
    assert hits[0]["title"] == "Prompt Injection Survey"
    assert "[prompt injection]" in hits[0]["snippet"] or "prompt" in hits[0]["snippet"].lower()


def test_index_fulltext_is_idempotent_on_reextraction(conn, tmp_path):
    db.upsert_papers(conn, [make_paper()])
    text_path = _seed_text(tmp_path, "p1.txt", "graph neural networks are great")
    db.index_fulltext(conn, "2401.00001", text_path)
    db.index_fulltext(conn, "2401.00001", text_path)  # re-extract shouldn't duplicate rows
    count = conn.execute("SELECT COUNT(*) FROM papers_fts WHERE arxiv_id=?", ("2401.00001",)).fetchone()[0]
    assert count == 1


def test_backfill_indexes_papers_extracted_before_fts_existed(conn, tmp_path):
    db.upsert_papers(conn, [make_paper()])
    text_path = _seed_text(tmp_path, "p1.txt", "graph neural networks are great")
    # Simulate a pre-FTS database: text_path/extracted_at set directly, no FTS rows.
    db.update_extraction(
        conn, "2401.00001",
        {"text_path": text_path, "text_chars": 30, "page_count": 1, "headings": []},
        "2024-01-01T00:00:00Z",
    )
    assert conn.execute("SELECT COUNT(*) FROM papers_fts").fetchone()[0] == 0

    indexed = db.backfill_fts(conn)
    assert indexed == 1
    assert db.search_fulltext(conn, "graph neural")[0]["arxiv_id"] == "2401.00001"

    # backfill is a no-op once everything is indexed
    assert db.backfill_fts(conn) == 0


def test_search_fulltext_does_not_break_metadata_search(conn, tmp_path):
    db.upsert_papers(conn, [make_paper(title="Graphs")])
    assert [p["arxiv_id"] for p in db.search(conn, "graph")] == ["2401.00001"]
