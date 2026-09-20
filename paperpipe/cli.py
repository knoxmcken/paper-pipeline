"""paperpipe command line interface."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

import requests

from . import (
    __version__,
    arxiv,
    config,
    crossref,
    db,
    export,
    extract,
    fetch,
    index,
    openalex,
    reconcile,
    semanticscholar,
    unpaywall,
)

STAGES = ("fetch", "download", "extract", "index", "export")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _paths(args) -> Dict[str, Path]:
    data_dir = Path(args.data_dir)
    return {
        "data_dir": data_dir,
        "db": config.db_path(data_dir),
        "pdfs": config.pdfs_dir(data_dir),
        "text": config.text_dir(data_dir),
        "exports": config.exports_dir(data_dir),
        "index": config.index_path(data_dir),
    }


def _open(args):
    paths = _paths(args)
    config.ensure_dirs(paths["data_dir"])
    conn = db.connect(paths["db"])
    db.init_db(conn)
    return conn, paths


def _session() -> requests.Session:
    sess = requests.Session()
    sess.headers["User-Agent"] = config.USER_AGENT
    return sess


def _resolve_queries(args) -> List[str]:
    """Collect the query list for a discovery run from ``--query``/``--queries-file``.

    ``--query`` is repeatable, and a ``--queries-file`` (one query per line, ``#``
    comments and blank lines ignored) can supply additional seeds or replace the
    flag entirely. Order is preserved and duplicates are kept (a repeated seed just
    re-runs discovery, which is harmless since results dedupe by paper key).
    """
    queries: List[str] = list(getattr(args, "query", None) or [])
    queries_file = getattr(args, "queries_file", None)
    if queries_file:
        text = Path(queries_file).read_text(encoding="utf-8")
        for line in text.splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                queries.append(line)
    if not queries:
        raise arxiv.ArxivError("need at least one --query or --queries-file")
    return queries


def _discover(args, session) -> List[Dict[str, object]]:
    """Run the selected discovery source and return normalised paper dicts."""
    source = getattr(args, "source", "api")
    if source == "rss":
        if not args.category:
            raise arxiv.ArxivError("the rss source needs --category (e.g. --category cs.CL)")
        categories = [c.strip() for c in args.category.split(",") if c.strip()]
        return arxiv.latest(
            categories,
            keyword=args.query,
            max_results=args.max,
            session=session,
            delay=args.delay,
        )
    if source == "openalex":
        return openalex.search(
            args.query,
            max_results=args.max,
            session=session,
            delay=args.delay,
            mailto=getattr(args, "mailto", None),
            arxiv_only=getattr(args, "arxiv_only", False),
            search_field=getattr(args, "search_field", "default"),
        )
    if source == "crossref":
        return crossref.search(
            args.query,
            max_results=args.max,
            session=session,
            delay=args.delay,
            mailto=getattr(args, "mailto", None),
        )
    if source == "semanticscholar":
        return semanticscholar.search(
            args.query,
            max_results=args.max,
            session=session,
            delay=args.delay,
        )
    return arxiv.search(
        args.query,
        max_results=args.max,
        sort=args.sort,
        session=session,
        delay=args.delay,
        category=args.category,
    )


def cmd_search(args) -> int:
    session = _session()
    try:
        queries = _resolve_queries(args)
    except arxiv.ArxivError as exc:
        print(f"search failed: {exc}", file=sys.stderr)
        return 1
    results: List[Dict[str, object]] = []
    seen = set()
    try:
        for q in queries:
            qargs = argparse.Namespace(**vars(args))
            qargs.query = q
            for paper in _discover(qargs, session):
                if paper["arxiv_id"] in seen:
                    continue
                seen.add(paper["arxiv_id"])
                results.append(paper)
    except (arxiv.ArxivError, openalex.OpenAlexError, crossref.CrossrefError,
            semanticscholar.SemanticScholarError) as exc:
        print(f"search failed: {exc}", file=sys.stderr)
        return 1
    for paper in results:
        authors = ", ".join((paper.get("authors") or [])[:3]) or "?"
        print(f"{paper['arxiv_id']:<16} {(paper.get('published') or '')[:10]}  {paper['title'][:70]}")
        print(f"{'':<16} {authors}  [{paper.get('primary_category')}]")
    print(f"\n{len(results)} result(s).")
    return 0


def cmd_fetch(args) -> int:
    conn, paths = _open(args)
    run_id = db.start_run(conn, "fetch", json.dumps(vars(args), default=str), _now())
    session = _session()
    try:
        queries = _resolve_queries(args)
        corpus_max = args.max
        papers: List[Dict[str, object]] = []
        seen = set()
        for q in queries:
            if corpus_max is not None and len(papers) >= corpus_max:
                print(f"  query {q!r}: skipped (corpus --max {corpus_max} already reached)")
                continue
            qargs = argparse.Namespace(**vars(args))
            qargs.query = q
            if corpus_max is not None:
                qargs.max = corpus_max - len(papers)
            try:
                found = _discover(qargs, session)
            except (arxiv.ArxivError, openalex.OpenAlexError, crossref.CrossrefError,
                    semanticscholar.SemanticScholarError) as exc:
                print(f"  query {q!r}: FAILED ({exc})", file=sys.stderr)
                continue
            new = 0
            for paper in found:
                if paper["arxiv_id"] in seen:
                    continue
                seen.add(paper["arxiv_id"])
                papers.append(paper)
                new += 1
                if corpus_max is not None and len(papers) >= corpus_max:
                    break
            dropped = len(found) - new
            suffix = f" ({dropped} duplicate key(s) dropped)" if dropped else ""
            print(f"  query {q!r}: {len(found)} result(s), {new} new{suffix}")
        print(f"{args.source}: {len(papers)} unique paper(s) across {len(queries)} query/queries")
        if not getattr(args, "no_unpaywall", False):
            filled = unpaywall.enrich_missing_pdfs(
                papers, session=session, mailto=getattr(args, "mailto", None), delay=args.delay
            )
            if filled:
                print(f"  unpaywall: resolved {filled} open-access pdf url(s)")
        for paper in papers:
            paper["fetched_at"] = _now()
        db.upsert_papers(conn, papers)

        downloaded = failed = unlinked = 0
        if not args.no_download:
            for paper in papers:
                url = paper.get("pdf_url")
                if not url:
                    unlinked += 1
                    print(f"  skip {paper['arxiv_id']}: source listed no open-access PDF")
                    continue
                try:
                    info = fetch.download_pdf(
                        paper["arxiv_id"],
                        paths["pdfs"],
                        session=session,
                        url=url,
                        delay=args.delay,
                        force=args.force,
                    )
                    db.update_pdf(conn, paper["arxiv_id"], info)
                    downloaded += 1
                    print(f"  pdf  {paper['arxiv_id']}  {info['bytes']:,} B")
                except fetch.FetchError as exc:
                    failed += 1
                    print(f"  FAIL {paper['arxiv_id']}: {exc}", file=sys.stderr)
        message = f"{len(papers)} metadata, {downloaded} pdf, {unlinked} no-url, {failed} failed"
        db.finish_run(conn, run_id, True, message, _now())
        print(message)
        return 0
    except (arxiv.ArxivError, openalex.OpenAlexError, crossref.CrossrefError,
            semanticscholar.SemanticScholarError) as exc:  # noqa: BLE001
        db.finish_run(conn, run_id, False, str(exc), _now())
        print(f"fetch failed: {exc}", file=sys.stderr)
        return 1
    finally:
        conn.close()


def cmd_download(args) -> int:
    """Fill in PDFs for papers already stored in the database.

    Re-running ``fetch`` only covers whatever discovery returns *this* time, which
    on a shifting relevance ranking can quietly leave stored papers without files.
    This stage works from the database instead, so a corpus can be completed or
    repaired after a host-level download failure.
    """
    conn, paths = _open(args)
    session = _session()
    if args.id:
        rows = [row for row in (db.get_paper(conn, paper_id) for paper_id in args.id) if row]
    else:
        rows = db.papers_missing(conn, "pdf")
    if args.url and len(rows) != 1:
        print("--url needs exactly one --id", file=sys.stderr)
        conn.close()
        return 2

    done = failed = skipped = 0
    for row in rows:
        url = args.url or row.get("pdf_url")
        if not url:
            skipped += 1
            print(f"  skip {row['arxiv_id']}: no stored PDF url")
            continue
        try:
            info = fetch.download_pdf(
                row["arxiv_id"],
                paths["pdfs"],
                session=session,
                url=url,
                delay=args.delay,
                force=args.force,
            )
            db.update_pdf(conn, row["arxiv_id"], info)
            done += 1
            print(f"  pdf  {row['arxiv_id']}  {info['bytes']:,} B")
        except fetch.FetchError as exc:
            failed += 1
            print(f"  FAIL {row['arxiv_id']}: {exc}", file=sys.stderr)
    print(f"downloaded {done}, skipped {skipped}, failed {failed}")
    conn.close()
    return 0 if failed == 0 else 1


def cmd_extract(args) -> int:
    conn, paths = _open(args)
    if args.id:
        rows = [db.get_paper(conn, paper_id) for paper_id in args.id]
        rows = [r for r in rows if r]
    else:
        rows = db.papers_missing(conn, "text")
    backend = extract.available_backend()
    if backend is None:
        print("no PDF text backend (need pdftotext or mutool)", file=sys.stderr)
        return 1
    done = failed = 0
    for row in rows:
        pdf = row.get("pdf_path")
        if not pdf or not Path(pdf).exists():
            print(f"  skip {row['arxiv_id']}: no local PDF", file=sys.stderr)
            failed += 1
            continue
        try:
            info = extract.extract(Path(pdf), paths["text"], backend=backend)
            db.update_extraction(conn, row["arxiv_id"], info, _now())
            db.index_fulltext(conn, row["arxiv_id"], info["text_path"])
            done += 1
            print(f"  text {row['arxiv_id']}  {info['page_count']}p  {info['text_chars']:,} chars")
        except extract.ExtractError as exc:
            failed += 1
            print(f"  FAIL {row['arxiv_id']}: {exc}", file=sys.stderr)
    print(f"extracted {done}, failed {failed} (backend={backend})")
    conn.close()
    return 0 if failed == 0 else 1


def cmd_index(args) -> int:
    conn, paths = _open(args)
    if args.check:
        ok = index.check(conn, paths["index"])
        print("index is in sync" if ok else "index is STALE - run `paperpipe index`")
        conn.close()
        return 0 if ok else 1
    payload = index.build(conn, paths["index"])
    print(f"wrote {paths['index']} ({payload['count']} papers)")
    conn.close()
    return 0


def cmd_export(args) -> int:
    conn, paths = _open(args)
    written = []
    if args.format in ("md", "all"):
        path = export.write_markdown(
            conn, paths["exports"] / "papers.md", title=args.title, category=not args.flat
        )
        written.append(path)
    if args.format in ("csv", "all"):
        written.append(export.build_csv(conn, paths["exports"] / "papers.csv"))
    for path in written:
        print(f"wrote {path}")
    conn.close()
    return 0


def cmd_run(args) -> int:
    codes = []
    for stage in STAGES:
        sargs = argparse.Namespace(**vars(args))
        sargs.__dict__.setdefault("id", None)
        print(f"\n=== {stage} ===")
        if stage == "fetch":
            codes.append(cmd_fetch(sargs))
        elif stage == "download":
            continue  # handled inside fetch
        elif stage == "extract":
            codes.append(cmd_extract(sargs))
        elif stage == "index":
            sargs.check = False
            codes.append(cmd_index(sargs))
        elif stage == "export":
            if not hasattr(sargs, "flat"):
                sargs.flat = False
            sargs.title = args.title if hasattr(args, "title") else "Paper Index"
            codes.append(cmd_export(sargs))
    return 0 if all(c == 0 for c in codes) else 1


def cmd_stats(args) -> int:
    conn, _ = _open(args)
    info = db.stats(conn)
    print(
        f"papers={info['papers']}  with_pdf={info['with_pdf']}  "
        f"with_text={info['with_text']}  pages={info['pages']}"
    )
    rows = conn.execute(
        "SELECT primary_category, COUNT(*) c FROM papers GROUP BY 1 ORDER BY c DESC LIMIT 15"
    ).fetchall()
    for row in rows:
        print(f"  {row[0] or '?':<20} {row[1]}")
    conn.close()
    return 0


def cmd_serve(args) -> int:
    try:
        import uvicorn
    except ImportError:
        print("serve needs the 'web' extra: pip install -e '.[web]'", file=sys.stderr)
        return 1
    from .webapp import create_app

    app = create_app(Path(args.data_dir))
    uvicorn.run(app, host=args.host, port=args.port)
    return 0


def cmd_show(args) -> int:
    conn, _ = _open(args)
    term = args.term
    if args.fulltext:
        if not term:
            print("--fulltext needs a search phrase", file=sys.stderr)
            conn.close()
            return 2
        db.backfill_fts(conn)
        hits = db.search_fulltext(conn, term, limit=args.limit)
        for hit in hits:
            print(f"{hit['arxiv_id']:<16} p{hit['page']:<4} {hit['title'][:60]}")
            print(f"{'':<22} {hit['snippet']}")
        print(f"\n{len(hits)} hit(s)")
        conn.close()
        return 0
    rows = db.search(conn, term, limit=args.limit) if term else db.list_papers(conn, limit=args.limit)
    for row in rows:
        print(f"{row['arxiv_id']:<16} {(row.get('published') or '')[:10]}  {row['title'][:80]}")
    print(f"\n{len(rows)} paper(s)")
    conn.close()
    return 0


def cmd_reconcile(args) -> int:
    conn, paths = _open(args)
    session = _session()
    report = reconcile.reconcile(conn, paths["pdfs"], session=session, fix=args.fix, delay=args.delay)

    labels = (
        ("missing_file", "missing PDF file on disk"),
        ("dead_link", "dead or non-PDF link"),
        ("key_collision", "key collision (same DOI, different keys)"),
    )
    for key, label in labels:
        items = report[key]
        print(f"{label}: {len(items)}")
        for item in items:
            print(f"  {item}")

    if args.fix:
        print(f"fixed: {len(report['fixed'])}")
        for item in report["fixed"]:
            print(f"  {item['arxiv_id']} ({', '.join(item['kinds'])}) -> {item['url']}")
        print(f"unresolved: {len(report['unresolved'])}")
        for item in report["unresolved"]:
            print(f"  {item}")

    conn.close()
    return 0 if not args.fix or not report["unresolved"] else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="paperpipe", description="Research paper data pipeline")
    parser.add_argument("--version", action="version", version=f"paperpipe {__version__}")
    parser.add_argument("--data-dir", default=str(config.DEFAULT_DATA_DIR),
                        help="pipeline data directory (default: %(default)s)")
    sub = parser.add_subparsers(dest="command", required=True)

    # Let --data-dir appear before OR after the subcommand. SUPPRESS keeps the
    # subparser copy from clobbering a value already parsed by the main parser.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--data-dir", default=argparse.SUPPRESS)

    def add_fetch_flags(p):
        p.add_argument("-q", "--query", action="append", default=None,
                       help="search terms, an arXiv field query, or a client-side "
                            "keyword filter when --source rss; repeat -q to harvest "
                            "several seed queries into one corpus")
        p.add_argument("--queries-file",
                       help="file with one query per line (# comments and blank lines "
                            "ignored); combined with any -q/--query given")
        p.add_argument("-n", "--max", type=int, default=config.DEFAULT_MAX,
                       help="cap on unique papers across the whole batch of queries, "
                            "not per query (default: %(default)s)")
        p.add_argument("--category", help="arXiv category filter, e.g. cs.CL")
        p.add_argument(
            "--source", default="api",
            choices=["api", "rss", "openalex", "crossref", "semanticscholar"],
            help="api = arXiv search endpoint; rss = newest arXiv announcements per "
                 "category; openalex = topical scholarly search; crossref = DOI/venue "
                 "metadata for publisher-registered work; semanticscholar = metadata "
                 "plus citation counts",
        )
        p.add_argument("--mailto", default=None,
                       help="contact address for the OpenAlex/Crossref/Unpaywall polite pool")
        p.add_argument("--arxiv-only", action="store_true",
                       help="openalex: keep only works with an arXiv copy, i.e. ones "
                            "whose full text is actually downloadable")
        p.add_argument("--search-field", default="default",
                       choices=["default", "title-and-abstract"],
                       help="openalex: 'default' is broad and fuzzy, "
                            "'title-and-abstract' is strict and better for corpora")
        p.add_argument("--sort", default="relevance", choices=["relevance", "date"])
        p.add_argument("--delay", type=float, default=config.DEFAULT_DELAY,
                       help="seconds between requests (arXiv asks for >=3)")
        p.add_argument("--no-download", action="store_true")
        p.add_argument("--no-unpaywall", action="store_true",
                       help="skip the Unpaywall lookup for papers with a DOI but no "
                            "known open-access PDF")
        p.add_argument("--force", action="store_true", help="re-download existing PDFs")

    p_search = sub.add_parser("search", parents=[common], help="query arXiv without storing anything")
    add_fetch_flags(p_search)
    p_search.set_defaults(func=cmd_search)

    p_fetch = sub.add_parser("fetch", parents=[common], help="query arXiv and store metadata + PDFs")
    add_fetch_flags(p_fetch)
    p_fetch.set_defaults(func=cmd_fetch)

    p_extract = sub.add_parser("extract", parents=[common], help="extract text for stored PDFs")
    p_extract.add_argument("--id", action="append", help="limit to specific arXiv ids")
    p_extract.set_defaults(func=cmd_extract)

    p_download = sub.add_parser("download", parents=[common],
                                help="download PDFs for stored papers that lack them")
    p_download.add_argument("--id", action="append", help="limit to specific paper keys")
    p_download.add_argument("--url", help="explicit PDF url; needs exactly one --id")
    p_download.add_argument("--delay", type=float, default=config.DEFAULT_DELAY)
    p_download.add_argument("--force", action="store_true", help="re-download existing PDFs")
    p_download.set_defaults(func=cmd_download)

    p_index = sub.add_parser("index", parents=[common], help="regenerate the derived JSON index")
    p_index.add_argument("--check", action="store_true", help="verify index matches the DB")
    p_index.set_defaults(func=cmd_index, id=None)

    p_export = sub.add_parser("export", parents=[common], help="write markdown/csv digests")
    p_export.add_argument("--format", default="md", choices=["md", "csv", "all"])
    p_export.add_argument("--title", default="Paper Index")
    p_export.add_argument("--flat", action="store_true", help="skip category grouping")
    p_export.set_defaults(func=cmd_export, id=None)

    p_run = sub.add_parser("run", parents=[common], help="fetch -> extract -> index -> export")
    add_fetch_flags(p_run)
    p_run.add_argument("--format", default="all", choices=["md", "csv", "all"])
    p_run.add_argument("--title", default="Paper Index")
    p_run.add_argument("--flat", action="store_true")
    p_run.set_defaults(func=cmd_run, id=None)

    p_stats = sub.add_parser("stats", parents=[common], help="database summary")
    p_stats.set_defaults(func=cmd_stats, id=None)

    p_serve = sub.add_parser("serve", parents=[common], help="run the local web UI")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8000)
    p_serve.set_defaults(func=cmd_serve, id=None)

    p_show = sub.add_parser("show", parents=[common], help="list or search stored papers")
    p_show.add_argument("term", nargs="?", default=None)
    p_show.add_argument("--limit", type=int, default=25)
    p_show.add_argument("--fulltext", action="store_true",
                        help="phrase search over extracted text instead of metadata")
    p_show.set_defaults(func=cmd_show, id=None, query=None)

    p_reconcile = sub.add_parser(
        "reconcile", parents=[common],
        help="report (and optionally repair) drift between stored papers and their sources",
    )
    p_reconcile.add_argument("--fix", action="store_true",
                             help="repair stale links and missing files (dry-run by default)")
    p_reconcile.add_argument("--delay", type=float, default=config.DEFAULT_DELAY)
    p_reconcile.set_defaults(func=cmd_reconcile, id=None)
    return parser


def main(argv: List[str] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
