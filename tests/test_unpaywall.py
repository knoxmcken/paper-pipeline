import pytest

from paperpipe import unpaywall


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
        self.calls.append((url, kwargs.get("params") or {}))
        return self.responses.pop(0)


def test_lookup_returns_the_best_oa_pdf_url():
    payload = {"best_oa_location": {"url_for_pdf": "https://example.org/x.pdf", "url": "https://example.org/x"}}
    session = FakeSession([FakeResponse(200, payload)])
    url = unpaywall.lookup_pdf_url("10.1/x", session=session)
    assert url == "https://example.org/x.pdf"
    assert session.calls[0][1]["email"]  # polite contact address always sent


def test_lookup_falls_back_to_landing_url_without_a_pdf_link():
    payload = {"best_oa_location": {"url": "https://example.org/x"}}
    session = FakeSession([FakeResponse(200, payload)])
    assert unpaywall.lookup_pdf_url("10.1/x", session=session) == "https://example.org/x"


def test_lookup_returns_none_when_unpaywall_has_no_record():
    session = FakeSession([FakeResponse(404)])
    assert unpaywall.lookup_pdf_url("10.1/unknown", session=session) is None


def test_lookup_returns_none_when_not_open_access():
    session = FakeSession([FakeResponse(200, {"best_oa_location": None})])
    assert unpaywall.lookup_pdf_url("10.1/closed", session=session) is None


def test_lookup_backs_off_on_429_then_succeeds(monkeypatch):
    monkeypatch.setattr(unpaywall.time, "sleep", lambda _s: None)
    limited = FakeResponse(429, headers={"Retry-After": "1"})
    ok = FakeResponse(200, {"best_oa_location": {"url_for_pdf": "https://example.org/x.pdf"}})
    session = FakeSession([limited, ok])
    assert unpaywall.lookup_pdf_url("10.1/x", session=session, attempts=2) == "https://example.org/x.pdf"


def test_lookup_raises_a_clear_error_on_repeated_http_failure(monkeypatch):
    monkeypatch.setattr(unpaywall.time, "sleep", lambda _s: None)
    session = FakeSession([FakeResponse(500), FakeResponse(500)])
    with pytest.raises(unpaywall.UnpaywallError) as exc:
        unpaywall.lookup_pdf_url("10.1/x", session=session, attempts=2)
    assert "500" in str(exc.value)


def test_enrich_only_touches_papers_with_a_doi_and_no_pdf_url(monkeypatch):
    monkeypatch.setattr(unpaywall.time, "sleep", lambda _s: None)
    papers = [
        {"arxiv_id": "a", "doi": "10.1/a", "pdf_url": None},
        {"arxiv_id": "b", "doi": None, "pdf_url": None},  # no DOI: nothing to look up
        {"arxiv_id": "c", "doi": "10.1/c", "pdf_url": "https://already/known.pdf"},  # skipped
    ]

    def fake_lookup(doi, **kwargs):
        assert doi == "10.1/a"
        return "https://example.org/a.pdf"

    monkeypatch.setattr(unpaywall, "lookup_pdf_url", fake_lookup)
    filled = unpaywall.enrich_missing_pdfs(papers)
    assert filled == 1
    assert papers[0]["pdf_url"] == "https://example.org/a.pdf"
    assert papers[1]["pdf_url"] is None
    assert papers[2]["pdf_url"] == "https://already/known.pdf"


def test_enrich_skips_a_paper_whose_lookup_fails(monkeypatch):
    monkeypatch.setattr(unpaywall.time, "sleep", lambda _s: None)
    papers = [{"arxiv_id": "a", "doi": "10.1/a", "pdf_url": None}]

    def boom(doi, **kwargs):
        raise unpaywall.UnpaywallError("nope")

    monkeypatch.setattr(unpaywall, "lookup_pdf_url", boom)
    assert unpaywall.enrich_missing_pdfs(papers) == 0
    assert papers[0]["pdf_url"] is None
