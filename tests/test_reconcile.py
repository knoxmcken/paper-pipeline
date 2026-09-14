import pytest

from paperpipe import db, reconcile
from tests.helpers import make_paper


@pytest.fixture()
def conn(tmp_path):
    c = db.connect(tmp_path / "papers.db")
    db.init_db(c)
    yield c
    c.close()


class FakeResponse:
    def __init__(self, content, status_code=200):
        self.content = content
        self.status_code = status_code


class FakeSession:
    def __init__(self, by_url):
        self.by_url = by_url
        self.headers = {}
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(url)
        item = self.by_url[url]
        if isinstance(item, Exception):
            raise item
        return item


def test_check_missing_files_flags_gone_pdfs(conn, tmp_path):
    pdf = tmp_path / "keep.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    db.upsert_papers(conn, [
        make_paper(pdf_path=str(pdf)),
        make_paper(arxiv_id="2401.00002", pdf_path=str(tmp_path / "gone.pdf")),
    ])
    rows = db.list_papers(conn)
    drift = reconcile.check_missing_files(rows)
    assert [d["arxiv_id"] for d in drift] == ["2401.00002"]


def test_check_dead_links_flags_non_pdf_and_failed_requests(conn):
    db.upsert_papers(conn, [
        make_paper(pdf_url="https://good/x.pdf"),
        make_paper(arxiv_id="2401.00002", pdf_url="https://paywalled/x"),
        make_paper(arxiv_id="2401.00003", pdf_url=None),
    ])
    session = FakeSession({
        "https://good/x.pdf": FakeResponse(b"%PDF-1.4 body"),
        "https://paywalled/x": FakeResponse(b"<html>login</html>"),
    })
    rows = db.list_papers(conn)
    drift = reconcile.check_dead_links(rows, session)
    assert [d["arxiv_id"] for d in drift] == ["2401.00002"]  # no-url row is skipped, not flagged


def test_check_key_collisions_flags_shared_doi(conn):
    db.upsert_papers(conn, [
        make_paper(doi="10.1/x"),
        make_paper(arxiv_id="2401.00002", doi="10.1/x"),
        make_paper(arxiv_id="2401.00003", doi="10.1/other"),
    ])
    rows = db.list_papers(conn)
    collisions = reconcile.check_key_collisions(rows)
    assert len(collisions) == 1
    assert sorted(collisions[0]["arxiv_ids"]) == ["2401.00001", "2401.00002"]


def test_reconcile_dry_run_changes_nothing(conn, tmp_path):
    db.upsert_papers(conn, [make_paper(arxiv_id="2401.00002", pdf_url=None,
                                        pdf_path=str(tmp_path / "gone.pdf"))])
    report = reconcile.reconcile(conn, tmp_path, session=FakeSession({}), fix=False)
    assert len(report["missing_file"]) == 1
    assert report["fixed"] == [] and report["unresolved"] == []
    assert db.get_paper(conn, "2401.00002")["pdf_path"] == str(tmp_path / "gone.pdf")


def test_reconcile_fix_redownloads_missing_file(conn, tmp_path):
    db.upsert_papers(conn, [
        make_paper(pdf_url="https://good/x.pdf", pdf_path=str(tmp_path / "gone.pdf"))
    ])
    session = FakeSession({"https://good/x.pdf": FakeResponse(b"%PDF-1.4 body")})
    report = reconcile.reconcile(conn, tmp_path, session=session, fix=True, delay=0)
    assert len(report["fixed"]) == 1
    row = db.get_paper(conn, "2401.00001")
    assert row["pdf_path"] is not None
    from pathlib import Path
    assert Path(row["pdf_path"]).exists()


def test_reconcile_fix_prefers_arxiv_copy_over_dead_publisher_link(conn, tmp_path):
    db.upsert_papers(conn, [make_paper(pdf_url="https://publisher.example/paywalled")])
    arxiv_url = reconcile.arxiv_pdf_url("2401.00001")
    session = FakeSession({
        "https://publisher.example/paywalled": FakeResponse(b"<html>paywall</html>"),
        arxiv_url: FakeResponse(b"%PDF-1.4 body"),
    })
    report = reconcile.reconcile(conn, tmp_path, session=session, fix=True, delay=0)
    assert report["fixed"][0]["url"] == arxiv_url
    assert db.get_paper(conn, "2401.00001")["pdf_url"] == arxiv_url


def test_reconcile_never_deletes_a_row_on_unresolved_fix(conn, tmp_path):
    db.upsert_papers(conn, [make_paper(arxiv_id="doi:10.9/nope", pdf_url=None,
                                        pdf_path=str(tmp_path / "gone.pdf"))])
    report = reconcile.reconcile(conn, tmp_path, session=FakeSession({}), fix=True, delay=0)
    assert len(report["unresolved"]) == 1
    assert db.get_paper(conn, "doi:10.9/nope") is not None  # still present, untouched


def test_reconcile_fix_never_touches_key_collisions(conn, tmp_path):
    db.upsert_papers(conn, [
        make_paper(doi="10.1/x", pdf_url=None),
        make_paper(arxiv_id="2401.00002", doi="10.1/x", pdf_url=None),
    ])
    report = reconcile.reconcile(conn, tmp_path, session=FakeSession({}), fix=True, delay=0)
    assert len(report["key_collision"]) == 1
    assert db.get_paper(conn, "2401.00001") is not None
    assert db.get_paper(conn, "2401.00002") is not None
