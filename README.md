# paper-pipeline

A small, dependency-light pipeline for turning a research topic into a queryable local
corpus of papers.

```
arXiv search  ->  PDFs + metadata  ->  text extraction  ->  SQLite (master)  ->  JSON index + markdown/CSV exports
```

## Why

Reading lists rot. This keeps the intermediate artefacts you actually need — metadata,
PDFs, extracted text, a derived index — in one reproducible place, so you can re-run,
diff and query instead of re-googling.

## Install

```bash
git clone https://github.com/knoxmcken/paper-pipeline.git
cd paper-pipeline
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
```

Runtime needs `requests`; text extraction shells out to `pdftotext` (poppler-utils)
and falls back to `mutool` (mupdf-tools). Nothing else.

## Use

```bash
# one-shot: search, download, extract, index, export
paperpipe run -q "retrieval augmented generation" -n 25 --category cs.CL

# when the search API is rate-limiting you, harvest the newest announcements instead
paperpipe run -q "retrieval" --source rss --category cs.CL,cs.LG -n 10

# or search across publishers with open-access PDF links
paperpipe run -q "LLM agents in cybersecurity" --source openalex -n 30

# step by step
paperpipe search -q "graph neural networks" -n 10     # query only, stores nothing
paperpipe fetch  -q "graph neural networks" -n 10     # metadata + PDFs into data/
paperpipe extract                                       # everything missing text
paperpipe index                                         # regenerate data/index.json
paperpipe export --format all                           # data/exports/papers.{md,csv}

# inspect
paperpipe stats
paperpipe show "attention"
paperpipe index --check                                 # is index.json in sync with the DB?
```

## Web UI

```bash
pip install -e ".[web]"
paperpipe serve                # http://127.0.0.1:8000
```

Browse and search the stored corpus, view a paper's abstract/headings/artifact status,
and trigger `fetch` / `extract` / `index` / `export` as background jobs with live log
output. Each action shells out to the same `paperpipe` CLI commands, so behaviour never
drifts from the command line; `--data-dir`/`PAPERPIPE_DATA` apply the same way.

Data layout (git-ignored):

```
data/
├── papers.db        # source of truth (SQLite)
├── index.json       # derived navigation guide - regenerable, never authoritative
├── pdfs/<id>.pdf
├── text/<id>.txt
└── exports/papers.md, papers.csv
```

Point it elsewhere with `--data-dir` or `PAPERPIPE_DATA`.

## Design notes

- **The DB is the master.** `index.json` and the exports are derived and can be deleted
  and regenerated at any time (`paperpipe index`, `paperpipe export`).
- **Rate limiting is not optional.** arXiv asks for ≥3s between requests; `--delay`
  controls it and both the API and PDF downloads respect it.
- **HTTP 200 is not proof of a PDF.** Every download is checked for the `%PDF-` magic
  bytes before it is stored — misconfigured mirrors and blocked paths happily return
  HTML with a 200.
- **Downloads are atomic.** Bodies land in `<id>.pdf.part` and are renamed on success, so
  a killed run never leaves a truncated PDF that later looks cached.
- **Re-fetching is safe.** `upsert_papers` merges on `arxiv_id` and `COALESCE`s the
  artifact columns, so richer metadata will not wipe already-downloaded file paths.
- **Every run is recorded** in the `runs` table (kind, args, timing, outcome).

## Discovery sources

Three, selected with `--source`:

| Source | Endpoint | Semantics |
|---|---|---|
| `api` (default) | `export.arxiv.org/api/query` | real search: relevance/date ranking, huge recall, field queries |
| `rss` | `rss.arxiv.org/rss/<category>` | newest announcement batch per category; `-q` filters client-side |
| `openalex` | `api.openalex.org/works` | topical scholarly search across all publishers, with open-access PDF links |

Fallbacks exist because arXiv throttles by IP, and shared/cloud egress addresses get hit
hard (`429 Rate exceeded.`, and sometimes read timeouts). PDF fetches come from
`arxiv.org/pdf/...`, a different service that keeps working while the API is throttled, so
an already-known paper list still downloads fine. `openalex` is the source that stays up
when both arXiv search routes fail.

Each paper records its `source` column, so a corpus can mix routes and still be auditable.
The paper key (`arxiv_id` in the schema) is the arXiv id when the work has an arXiv
location, otherwise `doi:<doi>`, otherwise the OpenAlex work id.

## Query syntax

`-q` takes plain words (auto-quoted as an `all:` search) or an arXiv field query:

```bash
paperpipe run -q 'au:Hinton' -n 20
paperpipe run -q 'ti:"chain of thought"' -n 20
paperpipe run -q 'abs:transformer' -n 20 --sort date
```

With `--source rss` there is no server-side search, so `-q` is a plain keyword
filter: every whitespace-separated term must appear in the title, abstract or
author list. Pass `-q ""` to keep the whole batch.

## Tests

```bash
pytest -q
```

Unit tests run fully offline against canned Atom fixtures and a temp SQLite file; the
only test that touches real PDF tooling builds its own fixture and skips if the tools
are absent.

## Roadmap

- SQLite FTS5 over extracted text (currently `LIKE` on title/abstract/authors)
- DOI/Crossref and Semantic Scholar enrichment for published versions
- Full TOC from the PDF outline (`mutool show outline`) where one exists
- Optional embeddings + duplicate detection

## License

MIT
