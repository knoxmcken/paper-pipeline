"""Find (and, only on request, merge) the same work stored under two keys.

A corpus grown from several sources holds the same paper twice - once under its
arXiv id, once as ``doi:<doi>`` - and a preprint and its journal version can carry
different DOIs and different years. Candidates are paired on any of:

  doi          - both rows resolve to the same DOI (case-insensitive)
  arxiv_id     - both rows point at the same arXiv id: a key, an arXiv DOI
                 (``10.48550/arXiv.<id>``) or an ``arxiv.org/abs|pdf/<id>`` link
  title        - same normalised title, first author and year (a year apart is
                 allowed: preprints are often published the following year)
  near_title   - titles at least ``NEAR_TITLE_RATIO`` similar, same first author
                 and year (within one)

Nothing is ever deleted by detection. ``merge()`` folds one row into another and is
reached only through an explicit ``paperpipe duplicates --merge KEEP DROP``.
"""

from __future__ import annotations

import re
import sqlite3
import unicodedata
from difflib import SequenceMatcher
from typing import Dict, List, Optional, Set

from . import bibliography, db

NEAR_TITLE_RATIO = 0.9

ARXIV_IN_TEXT_RE = re.compile(
    r"(?:arxiv\.org/(?:abs|pdf)/|10\.48550/arxiv\.)"
    r"(\d{4}\.\d{4,5}|[a-z\-]+(?:\.[a-z]{2})?/\d{7})",
    re.IGNORECASE,
)

# Column groups that belong together: a PDF from one row and text from another
# would describe different files, so a merge takes each group whole or not at all.
PDF_GROUP = ("pdf_path", "pdf_sha256", "pdf_bytes")
TEXT_GROUP = ("text_path", "text_chars", "page_count", "headings", "headings_method", "extracted_at")


def _fold(text: str) -> str:
    """Casefold and drop accents, keeping letters from every script (not just ASCII)."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch)).casefold()


def normalise_title(title: str) -> str:
    text = re.sub(r"\$[^$]*\$", " ", title or "")  # inline LaTeX renders differently per source
    return " ".join("".join(ch if ch.isalnum() else " " for ch in _fold(text)).split())


def first_author(paper: Dict[str, object]) -> str:
    authors = paper.get("authors") or []
    if not authors:
        return ""
    family = bibliography.split_name(str(authors[0]))[1]
    return "".join(ch for ch in _fold(family) if ch.isalpha())


def _year(paper: Dict[str, object]) -> Optional[int]:
    found = bibliography.year(paper)
    return int(found) if found else None


def _strip_version(arxiv_id: str) -> str:
    return re.sub(r"v\d+$", "", arxiv_id).lower()


def arxiv_ids(paper: Dict[str, object]) -> Set[str]:
    """Every arXiv id (version-less) this row points at, from its key, DOI and links."""
    found: Set[str] = set()
    key = bibliography.arxiv_id(paper)
    if key:
        found.add(_strip_version(key))
    for field in ("doi", "abs_url", "pdf_url", "arxiv_id"):
        for match in ARXIV_IN_TEXT_RE.finditer(str(paper.get(field) or "")):
            found.add(_strip_version(match.group(1)))
    return found


def _doi(paper: Dict[str, object]) -> Optional[str]:
    value = bibliography.doi(paper)
    return value.strip().lower() if value else None


def _features(paper: Dict[str, object]) -> Dict[str, object]:
    return {
        "doi": _doi(paper),
        "arxiv": arxiv_ids(paper),
        "author": first_author(paper),
        "year": _year(paper),
        "title": normalise_title(str(paper.get("title") or "")),
    }


def _compare_features(a: Dict[str, object], b: Dict[str, object]) -> List[str]:
    evidence = []
    if a["doi"] and a["doi"] == b["doi"]:
        evidence.append(f"doi: {a['doi']}")
    shared = sorted(a["arxiv"] & b["arxiv"])
    if shared:
        evidence.append(f"arxiv_id: {', '.join(shared)}")
    ya, yb = a["year"], b["year"]
    if not (a["author"] and a["author"] == b["author"] and ya is not None and yb is not None
            and abs(ya - yb) <= 1):
        return evidence
    years = str(ya) if ya == yb else f"{min(ya, yb)}/{max(ya, yb)}"
    if a["title"] and a["title"] == b["title"]:
        evidence.append(f"title: same title, first author '{a['author']}', year {years}")
    elif a["title"] and b["title"]:
        matcher = SequenceMatcher(None, a["title"], b["title"])
        # real_quick_ratio/quick_ratio are cheap upper bounds on ratio(); skip early
        ratio = 0.0
        if (matcher.real_quick_ratio() >= NEAR_TITLE_RATIO
                and matcher.quick_ratio() >= NEAR_TITLE_RATIO):
            ratio = matcher.ratio()
        if ratio >= NEAR_TITLE_RATIO:
            evidence.append(
                f"near_title: {ratio:.2f} similar, first author '{a['author']}', year {years}"
            )
    return evidence


def compare(a: Dict[str, object], b: Dict[str, object]) -> List[str]:
    """The evidence that ``a`` and ``b`` are the same work (empty if none)."""
    return _compare_features(_features(a), _features(b))


def find_duplicates(papers: List[Dict[str, object]]) -> List[Dict[str, object]]:
    """Candidate pairs, strongest evidence first, each pair reported once.

    Only papers sharing a DOI, an arXiv id or a first author are compared, so this
    stays fast on large corpora; every kind of evidence needs one of the three.
    """
    ordered = sorted(papers, key=lambda p: str(p["arxiv_id"]))
    features = [_features(p) for p in ordered]
    buckets: Dict[str, List[int]] = {}
    for i, f in enumerate(features):
        keys = [f"doi:{f['doi']}"] if f["doi"] else []
        keys += [f"arxiv:{a}" for a in f["arxiv"]]
        keys += [f"author:{f['author']}"] if f["author"] else []
        for key in keys:
            buckets.setdefault(key, []).append(i)
    pairs = set()
    for members in buckets.values():
        for n, i in enumerate(members):
            for j in members[n + 1:]:
                pairs.add((i, j))

    candidates = []
    for i, j in sorted(pairs):
        evidence = _compare_features(features[i], features[j])
        if evidence:
            candidates.append(
                {"a": ordered[i]["arxiv_id"], "b": ordered[j]["arxiv_id"], "evidence": evidence}
            )
    candidates.sort(key=lambda c: (c["evidence"][0].startswith("near_title"), -len(c["evidence"])))
    return candidates


def merge(conn: sqlite3.Connection, keep: str, drop: str) -> Dict[str, object]:
    """Fold ``drop`` into ``keep``, then delete ``drop``. Files on disk are left alone.

    ``keep`` wins every column it already has; its gaps are filled from ``drop``
    (the PDF and text column groups move whole), ``cited_by`` takes the larger
    count, and ``drop``'s full-text rows move over if its text did.
    """
    if keep == drop:
        raise ValueError("cannot merge a paper into itself")
    kept, dropped = db.get_paper(conn, keep), db.get_paper(conn, drop)
    for key, row in ((keep, kept), (drop, dropped)):
        if row is None:
            raise ValueError(f"no paper with key {key!r}")

    updates: Dict[str, object] = {}
    grouped = set(PDF_GROUP) | set(TEXT_GROUP)
    for column, value in dropped.items():
        if column in grouped or column == "arxiv_id":
            continue
        if kept.get(column) in (None, "", []) and value not in (None, "", []):
            updates[column] = value
    if kept.get("cited_by") is not None and dropped.get("cited_by") is not None:
        if dropped["cited_by"] > kept["cited_by"]:
            updates["cited_by"] = dropped["cited_by"]
    took_text = False
    for group in (PDF_GROUP, TEXT_GROUP):
        if not kept.get(group[0]) and dropped.get(group[0]):
            updates.update({column: dropped.get(column) for column in group})
            took_text = took_text or group is TEXT_GROUP

    encoded = db._encode(updates)
    with conn:
        if encoded:
            assignments = ", ".join(f"{column}=?" for column in encoded)
            conn.execute(
                f"UPDATE papers SET {assignments} WHERE arxiv_id=?", [*encoded.values(), keep]
            )
        if took_text:
            conn.execute("DELETE FROM papers_fts WHERE arxiv_id=?", (keep,))
            conn.execute("UPDATE papers_fts SET arxiv_id=? WHERE arxiv_id=?", (keep, drop))
        else:
            conn.execute("DELETE FROM papers_fts WHERE arxiv_id=?", (drop,))
        conn.execute("DELETE FROM papers WHERE arxiv_id=?", (drop,))

    orphans = [
        str(dropped[column]) for column in ("pdf_path", "text_path")
        if dropped.get(column) and dropped[column] != updates.get(column)
    ]
    return {"keep": keep, "drop": drop, "filled": sorted(updates), "orphaned_files": orphans}
