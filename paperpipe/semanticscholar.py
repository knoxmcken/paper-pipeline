"""Semantic Scholar discovery: metadata plus the citation graph.

Semantic Scholar (https://api.semanticscholar.org) indexes most published
computer science and adjacent work regardless of paywall status, and its
citation counts double as a ranking signal the other sources don't carry.
Records are normalised to the same dict shape as the other discovery sources.
"""

from __future__ import annotations

import time
from typing import Dict, List, Optional

import requests

from . import config

FIELDS = (
    "title,abstract,year,publicationDate,authors,venue,citationCount,"
    "externalIds,openAccessPdf,isOpenAccess"
)


class SemanticScholarError(RuntimeError):
    """Raised when Semantic Scholar cannot be queried or parsed."""


def _retry_after(resp: requests.Response) -> Optional[float]:
    raw = resp.headers.get("Retry-After")
    if not raw:
        return None
    try:
        return max(1.0, float(raw))
    except ValueError:
        return None


def _get(session: requests.Session, params: dict, attempts: int = 4) -> dict:
    headers = {"x-api-key": config.S2_API_KEY} if config.S2_API_KEY else {}
    last: Optional[Exception] = None
    for attempt in range(attempts):
        try:
            resp = session.get(
                config.SEMANTIC_SCHOLAR_API, params=params, headers=headers, timeout=45
            )
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code == 429:
                # Unauthenticated callers share a tight, undocumented quota;
                # back off harder than a generic HTTP error.
                wait = _retry_after(resp) or 10 * (attempt + 1)
                last = SemanticScholarError(
                    f"Semantic Scholar rate limit hit (HTTP 429); waited {wait:.0f}s"
                )
                if attempt < attempts - 1:
                    time.sleep(wait)
                    continue
            last = SemanticScholarError(f"Semantic Scholar returned HTTP {resp.status_code}")
        except (requests.RequestException, ValueError) as exc:
            last = exc
        if attempt < attempts - 1:
            time.sleep(3 * (attempt + 1))
    raise SemanticScholarError(f"Semantic Scholar request failed: {last}")


def _pdf_url(paper: Dict[str, object], arxiv_id: Optional[str]) -> Optional[str]:
    # Prefer the arXiv copy: publisher OA links routinely 403 for us.
    if arxiv_id:
        return config.ARXIV_PDF.format(arxiv_id=arxiv_id)
    oa = paper.get("openAccessPdf") or {}
    return oa.get("url")


def paper_to_dict(paper: Dict[str, object]) -> Dict[str, object]:
    external = paper.get("externalIds") or {}
    arxiv_id = external.get("ArXiv")
    doi = (external.get("DOI") or "").lower() or None
    key = arxiv_id or (f"doi:{doi}" if doi else str(paper.get("paperId") or ""))
    published = paper.get("publicationDate") or (
        f"{paper['year']}-01-01" if paper.get("year") else None
    )
    return {
        "arxiv_id": key,
        "version": "",
        "title": (paper.get("title") or "(untitled)").strip(),
        "abstract": paper.get("abstract"),
        "authors": [a.get("name") for a in paper.get("authors") or [] if a.get("name")],
        "primary_category": paper.get("venue") or None,
        "categories": [paper["venue"]] if paper.get("venue") else [],
        "published": published,
        "updated": published,
        "doi": doi,
        "journal_ref": paper.get("venue"),
        "comment": f"citationCount={paper.get('citationCount')}",
        "pdf_url": _pdf_url(paper, arxiv_id),
        "abs_url": (
            f"https://www.semanticscholar.org/paper/{paper['paperId']}"
            if paper.get("paperId")
            else (f"https://doi.org/{doi}" if doi else None)
        ),
        "source": "semanticscholar",
        "cited_by": paper.get("citationCount"),
    }


def search(
    query: str,
    max_results: int = config.DEFAULT_MAX,
    session: Optional[requests.Session] = None,
    delay: float = config.DEFAULT_DELAY,
    page_size: int = 100,
    attempts: int = 4,
) -> List[Dict[str, object]]:
    """Keyword search with offset paging."""
    sess = session or requests.Session()
    sess.headers.setdefault("User-Agent", config.USER_AGENT)
    limit = min(page_size, 100)
    collected: List[Dict[str, object]] = []
    offset = 0
    while len(collected) < max_results:
        params = {"query": query, "limit": str(limit), "offset": str(offset), "fields": FIELDS}
        payload = _get(sess, params, attempts=attempts)
        items = payload.get("data") or []
        if not items:
            break
        for item in items:
            collected.append(paper_to_dict(item))
            if len(collected) >= max_results:
                break
        offset += len(items)
        if len(collected) >= max_results or len(items) < limit:
            break
        time.sleep(delay)
    return collected[:max_results]
