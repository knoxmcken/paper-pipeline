import pytest

from paperpipe import cli, crossref

ITEM = {
    "DOI": "10.1109/TIFS.2024.1234",
    "title": ["Adversarial Robustness in Practice"],
    "author": [{"given": "Ada", "family": "Lovelace"}, {"given": "Alan", "family": "Turing"}],
    "abstract": "<jats:p>We study <jats:italic>robustness</jats:italic>.</jats:p>",
    "published": {"date-parts": [[2024, 3, 5]]},
    "container-title": ["IEEE Transactions on Information Forensics and Security"],
    "type": "journal-article",
    "is-referenced-by-count": 12,
    "link": [{"URL": "https://example.org/paper.pdf", "content-type": "application/pdf"}],
    "URL": "https://doi.org/10.1109/TIFS.2024.1234",
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


def test_work_to_dict_maps_a_publisher_item():
    paper = crossref.work_to_dict(ITEM)
    assert paper["arxiv_id"] == "doi:10.1109/tifs.2024.1234"
    assert paper["source"] == "crossref"
    assert paper["title"] == "Adversarial Robustness in Practice"
    assert paper["abstract"] == "We study robustness ."  # JATS tags stripped
    assert paper["authors"] == ["Ada Lovelace", "Alan Turing"]
    assert paper["journal_ref"] == "IEEE Transactions on Information Forensics and Security"
    assert paper["published"] == "2024-03-05"
    assert paper["doi"] == "10.1109/tifs.2024.1234"
    assert paper["pdf_url"] == "https://example.org/paper.pdf"
    assert paper["abs_url"] == "https://doi.org/10.1109/TIFS.2024.1234"
    assert paper["cited_by"] == 12


def test_key_falls_back_to_url_when_no_doi():
    item = dict(ITEM, DOI=None, URL="https://example.org/x")
    assert crossref.work_to_dict(item)["arxiv_id"] == "https://example.org/x"


def test_missing_abstract_and_pdf_link_are_tolerated():
    item = dict(ITEM, abstract=None, link=[])
    paper = crossref.work_to_dict(item)
    assert paper["abstract"] is None
    assert paper["pdf_url"] is None


def test_incomplete_date_parts_default_month_and_day():
    item = dict(ITEM, published={"date-parts": [[2024]]})
    assert crossref.work_to_dict(item)["published"] == "2024-01-01"


def test_search_stops_once_a_short_page_signals_no_more_results(monkeypatch):
    monkeypatch.setattr(crossref.time, "sleep", lambda _s: None)
    first = {"message": {"items": [ITEM]}}
    session = FakeSession([FakeResponse(200, first)])

    # one item on a 100-row page is a short page: no second request is made.
    papers = crossref.search("robustness", max_results=5, session=session, delay=0)
    assert len(papers) == 1
    assert session.calls[0]["query.bibliographic"] == "robustness"
    assert session.calls[0]["mailto"]  # polite pool address always sent


def test_search_pages_with_an_offset_across_full_pages(monkeypatch):
    monkeypatch.setattr(crossref.time, "sleep", lambda _s: None)
    first = {"message": {"items": [ITEM]}}
    second = {"message": {"items": [dict(ITEM, DOI="10.1/other")]}}
    session = FakeSession([FakeResponse(200, first), FakeResponse(200, second)])

    papers = crossref.search("robustness", max_results=2, session=session, delay=0, page_size=1)
    assert [p["doi"] for p in papers] == ["10.1109/tifs.2024.1234", "10.1/other"]
    assert session.calls[1]["offset"] == "1"


def test_search_stops_early_once_max_results_is_reached(monkeypatch):
    monkeypatch.setattr(crossref.time, "sleep", lambda _s: None)
    session = FakeSession([FakeResponse(200, {"message": {"items": [ITEM]}})])
    capped = crossref.search("robustness", max_results=1, session=session, delay=0, page_size=1)
    assert len(capped) == 1 and len(session.calls) == 1  # no second page requested


def test_search_raises_a_clear_error_on_http_failure(monkeypatch):
    monkeypatch.setattr(crossref.time, "sleep", lambda _s: None)
    session = FakeSession([FakeResponse(500), FakeResponse(500)])
    with pytest.raises(crossref.CrossrefError) as exc:
        crossref.search("x", max_results=1, session=session, attempts=2)
    assert "500" in str(exc.value)


def test_search_backs_off_on_429_then_succeeds(monkeypatch):
    monkeypatch.setattr(crossref.time, "sleep", lambda _s: None)
    limited = FakeResponse(429, headers={"Retry-After": "1"})
    ok = FakeResponse(200, {"message": {"items": [ITEM]}})
    session = FakeSession([limited, ok])
    papers = crossref.search("x", max_results=1, session=session, attempts=2)
    assert len(papers) == 1


def test_cli_accepts_the_crossref_source():
    args = cli.build_parser().parse_args(
        ["fetch", "-q", "adversarial robustness", "--source", "crossref", "--mailto", "a@b.c"]
    )
    assert args.source == "crossref" and args.mailto == "a@b.c"
