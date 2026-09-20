"""On-disk response cache + shared rate limiter (issue #5). All offline, no sleeps."""

import pytest

from paperpipe import netcache


class FakeResponse:
    def __init__(self, status_code=200, text="{}"):
        self.status_code = status_code
        self.text = text


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs.get("params")))
        return self.responses.pop(0)


class FakeClock:
    """A controllable clock: advances only when told to."""

    def __init__(self, start=0.0):
        self.now = start

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def test_cache_key_is_stable_regardless_of_param_order():
    a = netcache.cache_key("arxiv", "https://x", {"a": 1, "b": 2})
    b = netcache.cache_key("arxiv", "https://x", {"b": 2, "a": 1})
    assert a == b


def test_cache_key_differs_by_source_url_or_params():
    base = netcache.cache_key("arxiv", "https://x", {"q": "a"})
    assert base != netcache.cache_key("openalex", "https://x", {"q": "a"})
    assert base != netcache.cache_key("arxiv", "https://y", {"q": "a"})
    assert base != netcache.cache_key("arxiv", "https://x", {"q": "b"})


def test_rate_limiter_sleeps_only_when_the_interval_has_not_elapsed():
    clock = FakeClock()
    sleeps = []
    limiter = netcache.RateLimiter(3.0, clock=clock, sleep=sleeps.append)

    limiter.wait()  # first call: nothing to wait for
    assert sleeps == []

    clock.advance(1.0)
    limiter.wait()  # only 1s elapsed of a 3s budget -> wait 2s
    assert sleeps == [2.0]

    clock.advance(5.0)
    limiter.wait()  # plenty of time already passed -> no wait
    assert sleeps == [2.0]


def test_rate_limiter_is_shared_across_two_independent_callers():
    """One instance handed to two 'sources' enforces one combined budget."""
    clock = FakeClock()
    sleeps = []
    limiter = netcache.RateLimiter(2.0, clock=clock, sleep=sleeps.append)

    limiter.wait()  # source A's first request
    limiter.wait()  # source B's request, right after A with no time advance
    assert sleeps == [2.0]


def test_response_cache_hit_skips_the_network_entirely(tmp_path):
    session = FakeSession([FakeResponse(text="first")])
    cache = netcache.ResponseCache(tmp_path / "cache")
    limiter = netcache.RateLimiter(5.0, clock=lambda: 0.0, sleep=lambda s: pytest.fail("should not sleep"))

    resp1 = netcache.cached_get(session, "https://x", {"q": "a"}, source="s", cache=cache, limiter=limiter)
    assert resp1.text == "first"
    assert len(session.calls) == 1

    resp2 = netcache.cached_get(session, "https://x", {"q": "a"}, source="s", cache=cache, limiter=limiter)
    assert resp2.text == "first"
    assert len(session.calls) == 1  # no second network call
    assert cache.hits == 1 and cache.misses == 1


def test_response_cache_does_not_cache_non_200(tmp_path):
    session = FakeSession([FakeResponse(status_code=500, text="oops"), FakeResponse(text="ok")])
    cache = netcache.ResponseCache(tmp_path / "cache")

    first = netcache.cached_get(session, "https://x", {}, source="s", cache=cache)
    assert first.status_code == 500

    second = netcache.cached_get(session, "https://x", {}, source="s", cache=cache)
    assert second.status_code == 200
    assert len(session.calls) == 2  # the error was never replayed from cache


def test_response_cache_expires_after_ttl(tmp_path):
    clock = FakeClock()
    session = FakeSession([FakeResponse(text="a"), FakeResponse(text="b")])
    cache = netcache.ResponseCache(tmp_path / "cache", ttl=10.0, clock=clock)

    first = netcache.cached_get(session, "https://x", {}, source="s", cache=cache)
    assert first.text == "a"

    clock.advance(11.0)
    second = netcache.cached_get(session, "https://x", {}, source="s", cache=cache)
    assert second.text == "b"  # ttl elapsed -> treated as a miss, refetched
    assert len(session.calls) == 2


def test_no_cache_argument_means_always_hit_the_network(tmp_path):
    session = FakeSession([FakeResponse(text="a"), FakeResponse(text="b")])

    first = netcache.cached_get(session, "https://x", {}, source="s", cache=None)
    second = netcache.cached_get(session, "https://x", {}, source="s", cache=None)
    assert (first.text, second.text) == ("a", "b")
    assert len(session.calls) == 2
