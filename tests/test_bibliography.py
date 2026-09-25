import json

import pytest

from paperpipe import bibliography, cli, db
from tests.helpers import make_paper

ARXIV = make_paper(
    arxiv_id="2401.00001",
    title="Attention  Is All You Need & More: 100% {Transformers}",
    authors=["Ada Lovelace", "Turing, Alan", "Plato"],
    published="2024-01-15T00:00:00Z",
    abstract="We study $O(n^2)$ attention.",
    primary_category="cs.CL",
)
JOURNAL = make_paper(
    arxiv_id="doi:10.1000/xyz.1",
    title="A Published Study",
    authors=["Grace Hopper"],
    published="2023-06-01",
    abs_url=None,
    doi=None,
    journal_ref="Journal of Tests",
)


def test_ids_are_derived_from_the_paper_key():
    assert bibliography.arxiv_id(ARXIV) == "2401.00001"
    assert bibliography.arxiv_id(make_paper(arxiv_id="hep-th/9901001v2")) == "hep-th/9901001v2"
    assert bibliography.arxiv_id(JOURNAL) is None
    assert bibliography.doi(JOURNAL) == "10.1000/xyz.1"
    assert bibliography.url(JOURNAL) == "https://doi.org/10.1000/xyz.1"
    assert bibliography.url(ARXIV) == "http://arxiv.org/abs/2401.00001"


def test_citation_keys_are_stable_and_unique():
    twin = dict(ARXIV, arxiv_id="2401.00002")
    keys = [key for key, _ in bibliography.keyed([twin, JOURNAL, ARXIV])]
    assert keys == ["hopper2023published", "lovelace2024attention", "lovelace2024attentionb"]
    assert keys == [key for key, _ in bibliography.keyed([ARXIV, JOURNAL, twin])]


def test_bibtex_round_trips_through_a_parser():
    pybtex = pytest.importorskip("pybtex.database")
    parsed = pybtex.parse_string(bibliography.to_bibtex([ARXIV, JOURNAL]), "bibtex")
    assert set(parsed.entries) == {"lovelace2024attention", "hopper2023published"}

    preprint = parsed.entries["lovelace2024attention"]
    assert preprint.type == "misc"
    assert preprint.fields["title"] == r"{Attention Is All You Need \& More: 100\% {Transformers}}"
    assert preprint.fields["year"] == "2024"
    assert preprint.fields["eprint"] == "2401.00001"
    assert preprint.fields["archiveprefix"] == "arXiv"
    assert preprint.fields["url"] == "http://arxiv.org/abs/2401.00001"
    assert [str(p) for p in preprint.persons["author"]] == ["Lovelace, Ada", "Turing, Alan", "Plato"]

    article = parsed.entries["hopper2023published"]
    assert article.type == "article"
    assert article.fields["journal"] == "Journal of Tests"
    assert article.fields["doi"] == "10.1000/xyz.1"
    assert "eprint" not in article.fields


def test_bibtex_strips_unbalanced_braces():
    paper = make_paper(title="Broken {brace title")
    pybtex = pytest.importorskip("pybtex.database")
    entry = next(iter(pybtex.parse_string(bibliography.to_bibtex([paper]), "bibtex").entries.values()))
    assert entry.fields["title"] == "{Broken brace title}"


def test_csljson_items():
    items = json.loads(bibliography.to_csljson([ARXIV, JOURNAL]))
    preprint, article = items[1], items[0]
    assert preprint["id"] == "lovelace2024attention"
    assert preprint["type"] == "article" and preprint["publisher"] == "arXiv"
    assert preprint["number"] == "arXiv:2401.00001"
    assert preprint["author"] == [
        {"family": "Lovelace", "given": "Ada"},
        {"family": "Turing", "given": "Alan"},
        {"literal": "Plato"},
    ]
    assert preprint["issued"] == {"date-parts": [[2024, 1, 15]]}
    assert preprint["title"] == "Attention Is All You Need & More: 100% {Transformers}"
    assert article["type"] == "article-journal"
    assert article["container-title"] == "Journal of Tests"
    assert article["DOI"] == "10.1000/xyz.1"
    assert "number" not in article


def test_ris_records():
    text = bibliography.to_ris([ARXIV, JOURNAL])
    assert text.endswith("ER  - \n")
    records = text.rstrip("\n").split("\n\n")
    assert len(records) == 2
    preprint = records[1].splitlines()
    assert preprint[0] == "TY  - GEN"
    assert preprint[-1] == "ER  - "
    assert "AU  - Ada Lovelace" in preprint and "AU  - Plato" in preprint
    assert "PY  - 2024" in preprint and "DA  - 2024/01/15/" in preprint
    assert "AN  - arXiv:2401.00001" in preprint
    article = records[0].splitlines()
    assert article[0] == "TY  - JOUR"
    assert "JO  - Journal of Tests" in article and "DO  - 10.1000/xyz.1" in article


def test_empty_corpus_exports_are_empty():
    assert bibliography.to_bibtex([]) == ""
    assert json.loads(bibliography.to_csljson([])) == []
    assert bibliography.to_ris([]) == ""


def test_cli_export_writes_stable_files_to_out_dir(tmp_path):
    conn = db.connect(tmp_path / "data" / "papers.db")
    db.init_db(conn)
    db.upsert_papers(conn, [ARXIV, JOURNAL])
    conn.close()
    out = tmp_path / "refs"
    argv = ["--data-dir", str(tmp_path / "data"), "export", "--format", "all", "--out", str(out)]
    args = cli.build_parser().parse_args(argv)
    assert cli.cmd_export(args) == 0
    names = sorted(p.name for p in out.iterdir())
    assert names == ["papers.bib", "papers.csl.json", "papers.csv", "papers.md", "papers.ris"]
    first = {name: (out / name).read_text() for name in names}
    assert cli.cmd_export(cli.build_parser().parse_args(argv)) == 0
    assert {name: (out / name).read_text() for name in names} == first


def test_bibtex_keeps_an_organisation_with_and_as_one_author():
    pybtex = pytest.importorskip("pybtex.database")
    paper = make_paper(authors=["Research and Development Team", "Ada Lovelace"])
    entry = next(iter(pybtex.parse_string(bibliography.to_bibtex([paper]), "bibtex").entries.values()))
    assert len(entry.persons["author"]) == 2
