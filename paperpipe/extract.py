"""Text and outline extraction from downloaded PDFs.

Preferred backend is ``pdftotext`` (poppler); falls back to ``mutool draw``.
Both are already present on most Linux boxes and avoid a wheel dependency.

Headings come from the PDF's own outline (``mutool show <pdf> outline``) when
mutool is installed and the PDF carries one; otherwise they are regex-guessed from
the extracted text. ``headings_method`` records which ("outline" or "regex").
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Dict, List, Optional

# "1 Introduction", "2.3.1 Method", "IV. Results", "Section 4 Evaluation"
HEADING_RE = re.compile(
    r"^\s*(?:Section\s+)?(?:\d{1,2}(?:\.\d{1,2})*|[IVXLC]{1,5})[.)]?\s+"
    r"([A-Z][A-Za-z0-9 ,:'\-\(\)/&]{2,60})\s*$"
)


# One ``mutool show outline`` line: an open/closed marker, one tab per depth level,
# the quoted title, then a tab and the destination.
OUTLINE_LINE_RE = re.compile(r'^\S?(\t+)"((?:[^"\\]|\\.)*)"')


class ExtractError(RuntimeError):
    """Raised when no extraction backend can read the PDF."""


def _which(*names: str) -> Optional[str]:
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    return None


def available_backend() -> Optional[str]:
    if _which("pdftotext"):
        return "pdftotext"
    if _which("mutool"):
        return "mutool"
    return None


def pdf_to_text(pdf: Path, backend: Optional[str] = None) -> str:
    """Return the plain text of ``pdf`` (``\\f`` separates pages)."""
    pdf = Path(pdf)
    backend = backend or available_backend()
    if backend is None:
        raise ExtractError("no PDF text backend found (need pdftotext or mutool)")
    if backend == "pdftotext":
        cmd: List[str] = [_which("pdftotext"), str(pdf), "-"]
    else:
        cmd = [_which("mutool"), "draw", "-F", "txt", str(pdf)]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=600)
    except subprocess.TimeoutExpired as exc:
        raise ExtractError(f"extraction timed out for {pdf}") from exc
    if proc.returncode != 0:
        raise ExtractError(
            f"{backend} failed for {pdf}: {proc.stderr.decode('utf-8', 'replace')[:200]}"
        )
    return proc.stdout.decode("utf-8", "replace")


def page_count(text: str) -> int:
    """``pdftotext`` and ``mutool`` both emit a form feed per page."""
    if not text:
        return 0
    return text.count("\f") + (0 if text.endswith("\f") else 1)


def _unescape(title: str) -> str:
    return re.sub(r"\\(.)", lambda m: " " if m.group(1) in "nrt" else m.group(1), title)


def parse_outline(output: str, limit: int = 60) -> List[str]:
    """Titles from ``mutool show outline`` output, nested entries indented two spaces per level."""
    headings: List[str] = []
    for line in output.splitlines():
        match = OUTLINE_LINE_RE.match(line)
        if not match:
            continue
        title = re.sub(r"\s+", " ", _unescape(match.group(2))).strip()
        if title:
            headings.append("  " * (len(match.group(1)) - 1) + title)
        if len(headings) >= limit:
            break
    return headings


def pdf_outline(pdf: Path, limit: int = 60) -> List[str]:
    """The PDF's embedded outline, or ``[]`` if it has none or mutool is unavailable."""
    mutool = _which("mutool")
    if mutool is None:
        return []
    try:
        proc = subprocess.run(
            [mutool, "show", str(pdf), "outline"], capture_output=True, timeout=120
        )
    except subprocess.TimeoutExpired:
        return []
    if proc.returncode != 0:
        return []
    return parse_outline(proc.stdout.decode("utf-8", "replace"), limit=limit)


def extract_headings(text: str, limit: int = 60) -> List[str]:
    """Best-effort section headings for papers without a PDF outline."""
    seen: List[str] = []
    for raw in text.splitlines()[:4000]:
        line = raw.strip()
        if not line or len(line) > 80:
            continue
        match = HEADING_RE.match(line)
        if match:
            heading = re.sub(r"\s+", " ", line).strip()
            if heading not in seen:
                seen.append(heading)
        if len(seen) >= limit:
            break
    return seen


def extract(pdf: Path, text_dir: Path, backend: Optional[str] = None) -> Dict[str, object]:
    """Extract text for one PDF and persist it next to the database."""
    pdf = Path(pdf)
    text_dir = Path(text_dir)
    text_dir.mkdir(parents=True, exist_ok=True)
    text = pdf_to_text(pdf, backend=backend)
    out = text_dir / (pdf.stem + ".txt")
    out.write_text(text, encoding="utf-8")
    headings = pdf_outline(pdf)
    method = "outline"
    if not headings:
        headings = extract_headings(text)
        method = "regex"
    return {
        "text_path": str(out),
        "text_chars": len(text),
        "page_count": page_count(text),
        "headings": headings,
        "headings_method": method,
    }
