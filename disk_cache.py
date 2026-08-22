# disk_cache.py
# One place where every external HTTP response is stored on disk, so a demo
# cannot fail because an API is down or a quota is spent.
#
# TWO RULES, and the first one is not negotiable:
#
# 1. A FAILED REQUEST IS NEVER CACHED. Only an HTTP 200 with valid JSON is
#    written. This is the trap that already cost this project once: on
#    2026-07-25 a benchmark harness let an HTTP 503 fall through to an empty
#    result, wrote that empty result into its cache, and the empty result was
#    then indistinguishable from a query that genuinely matched no books. It
#    made one configuration look ~10 books worse than it was (EVALUATION.md
#    section 8.1). An absence and a failure are different facts and must never
#    be stored as the same thing.
#
# 2. DEMO_OFFLINE=1 OPENS NO SOCKETS. Every lookup is served from the cache,
#    and a miss raises OfflineCacheMiss with the key that was missing. It
#    fails loudly rather than quietly degrading, because a demo that silently
#    returns worse answers is harder to notice than one that stops.

import hashlib
import json
import logging
import os
import time

import requests

# Where the cache lives. Overridable so tests do not write into the real one.
CACHE_DIR = os.environ.get(
    "BOOKLENS_CACHE_DIR",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "cache"))


def demo_offline():
    # Read at call time, not import time, so tests and scripts can flip it.
    return os.environ.get("DEMO_OFFLINE", "") == "1"


class OfflineCacheMiss(Exception):
    # Raised only when DEMO_OFFLINE=1 and the answer is not on disk.
    pass


class FetchFailed(Exception):
    # Raised by fetch_json when strict=True and the request did not return a
    # usable 200. Scripts that build datasets use strict=True so a bad
    # response stops the run instead of being recorded as an absence.
    pass


def _path(namespace, key):
    # Keys are arbitrary strings - search queries contain spaces, colons and
    # non-ASCII - so the filename is a hash. The original key is stored INSIDE
    # the file so the cache stays readable and debuggable by hand.
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()
    return os.path.join(CACHE_DIR, namespace, digest + ".json")


def cache_get(namespace, key):
    # Return the cached payload, or None if we have never stored this key.
    path = _path(namespace, key)
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)["payload"]
    except Exception:
        # A truncated or hand-edited cache file must not crash a scan.
        logging.warning("Ignoring unreadable cache file %s", path)
        return None


def cache_put(namespace, key, payload):
    # Store one successful response. Callers must not call this for failures.
    path = _path(namespace, key)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"key": key, "payload": payload}, f, ensure_ascii=False)
    # Atomic replace, so an interrupted write can never leave a half-written
    # file that a later run would read as real data.
    os.replace(tmp, path)


def fetch_json(namespace, key, url, params=None, headers=None, timeout=10,
               strict=False, retries=3):
    # Cache-first JSON GET.
    #
    # namespace groups related keys ("google_query", "ol_edition", ...).
    # key is what identifies this lookup - a volume id, an ISBN, or the exact
    # query string. Anything derived from the key must be in the key.
    #
    # Returns the decoded JSON, or None when the request failed and
    # strict=False. Raises FetchFailed when it failed and strict=True.
    cached = cache_get(namespace, key)
    if cached is not None:
        return cached

    if demo_offline():
        # No network in demo mode, ever. Say exactly what is missing so the
        # cache can be pre-warmed for it.
        raise OfflineCacheMiss(
            f"DEMO_OFFLINE=1 and nothing cached for {namespace}:{key!r}. "
            f"Pre-warm it with test_covers/prewarm_demo_cache.py, or unset "
            f"DEMO_OFFLINE to go online.")

    # Google Books throws 503s in bursts. A single transient one must not end
    # a 100-book build, so 5xx is RETRIED with growing backoff before it is
    # treated as a real failure. 429 is the daily quota and is never retried -
    # it will not recover in seconds. This is the same policy _fetch_google_query
    # uses; the difference is only what happens after the retries run out.
    response = None
    last_error = None
    for attempt in range(max(1, retries)):
        try:
            response = requests.get(url, params=params, headers=headers,
                                    timeout=timeout)
            if response.status_code < 500 or response.status_code == 429:
                break
            last_error = f"HTTP {response.status_code}"
            response = None
        except Exception as exc:
            last_error = str(exc)
            response = None
        if attempt < retries - 1:
            time.sleep(1.5 * (attempt + 1))

    if response is None:
        if strict:
            raise FetchFailed(f"{namespace}:{key!r} - {last_error}")
        return None

    # THE RULE. Anything that is not a clean 200 is a FAILURE, and a failure
    # is never written to disk. Note what is deliberately absent here: there
    # is no branch that caches [] or {} on a non-200.
    if response.status_code != 200:
        if strict:
            raise FetchFailed(
                f"{namespace}:{key!r} - HTTP {response.status_code}")
        return None

    try:
        payload = response.json()
    except Exception as exc:
        if strict:
            raise FetchFailed(f"{namespace}:{key!r} - bad JSON") from exc
        return None

    cache_put(namespace, key, payload)
    return payload
