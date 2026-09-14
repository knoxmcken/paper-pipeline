"""End-to-end coverage of the local stages, offline.

Seeds a real SQLite database next to a real PDF, then drives the CLI through
extract -> index -> export and checks the artefacts on disk. No network, so it is
safe to run in CI on every push.
"""

import shutil

import pytest

from paperpipe import cli, db
from tests.helpers import make_paper, write_pdf

pytestmark = pytest.mark.skipif(
    shutil.which("pdftotext") is None and shutil.which("mutool") is None,
    reason="no PDF text backend installed",
)


def _args(tmp_path, *argv):
    return cli.build_parser().parse_args(["--data-dir", str(tmp_path), *argv])


def _seed(tmp_path, arxiv_id="2401.00001", text="Hello paperpipe"):
    conn = db.connect(tmp_path / "papers.db")
    db.init_db(conn)
    pdf = write_pdf(tmp_path / "pdfs" / f"{arxiv_id}.pdf", text)
    db.upsert_papers(conn, [make_paper(arxiv_id=arxiv_id, pdf_path=str(pdf))])
    conn.close()
    return pdf


def test_extract_index_export_round_trip(tmp_path):
    _seed(tmp_path)

    assert cli.cmd_extract(_args(tmp_path, "extract")) == 0
    assert cli.cmd_index(_args(tmp_path, "index")) == 0
    assert cli.cmd_index(_args(tmp_path, "index", "--check")) == 0
    assert cli.cmd_export(_args(tmp_path, "export", "--format", "all")) == 0

    assert "paperpipe" in (tmp_path / "text" / "2401.00001.txt").read_text(encoding="utf-8")
    assert "2401.00001" in (tmp_path / "exports" / "papers.md").read_text(encoding="utf-8")
    assert (tmp_path / "exports" / "papers.csv").exists()

    conn = db.connect(tmp_path / "papers.db")
    info = db.get_paper(conn, "2401.00001")
    conn.close()
    assert info["page_count"] == 1
    assert info["text_chars"] > 0


def test_index_check_catches_a_stale_index(tmp_path, capsys):
    _seed(tmp_path, arxiv_id="2401.00002")
    assert cli.cmd_index(_args(tmp_path, "index")) == 0

    # a paper arrives without regenerating the index -> --check must complain
    conn = db.connect(tmp_path / "papers.db")
    db.upsert_papers(conn, [make_paper(arxiv_id="2401.00003", title="Late arrival")])
    conn.close()

    assert cli.cmd_index(_args(tmp_path, "index", "--check")) == 1
    assert "STALE" in capsys.readouterr().out


def test_extract_fails_loudly_when_the_pdf_is_missing(tmp_path, capsys):
    conn = db.connect(tmp_path / "papers.db")
    db.init_db(conn)
    db.upsert_papers(conn, [make_paper(pdf_path="/nonexistent/nope.pdf")])
    conn.close()

    assert cli.cmd_extract(_args(tmp_path, "extract")) == 1
    assert "no local PDF" in capsys.readouterr().err


def test_stats_and_search_read_from_the_database(tmp_path, capsys):
    _seed(tmp_path)
    cli.cmd_extract(_args(tmp_path, "extract"))

    assert cli.cmd_stats(_args(tmp_path, "stats")) == 0
    assert "papers=1" in capsys.readouterr().out

    assert cli.cmd_show(_args(tmp_path, "show", "2401.00001")) == 0
    assert "2401.00001" in capsys.readouterr().out
