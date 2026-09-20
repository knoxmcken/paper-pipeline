"""OpenAlex discovery: topical search with metadata + open-access PDF locations.

OpenAlex (https://openalex.org) is a free, programmatic catalogue of scholarly
works. It is the third discovery source, and the one that keeps working when
arXiv's search API throttles a shared/cloud egress IP.

Records are normalised to the same dict shape as the arXiv sources. The paper key
is the arXiv id when the work has an arXiv location, otherwise ``doi:<doi>`` -
still one stable key per paper, which is all the rest of the pipeline needs.
"""

from __future__ import annotations

import time
from typing import Dict, List, Optional
from urllib.parse import quote

import requests

from . import config, netcache

ARXIV_URL_MARKERS = ("arxiv.org/abs/", "arxiv.org/pdf/")


class OpenAlexError(RuntimeError):
    """Raised when OpenAlex cannot be queried or parsed."""


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
                session, config.OPENALEX_API, params, source="openalex",
                cache=cache, limiter=limiter, timeout=45,
            )
            if resp.status_code == 200:
                return resp.json()
            last = OpenAlexError(f"OpenAlex returned HTTP {resp.status_code}")
        except (requests.RequestException, ValueError) as exc:
            last = exc
        if attempt < attempts - 1:
            time.sleep(3 * (attempt + 1))
    raise OpenAlexError(f"OpenAlex request failed: {last}")


def _abstract(work: Dict[str, object]) -> Optional[str]:
    """Rebuild plain text from OpenAlex's inverted index."""
    inverted = work.get("abstract_inverted_index")
    if not isinstance(inverted, dict) or not inverted:
        return None
    positioned = []
    for word, positions in inverted.items():
        for position in positions:
            positioned.append((position, word))
    positioned.sort()
    return " ".join(word for _, word in positioned) or None


def _arxiv_id(work: Dict[str, object]) -> Optional[str]:
    urls = []
    for key in ("best_oa_location", "primary_location"):
        location = work.get(key)
        if isinstance(location, dict):
            urls.extend(str(location.get(field) or "") for field in ("pdf_url", "landing_page_url"))
    for location in work.get("locations") or []:
        if isinstance(location, dict):
            urls.extend(str(location.get(field) or "") for field in ("pdf_url", "landing_page_url"))
    for url in urls:
        if any(marker in url for marker in ARXIV_URL_MARKERS):
            tail = url.split("/abs/", 1)[-1].split("/pdf/", 1)[-1]
            return tail.rstrip("/").removesuffix(".pdf")
    return None


def _doi(work: Dict[str, object]) -> Optional[str]:
    doi = work.get("doi") or (work.get("ids") or {}).get("doi")
    if not doi:
        return None
    return str(doi).replace("https://doi.org/", "").replace("http://doi.org/", "")


def _pdf_url(work: Dict[str, object]) -> Optional[str]:
    for key in ("best_oa_location", "primary_location"):
        location = work.get(key)
        if isinstance(location, dict) and location.get("pdf_url"):
            return str(location["pdf_url"])
    open_access = work.get("open_access") or {}
    return str(open_access["oa_url"]) if open_access.get("oa_url") else None


def work_to_dict(work: Dict[str, object]) -> Dict[str, object]:
    arxiv_id = _arxiv_id(work)
    doi = _doi(work)
    key = arxiv_id or (f"doi:{doi}" if doi else str(work.get("id") or "").rsplit("/", 1)[-1])
    authors = [
        (a.get("author") or {}).get("display_name")
        for a in work.get("authorships") or []
        if (a.get("author") or {}).get("display_name")
    ]
    topic = (work.get("primary_topic") or {}).get("display_name")
    categories = [t.get("display_name") for t in (work.get("topics") or []) if t.get("display_name")]
    if topic and topic not in categories:
        categories.insert(0, topic)
    source_name = ((work.get("primary_location") or {}).get("source") or {}).get("display_name")
    published = work.get("publication_date") or (
        f"{work['publication_year']}-01-01" if work.get("publication_year") else None
    )
    # Prefer the arXiv copy when the work has one: OpenAlex often lists a publisher
    # DOI as best_oa_location, and those routinely 403 for us (Elsevier, IEEE, MDPI).
    pdf_url = config.ARXIV_PDF.format(arxiv_id=arxiv_id) if arxiv_id else _pdf_url(work)
    return {
        "arxiv_id": key,
        "version": "",
        "title": (work.get("title") or work.get("display_name") or "(untitled)").strip(),
        "abstract": _abstract(work),
        "authors": list(authors),
        "primary_category": topic or source_name,
        "categories": categories[:5],
        "published": published,
        "updated": published,
        "doi": doi,
        "journal_ref": source_name,
        "comment": f"type={work.get('type')}, cited_by={work.get('cited_by_count')}",
        "pdf_url": pdf_url,
        "abs_url": (work.get("primary_location") or {}).get("landing_page_url")
        or (f"https://doi.org/{doi}" if doi else work.get("id")),
        "source": "openalex",
        "cited_by": work.get("cited_by_count"),
    }


def search(
    query: str,
    max_results: int = config.DEFAULT_MAX,
    session: Optional[requests.Session] = None,
    delay: float = config.DEFAULT_DELAY,
    mailto: Optional[str] = None,
    page_size: int = 100,
    open_access_only: bool = False,
    arxiv_only: bool = False,
    search_field: str = "default",
    attempts: int = 4,
    cache: Optional[netcache.ResponseCache] = None,
    limiter: Optional[netcache.RateLimiter] = None,
) -> List[Dict[str, object]]:
    """Topical search with cursor paging.

    ``search_field``: ``default`` searches broadly (title, abstract and more) and
    is fuzzy; ``title-and-abstract`` is far stricter and is the better choice for
    building a corpus, where off-topic hits are worse than a smaller yield.

    ``arxiv_only`` keeps only works that have an arXiv location, i.e. papers whose
    full text is actually downloadable right now. Paging continues through
    non-matching pages until ``max_results`` arXiv works are collected.
    """
    sess = session or requests.Session()
    sess.headers.setdefault("User-Agent", config.USER_AGENT)
    filters = []
    if open_access_only:
        filters.append("is_oa:true")
    if search_field == "title-and-abstract":
        filters.append(f"title_and_abstract.search:{query}")
    base_params = {
        "per-page": str(min(page_size, 200)),
        "mailto": mailto if mailto is not None else config.MAILTO,
        "select": (
            "id,doi,title,display_name,publication_date,publication_year,authorships,"
            "primary_location,best_oa_location,locations,open_access,primary_topic,topics,"
            "abstract_inverted_index,type,cited_by_count,ids"
        ),
    }
    if search_field != "title-and-abstract":
        base_params["search"] = query
    if filters:
        base_params["filter"] = ",".join(filters)

    collected: List[Dict[str, object]] = []
    cursor = "*"
    while len(collected) < max_results and cursor:
        params = dict(base_params, cursor=cursor)
        payload = _get(sess, params, attempts=attempts, cache=cache, limiter=limiter)
        results = payload.get("results") or []
        if not results:
            break
        for work in results:
            if arxiv_only and _arxiv_id(work) is None:
                continue
            collected.append(work_to_dict(work))
            if len(collected) >= max_results:
                break
        cursor = (payload.get("meta") or {}).get("next_cursor")
        if len(collected) < max_results and cursor and limiter is None:
            time.sleep(delay)
    return collected[:max_results]


def search_encoded(query: str, **kwargs) -> List[Dict[str, object]]:
    """``search`` but with the query pre-escaped, for callers passing raw syntax."""
    return search(quote(query, safe=""), **kwargs)
