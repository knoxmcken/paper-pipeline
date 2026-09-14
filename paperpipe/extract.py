"""Text and outline extraction from downloaded PDFs.

Preferred backend is ``pdftotext`` (poppler); falls back to ``mutool draw``.
Both are already present on most Linux boxes and avoid a wheel dependency.
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
    return {
        "text_path": str(out),
        "text_chars": len(text),
        "page_count": page_count(text),
        "headings": extract_headings(text),
    }
