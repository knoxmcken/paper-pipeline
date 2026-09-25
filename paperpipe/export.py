"""Human-readable exports derived from the database.

Every format renders from a plain list of papers, so the CLI (whole corpus, written
to disk) and the web UI (the currently listed papers, sent as a download) share one
implementation per format. ``FORMATS`` is the registry both use.
"""

from __future__ import annotations

import csv
import io
import zipfile
from pathlib import Path
from typing import Callable, Dict, List, NamedTuple, Optional
from xml.sax.saxutils import escape

from . import bibliography, db


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
    return render_markdown(db.list_papers(conn), title=title, category=category)


def render_markdown(papers: List[Dict[str, object]], title: str = "Paper Index",
                    category: bool = True) -> str:
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
            if not paper.get("pdf_path"):
                lines.append("- **Access:** metadata only — no open-access PDF found")
            if paper.get("status"):
                lines.append(f"- **Status:** {paper['status']}")
            if paper.get("notes"):
                lines.append(f"- **Notes:** {' '.join(str(paper['notes']).split())}")
            if paper.get("pages") or paper.get("page_count"):
                lines.append(f"- **Pages:** {paper.get('page_count')}")
            headings = paper.get("headings") or []
            if headings:
                preview = "; ".join(h.strip() for h in headings[:8])
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


# One row per paper for CSV and XLSX. ``arxiv_id`` is the stored key (which may be
# ``doi:...``); ``arxiv`` is the real arXiv id, blank for works that have none.
TABLE_COLUMNS = [
    "arxiv_id", "title", "primary_category", "published", "page_count", "abs_url", "pdf_path",
    "authors", "metadata_only", "year", "doi", "arxiv", "url", "status", "notes",
]


def table_rows(papers: List[Dict[str, object]]) -> List[List[object]]:
    rows = []
    for paper in papers:
        rows.append([
            paper.get("arxiv_id"), paper.get("title"), paper.get("primary_category"),
            paper.get("published"), paper.get("page_count"), paper.get("abs_url"),
            paper.get("pdf_path"), "; ".join(paper.get("authors") or []),
            not paper.get("pdf_path"), bibliography.year(paper), bibliography.doi(paper),
            bibliography.arxiv_id(paper), bibliography.url(paper),
            paper.get("status") or "new", paper.get("notes"),
        ])
    return rows


def render_csv(papers: List[Dict[str, object]]) -> str:
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(TABLE_COLUMNS)
    writer.writerows(table_rows(papers))
    return out.getvalue()


def build_csv(conn, path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        fh.write(render_csv(db.list_papers(conn)))
    return path


def _xlsx_column(index: int) -> str:
    name = ""
    index += 1
    while index:
        index, rem = divmod(index - 1, 26)
        name = chr(65 + rem) + name
    return name


def _xlsx_cell(ref: str, value: object) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, bool):
        return f'<c r="{ref}" t="b"><v>{int(value)}</v></c>'
    if isinstance(value, (int, float)):
        return f'<c r="{ref}"><v>{value}</v></c>'
    # Excel rejects most control characters, even escaped; cells cap at 32,767 chars.
    text = "".join(ch for ch in str(value) if ch in "\t\n" or ord(ch) >= 32)[:32767]
    return f'<c r="{ref}" t="inlineStr"><is><t xml:space="preserve">{escape(text)}</t></is></c>'


def render_xlsx(papers: List[Dict[str, object]]) -> bytes:
    """A one-sheet workbook, header row plus one row per paper, with no dependency.

    Inline strings keep it to the four parts Excel strictly needs; tests read it back
    with openpyxl to check it is a valid workbook.
    """
    rows = [TABLE_COLUMNS] + table_rows(papers)
    sheet_rows = []
    for r, row in enumerate(rows, start=1):
        cells = "".join(_xlsx_cell(f"{_xlsx_column(c)}{r}", v) for c, v in enumerate(row))
        sheet_rows.append(f'<row r="{r}">{cells}</row>')
    ns = "http://schemas.openxmlformats.org"
    parts = {
        "[Content_Types].xml": (
            f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<Types xmlns="{ns}/package/2006/content-types">'
            f'<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            f'<Default Extension="xml" ContentType="application/xml"/>'
            f'<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            f'<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            f"</Types>"
        ),
        "_rels/.rels": (
            f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<Relationships xmlns="{ns}/package/2006/relationships">'
            f'<Relationship Id="rId1" Type="{ns}/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            f"</Relationships>"
        ),
        "xl/workbook.xml": (
            f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<workbook xmlns="{ns}/spreadsheetml/2006/main" xmlns:r="{ns}/officeDocument/2006/relationships">'
            f'<sheets><sheet name="papers" sheetId="1" r:id="rId1"/></sheets></workbook>'
        ),
        "xl/_rels/workbook.xml.rels": (
            f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<Relationships xmlns="{ns}/package/2006/relationships">'
            f'<Relationship Id="rId1" Type="{ns}/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
            f"</Relationships>"
        ),
        "xl/worksheets/sheet1.xml": (
            f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<worksheet xmlns="{ns}/spreadsheetml/2006/main"><sheetData>'
            + "".join(sheet_rows)
            + "</sheetData></worksheet>"
        ),
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, body in parts.items():
            # fixed timestamp so an unchanged corpus exports byte-identical files
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, body)
    return buf.getvalue()


class Format(NamedTuple):
    filename: str  # what the CLI writes under the export dir
    extension: str  # what a download is named: paper-export.<extension>
    media_type: str
    render: Callable[[List[Dict[str, object]]], object]  # -> str, or bytes for binary formats


FORMATS: Dict[str, Format] = {
    "md": Format("papers.md", "md", "text/markdown; charset=utf-8", render_markdown),
    "csv": Format("papers.csv", "csv", "text/csv; charset=utf-8", render_csv),
    "xlsx": Format(
        "papers.xlsx", "xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", render_xlsx,
    ),
    "bibtex": Format("papers.bib", "bib", "application/x-bibtex; charset=utf-8",
                     bibliography.to_bibtex),
    "biblatex": Format("papers.biblatex.bib", "bib", "application/x-bibtex; charset=utf-8",
                       bibliography.to_biblatex),
    "csljson": Format("papers.csl.json", "json", "application/vnd.citationstyles.csl+json",
                      bibliography.to_csljson),
    "ris": Format("papers.ris", "ris", "application/x-research-info-systems",
                  bibliography.to_ris),
}


def render(fmt: str, papers: List[Dict[str, object]]) -> bytes:
    body = FORMATS[fmt].render(papers)
    return body if isinstance(body, bytes) else body.encode("utf-8")
