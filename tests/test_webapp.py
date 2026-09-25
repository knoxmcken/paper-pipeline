import pytest

fastapi_testclient = pytest.importorskip("fastapi.testclient")

from paperpipe import config, db
from paperpipe.webapp import create_app
from tests.helpers import make_paper


@pytest.fixture()
def client(tmp_path):
    conn = db.connect(config.db_path(tmp_path))
    db.init_db(conn)
    db.upsert_papers(
        conn,
        [
            make_paper(title="Beta paper", arxiv_id="2401.00001"),
            make_paper(title="Alpha paper", arxiv_id="2401.00002", abstract="mentions prompt injection"),
        ],
    )
    conn.close()
    app = create_app(tmp_path)
    return fastapi_testclient.TestClient(app)


def test_stats_reflects_db(client):
    resp = client.get("/api/stats")
    assert resp.status_code == 200
    body = resp.json()
    assert body["papers"] == 2
    assert body["with_pdf"] == 0


def test_list_papers(client):
    resp = client.get("/api/papers")
    assert resp.status_code == 200
    assert resp.json()["count"] == 2


def test_search_papers(client):
    resp = client.get("/api/papers", params={"q": "prompt injection"})
    body = resp.json()
    assert body["count"] == 1
    assert body["papers"][0]["arxiv_id"] == "2401.00002"


def test_get_single_paper(client):
    resp = client.get("/api/papers/2401.00001")
    assert resp.status_code == 200
    assert resp.json()["title"] == "Beta paper"


def test_get_missing_paper_is_404(client):
    resp = client.get("/api/papers/9999.99999")
    assert resp.status_code == 404


def test_fulltext_search_backfills_and_returns_hits(client, tmp_path):
    text_path = tmp_path / "text" / "2401.00002.txt"
    text_path.parent.mkdir(parents=True, exist_ok=True)
    text_path.write_text("intro\fdiscusses prompt injection against agents", encoding="utf-8")
    conn = db.connect(config.db_path(tmp_path))
    db.update_extraction(
        conn, "2401.00002",
        {"text_path": str(text_path), "text_chars": 40, "page_count": 2, "headings": []},
        "2024-01-01T00:00:00Z",
    )
    conn.close()

    resp = client.get("/api/search/fulltext", params={"q": "prompt injection"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 1
    assert body["hits"][0]["arxiv_id"] == "2401.00002"
    assert body["hits"][0]["page"] == 2


def test_fulltext_search_requires_query_param(client):
    resp = client.get("/api/search/fulltext")
    assert resp.status_code == 422


def test_reconcile_action_runs_to_completion(tmp_path):
    # A dedicated app/corpus with no pdf_url on the paper: reconcile's dead-link
    # check has nothing to call out to, so this stays offline like the rest of the
    # suite. paperpipe/reconcile.py has full offline network-check coverage with a
    # fake session; this just checks the web action wires the CLI correctly.
    conn = db.connect(config.db_path(tmp_path))
    db.init_db(conn)
    db.upsert_papers(conn, [make_paper(pdf_url=None)])
    conn.close()
    client = fastapi_testclient.TestClient(create_app(tmp_path))

    resp = client.post("/api/actions/reconcile", json={"fix": False})
    assert resp.status_code == 200
    job = resp.json()

    import time

    for _ in range(50):
        job = client.get(f"/api/jobs/{job['id']}").json()
        if job["status"] in ("done", "error"):
            break
        time.sleep(0.1)

    assert job["status"] == "done", job["output"]
    assert job["returncode"] == 0


def test_index_action_runs_to_completion(client):
    resp = client.post("/api/actions/index")
    assert resp.status_code == 200
    job = resp.json()
    assert job["status"] in ("queued", "running", "done")

    import time

    for _ in range(50):
        job = client.get(f"/api/jobs/{job['id']}").json()
        if job["status"] in ("done", "error"):
            break
        time.sleep(0.1)

    assert job["status"] == "done", job["output"]
    assert job["returncode"] == 0


def test_unknown_job_is_404(client):
    resp = client.get("/api/jobs/does-not-exist")
    assert resp.status_code == 404


def test_frontend_served_at_root(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "paper-pipeline" in resp.text


EXPORT_EXTENSIONS = {
    "csv": "csv", "md": "md", "biblatex": "bib", "xlsx": "xlsx",
    "bibtex": "bib", "csljson": "json", "ris": "ris",
}


@pytest.mark.parametrize("fmt,ext", sorted(EXPORT_EXTENSIONS.items()))
def test_export_download_is_an_attachment(client, fmt, ext):
    resp = client.get("/api/export/download", params={"format": fmt})
    assert resp.status_code == 200
    assert resp.headers["content-disposition"] == f'attachment; filename="paper-export.{ext}"'
    assert resp.headers["x-paper-count"] == "2"
    assert resp.content


def test_export_download_honours_the_search(client):
    resp = client.get("/api/export/download", params={"format": "csv", "q": "prompt injection"})
    lines = resp.text.strip().splitlines()
    assert resp.headers["x-paper-count"] == "1"
    assert len(lines) == 2 and "Alpha paper" in lines[1]


def test_export_download_is_not_capped_like_the_list(client, tmp_path):
    conn = db.connect(config.db_path(tmp_path))
    db.upsert_papers(conn, [make_paper(arxiv_id=f"2402.{i:05d}", title=f"P{i}") for i in range(150)])
    conn.close()
    assert client.get("/api/papers").json()["count"] == 100
    resp = client.get("/api/export/download", params={"format": "csv"})
    assert resp.headers["x-paper-count"] == "152"


def test_export_download_rejects_unknown_format(client):
    resp = client.get("/api/export/download", params={"format": "docx"})
    assert resp.status_code == 400
    assert "one of" in resp.json()["detail"]


def test_patch_paper_sets_status_and_notes(client):
    resp = client.patch("/api/papers/2401.00001", json={"status": "reading", "notes": "look at §3"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "reading" and body["notes"] == "look at §3"
    # fields left out are left alone
    body = client.patch("/api/papers/2401.00001", json={"notes": ""}).json()
    assert body["status"] == "reading" and body["notes"] is None
    assert client.patch("/api/papers/2401.00001", json={"status": "maybe"}).status_code == 400
    assert client.patch("/api/papers/9999.99999", json={"notes": "x"}).status_code == 404


def test_collections_filter_the_list_and_the_download(client, tmp_path):
    conn = db.connect(config.db_path(tmp_path))
    db.add_to_collection(conn, "short", ["2401.00002"], "2024-01-01T00:00:00Z")
    conn.close()
    body = client.get("/api/collections").json()
    assert body["collections"][0]["name"] == "short" and body["collections"][0]["papers"] == 1
    assert "shortlisted" in body["statuses"]

    listed = client.get("/api/papers", params={"collection": "short"}).json()
    assert [p["arxiv_id"] for p in listed["papers"]] == ["2401.00002"]
    none = client.get("/api/papers", params={"collection": "short", "q": "Beta"}).json()
    assert none["count"] == 0
    assert client.get("/api/papers", params={"collection": "ghost"}).status_code == 404

    resp = client.get("/api/export/download", params={"format": "csv", "collection": "short"})
    assert resp.headers["x-paper-count"] == "1"
    assert client.get("/api/papers/2401.00002").json()["collections"] == ["short"]
