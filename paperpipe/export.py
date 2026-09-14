"""Human-readable exports derived from the database."""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

from . import db


def _fmt_authors(authors: Optional[List[str]], limit: int = 6) -> str:
    authors = authors or []
    if not authors:
        return "Unknown"
    if len(authors) <= limit:
        return ", ".join(authors)
    return ", ".join(authors[:limit]) + f", et al. ({len(authors)} authors)"


def _fmt_date(value: Optional[str]) -> str:
    return (value or "")[:10] or "n.d."


def build_markdown(conn, title: str = "Paper Index", category: bool = True) -> str:
    papers = db.list_papers(conn)
    groups: Dict[str, List[Dict[str, object]]] = {}
    for paper in papers:
        key = (paper.get("primary_category") or "uncategorised") if category else "all"
        groups.setdefault(key, []).append(paper)

    lines = [f"# {title}", ""]
    lines.append(f"**{len(papers)} papers** across {len(groups)} categories. Generated from `papers.db`.")
    lines.append("")

    for key in sorted(groups, key=lambda k: (k == "uncategorised", k)):
        items = sorted(groups[key], key=lambda p: (p.get("title") or "").lower())
        if category:
            lines.append(f"## {key}  ({len(items)})")
            lines.append("")
        for paper in items:
            lines.append(f"### {paper.get('title')}")
            lines.append("")
            lines.append(f"- **Authors:** {_fmt_authors(paper.get('authors'))}")
            lines.append(f"- **Published:** {_fmt_date(paper.get('published'))}")
            lines.append(f"- **arXiv:** [{paper.get('arxiv_id')}]({paper.get('abs_url')})")
            if paper.get("journal_ref"):
                lines.append(f"- **Journal:** {paper['journal_ref']}")
            if paper.get("pages") or paper.get("page_count"):
                lines.append(f"- **Pages:** {paper.get('page_count')}")
            headings = paper.get("headings") or []
            if headings:
                preview = "; ".join(headings[:8])
                lines.append(f"- **Sections:** {preview}")
            abstract = paper.get("abstract")
            if abstract:
                lines.append("")
                lines.append(f"> {abstract}")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_markdown(conn, path: Path, title: str = "Paper Index", category: bool = True) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build_markdown(conn, title=title, category=category), encoding="utf-8")
    return path


def build_csv(conn, path: Path) -> Path:
    import csv

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    papers = db.list_papers(conn)
    fields = ["arxiv_id", "title", "primary_category", "published", "page_count", "abs_url", "pdf_path"]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(fields + ["authors"])
        for paper in papers:
            writer.writerow([paper.get(f) for f in fields] + ["; ".join(paper.get("authors") or [])])
    return path
