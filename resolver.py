"""DOI resolution via API resolvers + URL rendering for search engines.

In v1.1+, the Crossref-specific code is just *one* possible
``doi_resolver`` engine.  The URL-side has been generalised: we
iterate through the user-configured engine list, building URLs from
URL templates, and pick the first one that's appropriate for what
we have (DOI or just a text query).

Public API
----------
* :func:`resolve_doi` — text -> DOI, using enabled ``doi_resolver``
  engines in order.  Returns the first confident hit, or None.
* :func:`build_doi_url` — pick the first enabled ``doi_url`` engine
  and render its URL for *doi*.  Returns None if no enabled engine
  can handle a DOI.
* :func:`build_search_url` — pick the first enabled ``search_url``
  engine and render its URL.  Returns a fallback URL built from the
  fields if nothing else is configured.
* :func:`doi_url` — convenience: doi -> canonical ``https://doi.org/<doi>``.

The Crossref-specific helpers (``_normalise_title``,
``_best_title_match``, ``_user_agent``) are kept for testing.
"""

from __future__ import annotations

import difflib
import re
import urllib.parse
from typing import Any, Iterable, List, Optional

import requests

from .engines import (
    Engine,
    STAGE_DOI_RESOLVER,
    STAGE_DOI_URL,
    STAGE_SEARCH_URL,
    by_stage,
    get_path,
)

# Kept for backward-compat with the v1.0 tests / public API.
CROSSREF_API = "https://api.crossref.org/works"
DEFAULT_MAILTO = "syz@shiyangzheng.top"
USER_AGENT = "citationHop/1.1 (mailto:{mailto})"
SIMILARITY_THRESHOLD = 0.85

_WS_RE = re.compile(r"\s+")


# ---------------------------------------------------------------------------
# Crossref helpers (kept for tests + reused by the Crossref engine)
# ---------------------------------------------------------------------------

def _normalise_title(t: str) -> str:
    return _WS_RE.sub(" ", t).strip().lower()


def _best_title_match(query_text: str, candidate_title: str) -> float:
    """Return a 0..1 similarity between *query_text* and the candidate
    title.  Substring containment scores 1.0; otherwise fall back to
    SequenceMatcher ratio."""
    q = _normalise_title(query_text)
    c = _normalise_title(candidate_title)
    if not q or not c:
        return 0.0
    if c in q or q in c:
        return 1.0
    return difflib.SequenceMatcher(None, q, c).ratio()


def _user_agent(mailto: Optional[str]) -> str:
    return USER_AGENT.format(mailto=(mailto or DEFAULT_MAILTO))


# ---------------------------------------------------------------------------
# DOI resolution (uses doi_resolver engines)
# ---------------------------------------------------------------------------

def _resolve_via_engine(
    engine: Engine,
    text: str,
    *,
    mailto: Optional[str] = None,
    timeout: float = 8.0,
    session: Optional[requests.Session] = None,
) -> Optional[str]:
    """Run a single doi_resolver engine.  Returns a DOI string or None.

    The engine is expected to expose:
      - ``api_url`` (template, supports {query} {mailto})
      - ``api_params`` (dict of str -> str; values are templates too)
      - ``api_doi_path`` (dotted JSON path to the DOI in the response)
      - ``api_title_path`` (dotted JSON path to a candidate title)

    We score each item by title similarity (substring -> 1.0,
    otherwise SequenceMatcher) and accept the best match over
    ``SIMILARITY_THRESHOLD``.
    """
    if not engine.is_doi_resolver():
        return None
    if not engine.api_url or not engine.api_doi_path:
        return None

    sess = session or requests.Session()
    url = engine.api_url.format(query=text, mailto=mailto or "")
    params = {
        k: v.format(query=text, mailto=mailto or "")
        for k, v in (engine.api_params or {}).items()
    }
    headers = {
        "User-Agent": _user_agent(mailto),
        "Accept": "application/json",
    }

    try:
        resp = sess.get(url, params=params, headers=headers, timeout=timeout)
        resp.raise_for_status()
    except requests.RequestException:
        return None

    try:
        payload = resp.json()
    except ValueError:
        return None

    # Crossref-style: enumerate items[0..N] and find the best by title.
    # For a generic engine, we do the same: look at siblings of the
    # configured doi-path and pick the best one.
    items_root = get_path(payload, "message.items")
    if not isinstance(items_root, list):
        return None

    best_score = 0.0
    best_doi: Optional[str] = None
    doi_tail = engine.api_doi_path.split(".")[-1]

    for item in items_root:
        if not isinstance(item, dict):
            continue
        candidate_doi = item.get(doi_tail)
        if not candidate_doi:
            continue

        title = item.get("title")
        if isinstance(title, list):
            title = title[0] if title else ""
        if not isinstance(title, str) or not title:
            continue
        score = _best_title_match(text, title)
        if score > best_score:
            best_score = score
            best_doi = candidate_doi

    if best_doi and best_score >= SIMILARITY_THRESHOLD:
        return best_doi
    return None


def resolve_doi(
    text: str,
    engines: Iterable[Engine],
    *,
    mailto: Optional[str] = None,
    timeout: float = 8.0,
    session: Optional[requests.Session] = None,
) -> Optional[str]:
    """Try the enabled ``doi_resolver`` engines in order; return the
    first confident DOI, or None.

    The previous signature ``resolve_via_crossref(text, mailto=...)`` is
    kept as a thin shim that picks the first Crossref engine (or the
    first resolver if no Crossref exists).
    """
    for engine in by_stage(engines, STAGE_DOI_RESOLVER):
        doi = _resolve_via_engine(
            engine, text, mailto=mailto, timeout=timeout, session=session
        )
        if doi:
            return doi
    return None


# Backward-compat shim: pre-v1.1 tests + callers.
def resolve_via_crossref(
    text: str,
    *,
    mailto: Optional[str] = None,
    timeout: float = 8.0,
    session: Optional[requests.Session] = None,
) -> Optional[str]:
    """Deprecated: use ``resolve_doi(text, engines)`` instead.

    Kept for backward compatibility with the v1.0 test suite and
    any external callers.  Builds a minimal engine list containing
    only the built-in Crossref resolver.
    """
    from .engines import default_engines

    return resolve_doi(
        text,
        default_engines(),
        mailto=mailto,
        timeout=timeout,
        session=session,
    )


# ---------------------------------------------------------------------------
# URL building (uses url_template engines)
# ---------------------------------------------------------------------------

def build_doi_url(
    doi: str,
    engines: Iterable[Engine],
    *,
    mailto: Optional[str] = None,
) -> Optional[str]:
    """Pick the first enabled ``doi_url`` engine and render its URL."""
    for engine in by_stage(engines, STAGE_DOI_URL):
        try:
            return engine.render(doi=doi, query="", fields={}, mailto=mailto)
        except ValueError:
            continue
    return None


def build_search_url(
    text: str,
    fields: dict,
    engines: Iterable[Engine],
    *,
    mailto: Optional[str] = None,
) -> Optional[str]:
    """Pick the first enabled ``search_url`` engine and render its URL."""
    for engine in by_stage(engines, STAGE_SEARCH_URL):
        try:
            return engine.render(doi=None, query=text, fields=fields, mailto=mailto)
        except ValueError:
            continue
    return None


def doi_url(doi: str) -> str:
    """Return the canonical resolver URL for *doi* (``https://doi.org/<doi>``)."""
    return "https://doi.org/" + urllib.parse.quote(doi, safe="/")


__all__ = [
    "resolve_doi",
    "resolve_via_crossref",
    "build_doi_url",
    "build_search_url",
    "doi_url",
    "SIMILARITY_THRESHOLD",
    "CROSSREF_API",
]
