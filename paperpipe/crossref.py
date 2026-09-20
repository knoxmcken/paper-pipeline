"""Crossref discovery: DOI metadata and publication venue for non-arXiv work.

Crossref (https://api.crossref.org) indexes publisher-registered DOIs. It has
no open-access PDF links of its own, but it gives the venue, an abstract when
the publisher registered one, and a citation count - enough to catalogue a
paywalled item as a metadata-only row (see ``unpaywall`` for resolving an OA
copy afterwards). Records are normalised to the same dict shape as the other
discovery sources.
"""

from __future__ import annotations

import re
import time
from typing import Dict, List, Optional

import requests

from . import config, netcache

_TAG_RE = re.compile(r"<[^>]+>")


class CrossrefError(RuntimeError):
    """Raised when Crossref cannot be queried or parsed."""


def _retry_after(resp: requests.Response) -> Optional[float]:
    raw = resp.headers.get("Retry-After")
    if not raw:
        return None
    try:
        return max(1.0, float(raw))
    except ValueError:
        return None


def _get(
    session: requests.Session,
    params: dict,
    attempts: int = 4,
    cache: Optional[netcache.ResponseCache] = None,
    limiter: Optional[netcache.RateLimiter] = None,
) -> dict:
    last: Optional[Exception] = None
    for attempt in range(attempts):
        try:
            resp = netcache.cached_get(
                session, config.CROSSREF_API, params, source="crossref",
                cache=cache, limiter=limiter, timeout=45,
            )
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code == 429:
                wait = _retry_after(resp) or 5 * (attempt + 1)
                last = CrossrefError(f"Crossref rate limit hit (HTTP 429); waited {wait:.0f}s")
                if attempt < attempts - 1:
                    time.sleep(wait)
                    continue
            last = CrossrefError(f"Crossref returned HTTP {resp.status_code}")
        except (requests.RequestException, ValueError) as exc:
            last = exc
        if attempt < attempts - 1:
            time.sleep(3 * (attempt + 1))
    raise CrossrefError(f"Crossref request failed: {last}")


def _clean_abstract(raw: Optional[str]) -> Optional[str]:
    """Crossref abstracts are JATS XML fragments (``<jats:p>...</jats:p>``)."""
    if not raw:
        return None
    text = _TAG_RE.sub(" ", raw)
    return " ".join(text.split()) or None


def _authors(item: Dict[str, object]) -> List[str]:
    names = []
    for author in item.get("author") or []:
        name = " ".join(part for part in (author.get("given"), author.get("family")) if part)
        if name:
            names.append(name)
    return names


def _date(parts: Optional[Dict[str, object]]) -> Optional[str]:
    date_parts = ((parts or {}).get("date-parts") or [[]])[0]
    if not date_parts:
        return None
    year = date_parts[0]
    month = date_parts[1] if len(date_parts) > 1 else 1
    day = date_parts[2] if len(date_parts) > 2 else 1
    return f"{year:04d}-{month:02d}-{day:02d}"


def _pdf_url(item: Dict[str, object]) -> Optional[str]:
    """Crossref occasionally lists a publisher PDF link; most items have none."""
    for link in item.get("link") or []:
        if link.get("content-type") == "application/pdf" and link.get("URL"):
            return str(link["URL"])
    return None


def work_to_dict(item: Dict[str, object]) -> Dict[str, object]:
    doi = str(item.get("DOI") or "").lower() or None
    key = f"doi:{doi}" if doi else str(item.get("URL") or "")
    titles = item.get("title") or []
    published = _date(
        item.get("published") or item.get("published-print") or item.get("published-online")
    )
    container = item.get("container-title") or []
    return {
        "arxiv_id": key,
        "version": "",
        "title": (titles[0] if titles else "(untitled)").strip(),
        "abstract": _clean_abstract(item.get("abstract")),
        "authors": _authors(item),
        "primary_category": container[0] if container else item.get("type"),
        "categories": list(container[:1]),
        "published": published,
        "updated": published,
        "doi": doi,
        "journal_ref": container[0] if container else None,
        "comment": f"type={item.get('type')}, cited_by={item.get('is-referenced-by-count')}",
        "pdf_url": _pdf_url(item),
        "abs_url": item.get("URL") or (f"https://doi.org/{doi}" if doi else None),
        "source": "crossref",
        "cited_by": item.get("is-referenced-by-count"),
    }


def search(
    query: str,
    max_results: int = config.DEFAULT_MAX,
    session: Optional[requests.Session] = None,
    delay: float = config.DEFAULT_DELAY,
    mailto: Optional[str] = None,
    page_size: int = 100,
    attempts: int = 4,
    cache: Optional[netcache.ResponseCache] = None,
    limiter: Optional[netcache.RateLimiter] = None,
) -> List[Dict[str, object]]:
    """Bibliographic search with offset paging."""
    sess = session or requests.Session()
    sess.headers.setdefault("User-Agent", config.USER_AGENT)
    rows = min(page_size, 100)
    base_params = {
        "query.bibliographic": query,
        "rows": str(rows),
        "mailto": mailto if mailto is not None else config.MAILTO,
        "select": "DOI,title,author,abstract,published,published-print,published-online,"
        "container-title,type,is-referenced-by-count,link,URL",
    }
    collected: List[Dict[str, object]] = []
    offset = 0
    while len(collected) < max_results:
        params = dict(base_params, offset=str(offset))
        payload = _get(sess, params, attempts=attempts, cache=cache, limiter=limiter)
        items = (payload.get("message") or {}).get("items") or []
        if not items:
            break
        for item in items:
            collected.append(work_to_dict(item))
            if len(collected) >= max_results:
                break
        offset += len(items)
        if len(collected) >= max_results or len(items) < rows:
            break
        if limiter is None:
            time.sleep(delay)
    return collected[:max_results]
