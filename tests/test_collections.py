import csv

import pytest

from paperpipe import cli, db, duplicates
from tests.helpers import make_paper

A = make_paper(arxiv_id="2401.00001", title="Alpha")
B = make_paper(arxiv_id="2401.00002", title="Beta", abs_url="b", pdf_url="b")
C = make_paper(arxiv_id="2401.00003", title="Gamma", abs_url="c", pdf_url="c")
NOW = "2024-02-01T00:00:00Z"


@pytest.fixture()
def conn(tmp_path):
    c = db.connect(tmp_path / "papers.db")
    db.init_db(c)
    db.upsert_papers(c, [A, B, C])
    yield c
    c.close()


def test_add_is_upsert_safe_and_reports_unknown_ids(conn):
    first = db.add_to_collection(conn, "short", ["2401.00001", "2401.00002", "nope"], NOW, "pass 1")
    assert first == {"added": ["2401.00001", "2401.00002"], "already": [], "missing": ["nope"]}
    again = db.add_to_collection(conn, "short", ["2401.00001"], NOW)
    assert again == {"added": [], "already": ["2401.00001"], "missing": []}
    assert db.list_collections(conn) == [
        {"name": "short", "description": "pass 1", "created_at": NOW, "papers": 2}
    ]


def test_a_paper_can_sit_in_several_collections(conn):
    db.add_to_collection(conn, "short", ["2401.00001"], NOW)
    db.add_to_collection(conn, "cited", ["2401.00001", "2401.00003"], NOW)
    assert db.paper_collections(conn, "2401.00001") == ["cited", "short"]
    assert [p["arxiv_id"] for p in db.collection_papers(conn, "cited")] == ["2401.00001", "2401.00003"]


def test_remove_and_delete_leave_papers_alone(conn):
    db.add_to_collection(conn, "short", ["2401.00001", "2401.00002"], NOW)
    assert db.remove_from_collection(conn, "short", ["2401.00002", "2401.00003"]) == {
        "removed": ["2401.00002"], "absent": ["2401.00003"]
    }
    assert db.delete_collection(conn, "short") == 1
    assert db.list_collections(conn) == []
    assert db.stats(conn)["papers"] == 3
    for call in (lambda: db.collection_papers(conn, "short"),
                 lambda: db.remove_from_collection(conn, "short", ["x"]),
                 lambda: db.delete_collection(conn, "short")):
        with pytest.raises(KeyError):
            call()


def test_memberships_follow_a_deleted_paper(conn):
    db.add_to_collection(conn, "short", ["2401.00001", "2401.00002"], NOW)
    with conn:
        conn.execute("DELETE FROM papers WHERE arxiv_id='2401.00002'")
    assert db.list_collections(conn)[0]["papers"] == 1


def test_status_and_notes(conn):
    assert db.set_status(conn, ["2401.00001", "nope"], "reading") == ["nope"]
    assert db.get_paper(conn, "2401.00001")["status"] == "reading"
    db.set_status(conn, ["2401.00001"], "new")
    assert db.get_paper(conn, "2401.00001")["status"] is None  # NULL reads as "new"
    with pytest.raises(ValueError):
        db.set_status(conn, ["2401.00001"], "maybe")

    assert db.set_notes(conn, "2401.00001", "  check section 4  ") is True
    assert db.get_paper(conn, "2401.00001")["notes"] == "check section 4"
    db.set_notes(conn, "2401.00001", "   ")
    assert db.get_paper(conn, "2401.00001")["notes"] is None
    assert db.set_notes(conn, "nope", "x") is False


def test_status_and_notes_survive_a_refetch(conn):
    db.set_status(conn, ["2401.00001"], "cited")
    db.set_notes(conn, "2401.00001", "keep me")
    db.upsert_papers(conn, [dict(A, title="Alpha v2")])
    row = db.get_paper(conn, "2401.00001")
    assert (row["title"], row["status"], row["notes"]) == ("Alpha v2", "cited", "keep me")


def test_init_db_adds_status_and_notes_to_an_old_database(tmp_path):
    c = db.connect(tmp_path / "old.db")
    old = db.SCHEMA.replace("    status           TEXT,\n", "").replace("    notes            TEXT,\n", "")
    c.executescript(old)
    db.init_db(c)
    columns = {row[1] for row in c.execute("PRAGMA table_info(papers)")}
    assert {"status", "notes"} <= columns
    c.close()


def test_merging_duplicates_keeps_collections_and_both_notes(conn):
    db.add_to_collection(conn, "short", ["2401.00002"], NOW)
    db.add_to_collection(conn, "cited", ["2401.00001", "2401.00002"], NOW)
    db.set_notes(conn, "2401.00001", "keep notes")
    db.set_notes(conn, "2401.00002", "drop notes")
    db.set_status(conn, ["2401.00002"], "shortlisted")
    duplicates.merge(conn, "2401.00001", "2401.00002")
    assert db.paper_collections(conn, "2401.00001") == ["cited", "short"]
    kept = db.get_paper(conn, "2401.00001")
    assert kept["notes"] == "keep notes\n\n[merged from 2401.00002] drop notes"
    assert kept["status"] == "shortlisted"  # KEEP had none, so DROP's fills the gap


def _cli(data, *argv):
    return cli.build_parser().parse_args(["--data-dir", str(data), *argv])


def test_cli_collections_status_notes_and_scoped_export(tmp_path, capsys):
    data = tmp_path / "data"
    c = db.connect(data / "papers.db")
    db.init_db(c)
    db.upsert_papers(c, [A, B, C])
    c.close()

    assert cli.cmd_collection(_cli(data, "collection", "add", "short", "2401.00001",
                                   "2401.00003", "--description", "pass 1")) == 0
    assert cli.cmd_collection(_cli(data, "collection", "add", "short", "ghost")) == 1
    assert cli.cmd_status(_cli(data, "status", "shortlisted", "2401.00001")) == 0
    assert cli.cmd_notes(_cli(data, "notes", "2401.00001", "why it matters")) == 0
    capsys.readouterr()

    assert cli.cmd_notes(_cli(data, "notes", "2401.00001")) == 0
    assert capsys.readouterr().out.strip() == "why it matters"
    assert cli.cmd_collection(_cli(data, "collection", "show", "short")) == 0
    shown = capsys.readouterr().out
    assert "2401.00001" in shown and "shortlisted" in shown and "2401.00002" not in shown
    assert cli.cmd_show(_cli(data, "show", "--status", "shortlisted")) == 0
    assert "1 paper(s)" in capsys.readouterr().out

    out = tmp_path / "out"
    assert cli.cmd_export(_cli(data, "export", "--collection", "short", "--format", "csv",
                               "--out", str(out))) == 0
    rows = list(csv.DictReader((out / "papers.csv").open()))
    assert sorted(r["arxiv_id"] for r in rows) == ["2401.00001", "2401.00003"]
    alpha = next(r for r in rows if r["arxiv_id"] == "2401.00001")
    assert alpha["status"] == "shortlisted" and alpha["notes"] == "why it matters"

    assert cli.cmd_export(_cli(data, "export", "--collection", "ghost")) == 2
    assert cli.cmd_collection(_cli(data, "collection", "show", "ghost")) == 2
    assert cli.cmd_notes(_cli(data, "notes", "ghost")) == 2
    assert cli.cmd_notes(_cli(data, "notes", "2401.00001", "--clear")) == 0
    assert cli.cmd_collection(_cli(data, "collection", "delete", "short")) == 0
