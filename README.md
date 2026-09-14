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
paperpipe show --fulltext "prompt injection"            # ranked phrase search over extracted text, with a snippet + page
paperpipe index --check                                 # is index.json in sync with the DB?

# repair drift between the stored corpus and its sources
paperpipe reconcile                                     # dry run: lists dead/paywalled links, missing files, key collisions
paperpipe reconcile --fix                                # re-resolves stale links (preferring the arXiv copy) and re-downloads
```

## Web UI

```bash
pip install -e ".[web]"
paperpipe serve                # http://127.0.0.1:8000
```

Browse and search the stored corpus, run a ranked full-text phrase search over extracted
text, view a paper's abstract/headings/artifact status, and trigger `fetch` / `extract` /
`index` / `export` / `reconcile` as background jobs with live log output. Each action
shells out to the same `paperpipe` CLI commands, so behaviour never drifts from the
command line; `--data-dir`/`PAPERPIPE_DATA` apply the same way.

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

## Deploying to Cloud Run

A `Dockerfile` builds the web UI into a container (`paperpipe serve --host 0.0.0.0
--port $PORT`, reading `PAPERPIPE_DATA` for the data dir, defaulting to `/data`).

Cloud Run's local filesystem is ephemeral, so `/data` is backed by a GCS bucket mounted
as a Cloud Run volume (gen2 execution environment). `deploy/cloudrun-deploy.sh` sets
this all up: an Artifact Registry repo, the data bucket, IAM so the runtime service
account can read/write it, a Cloud Build image build, and the `gcloud run deploy`.

```bash
PROJECT_ID=personal-tools-isotopes55 \
REGION=us-central1 \
SERVICE_ACCOUNT=paper-pipeline-deploy@personal-tools-isotopes55.iam.gserviceaccount.com \
./deploy/cloudrun-deploy.sh
```

Notes:

- **Single writer.** SQLite plus GCS FUSE doesn't give real cross-instance file
  locking, so the service deploys with `--max-instances=1`. Fine for personal/low-traffic
  use; not a scale-out setup.
- **Private by default.** The service deploys with `--no-allow-unauthenticated` since
  the action endpoints can trigger pipeline runs against your corpus. The script prints
  the commands to grant yourself `roles/run.invoker`, or to open it up with `allUsers`
  if you actually want a public instance.
- **First deploy starts with an empty `/data`.** Seed it by copying an existing corpus
  into the bucket (`gcloud storage cp -r data/* gs://<bucket>/`) before or after the
  first deploy, or just run `fetch`/`index` from the deployed UI.

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

- DOI/Crossref and Semantic Scholar enrichment for published versions
- Full TOC from the PDF outline (`mutool show outline`) where one exists
- Optional embeddings + duplicate detection

## License

MIT
