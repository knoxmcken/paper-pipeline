import pytest

from paperpipe import cli, db, duplicates
from tests.helpers import make_paper

PREPRINT = make_paper(
    arxiv_id="2401.00001",
    title="Attention Is All You Need",
    authors=["Ada Lovelace", "Alan Turing"],
    published="2023-12-30T00:00:00Z",
    doi=None,
)
# The journal version: its own DOI, a year later, found through Crossref.
PUBLISHED = make_paper(
    arxiv_id="doi:10.1000/journal.42",
    title="Attention is all you need.",
    authors=["Lovelace, Ada", "Turing, Alan"],
    published="2024-06-01",
    doi="10.1000/journal.42",
    abs_url=None,
    pdf_url=None,
    journal_ref="Journal of Tests",
    cited_by=7,
)
UNRELATED = make_paper(
    arxiv_id="2402.00002",
    title="Graph Neural Networks for Chemistry",
    authors=["Grace Hopper"],
    published="2024-02-01",
    abs_url="http://arxiv.org/abs/2402.00002",
    pdf_url="http://arxiv.org/pdf/2402.00002",
)


@pytest.fixture()
def conn(tmp_path):
    c = db.connect(tmp_path / "papers.db")
    db.init_db(c)
    yield c
    c.close()


def _pairs(papers):
    return {(c["a"], c["b"]): c["evidence"] for c in duplicates.find_duplicates(papers)}


def test_normalise_title_ignores_case_punctuation_accents_and_latex():
    assert duplicates.normalise_title("Déjà  Vu: $O(n^2)$ Attention!") == "deja vu attention"


def test_first_author_keeps_non_latin_names():
    assert duplicates.first_author(make_paper(authors=["王小明"])) == "王小明"
    assert duplicates.first_author(make_paper(authors=["José Núñez"])) == "nunez"
    assert duplicates.normalise_title("深度 学习!") == "深度 学习"


def test_preprint_and_published_version_with_different_dois_pair_on_title():
    pairs = _pairs([PREPRINT, PUBLISHED, UNRELATED])
    assert list(pairs) == [("2401.00001", "doi:10.1000/journal.42")]
    [evidence] = pairs.values()
    assert evidence == ["title: same title, first author 'lovelace', year 2023/2024"]


def test_shared_doi_pairs_exactly():
    arxiv_copy = dict(PREPRINT, doi="10.1000/JOURNAL.42")  # case differs
    evidence = duplicates.compare(arxiv_copy, PUBLISHED)
    assert evidence[0] == "doi: 10.1000/journal.42"


def test_shared_arxiv_id_via_arxiv_doi_or_link():
    via_doi = make_paper(arxiv_id="doi:10.48550/arXiv.2401.00001", title="Other", authors=["X Y"])
    via_link = make_paper(arxiv_id="W123", title="Else", authors=["Z W"],
                          abs_url="https://arxiv.org/abs/2401.00001v3")
    assert duplicates.compare(PREPRINT, via_doi) == ["arxiv_id: 2401.00001"]
    assert duplicates.compare(PREPRINT, via_link) == ["arxiv_id: 2401.00001"]


def test_near_duplicate_title_is_reported_separately():
    near = dict(PUBLISHED, title="Attention Is All You Needed")
    [evidence] = _pairs([PREPRINT, near]).values()
    assert evidence[0].startswith("near_title: 0.9")


def test_non_duplicates_are_not_paired():
    other_author = dict(PUBLISHED, authors=["Someone Else"])
    far_apart = dict(PUBLISHED, published="2019-01-01")
    different = dict(PUBLISHED, title="Attention Is Not All You Need At All, Really")
    for paper in (other_author, far_apart, different, UNRELATED):
        assert duplicates.compare(PREPRINT, paper) == []


def test_merge_fills_gaps_moves_text_and_deletes_the_other_row(conn, tmp_path):
    text = tmp_path / "t.txt"
    text.write_text("page one\fpage two")
    db.upsert_papers(conn, [PREPRINT, PUBLISHED])
    db.update_extraction(
        conn, PUBLISHED["arxiv_id"],
        {"text_path": str(text), "text_chars": 17, "page_count": 2,
         "headings": ["Intro"], "headings_method": "outline"},
        "2024-07-01T00:00:00Z",
    )
    db.index_fulltext(conn, PUBLISHED["arxiv_id"], str(text))

    result = duplicates.merge(conn, "2401.00001", "doi:10.1000/journal.42")

    assert db.get_paper(conn, "doi:10.1000/journal.42") is None
    kept = db.get_paper(conn, "2401.00001")
    assert kept["title"] == "Attention Is All You Need"  # KEEP's values win
    assert kept["doi"] == "10.1000/journal.42" and kept["journal_ref"] == "Journal of Tests"
    assert kept["cited_by"] == 7
    assert kept["text_path"] == str(text) and kept["headings"] == ["Intro"]
    assert kept["headings_method"] == "outline" and kept["page_count"] == 2
    fts = conn.execute("SELECT DISTINCT arxiv_id FROM papers_fts").fetchall()
    assert [row[0] for row in fts] == ["2401.00001"]
    assert "text_path" in result["filled"] and result["orphaned_files"] == []


def test_merge_keeps_existing_values_and_reports_orphaned_files(conn):
    db.upsert_papers(conn, [PREPRINT, PUBLISHED])
    db.update_pdf(conn, "2401.00001", {"path": "a.pdf", "sha256": "a", "bytes": 1})
    db.update_pdf(conn, "doi:10.1000/journal.42", {"path": "b.pdf", "sha256": "b", "bytes": 2})
    result = duplicates.merge(conn, "2401.00001", "doi:10.1000/journal.42")
    assert db.get_paper(conn, "2401.00001")["pdf_path"] == "a.pdf"
    assert result["orphaned_files"] == ["b.pdf"]


def test_merge_refuses_unknown_or_identical_keys(conn):
    db.upsert_papers(conn, [PREPRINT])
    with pytest.raises(ValueError):
        duplicates.merge(conn, "2401.00001", "2401.00001")
    with pytest.raises(ValueError):
        duplicates.merge(conn, "2401.00001", "nope")
    assert db.get_paper(conn, "2401.00001") is not None


def test_cli_lists_without_changing_anything_and_merges_only_on_request(tmp_path, capsys):
    data = tmp_path / "data"
    c = db.connect(data / "papers.db")
    db.init_db(c)
    db.upsert_papers(c, [PREPRINT, PUBLISHED, UNRELATED])
    c.close()
    parser = cli.build_parser()

    assert cli.cmd_duplicates(parser.parse_args(["--data-dir", str(data), "duplicates"])) == 0
    out = capsys.readouterr().out
    assert "2401.00001  <->  doi:10.1000/journal.42" in out and "1 candidate pair(s)" in out
    c = db.connect(data / "papers.db")
    assert db.stats(c)["papers"] == 3
    c.close()

    args = parser.parse_args(
        ["--data-dir", str(data), "duplicates", "--merge", "2401.00001", "doi:10.1000/journal.42"]
    )
    assert cli.cmd_duplicates(args) == 0
    c = db.connect(data / "papers.db")
    assert db.stats(c)["papers"] == 2
    assert tuple(c.execute("SELECT kind, ok FROM runs").fetchall()[-1]) == ("merge", 1)
    c.close()

    bad = parser.parse_args(["--data-dir", str(data), "duplicates", "--merge", "2401.00001", "gone"])
    assert cli.cmd_duplicates(bad) == 2
