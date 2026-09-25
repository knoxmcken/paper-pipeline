"""SQLite storage - the pipeline's source of truth."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Dict, Iterable, List, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS papers (
    arxiv_id         TEXT PRIMARY KEY,
    version          TEXT,
    title            TEXT NOT NULL,
    abstract         TEXT,
    authors          TEXT,
    primary_category TEXT,
    categories       TEXT,
    published        TEXT,
    updated          TEXT,
    doi              TEXT,
    journal_ref      TEXT,
    comment          TEXT,
    abs_url          TEXT,
    pdf_url          TEXT,
    pdf_path         TEXT,
    pdf_sha256       TEXT,
    pdf_bytes        INTEGER,
    text_path        TEXT,
    text_chars       INTEGER,
    page_count       INTEGER,
    headings         TEXT,
    headings_method  TEXT,
    source           TEXT,
    cited_by         INTEGER,
    fetched_at       TEXT,
    extracted_at     TEXT
);

CREATE TABLE IF NOT EXISTS runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    kind        TEXT NOT NULL,
    args        TEXT,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    ok          INTEGER,
    message     TEXT
);

CREATE INDEX IF NOT EXISTS idx_papers_primary_category ON papers(primary_category);
CREATE INDEX IF NOT EXISTS idx_papers_published ON papers(published);

CREATE VIRTUAL TABLE IF NOT EXISTS papers_fts USING fts5(
    arxiv_id UNINDEXED, page UNINDEXED, content
);
"""

JSON_FIELDS = ("authors", "categories", "headings")

UPSERT = """
INSERT INTO papers (
    arxiv_id, version, title, abstract, authors, primary_category, categories,
    published, updated, doi, journal_ref, comment, abs_url, pdf_url,
    pdf_path, pdf_sha256, pdf_bytes, text_path, text_chars, page_count,
    headings, source, cited_by, fetched_at, extracted_at
) VALUES (
    :arxiv_id, :version, :title, :abstract, :authors, :primary_category, :categories,
    :published, :updated, :doi, :journal_ref, :comment, :abs_url, :pdf_url,
    :pdf_path, :pdf_sha256, :pdf_bytes, :text_path, :text_chars, :page_count,
    :headings, :source, :cited_by, :fetched_at, :extracted_at
)
ON CONFLICT(arxiv_id) DO UPDATE SET
    version          = COALESCE(excluded.version, papers.version),
    title            = excluded.title,
    abstract         = COALESCE(excluded.abstract, papers.abstract),
    authors          = COALESCE(excluded.authors, papers.authors),
    primary_category = COALESCE(excluded.primary_category, papers.primary_category),
    categories       = COALESCE(excluded.categories, papers.categories),
    published        = COALESCE(excluded.published, papers.published),
    updated          = COALESCE(excluded.updated, papers.updated),
    doi              = COALESCE(excluded.doi, papers.doi),
    journal_ref      = COALESCE(excluded.journal_ref, papers.journal_ref),
    comment          = COALESCE(excluded.comment, papers.comment),
    abs_url          = COALESCE(excluded.abs_url, papers.abs_url),
    pdf_url          = COALESCE(excluded.pdf_url, papers.pdf_url),
    pdf_path         = COALESCE(excluded.pdf_path, papers.pdf_path),
    pdf_sha256       = COALESCE(excluded.pdf_sha256, papers.pdf_sha256),
    pdf_bytes        = COALESCE(excluded.pdf_bytes, papers.pdf_bytes),
    text_path        = COALESCE(excluded.text_path, papers.text_path),
    text_chars       = COALESCE(excluded.text_chars, papers.text_chars),
    page_count       = COALESCE(excluded.page_count, papers.page_count),
    headings         = COALESCE(excluded.headings, papers.headings),
    source           = COALESCE(papers.source, excluded.source),
    cited_by         = COALESCE(excluded.cited_by, papers.cited_by),
    fetched_at       = COALESCE(papers.fetched_at, excluded.fetched_at),
    extracted_at     = COALESCE(excluded.extracted_at, papers.extracted_at)
"""

# Columns added after the first release; merged into existing databases on open.
MIGRATIONS = (("source", "TEXT"), ("cited_by", "INTEGER"), ("headings_method", "TEXT"))


def connect(path: Path) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    existing = {row[1] for row in conn.execute("PRAGMA table_info(papers)")}
    for column, ddl in MIGRATIONS:
        if column not in existing:
            conn.execute(f"ALTER TABLE papers ADD COLUMN {column} {ddl}")
    conn.commit()


def _encode(row: Dict[str, object]) -> Dict[str, object]:
    out = dict(row)
    for field in JSON_FIELDS:
        if field in out and not isinstance(out[field], (str, type(None))):
            out[field] = json.dumps(out[field])
    return out


def _decode(row: sqlite3.Row) -> Dict[str, object]:
    out = dict(row)
    for field in JSON_FIELDS:
        raw = out.get(field)
        if isinstance(raw, str):
            try:
                out[field] = json.loads(raw)
            except json.JSONDecodeError:
                out[field] = []
    return out


def upsert_papers(conn: sqlite3.Connection, records: Iterable[Dict[str, object]]) -> int:
    columns = {r[1] for r in conn.execute("PRAGMA table_info(papers)")}
    count = 0
    for record in records:
        row = {c: None for c in columns}
        row.update({k: v for k, v in record.items() if k in columns})
        row["title"] = row.get("title") or "(untitled)"
        conn.execute(UPSERT, _encode(row))
        count += 1
    conn.commit()
    return count


def update_pdf(conn: sqlite3.Connection, arxiv_id: str, info: Dict[str, object]) -> None:
    conn.execute(
        "UPDATE papers SET pdf_path=?, pdf_sha256=?, pdf_bytes=? WHERE arxiv_id=?",
        (info.get("path"), info.get("sha256"), info.get("bytes"), arxiv_id),
    )
    conn.commit()


def update_pdf_url(conn: sqlite3.Connection, arxiv_id: str, url: str) -> None:
    conn.execute("UPDATE papers SET pdf_url=? WHERE arxiv_id=?", (url, arxiv_id))
    conn.commit()


def update_extraction(conn: sqlite3.Connection, arxiv_id: str, info: Dict[str, object],
                      extracted_at: str) -> None:
    conn.execute(
        """UPDATE papers SET text_path=?, text_chars=?, page_count=?, headings=?,
           headings_method=?, extracted_at=? WHERE arxiv_id=?""",
        (
            info.get("text_path"),
            info.get("text_chars"),
            info.get("page_count"),
            json.dumps(info.get("headings") or []),
            info.get("headings_method"),
            extracted_at,
            arxiv_id,
        ),
    )
    conn.commit()


def get_paper(conn: sqlite3.Connection, arxiv_id: str) -> Optional[Dict[str, object]]:
    row = conn.execute("SELECT * FROM papers WHERE arxiv_id=?", (arxiv_id,)).fetchone()
    return _decode(row) if row else None


def list_papers(conn: sqlite3.Connection, limit: Optional[int] = None,
                order: str = "published DESC") -> List[Dict[str, object]]:
    sql = f"SELECT * FROM papers ORDER BY {order}"
    if limit:
        sql += f" LIMIT {int(limit)}"
    return [_decode(r) for r in conn.execute(sql)]


def papers_missing(conn: sqlite3.Connection, what: str) -> List[Dict[str, object]]:
    column = {"pdf": "pdf_path", "text": "text_path"}[what]
    rows = conn.execute(f"SELECT * FROM papers WHERE {column} IS NULL ORDER BY published DESC")
    return [_decode(r) for r in rows]


def search(conn: sqlite3.Connection, term: str, limit: int = 25) -> List[Dict[str, object]]:
    like = f"%{term}%"
    rows = conn.execute(
        """SELECT * FROM papers
           WHERE title LIKE ? OR abstract LIKE ? OR authors LIKE ? OR arxiv_id LIKE ?
           ORDER BY published DESC LIMIT ?""",
        (like, like, like, like, limit),
    )
    return [_decode(r) for r in rows]


def index_fulltext(conn: sqlite3.Connection, arxiv_id: str, text_path: str) -> int:
    """(Re)populate ``papers_fts`` for one paper, one row per non-blank page."""
    conn.execute("DELETE FROM papers_fts WHERE arxiv_id=?", (arxiv_id,))
    text = Path(text_path).read_text(encoding="utf-8", errors="replace")
    rows = [
        (arxiv_id, page_num, page_text.strip())
        for page_num, page_text in enumerate(text.split("\f"), start=1)
        if page_text.strip()
    ]
    conn.executemany(
        "INSERT INTO papers_fts (arxiv_id, page, content) VALUES (?, ?, ?)", rows
    )
    conn.commit()
    return len(rows)


def backfill_fts(conn: sqlite3.Connection) -> int:
    """Index any paper with extracted text that isn't in ``papers_fts`` yet.

    Lets ``--fulltext`` work against a database created before FTS existed,
    and against text extracted before FTS existed, without a separate
    migration step.
    """
    rows = conn.execute(
        """SELECT arxiv_id, text_path FROM papers
           WHERE text_path IS NOT NULL
             AND arxiv_id NOT IN (SELECT DISTINCT arxiv_id FROM papers_fts)"""
    ).fetchall()
    indexed = 0
    for row in rows:
        path = row["text_path"]
        if path and Path(path).exists():
            index_fulltext(conn, row["arxiv_id"], path)
            indexed += 1
    return indexed


def search_fulltext(conn: sqlite3.Connection, phrase: str, limit: int = 20) -> List[Dict[str, object]]:
    """Ranked phrase search over extracted text, with a snippet per hit."""
    query = '"' + phrase.replace('"', '""') + '"'
    rows = conn.execute(
        """SELECT f.arxiv_id, f.page, p.title,
                  snippet(papers_fts, 2, '[', ']', ' ... ', 12) AS snippet,
                  bm25(papers_fts) AS rank
           FROM papers_fts f JOIN papers p ON p.arxiv_id = f.arxiv_id
           WHERE papers_fts MATCH ?
           ORDER BY rank LIMIT ?""",
        (query, limit),
    )
    return [dict(r) for r in rows]


def stats(conn: sqlite3.Connection) -> Dict[str, int]:
    total = conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0]
    with_pdf = conn.execute("SELECT COUNT(*) FROM papers WHERE pdf_path IS NOT NULL").fetchone()[0]
    with_text = conn.execute("SELECT COUNT(*) FROM papers WHERE text_path IS NOT NULL").fetchone()[0]
    pages = conn.execute("SELECT COALESCE(SUM(page_count), 0) FROM papers").fetchone()[0]
    return {"papers": total, "with_pdf": with_pdf, "with_text": with_text, "pages": pages}


def start_run(conn: sqlite3.Connection, kind: str, args: str, started_at: str) -> int:
    cur = conn.execute(
        "INSERT INTO runs (kind, args, started_at) VALUES (?, ?, ?)", (kind, args, started_at)
    )
    conn.commit()
    return int(cur.lastrowid)


def finish_run(conn: sqlite3.Connection, run_id: int, ok: bool, message: str,
               finished_at: str) -> None:
    conn.execute(
        "UPDATE runs SET ok=?, message=?, finished_at=? WHERE id=?",
        (1 if ok else 0, message, finished_at, run_id),
    )
    conn.commit()
