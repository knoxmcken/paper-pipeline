"""Paths, endpoints and defaults.

Everything is overridable from the CLI; these are only fallbacks.
"""

from __future__ import annotations

import os
from pathlib import Path

DEFAULT_DATA_DIR = Path(os.environ.get("PAPERPIPE_DATA", "data"))

ARXIV_API = os.environ.get("PAPERPIPE_ARXIV_API", "https://export.arxiv.org/api/query")
ARXIV_RSS = os.environ.get("PAPERPIPE_ARXIV_RSS", "https://rss.arxiv.org/rss/{category}")
OPENALEX_API = os.environ.get("PAPERPIPE_OPENALEX_API", "https://api.openalex.org/works")

# OpenAlex asks for a contact address so it can put you in the polite pool.
MAILTO = os.environ.get("PAPERPIPE_MAILTO", "knoxmcken@gmail.com")
ARXIV_PDF = "https://arxiv.org/pdf/{arxiv_id}"
ARXIV_ABS = "https://arxiv.org/abs/{arxiv_id}"

# arXiv asks for a descriptive UA with a contact/URL.
USER_AGENT = os.environ.get(
    "PAPERPIPE_UA",
    "paperpipe/0.1 (+https://github.com/knoxmcken/paper-pipeline)",
)

# arXiv API terms: keep >=3s between requests of the same kind.
DEFAULT_DELAY = 3.0
DEFAULT_MAX = 50


def db_path(data_dir: Path) -> Path:
    return Path(data_dir) / "papers.db"


def pdfs_dir(data_dir: Path) -> Path:
    return Path(data_dir) / "pdfs"


def text_dir(data_dir: Path) -> Path:
    return Path(data_dir) / "text"


def exports_dir(data_dir: Path) -> Path:
    return Path(data_dir) / "exports"


def index_path(data_dir: Path) -> Path:
    return Path(data_dir) / "index.json"


def ensure_dirs(data_dir: Path) -> None:
    for d in (data_dir, pdfs_dir(data_dir), text_dir(data_dir), exports_dir(data_dir)):
        Path(d).mkdir(parents=True, exist_ok=True)
