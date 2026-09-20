"""Multi-query harvest: repeatable --query / --queries-file, one pass, shared --max.

All offline: discovery is stubbed so no network call is made. See issue #1.
"""

from paperpipe import cli, db
from tests.helpers import make_paper


def _args(tmp_path, *argv):
    return cli.build_parser().parse_args(["--data-dir", str(tmp_path), *argv])


def _stub_discover_by_query(monkeypatch, table):
    """Route ``cli._discover`` to ``table[qargs.query]``, recording call order."""
    calls = []

    def fake(qargs, session):
        calls.append(qargs.query)
        return list(table.get(qargs.query, []))

    monkeypatch.setattr(cli, "_discover", fake)
    return calls


def test_repeatable_query_collapses_overlap_into_one_row(tmp_path, monkeypatch, capsys):
    table = {
        "llm agents": [make_paper(arxiv_id="2401.00001"), make_paper(arxiv_id="2401.00002")],
        "tool use": [make_paper(arxiv_id="2401.00002"), make_paper(arxiv_id="2401.00003")],
    }
    calls = _stub_discover_by_query(monkeypatch, table)

    args = _args(tmp_path, "fetch", "-q", "llm agents", "-q", "tool use", "--no-download")
    assert cli.cmd_fetch(args) == 0
    assert calls == ["llm agents", "tool use"]

    out = capsys.readouterr().out
    assert "query 'llm agents': 2 result(s), 2 new" in out
    assert "query 'tool use': 2 result(s), 1 new (1 duplicate key(s) dropped)" in out
    assert "3 unique paper(s) across 2 query/queries" in out

    conn = db.connect(tmp_path / "papers.db")
    assert db.stats(conn)["papers"] == 3
    conn.close()


def test_queries_file_merges_with_repeated_query_flags(tmp_path, monkeypatch):
    table = {
        "a": [make_paper(arxiv_id="2401.00001")],
        "b": [make_paper(arxiv_id="2401.00002")],
        "c": [make_paper(arxiv_id="2401.00003")],
    }
    calls = _stub_discover_by_query(monkeypatch, table)

    seeds = tmp_path / "seeds.txt"
    seeds.write_text("# comment\nb\n\nc\n")

    args = _args(tmp_path, "fetch", "-q", "a", "--queries-file", str(seeds), "--no-download")
    assert cli.cmd_fetch(args) == 0
    assert calls == ["a", "b", "c"]

    conn = db.connect(tmp_path / "papers.db")
    assert db.stats(conn)["papers"] == 3
    conn.close()


def test_max_is_enforced_across_the_whole_batch(tmp_path, monkeypatch, capsys):
    table = {
        "q1": [make_paper(arxiv_id="2401.00001"), make_paper(arxiv_id="2401.00002")],
        "q2": [make_paper(arxiv_id="2401.00003"), make_paper(arxiv_id="2401.00004")],
        "q3": [make_paper(arxiv_id="2401.00005")],
    }
    calls = _stub_discover_by_query(monkeypatch, table)

    args = _args(tmp_path, "fetch", "-q", "q1", "-q", "q2", "-q", "q3", "-n", "3", "--no-download")
    assert cli.cmd_fetch(args) == 0

    # q3 is never even queried once the cap is already hit.
    assert calls == ["q1", "q2"]

    conn = db.connect(tmp_path / "papers.db")
    assert db.stats(conn)["papers"] == 3
    conn.close()

    out = capsys.readouterr().out
    assert "already reached" in out
    assert "3 unique paper(s) across 3 query/queries" in out


def test_a_failing_seed_is_reported_but_does_not_abort_the_batch(tmp_path, monkeypatch, capsys):
    def fake(qargs, session):
        if qargs.query == "bad":
            raise cli.arxiv.ArxivError("boom")
        return [make_paper(arxiv_id="2401.00001")]

    monkeypatch.setattr(cli, "_discover", fake)

    args = _args(tmp_path, "fetch", "-q", "bad", "-q", "good", "--no-download")
    assert cli.cmd_fetch(args) == 0
    assert "query 'bad': FAILED (boom)" in capsys.readouterr().err

    conn = db.connect(tmp_path / "papers.db")
    assert db.stats(conn)["papers"] == 1
    conn.close()


def test_missing_query_and_queries_file_fails_cleanly(tmp_path):
    args = _args(tmp_path, "fetch", "--no-download")
    assert cli.cmd_fetch(args) == 1


def test_search_also_accepts_repeated_query_and_dedupes(tmp_path, monkeypatch, capsys):
    table = {
        "a": [make_paper(arxiv_id="2401.00001")],
        "b": [make_paper(arxiv_id="2401.00001"), make_paper(arxiv_id="2401.00002")],
    }
    _stub_discover_by_query(monkeypatch, table)

    args = _args(tmp_path, "search", "-q", "a", "-q", "b")
    assert cli.cmd_search(args) == 0
    assert "2 result(s)" in capsys.readouterr().out
