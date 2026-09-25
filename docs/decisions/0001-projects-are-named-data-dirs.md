# 0001: Projects are named data dirs

- **Status:** accepted
- **Date:** 2026-09-25
- **Issue:** #12

## Context

Every corpus lives in one data dir (`--data-dir` / `PAPERPIPE_DATA`) holding a
single-writer SQLite `papers.db` plus its PDFs, extracted text, exports and cache.
#12 asks for named, resumable projects, collections within a project, and per-paper
status and notes. The open question was how to store projects:

1. **Named data dirs.** A project is an ordinary data dir with a name, listed in a
   small registry.
2. **One shared database with a `project_id` column** on papers (and collections).

## Decision

Option 1. A JSON registry (`$PAPERPIPE_PROJECTS`, default
`~/.config/paperpipe/projects.json`) maps names to data dirs and remembers which
one is in use. `--project NAME` and `paperpipe projects use NAME` pick one;
`--data-dir` still wins when given. Collections, status and notes live inside each
project's own `papers.db`.

## Why

- **No storage-model change.** Every query, the FTS index, `index.json`, exports,
  `reconcile`, `duplicates` and the Cloud Run GCS-FUSE volume keep working on one
  file per corpus. Option 2 would touch all of them and need a migration.
- **Existing corpora become projects for free** by registering their path; nothing
  is moved or rewritten.
- **Isolation is physical.** Deleting, backing up or sharing a project is copying a
  directory, and a bad run cannot leak into another project.
- **It fits the single-writer SQLite model** already documented for Cloud Run.

## Consequences

- A paper in two projects is stored twice (PDF included). Acceptable for a personal
  tool; `paperpipe duplicates` works within a project, not across them.
- There is no cross-project search. If that is ever needed, SQLite's `ATTACH` can
  query several project databases without changing this decision.
- The registry is per machine. The deployed web UI serves exactly one data dir, as
  before; a project switcher there is out of scope (#12) until that is revisited.
- Collections are many-to-many (a paper can sit in several, as in Zotero). Status is
  a fixed list (`new`, `reading`, `shortlisted`, `cited`, `discarded`; NULL reads as
  `new`) so filters and exports stay reliable.
