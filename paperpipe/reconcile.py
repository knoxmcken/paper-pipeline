"""Detect (and optionally repair) drift between stored papers and their sources.

Three independent drift classes, always reported and never deleting a row:

  missing_file   - ``pdf_path`` is set but the file is gone from disk
  dead_link      - ``pdf_url`` no longer serves a PDF (dead, paywalled, or a
                   redirect to an HTML landing page)
  key_collision  - two different ``arxiv_id`` keys share the same ``doi``,
                   meaning they are almost certainly the same underlying work
                   stored under two keys

``--fix`` re-downloads for the first two classes. For an arXiv-shaped key with a
dead link it re-resolves to the canonical ``arxiv.org/pdf/<id>`` URL first, the
same "prefer the arXiv copy" preference ``fetch``/``openalex`` already apply -
publisher DOI links are the ones that tend to rot or sit behind a paywall.
Collisions are reported only: picking which of two rows is canonical is a
human judgement call, not something to automate.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Optional

import requests

from . import config, db, fetch

ARXIV_ID_RE = re.compile(r"^(\d{4}\.\d{4,5}|[a-zA-Z][a-zA-Z\-.]*(\.[A-Z]{2})?/\d{7})(v\d+)?$")


def _looks_like_arxiv_id(arxiv_id: str) -> bool:
    return bool(ARXIV_ID_RE.match(arxiv_id))


def arxiv_pdf_url(arxiv_id: str) -> str:
    return config.ARXIV_PDF.format(arxiv_id=arxiv_id)


def check_missing_files(rows: List[Dict[str, object]]) -> List[Dict[str, object]]:
    drift = []
    for row in rows:
        path = row.get("pdf_path")
        if path and not Path(path).exists():
            drift.append({"kind": "missing_file", "arxiv_id": row["arxiv_id"], "path": path})
    return drift


def _pdf_link_ok(url: str, session: requests.Session, timeout: float = 15.0) -> bool:
    """Best-effort check that ``url`` currently serves a PDF."""
    try:
        resp = session.get(url, timeout=timeout)
    except requests.RequestException:
        return False
    if getattr(resp, "status_code", None) != 200:
        return False
    return getattr(resp, "content", b"").startswith(fetch.PDF_MAGIC)


def check_dead_links(rows: List[Dict[str, object]], session: requests.Session) -> List[Dict[str, object]]:
    drift = []
    for row in rows:
        url = row.get("pdf_url")
        if not url:
            continue
        if not _pdf_link_ok(url, session):
            drift.append({"kind": "dead_link", "arxiv_id": row["arxiv_id"], "url": url})
    return drift


def check_key_collisions(rows: List[Dict[str, object]]) -> List[Dict[str, object]]:
    by_doi: Dict[str, List[str]] = {}
    for row in rows:
        doi = row.get("doi")
        if doi:
            by_doi.setdefault(doi, []).append(row["arxiv_id"])
    return [
        {"kind": "key_collision", "doi": doi, "arxiv_ids": ids}
        for doi, ids in by_doi.items()
        if len(ids) > 1
    ]


def reconcile(
    conn,
    pdfs_dir: Path,
    session: Optional[requests.Session] = None,
    fix: bool = False,
    delay: float = config.DEFAULT_DELAY,
) -> Dict[str, object]:
    rows = db.list_papers(conn, limit=None)
    session = session or requests.Session()
    session.headers.setdefault("User-Agent", config.USER_AGENT)

    missing = check_missing_files(rows)
    dead = check_dead_links(rows, session)
    collisions = check_key_collisions(rows)

    fixed: List[Dict[str, object]] = []
    unresolved: List[Dict[str, object]] = []
    if fix:
        targets: Dict[str, List[str]] = {}
        for item in missing:
            targets.setdefault(item["arxiv_id"], []).append("missing_file")
        for item in dead:
            targets.setdefault(item["arxiv_id"], []).append("dead_link")

        for arxiv_id, kinds in targets.items():
            row = db.get_paper(conn, arxiv_id)
            url = row.get("pdf_url") if row else None
            if "dead_link" in kinds and _looks_like_arxiv_id(arxiv_id):
                url = arxiv_pdf_url(arxiv_id)
            if not url:
                unresolved.append({"arxiv_id": arxiv_id, "kinds": kinds, "reason": "no pdf url available"})
                continue
            try:
                info = fetch.download_pdf(
                    arxiv_id, pdfs_dir, session=session, url=url, delay=delay, force=True
                )
                db.update_pdf(conn, arxiv_id, info)
                if row and url != row.get("pdf_url"):
                    db.update_pdf_url(conn, arxiv_id, url)
                fixed.append({"arxiv_id": arxiv_id, "kinds": kinds, "url": url})
            except fetch.FetchError as exc:
                unresolved.append({"arxiv_id": arxiv_id, "kinds": kinds, "error": str(exc)})

    return {
        "missing_file": missing,
        "dead_link": dead,
        "key_collision": collisions,
        "fixed": fixed,
        "unresolved": unresolved,
    }
