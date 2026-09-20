"""Unpaywall enrichment: resolve an open-access PDF location from a DOI.

Unlike arXiv/OpenAlex/Crossref/Semantic Scholar this is not a discovery
source - it never returns new papers, only fills the ``pdf_url`` gap for a
paper that is already known but has no open-access copy from its original
source. Looked up once per DOI, and only when nothing better is already
known, since an OA copy found upstream is more likely to actually download.
"""

from __future__ import annotations

import time
from typing import Dict, Iterable, Optional

import requests

from . import config, netcache


class UnpaywallError(RuntimeError):
    """Raised when Unpaywall cannot be queried."""


def _retry_after(resp: requests.Response) -> Optional[float]:
    raw = resp.headers.get("Retry-After")
    if not raw:
        return None
    try:
        return max(1.0, float(raw))
    except ValueError:
        return None


def lookup_pdf_url(
    doi: str,
    session: Optional[requests.Session] = None,
    mailto: Optional[str] = None,
    attempts: int = 3,
    cache: Optional[netcache.ResponseCache] = None,
    limiter: Optional[netcache.RateLimiter] = None,
) -> Optional[str]:
    """Return an open-access PDF url for ``doi``, or ``None`` if Unpaywall has none."""
    sess = session or requests.Session()
    sess.headers.setdefault("User-Agent", config.USER_AGENT)
    url = config.UNPAYWALL_API.format(doi=doi)
    params = {"email": mailto if mailto is not None else config.MAILTO}
    last: Optional[Exception] = None
    for attempt in range(attempts):
        try:
            resp = netcache.cached_get(
                sess, url, params, source="unpaywall", cache=cache, limiter=limiter, timeout=30
            )
            if resp.status_code == 404:
                return None  # Unpaywall has no record for this DOI
            if resp.status_code == 200:
                payload = resp.json()
                best = payload.get("best_oa_location") or {}
                return best.get("url_for_pdf") or best.get("url")
            if resp.status_code == 429:
                wait = _retry_after(resp) or 5 * (attempt + 1)
                last = UnpaywallError(f"Unpaywall rate limit hit (HTTP 429); waited {wait:.0f}s")
                if attempt < attempts - 1:
                    time.sleep(wait)
                    continue
            last = UnpaywallError(f"Unpaywall returned HTTP {resp.status_code}")
        except (requests.RequestException, ValueError) as exc:
            last = exc
        if attempt < attempts - 1:
            time.sleep(2 * (attempt + 1))
    raise UnpaywallError(f"Unpaywall request failed for {doi}: {last}")


def enrich_missing_pdfs(
    papers: Iterable[Dict[str, object]],
    session: Optional[requests.Session] = None,
    mailto: Optional[str] = None,
    delay: float = config.DEFAULT_DELAY,
    cache: Optional[netcache.ResponseCache] = None,
    limiter: Optional[netcache.RateLimiter] = None,
) -> int:
    """Fill ``pdf_url`` for papers that have a DOI but no known OA copy.

    Mutates ``papers`` in place; returns how many were filled. Papers that
    already have a ``pdf_url`` (from arXiv/OpenAlex/Crossref/Semantic
    Scholar) are skipped, since Unpaywall is a last resort, not a preferred
    source. Lookup failures are swallowed per-paper so one bad DOI doesn't
    abort enrichment for the rest of the batch.
    """
    filled = 0
    candidates = [p for p in papers if p.get("doi") and not p.get("pdf_url")]
    for index, paper in enumerate(candidates):
        if index and limiter is None:
            time.sleep(delay)
        try:
            pdf_url = lookup_pdf_url(
                str(paper["doi"]), session=session, mailto=mailto, cache=cache, limiter=limiter
            )
        except UnpaywallError:
            continue
        if pdf_url:
            paper["pdf_url"] = pdf_url
            filled += 1
    return filled
