import pytest

from paperpipe import cli, semanticscholar

PAPER = {
    "paperId": "abc123",
    "title": "Agents in Security",
    "abstract": "We study agents.",
    "year": 2024,
    "publicationDate": "2024-05-01",
    "authors": [{"authorId": "1", "name": "Ada Lovelace"}, {"authorId": "2", "name": "Alan Turing"}],
    "venue": "USENIX Security",
    "citationCount": 42,
    "externalIds": {"ArXiv": "2401.12345", "DOI": "10.1145/1234"},
    "openAccessPdf": {"url": "https://example.org/publisher.pdf"},
    "isOpenAccess": True,
}


class FakeResponse:
    def __init__(self, status_code, payload=None, headers=None):
        self.status_code = status_code
        self._payload = payload or {}
        self.headers = headers or {}

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.headers = {}

    def get(self, url, **kwargs):
        self.calls.append(kwargs.get("params") or {})
        return self.responses.pop(0)


def test_paper_to_dict_prefers_the_arxiv_copy():
    paper = semanticscholar.paper_to_dict(PAPER)
    assert paper["arxiv_id"] == "2401.12345"  # arXiv id wins as the key
    assert paper["source"] == "semanticscholar"
    assert paper["title"] == "Agents in Security"
    assert paper["abstract"] == "We study agents."
    assert paper["authors"] == ["Ada Lovelace", "Alan Turing"]
    assert paper["journal_ref"] == "USENIX Security"
    assert paper["published"] == "2024-05-01"
    assert paper["doi"] == "10.1145/1234"
    assert paper["pdf_url"] == "https://arxiv.org/pdf/2401.12345"  # not the publisher link
    assert paper["cited_by"] == 42


def test_key_falls_back_to_doi_then_paper_id():
    no_arxiv = dict(PAPER, externalIds={"DOI": "10.1145/1234"})
    assert semanticscholar.paper_to_dict(no_arxiv)["arxiv_id"] == "doi:10.1145/1234"

    bare = dict(PAPER, externalIds={}, openAccessPdf={})
    paper = semanticscholar.paper_to_dict(bare)
    assert paper["arxiv_id"] == "abc123"
    assert paper["pdf_url"] is None


def test_year_is_used_when_the_full_date_is_missing():
    paper = semanticscholar.paper_to_dict(dict(PAPER, publicationDate=None))
    assert paper["published"] == "2024-01-01"


def test_search_stops_once_a_short_page_signals_no_more_results(monkeypatch):
    monkeypatch.setattr(semanticscholar.time, "sleep", lambda _s: None)
    first = {"data": [PAPER]}
    session = FakeSession([FakeResponse(200, first)])

    # one item on a 100-row page is a short page: no second request is made.
    papers = semanticscholar.search("agents", max_results=5, session=session, delay=0)
    assert len(papers) == 1
    assert session.calls[0]["query"] == "agents"


def test_search_pages_with_an_offset_across_full_pages(monkeypatch):
    monkeypatch.setattr(semanticscholar.time, "sleep", lambda _s: None)
    first = {"data": [PAPER]}
    second = {"data": [dict(PAPER, paperId="def456", externalIds={})]}
    session = FakeSession([FakeResponse(200, first), FakeResponse(200, second)])

    papers = semanticscholar.search("agents", max_results=2, session=session, delay=0, page_size=1)
    assert [p["arxiv_id"] for p in papers] == ["2401.12345", "def456"]
    assert session.calls[1]["offset"] == "1"


def test_search_stops_early_once_max_results_is_reached(monkeypatch):
    monkeypatch.setattr(semanticscholar.time, "sleep", lambda _s: None)
    session = FakeSession([FakeResponse(200, {"data": [PAPER]})])
    capped = semanticscholar.search("agents", max_results=1, session=session, delay=0, page_size=1)
    assert len(capped) == 1 and len(session.calls) == 1  # no second page requested


def test_search_raises_a_clear_error_on_http_failure(monkeypatch):
    monkeypatch.setattr(semanticscholar.time, "sleep", lambda _s: None)
    session = FakeSession([FakeResponse(500), FakeResponse(500)])
    with pytest.raises(semanticscholar.SemanticScholarError) as exc:
        semanticscholar.search("x", max_results=1, session=session, attempts=2)
    assert "500" in str(exc.value)


def test_search_backs_off_on_429_then_succeeds(monkeypatch):
    monkeypatch.setattr(semanticscholar.time, "sleep", lambda _s: None)
    limited = FakeResponse(429, headers={"Retry-After": "1"})
    ok = FakeResponse(200, {"data": [PAPER]})
    session = FakeSession([limited, ok])
    papers = semanticscholar.search("x", max_results=1, session=session, attempts=2)
    assert len(papers) == 1


def test_cli_accepts_the_semanticscholar_source():
    args = cli.build_parser().parse_args(
        ["fetch", "-q", "agents in security", "--source", "semanticscholar"]
    )
    assert args.source == "semanticscholar"
