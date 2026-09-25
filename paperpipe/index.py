"""Derived JSON index - regenerated from the database, never hand-edited."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

from . import db


def build(conn, path: Path, limit: int = None) -> Dict[str, object]:
    """Write ``index.json`` from the DB and return the same structure.

    The DB is the master; this file is a navigation guide and is safe to delete.
    """
    papers: List[Dict[str, object]] = db.list_papers(conn, limit=limit, order="title COLLATE NOCASE ASC")
    entries = []
    for p in papers:
        entries.append(
            {
                "arxiv_id": p["arxiv_id"],
                "title": p["title"],
                "authors": p.get("authors") or [],
                "published": p.get("published"),
                "primary_category": p.get("primary_category"),
                "categories": p.get("categories") or [],
                "abs_url": p.get("abs_url"),
                "pdf_path": p.get("pdf_path"),
                "text_path": p.get("text_path"),
                "page_count": p.get("page_count"),
                "headings": p.get("headings") or [],
                "headings_method": p.get("headings_method"),
                "source": p.get("source"),
                "doi": p.get("doi"),
            }
        )
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "count": len(entries),
        "papers": entries,
    }
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return payload


def check(conn, path: Path) -> bool:
    """True when the on-disk index matches what the DB would generate."""
    path = Path(path)
    if not path.exists():
        return False
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    fresh = build(conn, path.with_suffix(".tmp.json"))
    path.with_suffix(".tmp.json").unlink(missing_ok=True)
    on_disk.pop("generated_at", None)
    fresh.pop("generated_at", None)
    return on_disk == fresh
