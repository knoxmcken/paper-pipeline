"""arXiv discovery: the search API (Atom) and the per-category RSS feeds.

Both are parsed with the stdlib XML parser and normalised to the same dict shape,
so every downstream stage is source-agnostic.
"""

from __future__ import annotations

import time
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from typing import Dict, List, Optional

import requests

from . import config

ATOM = "{http://www.w3.org/2005/Atom}"
ARXIV = "{http://arxiv.org/schemas/atom}"
RSS_ARXIV = "{http://arxiv.org/schemas/atom}"
DC = "{http://purl.org/dc/elements/1.1/}"


class ArxivError(RuntimeError):
    """Raised when the arXiv API cannot be queried or parsed."""


def _text(node: ET.Element, path: str) -> Optional[str]:
    el = node.find(path)
    if el is None or el.text is None:
        return None
    value = " ".join(el.text.split())
    return value or None


def _split_id(raw_id: str) -> tuple:
    """``http://arxiv.org/abs/2401.12345v2`` -> ``('2401.12345', 'v2')``.

    Old-style identifiers keep their archive prefix: ``math/0301234v1`` ->
    ``('math/0301234', 'v1')``, because ``arxiv.org/pdf/math/0301234`` is the
    address that actually resolves.
    """
    tail = raw_id.strip().rstrip("/")
    for marker in ("/abs/", "/pdf/"):
        if marker in tail:
            tail = tail.split(marker, 1)[1]
            break
    else:
        tail = tail.rsplit("/", 1)[-1]
    base, _, version = tail.rpartition("v")
    if base and version.isdigit():
        return base, "v" + version
    return tail, ""


def entry_to_dict(entry: ET.Element) -> Dict[str, object]:
    raw_id = _text(entry, f"{ATOM}id") or ""
    base_id, version = _split_id(raw_id)
    authors = [
        (a.find(f"{ATOM}name").text or "").strip()
        for a in entry.findall(f"{ATOM}author")
        if a.find(f"{ATOM}name") is not None
    ]
    categories = [c.get("term") for c in entry.findall(f"{ATOM}category") if c.get("term")]
    primary_el = entry.find(f"{ARXIV}primary_category")
    primary = primary_el.get("term") if primary_el is not None else (categories[0] if categories else None)
    doi = _text(entry, f"{ARXIV}doi")
    journal_ref = _text(entry, f"{ARXIV}journal_ref")
    comment = _text(entry, f"{ARXIV}comment")
    pdf_url = None
    for link in entry.findall(f"{ATOM}link"):
        if link.get("title") == "pdf" or link.get("type") == "application/pdf":
            pdf_url = link.get("href")
            break
    if not pdf_url and base_id:
        pdf_url = config.ARXIV_PDF.format(arxiv_id=base_id)
    return {
        "arxiv_id": base_id,
        "version": version,
        "title": _text(entry, f"{ATOM}title") or "(untitled)",
        "abstract": _text(entry, f"{ATOM}summary"),
        "authors": authors,
        "primary_category": primary,
        "categories": categories,
        "published": _text(entry, f"{ATOM}published"),
        "updated": _text(entry, f"{ATOM}updated"),
        "doi": doi,
        "journal_ref": journal_ref,
        "comment": comment,
        "pdf_url": pdf_url,
        "abs_url": config.ARXIV_ABS.format(arxiv_id=base_id) if base_id else None,
        "source": "arxiv",
        "cited_by": None,
    }


def parse_feed(xml_text: str) -> List[Dict[str, object]]:
    """Parse an arXiv Atom response into plain dicts."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:  # pragma: no cover - defensive
        raise ArxivError(f"could not parse arXiv response: {exc}") from exc
    return [entry_to_dict(e) for e in root.findall(f"{ATOM}entry")]


def _query_string(query: str, category: Optional[str]) -> str:
    q = query.strip()
    # If the caller already wrote field prefixes (all:/ti:/au:), pass it through.
    if ":" not in q.split(" ")[0]:
        q = f'all:"{q}"'
    if category:
        q = f"({q}) AND cat:{category}"
    return q


def search(
    query: str,
    max_results: int = config.DEFAULT_MAX,
    sort: str = "relevance",
    session: Optional[requests.Session] = None,
    delay: float = config.DEFAULT_DELAY,
    category: Optional[str] = None,
    page_size: int = 100,
) -> List[Dict[str, object]]:
    """Query the arXiv API, paging until ``max_results`` or exhaustion."""
    sess = session or requests.Session()
    sess.headers.setdefault("User-Agent", config.USER_AGENT)
    order = "submittedDate" if sort in ("date", "submittedDate") else "relevance"
    search_query = _query_string(query, category)

    results: List[Dict[str, object]] = []
    start = 0
    while len(results) < max_results:
        want = min(page_size, max_results - len(results))
        params = {
            "search_query": search_query,
            "start": start,
            "max_results": want,
            "sortBy": order,
            "sortOrder": "descending",
        }
        resp = _get(sess, params)
        page = parse_feed(resp.text)
        if not page:
            break
        results.extend(page)
        start += len(page)
        if len(page) < want:
            break
        if len(results) < max_results:
            time.sleep(delay)
    return results[:max_results]


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
    attempts: int = 5,
    base_backoff: float = 10.0,
    max_backoff: float = 120.0,
    url: Optional[str] = None,
) -> requests.Response:
    """GET the API, backing off hard on 429.

    arXiv throttles per IP (``429`` + a 14-byte ``Rate exceeded.`` body) and does
    not always send ``Retry-After``, so retries use exponential backoff that can
    outlive a single burst budget instead of hammering the endpoint.
    """
    target = url or config.ARXIV_API
    last: Optional[Exception] = None
    for attempt in range(attempts):
        try:
            resp = session.get(target, params=params, timeout=30)
            if resp.status_code == 200:
                return resp
            if resp.status_code == 429:
                wait = _retry_after(resp) or min(base_backoff * (2 ** attempt), max_backoff)
                last = ArxivError(
                    f"arXiv rate limit hit (HTTP 429, body={resp.text.strip()[:60]!r}); "
                    f"waited {wait:.0f}s"
                )
                if attempt < attempts - 1:
                    time.sleep(wait)
                    continue
            else:
                last = ArxivError(f"arXiv returned HTTP {resp.status_code}")
        except requests.RequestException as exc:
            last = exc
        if attempt < attempts - 1:
            time.sleep(min(3 * (attempt + 1), max_backoff))
    raise ArxivError(f"arXiv request failed: {last}")


def rss_url(category: str) -> str:
    return config.ARXIV_RSS.format(category=category)


def _abstract_from_description(description: Optional[str]) -> Optional[str]:
    if not description:
        return None
    marker = "Abstract:"
    text = description.split(marker, 1)[1] if marker in description else description
    return " ".join(text.split()) or None


def _iso_from_rfc822(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    try:
        return parsedate_to_datetime(value).isoformat()
    except (TypeError, ValueError):
        return None


def rss_item_to_dict(item: ET.Element) -> Dict[str, object]:
    raw_id = (item.findtext("guid") or "").strip()
    if raw_id.startswith("oai:arXiv.org:"):
        base_id, version = _split_id(raw_id.split("oai:arXiv.org:", 1)[1])
    else:
        base_id, version = _split_id(item.findtext("link") or "")
    categories = [c.text.strip() for c in item.findall("category") if (c.text or "").strip()]
    creators = [c.text for c in item.findall(f"{DC}creator") if c.text]
    authors: List[str] = []
    for creator in creators:
        authors.extend(a.strip() for a in creator.split(",") if a.strip())
    announce = item.findtext(f"{RSS_ARXIV}announce_type") or ""
    return {
        "arxiv_id": base_id,
        "version": version,
        "title": " ".join((item.findtext("title") or "(untitled)").split()),
        "abstract": _abstract_from_description(item.findtext("description")),
        "authors": authors,
        "primary_category": categories[0] if categories else None,
        "categories": categories,
        "published": _iso_from_rfc822(item.findtext("pubDate")),
        "updated": _iso_from_rfc822(item.findtext("pubDate")),
        "doi": None,
        "journal_ref": None,
        "comment": f"announce_type={announce}" if announce else None,
        "pdf_url": config.ARXIV_PDF.format(arxiv_id=base_id) if base_id else None,
        "abs_url": config.ARXIV_ABS.format(arxiv_id=base_id) if base_id else None,
        "source": "arxiv-rss",
        "cited_by": None,
    }


def parse_rss(xml_text: str) -> List[Dict[str, object]]:
    """Parse an arXiv category RSS feed into the same shape as ``parse_feed``."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise ArxivError(f"could not parse arXiv RSS: {exc}") from exc
    return [rss_item_to_dict(i) for i in root.findall(".//item")]


def _matches(paper: Dict[str, object], keyword: Optional[str]) -> bool:
    if not keyword or not keyword.strip():
        return True
    needle = keyword.lower()
    haystack = " ".join(
        [
            str(paper.get("title") or ""),
            str(paper.get("abstract") or ""),
            " ".join(paper.get("authors") or []),
        ]
    ).lower()
    return all(term in haystack for term in needle.split())


def latest(
    categories: List[str],
    keyword: Optional[str] = None,
    max_results: int = config.DEFAULT_MAX,
    session: Optional[requests.Session] = None,
    delay: float = config.DEFAULT_DELAY,
) -> List[Dict[str, object]]:
    """Newest announcements from the per-category RSS feeds.

    The feed carries one announcement batch per category (no server-side search),
    so ``keyword`` filters client-side over title/abstract/authors. An empty
    keyword returns the batch as-is.
    """
    sess = session or requests.Session()
    sess.headers.setdefault("User-Agent", config.USER_AGENT)
    collected: List[Dict[str, object]] = []
    seen = set()
    for index, category in enumerate(categories):
        if index:
            time.sleep(delay)
        resp = _get(sess, {}, url=rss_url(category))
        for paper in parse_rss(resp.text):
            if not paper["arxiv_id"] or paper["arxiv_id"] in seen:
                continue
            if _matches(paper, keyword):
                seen.add(paper["arxiv_id"])
                collected.append(paper)
        if len(collected) >= max_results:
            break
    return collected[:max_results]
