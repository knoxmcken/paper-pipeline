"""PDF download with polite rate limiting, retries and integrity hashing."""

from __future__ import annotations

import hashlib
import time
from pathlib import Path
from typing import Dict, Optional

import requests

from . import config

PDF_MAGIC = b"%PDF-"


class FetchError(RuntimeError):
    """Raised when a PDF cannot be downloaded or is not actually a PDF."""


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(chunk), b""):
            digest.update(block)
    return digest.hexdigest()


def pdf_name(arxiv_id: str) -> str:
    return f"{arxiv_id.replace('/', '_')}.pdf"


def download_pdf(
    arxiv_id: str,
    dest_dir: Path,
    session: Optional[requests.Session] = None,
    url: Optional[str] = None,
    delay: float = config.DEFAULT_DELAY,
    force: bool = False,
    attempts: int = 3,
) -> Dict[str, object]:
    """Download one PDF from ``url``.

    Never guesses a URL: a paper whose source gave no open-access PDF link is
    skipped by the caller rather than fetched from an invented address (which is
    how ``arxiv.org/pdf/doi:10.1109/...`` used to happen for DOI-keyed works).

    Always verifies the ``%PDF-`` magic bytes: arXiv (and many portals) answer
    bad/blocked paths with an HTML page under HTTP 200, so status alone is not
    proof of a PDF.
    """
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / pdf_name(arxiv_id)
    if dest.exists() and dest.stat().st_size > 0 and not force:
        return {
            "path": str(dest),
            "sha256": sha256_file(dest),
            "bytes": dest.stat().st_size,
            "skipped": True,
        }
    if not url:
        raise FetchError(f"no PDF url for {arxiv_id}")

    target = url
    sess = session or requests.Session()
    sess.headers.setdefault("User-Agent", config.USER_AGENT)
    last: Optional[Exception] = None

    for attempt in range(attempts):
        try:
            resp = sess.get(target, timeout=60, allow_redirects=True)
            if resp.status_code != 200:
                raise FetchError(f"HTTP {resp.status_code} for {target}")
            body = resp.content
            if not body.startswith(PDF_MAGIC):
                raise FetchError(
                    f"{target} did not return a PDF (magic={body[:8]!r}, {len(body)} bytes)"
                )
            tmp = dest.with_suffix(".pdf.part")
            tmp.write_bytes(body)
            tmp.replace(dest)
            time.sleep(delay)
            return {
                "path": str(dest),
                "sha256": sha256_file(dest),
                "bytes": dest.stat().st_size,
                "skipped": False,
            }
        except (requests.RequestException, FetchError) as exc:
            last = exc
            if attempt < attempts - 1:
                time.sleep(3 * (attempt + 1))
    raise FetchError(f"download failed for {arxiv_id}: {last}")
