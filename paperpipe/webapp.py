"""Local web UI: read the corpus directly, run pipeline stages as subprocess jobs.

Reads (`stats`, `papers`, `runs`) hit the database directly via :mod:`paperpipe.db`.
``/api/export/download`` renders the currently listed papers straight into the
response as an attachment. Actions (`fetch`, `extract`, `index`, `export`) shell
out to ``python -m paperpipe``
so the UI can never drift from the CLI's tested behaviour, and so a long-running
fetch doesn't block the API thread. Job state lives in memory - restarting the
server drops job history, but never touches the database, which stays the source
of truth.
"""

from __future__ import annotations

import subprocess
import sys
import threading
import uuid
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config, db, export

STATIC_DIR = Path(__file__).parent / "web" / "static"


class FetchRequest(BaseModel):
    query: str
    max: int = config.DEFAULT_MAX
    source: str = "api"
    category: Optional[str] = None
    no_download: bool = False


class ExportRequest(BaseModel):
    format: str = "all"


class ReconcileRequest(BaseModel):
    fix: bool = False


class Job:
    def __init__(self, job_id: str, kind: str, argv: List[str]):
        self.id = job_id
        self.kind = kind
        self.argv = argv
        self.status = "queued"  # queued -> running -> done | error
        self.output: List[str] = []
        self.returncode: Optional[int] = None

    def to_dict(self) -> Dict[str, object]:
        return {
            "id": self.id,
            "kind": self.kind,
            "status": self.status,
            "output": self.output,
            "returncode": self.returncode,
        }


class JobRunner:
    """Runs one pipeline-stage subprocess at a time, tracked in memory."""

    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.jobs: Dict[str, Job] = {}
        self._lock = threading.Lock()

    def submit(self, kind: str, argv: List[str]) -> Job:
        job_id = uuid.uuid4().hex[:12]
        full_argv = [sys.executable, "-m", "paperpipe", *argv, "--data-dir", str(self.data_dir)]
        job = Job(job_id, kind, full_argv)
        with self._lock:
            self.jobs[job_id] = job
        threading.Thread(target=self._run, args=(job,), daemon=True).start()
        return job

    def _run(self, job: Job) -> None:
        job.status = "running"
        try:
            proc = subprocess.Popen(
                job.argv,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            for line in proc.stdout:  # type: ignore[union-attr]
                job.output.append(line.rstrip("\n"))
            proc.wait()
            job.returncode = proc.returncode
            job.status = "done" if proc.returncode == 0 else "error"
        except OSError as exc:  # pragma: no cover - launch failure is environmental
            job.output.append(f"failed to launch: {exc}")
            job.status = "error"

    def get(self, job_id: str) -> Job:
        job = self.jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="unknown job id")
        return job

    def recent(self, limit: int = 20) -> List[Dict[str, object]]:
        jobs = sorted(self.jobs.values(), key=lambda j: j.id, reverse=True)
        return [j.to_dict() for j in jobs[:limit]]


def create_app(data_dir: Path) -> FastAPI:
    data_dir = Path(data_dir)
    config.ensure_dirs(data_dir)
    runner = JobRunner(data_dir)

    app = FastAPI(title="paper-pipeline")

    def _conn():
        conn = db.connect(config.db_path(data_dir))
        db.init_db(conn)
        return conn

    @app.get("/api/stats")
    def get_stats():
        conn = _conn()
        try:
            info = db.stats(conn)
            categories = conn.execute(
                "SELECT primary_category, COUNT(*) c FROM papers GROUP BY 1 ORDER BY c DESC LIMIT 15"
            ).fetchall()
            info["categories"] = [{"category": r[0] or "?", "count": r[1]} for r in categories]
            return info
        finally:
            conn.close()

    @app.get("/api/papers")
    def get_papers(q: Optional[str] = None, limit: int = 100):
        conn = _conn()
        try:
            rows = db.search(conn, q, limit=limit) if q else db.list_papers(conn, limit=limit)
            return {"count": len(rows), "papers": rows}
        finally:
            conn.close()

    @app.get("/api/export/download")
    def download_export(format: str, q: Optional[str] = None):
        """The papers matching ``q`` (the list's search box), or all of them, as a file.

        Honours the search but not the list's display cap: every matching paper is
        exported, not just the first page shown in the table.
        """
        spec = export.FORMATS.get(format)
        if spec is None:
            raise HTTPException(
                status_code=400,
                detail=f"unknown format {format!r}; one of {', '.join(export.FORMATS)}",
            )
        conn = _conn()
        try:
            papers = db.search(conn, q, limit=-1) if q else db.list_papers(conn)
        finally:
            conn.close()
        return Response(
            content=export.render(format, papers),
            media_type=spec.media_type,
            headers={
                "Content-Disposition": f'attachment; filename="paper-export.{spec.extension}"',
                "X-Paper-Count": str(len(papers)),
            },
        )

    @app.get("/api/search/fulltext")
    def get_fulltext(q: str, limit: int = 20):
        conn = _conn()
        try:
            db.backfill_fts(conn)
            hits = db.search_fulltext(conn, q, limit=limit)
            return {"count": len(hits), "hits": hits}
        finally:
            conn.close()

    @app.get("/api/papers/{arxiv_id}")
    def get_paper(arxiv_id: str):
        conn = _conn()
        try:
            row = db.get_paper(conn, arxiv_id)
            if row is None:
                raise HTTPException(status_code=404, detail="paper not found")
            return row
        finally:
            conn.close()

    @app.get("/api/runs")
    def get_runs(limit: int = 20):
        conn = _conn()
        try:
            rows = conn.execute(
                "SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
            return {"runs": [dict(r) for r in rows]}
        finally:
            conn.close()

    @app.post("/api/actions/fetch")
    def action_fetch(req: FetchRequest):
        argv = ["fetch", "-q", req.query, "-n", str(req.max), "--source", req.source]
        if req.category:
            argv += ["--category", req.category]
        if req.no_download:
            argv.append("--no-download")
        return runner.submit("fetch", argv).to_dict()

    @app.post("/api/actions/extract")
    def action_extract():
        return runner.submit("extract", ["extract"]).to_dict()

    @app.post("/api/actions/index")
    def action_index():
        return runner.submit("index", ["index"]).to_dict()

    @app.post("/api/actions/export")
    def action_export(req: ExportRequest):
        return runner.submit("export", ["export", "--format", req.format]).to_dict()

    @app.post("/api/actions/reconcile")
    def action_reconcile(req: ReconcileRequest):
        argv = ["reconcile"] + (["--fix"] if req.fix else [])
        return runner.submit("reconcile", argv).to_dict()

    @app.get("/api/jobs")
    def list_jobs():
        return {"jobs": runner.recent()}

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str):
        return runner.get(job_id).to_dict()

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/")
    def index_page():
        return FileResponse(str(STATIC_DIR / "index.html"))

    return app
