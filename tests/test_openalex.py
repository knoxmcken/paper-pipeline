import pytest

from paperpipe import cli, openalex

WORK = {
    "id": "https://openalex.org/W123",
    "doi": "https://doi.org/10.1145/1234",
    "title": "Agents in Security",
    "display_name": "Agents in Security",
    "publication_date": "2024-05-01",
    "publication_year": 2024,
    "type": "preprint",
    "cited_by_count": 7,
    "authorships": [
        {"author": {"display_name": "Ada Lovelace"}},
        {"author": {"display_name": "Alan Turing"}},
    ],
    "primary_location": {
        "source": {"display_name": "arXiv"},
        "landing_page_url": "https://arxiv.org/abs/2401.12345",
        "pdf_url": None,
    },
    "best_oa_location": {
        "pdf_url": "https://arxiv.org/pdf/2401.12345",
        "landing_page_url": "https://arxiv.org/abs/2401.12345",
    },
    "open_access": {"is_oa": True, "oa_url": "https://arxiv.org/pdf/2401.12345"},
    "primary_topic": {"display_name": "Artificial Intelligence"},
    "topics": [{"display_name": "Artificial Intelligence"}, {"display_name": "Computer Security"}],
    "abstract_inverted_index": {"agents.": [2], "study": [1], "We": [0]},
    "locations": [],
    "ids": {"openalex": "https://openalex.org/W123", "doi": "https://doi.org/10.1145/1234"},
}


class FakeResponse:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

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


def test_work_to_dict_maps_an_arxiv_work():
    paper = openalex.work_to_dict(WORK)
    assert paper["arxiv_id"] == "2401.12345"  # arXiv location wins as the key
    assert paper["source"] == "openalex"
    assert paper["title"] == "Agents in Security"
    assert paper["abstract"] == "We study agents."  # rebuilt in position order
    assert paper["authors"] == ["Ada Lovelace", "Alan Turing"]
    assert paper["primary_category"] == "Artificial Intelligence"
    assert paper["categories"][:2] == ["Artificial Intelligence", "Computer Security"]
    assert paper["journal_ref"] == "arXiv"
    assert paper["published"] == "2024-05-01"
    assert paper["doi"] == "10.1145/1234"
    assert paper["pdf_url"] == "https://arxiv.org/pdf/2401.12345"
    assert paper["abs_url"] == "https://arxiv.org/abs/2401.12345"
    assert paper["cited_by"] == 7


def test_arxiv_copy_wins_over_a_publisher_pdf_url():
    """Publisher OA links 403 for us; the arXiv copy is the one that downloads."""
    work = dict(
        WORK,
        best_oa_location={
            "pdf_url": "https://doi.org/10.3390/telecom7030073",
            "landing_page_url": "https://doi.org/10.3390/telecom7030073",
        },
    )
    assert openalex.work_to_dict(work)["pdf_url"] == "https://arxiv.org/pdf/2401.12345"

    # without an arXiv location there is nothing better to fall back to
    no_arxiv = dict(
        WORK,
        primary_location=None,
        locations=[],
        best_oa_location={"pdf_url": "https://example.org/paper.pdf"},
    )
    assert openalex.work_to_dict(no_arxiv)["pdf_url"] == "https://example.org/paper.pdf"


def test_key_falls_back_to_doi_then_openalex_id():
    no_arxiv = dict(WORK, primary_location=None, best_oa_location=None, open_access={}, locations=[])
    assert openalex.work_to_dict(no_arxiv)["arxiv_id"] == "doi:10.1145/1234"

    bare = dict(no_arxiv, doi=None, ids={})
    assert openalex.work_to_dict(bare)["arxiv_id"] == "W123"
    assert openalex.work_to_dict(bare)["pdf_url"] is None


def test_year_is_used_when_the_full_date_is_missing():
    paper = openalex.work_to_dict(dict(WORK, publication_date=None))
    assert paper["published"] == "2024-01-01"


def test_missing_abstract_index_is_tolerated():
    assert openalex.work_to_dict(dict(WORK, abstract_inverted_index=None))["abstract"] is None
    assert openalex.work_to_dict(dict(WORK, abstract_inverted_index={}))["abstract"] is None


def test_search_pages_with_a_cursor_and_stops_early(monkeypatch):
    monkeypatch.setattr(openalex.time, "sleep", lambda _s: None)
    first = {"results": [WORK], "meta": {"next_cursor": "abc"}}
    second = {"results": [dict(WORK, id="https://openalex.org/W999", doi=None, ids={},
                               primary_location=None, best_oa_location=None, locations=[])],
              "meta": {"next_cursor": None}}
    session = FakeSession([FakeResponse(200, first), FakeResponse(200, second)])

    papers = openalex.search("agents", max_results=5, session=session, delay=0, page_size=1)
    assert [p["arxiv_id"] for p in papers] == ["2401.12345", "W999"]
    assert session.calls[0]["search"] == "agents"
    assert session.calls[0]["mailto"]  # polite pool address always sent

    session = FakeSession([FakeResponse(200, first)])
    capped = openalex.search("agents", max_results=1, session=session, delay=0, page_size=1)
    assert len(capped) == 1 and len(session.calls) == 1  # no second page requested


def test_search_can_restrict_to_open_access(monkeypatch):
    monkeypatch.setattr(openalex.time, "sleep", lambda _s: None)
    session = FakeSession([FakeResponse(200, {"results": [WORK], "meta": {}})])
    openalex.search("agents", max_results=1, session=session, delay=0, open_access_only=True)
    assert session.calls[0]["filter"] == "is_oa:true"


def test_arxiv_only_skips_works_without_an_arxiv_copy(monkeypatch):
    monkeypatch.setattr(openalex.time, "sleep", lambda _s: None)
    plain = dict(WORK, id="https://openalex.org/W1", doi="https://doi.org/10.1/x", ids={},
                 primary_location=None, best_oa_location=None, open_access={}, locations=[])
    page = FakeResponse(200, {"results": [plain, WORK], "meta": {}})
    session = FakeSession([page, FakeResponse(200, {"results": [plain, WORK], "meta": {}})])

    kept = openalex.search("agents", max_results=5, session=session, delay=0, arxiv_only=True)
    assert [p["arxiv_id"] for p in kept] == ["2401.12345"]

    everything = openalex.search("agents", max_results=5, session=session, delay=0)
    assert len(everything) == 2


def test_title_and_abstract_field_switches_to_a_filter(monkeypatch):
    monkeypatch.setattr(openalex.time, "sleep", lambda _s: None)
    session = FakeSession([FakeResponse(200, {"results": [WORK], "meta": {}})])
    openalex.search("ai agents", max_results=1, session=session, delay=0,
                    search_field="title-and-abstract")
    params = session.calls[0]
    assert "search" not in params
    assert params["filter"] == "title_and_abstract.search:ai agents"


def test_search_raises_a_clear_error_on_http_failure(monkeypatch):
    monkeypatch.setattr(openalex.time, "sleep", lambda _s: None)
    session = FakeSession([FakeResponse(500), FakeResponse(500)])
    with pytest.raises(openalex.OpenAlexError) as exc:
        openalex.search("agents", max_results=1, session=session, attempts=2)
    assert "500" in str(exc.value)


def test_cli_accepts_the_openalex_source():
    args = cli.build_parser().parse_args(
        ["fetch", "-q", "ai agents cybersecurity", "--source", "openalex", "--mailto", "a@b.c",
         "--arxiv-only", "--search-field", "title-and-abstract"]
    )
    assert args.source == "openalex" and args.mailto == "a@b.c"
    assert args.arxiv_only is True and args.search_field == "title-and-abstract"
