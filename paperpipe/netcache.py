"""A shared on-disk response cache and a shared rate limiter for discovery sources.

Every source (``arxiv``, ``openalex``, ``crossref``, ``semanticscholar``,
``unpaywall``) implemented its own sleep/backoff and never shared a request
budget with the others, so an identical query re-hit the network every time and
a fetch across several sources could burst well past what any one of them
tolerates. Both pieces here are optional and off by default: existing callers
that don't pass ``cache=``/``limiter=`` see zero behaviour change.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Callable, Optional


def cache_key(source: str, url: str, params: Optional[dict] = None) -> str:
    """A stable key for ``source``'s request to ``url`` with ``params``.

    Key order in ``params`` must not matter, since callers build the dict in
    whatever order is convenient; values are stringified so ``int``/``float``
    params key the same as their string form.
    """
    normalised = sorted((str(k), str(v)) for k, v in (params or {}).items())
    raw = json.dumps([source, url, normalised], sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class RateLimiter:
    """Enforce a minimum interval between calls, shared across callers.

    One instance can be handed to several sources within a single process run
    so the combined request rate - not just each source's own - stays under
    ``min_interval``. ``clock``/``sleep`` are injectable so tests never sleep
    for real.
    """

    def __init__(
        self,
        min_interval: float,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.min_interval = max(0.0, min_interval)
        self._clock = clock
        self._sleep = sleep
        self._last_call: Optional[float] = None

    def wait(self) -> float:
        """Block until ``min_interval`` has passed since the last call.

        Returns the number of seconds actually waited (``0.0`` for the first
        call, or any call once enough time has already elapsed).
        """
        now = self._clock()
        waited = 0.0
        if self._last_call is not None:
            remaining = self.min_interval - (now - self._last_call)
            if remaining > 0:
                self._sleep(remaining)
                waited = remaining
                now = self._clock()
        self._last_call = now
        return waited


class ResponseCache:
    """A tiny on-disk cache: one JSON file per key, expiring after ``ttl`` seconds.

    Tracks ``hits``/``misses`` so callers can report "served from cache" without
    threading a flag through every discovery source's return value.
    """

    def __init__(
        self,
        directory: Path,
        ttl: float = 3600.0,
        clock: Callable[[], float] = time.time,
    ):
        self.directory = Path(directory)
        self.ttl = ttl
        self._clock = clock
        self.hits = 0
        self.misses = 0

    def _path(self, key: str) -> Path:
        return self.directory / f"{key}.json"

    def get(self, key: str) -> Optional[Any]:
        path = self._path(key)
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError:
            self.misses += 1
            return None
        try:
            payload = json.loads(raw)
        except ValueError:
            self.misses += 1
            return None
        if self._clock() - payload.get("cached_at", 0) > self.ttl:
            self.misses += 1
            return None
        self.hits += 1
        return payload.get("value")

    def set(self, key: str, value: Any) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self._path(key)
        tmp = path.with_suffix(".part")
        tmp.write_text(json.dumps({"cached_at": self._clock(), "value": value}), encoding="utf-8")
        tmp.replace(path)


class CachedResponse:
    """A minimal ``requests.Response`` stand-in for a cached ``(status, text)`` pair."""

    def __init__(self, status_code: int, text: str, headers: Optional[dict] = None):
        self.status_code = status_code
        self.text = text
        self.headers = headers or {}
        self.from_cache = True

    def json(self) -> Any:
        return json.loads(self.text)


def cached_get(
    session,
    url: str,
    params: dict,
    *,
    source: str,
    cache: Optional[ResponseCache] = None,
    limiter: Optional[RateLimiter] = None,
    headers: Optional[dict] = None,
    timeout: float = 45,
) -> "requests.Response":  # noqa: F821 - optional dependency, typed loosely
    """GET ``url`` (any content type) with an optional shared cache and rate limiter.

    Works for XML (arXiv) and JSON (OpenAlex/Crossref/Semantic Scholar/Unpaywall)
    bodies alike: only ``status_code``/``text`` are cached, and ``.json()`` is
    available on both a live ``requests.Response`` and a replayed ``CachedResponse``.

    A cache hit returns immediately with no network call and no limiter wait.
    A miss waits on ``limiter`` (if any), performs the request, and on a 200
    stores the body for next time. Non-200 responses are never cached, so a
    transient error doesn't get replayed for the whole TTL.
    """
    key = cache_key(source, url, params) if cache is not None else None
    if cache is not None:
        cached = cache.get(key)
        if cached is not None:
            return CachedResponse(cached["status_code"], cached["text"])
    if limiter is not None:
        limiter.wait()
    resp = session.get(url, params=params, headers=headers or {}, timeout=timeout)
    resp.from_cache = False
    if cache is not None and resp.status_code == 200:
        cache.set(key, {"status_code": resp.status_code, "text": resp.text})
    return resp
