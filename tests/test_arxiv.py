import textwrap
from pathlib import Path

import pytest

from paperpipe import arxiv

FEED = textwrap.dedent(
    """<?xml version="1.0" encoding="UTF-8"?>
    <feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
      <entry>
        <id>http://arxiv.org/abs/2401.12345v2</id>
        <updated>2024-02-01T00:00:00Z</updated>
        <published>2024-01-22T00:00:00Z</published>
        <title>A Study of
        Things</title>
        <summary>  We   study things.
        </summary>
        <author><name>Ada Lovelace</name></author>
        <author><name>Alan Turing</name></author>
        <arxiv:comment>12 pages, 3 figures</arxiv:comment>
        <arxiv:journal_ref>J. Things 1 (2024)</arxiv:journal_ref>
        <arxiv:doi>10.1000/xyz</arxiv:doi>
        <arxiv:primary_category term="cs.CL"/>
        <category term="cs.CL"/>
        <category term="cs.AI"/>
        <link href="http://arxiv.org/pdf/2401.12345v2" rel="related" title="pdf" type="application/pdf"/>
      </entry>
    </feed>
    """
)


def test_parse_feed_fields():
    (paper,) = arxiv.parse_feed(FEED)
    assert paper["arxiv_id"] == "2401.12345"
    assert paper["version"] == "v2"
    assert paper["title"] == "A Study of Things"
    assert paper["abstract"] == "We study things."
    assert paper["authors"] == ["Ada Lovelace", "Alan Turing"]
    assert paper["primary_category"] == "cs.CL"
    assert paper["categories"] == ["cs.CL", "cs.AI"]
    assert paper["journal_ref"] == "J. Things 1 (2024)"
    assert paper["pdf_url"] == "http://arxiv.org/pdf/2401.12345v2"
    assert paper["abs_url"].endswith("/abs/2401.12345")


def test_parse_feed_rejects_garbage():
    with pytest.raises(arxiv.ArxivError):
        arxiv.parse_feed("<not-a-feed")


def test_split_id_handles_old_style_and_missing_version():
    assert arxiv._split_id("http://arxiv.org/abs/math/0301234v1") == ("math/0301234", "v1")
    assert arxiv._split_id("http://arxiv.org/abs/2401.12345") == ("2401.12345", "")


def test_query_string_quotes_bare_terms():
    assert arxiv._query_string("llm agents", None) == 'all:"llm agents"'
    assert arxiv._query_string("au:Knuth", None) == "au:Knuth"
    assert arxiv._query_string("llm", "cs.CL") == '(all:"llm") AND cat:cs.CL'


class FakeResponse:
    def __init__(self, status_code, text="", headers=None):
        self.status_code = status_code
        self.text = text
        self.headers = headers or {}


class FakeSession:
    """Replays a scripted list of responses and records call count."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0
        self.headers = {}

    def get(self, url, **kwargs):
        self.calls += 1
        return self.responses.pop(0)


def test_get_retries_through_a_429(monkeypatch):
    monkeypatch.setattr(arxiv.time, "sleep", lambda _s: None)
    session = FakeSession([FakeResponse(429, "Rate exceeded."), FakeResponse(200, "<feed/>")])
    resp = arxiv._get(session, {}, attempts=3, base_backoff=0)
    assert resp.status_code == 200 and session.calls == 2


def test_get_raises_a_clear_error_when_always_throttled(monkeypatch):
    monkeypatch.setattr(arxiv.time, "sleep", lambda _s: None)
    session = FakeSession([FakeResponse(429, "Rate exceeded.")] * 3)
    with pytest.raises(arxiv.ArxivError) as exc:
        arxiv._get(session, {}, attempts=3, base_backoff=0)
    assert "rate limit" in str(exc.value).lower()
    assert session.calls == 3


def test_get_surfaces_non_200_status(monkeypatch):
    monkeypatch.setattr(arxiv.time, "sleep", lambda _s: None)
    session = FakeSession([FakeResponse(503, "nope")] * 2)
    with pytest.raises(arxiv.ArxivError) as exc:
        arxiv._get(session, {}, attempts=2, base_backoff=0)
    assert "503" in str(exc.value)


def test_retry_after_header_is_parsed():
    assert arxiv._retry_after(FakeResponse(429, "", {"Retry-After": "30"})) == 30.0
    assert arxiv._retry_after(FakeResponse(429, "", {"Retry-After": "soon"})) is None
    assert arxiv._retry_after(FakeResponse(429)) is None
