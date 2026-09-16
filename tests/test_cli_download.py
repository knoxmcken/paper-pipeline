"""The ``download`` stage: fill in PDFs for papers already in the database."""

import pytest

from paperpipe import cli, db
from tests.helpers import make_paper


def _args(tmp_path, *argv):
    return cli.build_parser().parse_args(["--data-dir", str(tmp_path), *argv])


def _seed(tmp_path, rows):
    conn = db.connect(tmp_path / "papers.db")
    db.init_db(conn)
    db.upsert_papers(conn, rows)
    conn.close()


def _stub(monkeypatch, calls=None, result=None):
    def fake(arxiv_id, dest_dir, **kwargs):
        if calls is not None:
            calls.append((arxiv_id, kwargs.get("url")))
        return result or {"path": "p.pdf", "sha256": "s", "bytes": 12, "skipped": False}

    monkeypatch.setattr(cli.fetch, "download_pdf", fake)


def test_download_fills_missing_pdfs_and_records_the_path(tmp_path, monkeypatch):
    _seed(tmp_path, [make_paper(pdf_url="https://x/a.pdf")])
    calls = []
    _stub(monkeypatch, calls)

    assert cli.cmd_download(_args(tmp_path, "download")) == 0
    assert calls == [("2401.00001", "https://x/a.pdf")]

    conn = db.connect(tmp_path / "papers.db")
    assert db.get_paper(conn, "2401.00001")["pdf_path"] == "p.pdf"
    conn.close()


def test_download_skips_rows_without_a_stored_url(tmp_path, monkeypatch, capsys):
    _seed(tmp_path, [make_paper(pdf_url=None)])
    monkeypatch.setattr(cli.fetch, "download_pdf",
                        lambda *a, **k: pytest.fail("should not attempt a download"))

    assert cli.cmd_download(_args(tmp_path, "download")) == 0
    assert "no stored PDF url" in capsys.readouterr().out


def test_download_url_override_is_used_for_one_id(tmp_path, monkeypatch):
    _seed(tmp_path, [make_paper(pdf_url="https://doi.org/10.1/paywalled")])
    calls = []
    _stub(monkeypatch, calls)

    argv = ("download", "--id", "2401.00001", "--url", "https://arxiv.org/pdf/2401.00001")
    assert cli.cmd_download(_args(tmp_path, *argv)) == 0
    assert calls == [("2401.00001", "https://arxiv.org/pdf/2401.00001")]


def test_download_url_override_requires_exactly_one_id(tmp_path, monkeypatch):
    _seed(tmp_path, [make_paper(), make_paper(arxiv_id="2401.00002")])
    monkeypatch.setattr(cli.fetch, "download_pdf",
                        lambda *a, **k: pytest.fail("should not attempt a download"))

    args = _args(tmp_path, "download", "--url", "https://x/y.pdf")
    assert cli.cmd_download(args) == 2


def test_download_reports_failure_as_nonzero(tmp_path, monkeypatch, capsys):
    _seed(tmp_path, [make_paper()])

    def boom(*a, **k):
        raise cli.fetch.FetchError("nope")

    monkeypatch.setattr(cli.fetch, "download_pdf", boom)
    assert cli.cmd_download(_args(tmp_path, "download")) == 1
    assert "FAIL" in capsys.readouterr().err


def test_download_prefers_an_explicit_id_list(tmp_path, monkeypatch):
    _seed(tmp_path, [make_paper(), make_paper(arxiv_id="2401.00002")])
    calls = []
    _stub(monkeypatch, calls)

    assert cli.cmd_download(_args(tmp_path, "download", "--id", "2401.00002")) == 0
    assert [c[0] for c in calls] == ["2401.00002"]


def test_fetch_drops_duplicate_discovery_keys(tmp_path, monkeypatch, capsys):
    """Two works can resolve to the same arXiv key (preprint + journal version)."""
    discovered = [make_paper(), make_paper(title="same key again"),
                  make_paper(arxiv_id="2401.00002", title="second")]
    monkeypatch.setattr(cli, "_discover", lambda args, session: discovered)

    assert cli.cmd_fetch(_args(tmp_path, "fetch", "-q", "x", "--no-download")) == 0
    assert "duplicate key(s) dropped" in capsys.readouterr().out

    conn = db.connect(tmp_path / "papers.db")
    assert db.stats(conn)["papers"] == 2


def test_fetch_runs_unpaywall_enrichment_before_storing(tmp_path, monkeypatch, capsys):
    """A paywalled discovery result with a DOI gets a shot at an OA copy first."""
    discovered = [make_paper(doi="10.1/x", pdf_url=None)]
    monkeypatch.setattr(cli, "_discover", lambda args, session: discovered)

    def fake_enrich(papers, **kwargs):
        papers[0]["pdf_url"] = "https://example.org/oa.pdf"
        return 1

    monkeypatch.setattr(cli.unpaywall, "enrich_missing_pdfs", fake_enrich)

    assert cli.cmd_fetch(_args(tmp_path, "fetch", "-q", "x", "--no-download")) == 0
    assert "unpaywall: resolved 1" in capsys.readouterr().out

    conn = db.connect(tmp_path / "papers.db")
    assert db.get_paper(conn, "2401.00001")["pdf_url"] == "https://example.org/oa.pdf"


def test_fetch_no_unpaywall_flag_skips_enrichment(tmp_path, monkeypatch):
    discovered = [make_paper(doi="10.1/x", pdf_url=None)]
    monkeypatch.setattr(cli, "_discover", lambda args, session: discovered)
    monkeypatch.setattr(
        cli.unpaywall, "enrich_missing_pdfs",
        lambda *a, **k: pytest.fail("should not be called"),
    )

    argv = ("fetch", "-q", "x", "--no-download", "--no-unpaywall")
    assert cli.cmd_fetch(_args(tmp_path, *argv)) == 0
