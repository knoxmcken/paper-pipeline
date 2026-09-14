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
