# paper-pipeline

[![CI](https://github.com/knoxmcken/paper-pipeline/actions/workflows/ci.yml/badge.svg)](https://github.com/knoxmcken/paper-pipeline/actions/workflows/ci.yml)

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

Section headings come from the PDF's own outline (`mutool show <pdf> outline`) when
`mutool` is installed and the PDF has one, and are otherwise guessed from numbered
lines in the extracted text. Each paper's `headings_method` (`outline` or `regex`)
records which, so you know how far to trust them; nested outline entries are indented
two spaces per level.

## Use

```bash
# one-shot: search, download, extract, index, export
paperpipe run -q "retrieval augmented generation" -n 25 --category cs.CL

# when the search API is rate-limiting you, harvest the newest announcements instead
paperpipe run -q "retrieval" --source rss --category cs.CL,cs.LG -n 10

# or search across publishers with open-access PDF links
paperpipe run -q "LLM agents in cybersecurity" --source openalex -n 30

# build one corpus from several seed queries in a single pass; -n caps the
# TOTAL unique papers across the whole batch, not each query, and a paper
# returned by more than one seed collapses to a single row
paperpipe run -q "llm agents" -q "tool use" -q "retrieval augmented generation" -n 60

# or keep the seeds in a file, one query per line (# comments, blank lines ignored)
paperpipe run --queries-file seeds.txt -n 60

# step by step
paperpipe search -q "graph neural networks" -n 10     # query only, stores nothing
paperpipe fetch  -q "graph neural networks" -n 10     # metadata + PDFs into data/
paperpipe extract                                       # everything missing text
paperpipe index                                         # regenerate data/index.json
paperpipe export --format all                           # every format below into data/exports/
paperpipe export --format bibtex --out ~/refs           # one format, somewhere else

# fill in PDFs for papers already stored in the DB (not just what the last search returned)
paperpipe download                                      # every stored paper missing a PDF, using its stored url
paperpipe download --id 2401.00001 --url https://arxiv.org/pdf/2401.00001v2  # pin one paper to an explicit url
paperpipe download --force --id 2401.00001              # re-download even if a PDF is already on disk

# inspect
paperpipe stats
paperpipe show "attention"
paperpipe show --fulltext "prompt injection"            # ranked phrase search over extracted text, with a snippet + page
paperpipe index --check                                 # is index.json in sync with the DB?

# repair drift between the stored corpus and its sources
paperpipe reconcile                                     # dry run: lists dead/paywalled links, missing files, key collisions
paperpipe reconcile --fix                                # re-resolves stale links (preferring the arXiv copy) and re-downloads

# the same work stored twice (arXiv id vs doi:..., preprint vs published version)
paperpipe duplicates                                    # lists candidate pairs and the evidence; changes nothing
paperpipe duplicates --merge 2401.00001 doi:10.1000/x   # fold the second into the first, delete the second
```

`duplicates` pairs papers that share a DOI, share an arXiv id (as a key, an arXiv
`10.48550/arXiv.*` DOI or an arxiv.org link), or have the same normalised title and
first author within a year of each other (preprint and journal versions usually carry
different DOIs); very similar titles are reported as `near_title`. Merging is never
automatic: `--merge KEEP DROP` keeps KEEP's values, fills its gaps from DROP (PDF and
extracted text move as a unit), deletes DROP's row, and leaves files on disk untouched.

### Projects, collections and reading status

A **project** is a named data dir, so you can come back to a corpus by name instead of
remembering its path ([why](docs/decisions/0001-projects-are-named-data-dirs.md)):

```bash
paperpipe projects add thesis --description "PhD lit review" --use   # data dir: ./projects/thesis
paperpipe projects add old-corpus ~/corpora/agents                  # register an existing data dir
paperpipe projects list                                             # * marks the one in use
paperpipe projects use old-corpus                                   # later commands use it (alias: open)
paperpipe --project thesis stats                                    # or pick one per command
paperpipe projects remove old-corpus                                # unregisters; never deletes data
```

The data dir is chosen from, in order: `--data-dir`, `--project`, `$PAPERPIPE_DATA`,
the project in use, then `./data`. The registry lives at
`~/.config/paperpipe/projects.json` (override with `$PAPERPIPE_PROJECTS`).

Within a project, **collections** group papers the way Zotero collections do (a paper
can be in several), and every paper has a **status** and free-text **notes**:

```bash
paperpipe collection add shortlist 2401.00001 2401.00002 --description "first pass"
paperpipe collection list
paperpipe collection show shortlist
paperpipe collection remove shortlist 2401.00002    # the paper stays in the corpus
paperpipe collection delete shortlist               # likewise for all its papers

paperpipe status reading 2401.00001                 # new | reading | shortlisted | cited | discarded
paperpipe notes 2401.00001 "check the ablation in section 4"
paperpipe notes 2401.00001                          # print them; --clear to remove
paperpipe show --status shortlisted

paperpipe export --collection shortlist --format bibtex   # any export format, one collection
```

Status and notes are never overwritten by a re-fetch, show up in the markdown/CSV/Excel
exports, and can also be edited from the web UI's paper detail pane. The web UI can
filter the list (and so the Export download) to one collection. `duplicates --merge`
keeps both papers' notes and all of their collections.

### The `download` stage

`fetch`/`run` only download whatever discovery returns *this* time. On a shifting
relevance ranking, or after a host-level failure partway through a run, that can
quietly leave stored papers without a PDF. `download` works from the database
instead of a fresh search, so it can complete or repair a corpus:

```bash
paperpipe download                       # every stored paper missing a PDF, via its stored url
paperpipe download --id 2401.00001       # just one paper, by its stored key
paperpipe download --id 2401.00001 --url https://arxiv.org/pdf/2401.00001v2
                                          # override the stored url (needs exactly one --id);
                                          # use this to pin a specific version or route around
                                          # a dead/paywalled link that reconcile flagged
paperpipe download --force               # re-fetch even where a PDF already exists on disk
```

Reach for it after: a partial `fetch` (host died, network dropped mid-run), a
`reconcile` report that names a `dead_link`/`missing_file` you'd rather fix by hand than
auto-repair, or when you already know a better URL (a newer arXiv version, a mirror)
than whatever discovery stored.

## Web UI

```bash
pip install -e ".[web]"
paperpipe serve                # http://127.0.0.1:8000
```

Browse and search the stored corpus, run a ranked full-text phrase search over extracted
text, view a paper's abstract/headings/artifact status, and trigger `fetch` / `extract` /
`index` / `reconcile` as background jobs with live log output. Each action
shells out to the same `paperpipe` CLI commands, so behaviour never drifts from the
command line; `--data-dir`/`PAPERPIPE_DATA` apply the same way.

**Export listed papers** downloads the papers matching the last search you ran (or the
whole corpus when there is none) as `paper-export.<ext>`, in any `export` format: CSV,
Markdown, BibLaTeX, Excel, BibTeX, CSL-JSON or RIS. It includes every match, not just the
first 100 rows the table shows, and writes nothing on the server. The same file is
available directly from `GET /api/export/download?format=<format>&q=<search>`.

Data layout (git-ignored):

```
data/
├── papers.db        # source of truth (SQLite)
├── index.json       # derived navigation guide - regenerable, never authoritative
├── pdfs/<id>.pdf
├── text/<id>.txt
└── exports/papers.{md,csv,xlsx,bib,biblatex.bib,csl.json,ris}
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

Five, selected with `--source`:

| Source | Endpoint | Semantics |
|---|---|---|
| `api` (default) | `export.arxiv.org/api/query` | real search: relevance/date ranking, huge recall, field queries |
| `rss` | `rss.arxiv.org/rss/<category>` | newest announcement batch per category; `-q` filters client-side |
| `openalex` | `api.openalex.org/works` | topical scholarly search across all publishers, with open-access PDF links |
| `crossref` | `api.crossref.org/works` | DOI/venue metadata for publisher-registered work; no OA PDF links of its own |
| `semanticscholar` | `api.semanticscholar.org/graph/v1/paper/search` | metadata plus citation counts, a ranking signal the others don't carry |

Fallbacks exist because arXiv throttles by IP, and shared/cloud egress addresses get hit
hard (`429 Rate exceeded.`, and sometimes read timeouts). PDF fetches come from
`arxiv.org/pdf/...`, a different service that keeps working while the API is throttled, so
an already-known paper list still downloads fine. `openalex`, `crossref` and
`semanticscholar` stay up when both arXiv search routes fail; `crossref` and
`semanticscholar` are also the way to catalogue paywalled work (Elsevier, IEEE, MDPI,
Springer, ...) whose PDF can't be downloaded at all - those become metadata-only rows,
marked as such in `paperpipe show`/exports.

Each source normalises to the same paper dict and records its `source` column, so a
corpus can mix routes and still be auditable. The paper key (`arxiv_id` in the schema) is
the arXiv id when the work has an arXiv location, otherwise `doi:<doi>`, otherwise a
source-specific id (the OpenAlex work id, the Semantic Scholar paper id, or the item's
plain URL for Crossref).

### Caching and rate limiting

Every `fetch`/`run` shares one on-disk response cache and one rate limiter across
every request in that run, whatever source(s) issued them:

- **Cache.** Identical requests (same source, url and params) within `--cache-ttl`
  seconds (default 3600) are replayed from `<data-dir>/cache/` with no network call at
  all - handy for a re-run seed query, an overlapping batch seed, or an interrupted
  harvest picked back up. `--no-cache` bypasses both reads and writes for a run. The
  summary line reports `cache: N hit(s), M miss(es)` so a cache hit is never a
  mystery. Only successful (`200`) responses are cached, so a transient error is
  never replayed for the TTL's duration. `search` never writes to the cache, since it
  stores nothing else on disk either.
- **Rate limiter.** `--delay` now also sets a limiter shared by every source hit in
  the run (not just each source's own internal pacing), so a multi-seed batch (see
  `--queries-file` above) can't burst past the configured rate just because several
  queries ran back-to-back. Each source's own 429/backoff handling is unchanged; the
  limiter only paces the *first* attempt of each request, not error retries.

### Filling gaps with Unpaywall

Every `fetch`/`run` also runs an Unpaywall lookup (`api.unpaywall.org`) for any
discovered paper that has a DOI but no open-access PDF link from its own source -
useful after a `crossref` search, which never returns one itself. It is a last resort,
not a preferred source: a `pdf_url` already known from arXiv/OpenAlex/Crossref/Semantic
Scholar is never replaced. Skip it with `--no-unpaywall`. `--mailto` sets the contact
address sent to OpenAlex, Crossref and Unpaywall's polite pools (falls back to
`PAPERPIPE_MAILTO`/the repo's contact address).

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

## Cookbook: building the "AI agents in cybersecurity" corpus

The 25-paper corpus checked in during early development was built with the exact
commands below - a useful copy-pasteable recipe for a topic-focused corpus grown from
multiple seed queries, with a manual pin for one stale link.

```bash
# 1. harvest from several angles in one batch; -n caps the TOTAL unique corpus size,
#    not each query, and an overlap between seeds collapses to one row
paperpipe run \
  -q "AI agents in cybersecurity" \
  -q "LLM agents security" \
  -q "autonomous agents penetration testing" \
  --source openalex --arxiv-only -n 25

# 2. spot-check for drift right away, rather than discovering it later
paperpipe reconcile

# 3. one entry came back with a dead publisher redirect; reconcile named it, so pin
#    the known-good arXiv copy by hand instead of trusting an automatic re-resolve
paperpipe download --id 2401.00001 --url https://arxiv.org/pdf/2401.00001v2 --force

# 4. everything is in the DB now; regenerate the derived artefacts
paperpipe extract
paperpipe index
paperpipe export --format all

# 5. sanity check the final shape
paperpipe stats
```

## Importing into Zotero

`paperpipe export` writes reference-manager formats alongside the digests, one entry per
paper with title, authors, year, DOI, arXiv id and URL (plus the abstract):

| `--format` | file | notes |
|---|---|---|
| `csljson` | `papers.csl.json` | best fidelity in Zotero; arXiv-only papers import as preprints |
| `bibtex` | `papers.bib` | `@misc` with `eprint`/`archiveprefix` for preprints, `@article` when a journal ref is known |
| `biblatex` | `papers.biblatex.bib` | `@online` with `eprinttype = {arxiv}` for preprints, `@article` with `journaltitle`, ISO `date` |
| `ris` | `papers.ris` | for tools that prefer RIS |

`csv` and `xlsx` are flat tables, one row per paper. `arxiv_id` is the stored key (which
may be `doi:...`); `year`, `doi`, `arxiv` (the real arXiv id, blank if none) and `url` are
the normalised identifiers.

Filenames are fixed and entries are sorted by citation key (`smith2024attention`), so
re-exporting overwrites the same files with a stable diff. `--out DIR` writes them
somewhere other than `data/exports/`.

In Zotero: select (or create) a collection, then **File → Import…**, choose the file,
and tick "Place imports into new collection" if you want each export kept separate.
Re-importing creates duplicates; Zotero's *Duplicate Items* view merges them. Papers
stored under a DOI key (from OpenAlex/Crossref/Semantic Scholar) carry the DOI but no
arXiv id.

## Tests

```bash
pytest -q
```

Unit tests run fully offline against canned Atom fixtures and a temp SQLite file; the
only test that touches real PDF tooling builds its own fixture and skips if the tools
are absent.

## Roadmap

- Optional embeddings

## License

MIT
