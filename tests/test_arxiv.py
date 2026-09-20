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


RSS = textwrap.dedent(
    """<?xml version="1.0" encoding="UTF-8"?>
    <rss xmlns:arxiv="http://arxiv.org/schemas/atom" xmlns:dc="http://purl.org/dc/elements/1.1/"
         xmlns:atom="http://www.w3.org/2005/Atom" version="2.0">
      <channel>
        <title>cs.CL updates on arXiv.org</title>
        <item>
          <title>R2VC: Modular
          Fact-Checking</title>
          <link>https://arxiv.org/abs/2609.11955</link>
          <description>arXiv:2609.11955v1 Announce Type: new
Abstract: We present a modular pipeline for retrieval and verification.</description>
          <guid isPermaLink="false">oai:arXiv.org:2609.11955v1</guid>
          <category>cs.CL</category>
          <category>cs.LG</category>
          <pubDate>Mon, 14 Sep 2026 00:00:00 -0400</pubDate>
          <arxiv:announce_type>new</arxiv:announce_type>
          <dc:creator>Dhruv Dixit, Paritosh Pandey</dc:creator>
        </item>
        <item>
          <title>A Graph Paper</title>
          <link>https://arxiv.org/abs/2609.99999</link>
          <description>arXiv:2609.99999v2 Announce Type: replace
Abstract: A graph paper about nodes and edges.</description>
          <guid isPermaLink="false">oai:arXiv.org:2609.99999v2</guid>
          <category>cs.CL</category>
          <pubDate>Mon, 14 Sep 2026 00:00:00 -0400</pubDate>
          <arxiv:announce_type>replace</arxiv:announce_type>
          <dc:creator>Ada Lovelace</dc:creator>
        </item>
      </channel>
    </rss>
    """
)


def test_parse_rss_normalises_to_the_api_shape():
    papers = arxiv.parse_rss(RSS)
    assert len(papers) == 2
    first = papers[0]
    assert first["arxiv_id"] == "2609.11955" and first["version"] == "v1"
    assert first["title"] == "R2VC: Modular Fact-Checking"
    assert first["abstract"].startswith("We present a modular pipeline")
    assert first["authors"] == ["Dhruv Dixit", "Paritosh Pandey"]
    assert first["categories"] == ["cs.CL", "cs.LG"]
    assert first["primary_category"] == "cs.CL"
    assert first["published"].startswith("2026-09-14")
    assert first["pdf_url"].endswith("/pdf/2609.11955")
    assert first["comment"] == "announce_type=new"


def test_parse_rss_rejects_garbage():
    with pytest.raises(arxiv.ArxivError):
        arxiv.parse_rss("<rss")


def test_keyword_filter_matches_all_terms():
    papers = arxiv.parse_rss(RSS)
    assert len([p for p in papers if arxiv._matches(p, None)]) == 2
    assert len([p for p in papers if arxiv._matches(p, "")]) == 2
    assert [p["arxiv_id"] for p in papers if arxiv._matches(p, "retrieval")] == ["2609.11955"]
    assert [p["arxiv_id"] for p in papers if arxiv._matches(p, "modular retrieval")] == ["2609.11955"]
    assert [p["arxiv_id"] for p in papers if arxiv._matches(p, "lovelace")] == ["2609.99999"]
    assert [p for p in papers if arxiv._matches(p, "nonexistentterm")] == []


def test_latest_uses_the_rss_endpoint_and_dedupes(monkeypatch):
    monkeypatch.setattr(arxiv.time, "sleep", lambda _s: None)
    session = FakeSession([FakeResponse(200, RSS), FakeResponse(200, RSS)])
    papers = arxiv.latest(["cs.CL", "cs.LG"], keyword=None, max_results=10, session=session)
    # the second feed repeats the same ids, so only the first batch is kept
    assert [p["arxiv_id"] for p in papers] == ["2609.11955", "2609.99999"]
    assert session.calls == 2


def test_latest_honours_max_results(monkeypatch):
    monkeypatch.setattr(arxiv.time, "sleep", lambda _s: None)
    session = FakeSession([FakeResponse(200, RSS)])
    papers = arxiv.latest(["cs.CL"], keyword=None, max_results=1, session=session)
    assert len(papers) == 1


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


def test_get_cache_hit_skips_the_network(tmp_path):
    from paperpipe import netcache

    session = FakeSession([FakeResponse(200, "<feed/>")])
    cache = netcache.ResponseCache(tmp_path / "cache")

    first = arxiv._get(session, {"search_query": "x"}, cache=cache)
    second = arxiv._get(session, {"search_query": "x"}, cache=cache)

    assert first.text == second.text == "<feed/>"
    assert session.calls == 1  # the second call never touched the network
    assert cache.hits == 1 and cache.misses == 1


def test_search_shares_one_rate_limiter_across_pages_and_never_sleeps_directly(monkeypatch):
    """When a limiter is supplied, search() must not also do its own time.sleep."""
    from paperpipe import netcache

    def fail_sleep(_s):
        pytest.fail("search() should defer all pacing to the shared limiter")

    monkeypatch.setattr(arxiv.time, "sleep", fail_sleep)
    session = FakeSession([FakeResponse(200, FEED), FakeResponse(200, "<feed/>")])
    waits = []
    limiter = netcache.RateLimiter(0.0, clock=lambda: 0.0, sleep=waits.append)

    arxiv.search("x", max_results=1, session=session, page_size=1, limiter=limiter)
    assert session.calls == 1  # one page was enough for max_results=1, no pacing needed at all
