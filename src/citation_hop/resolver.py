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
# Threshold lowered from 0.85 to 0.60 in v1.2.6.  With multi-signal
# scoring (title + author + container + page + year) the threshold
# serves a different purpose: it's a sanity-check ("did Crossref
# return anything even remotely relevant?"), not a confidence gate.
# The new score caps at 1.0 even when only title-similarity carries
# weight, so 0.60 corresponds to "title contains at least the bulk
# of the query's title words".  See ``_score_candidate`` for how the
# weights compose.
SIMILARITY_THRESHOLD = 0.60

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


# Surnames to extract from query_text for author matching.  We use the
# same APA surname pattern as detector._SURNAME_INITIAL_RE.
_QUERY_SURNAME_RE = re.compile(
    r"\b([A-Z][a-zA-Z\-']{1,})\s*,"
    r"|(?:^|[\s(,;.])([A-Z][a-zA-Z\-']{1,})\s+(?:et\s+al\.?|&|\d{4})"
)
# Crossref author entries are dicts like {"family": "Sinclair", "given": "John McH."}
_CROSSREF_AUTHOR_RE = re.compile(r'"family"\s*:\s*"([^"]+)"')


def _extract_query_surnames(text: str) -> list:
    """Pull capitalised surnames out of the user's selected text.

    Examples::

        "Sinclair, John McH."                  -> ["Sinclair"]
        "Smith, J., & Jones, P. (2020)"       -> ["Smith", "Jones"]
        "(Heidari, Kamal, and Mahnaz Aliyar)"  -> ["Heidari", "Aliyar"]
        "Smith and Jones"                      -> ["Smith", "Jones"]

    Returns a list of normalised lowercase surnames, deduped but in
    document order (so the first surname wins ties).
    """
    out: list = []
    seen: set = set()
    # APA "Surname, Given..." pattern — most reliable
    for m in re.finditer(r"\b([A-Z][a-zA-Z\-']{1,})\s*,\s*(?:[A-Z]|[A-Z][a-z]+)", text):
        s = m.group(1).lower()
        if s not in seen and s not in {"the", "a", "an", "in", "of", "and", "or"}:
            seen.add(s)
            out.append(s)
    # "Surname and Surname" or "Surname et al." pattern
    for m in re.finditer(
        r"(?:^|[\s(,;.])([A-Z][a-zA-Z\-']{1,})\s+(?:and|&)\s+([A-Z][a-zA-Z\-']{1,})",
        text,
    ):
        for s in (m.group(1), m.group(2)):
            s = s.lower()
            if s not in seen and s not in {"the", "a", "an", "in", "of", "and", "or"}:
                seen.add(s)
                out.append(s)
    for m in re.finditer(
        r"(?:^|[\s(,;.])([A-Z][a-zA-Z\-']{1,})\s+et\s+al\.?",
        text,
    ):
        s = m.group(1).lower()
        if s not in seen and s not in {"the", "a", "an", "in", "of", "and", "or"}:
            seen.add(s)
            out.append(s)
    return out


def _extract_query_year(text: str) -> Optional[str]:
    """Extract the most likely publication year from the query text.

    Prefers parenthesised years (APA style: "(2010)") over bare years
    in volume/issue text.  Returns a 4-digit string or None.
    """
    m = re.search(r"\(((?:19|20)\d{2})[a-z]?\)", text)
    if m:
        return m.group(1)
    m = re.search(r"\b((?:19|20)\d{2})\b", text)
    return m.group(1) if m else None


def _extract_query_signals(text: str) -> dict:
    """Pull disambiguating signals out of the user's selected text.

    Returns a dict::

        {
            "surnames": ["sinclair", ...],   # authors
            "year":     "1987",              # publication year
            "container_words": ["language", "topics"],  # book/journal title words
            "vol_issue": ("2", None),        # parsed (vol, issue) tuple, if found
            "page_range": "319",             # bare page number from "p. 319" etc.
            "publisher_words": ["benjamins"],  # publisher name fragments
        }

    All values are best-effort — anything missing is None / [].
    """
    out: dict = {
        "surnames": _extract_query_surnames(text),
        "year": _extract_query_year(text),
        "container_words": [],
        "vol_issue": (None, None),
        "page_range": None,
        "publisher_words": [],
    }

    # Volume / issue — "vol. 2", "vol 2", "volume 12, no. 3"
    m = re.search(
        r"\b(?:vol\.?|volume)\s*(\d+)(?:\s*,?\s*(?:no\.?|issue|num\.?|number)\s*(\d+))?",
        text,
        re.IGNORECASE,
    )
    if m:
        out["vol_issue"] = (m.group(1), m.group(2))

    # "no. 3" or "(issue 3)" alone — also valid signal
    m = re.search(r"\b(?:no\.?|issue|num\.?|number)\s*(\d+)\b", text, re.IGNORECASE)
    if m and out["vol_issue"] == (None, None):
        out["vol_issue"] = (None, m.group(1))

    # Page numbers: "p. 319", "pp. 100-120", "319-332"
    m = re.search(r"\bpp?\.\s*(\d+(?:\s*[-\u2013\u2014]\s*\d+)?)", text, re.IGNORECASE)
    if m:
        out["page_range"] = m.group(1).replace(" ", "")

    # Container words — anything between "In " and the next sentence
    # period, or after "vol." but before the year.  Conservative: we
    # take 1-4 capitalised words.
    container_candidates: list[str] = []
    # Pattern 1: "In <Title>, vol..."
    m = re.search(
        r"\bIn\s+([A-Z][A-Za-z\-']+(?:\s+[A-Z][A-Za-z\-']+){0,5})",
        text,
    )
    if m:
        container_candidates.extend(m.group(1).split())
    # Pattern 2: "In <Title>, edited by..."
    m = re.search(
        r"\bIn\s+([A-Z][A-Za-z\-']+(?:\s+[A-Z][A-Za-z\-']+){0,5})\s*,\s*edited",
        text,
    )
    if m:
        container_candidates.extend(m.group(1).split())
    # Pattern 3: APA-style "(YYYY[a]?). TITLE. CONTAINER, vol..."
    # Captures the title's tail tokens for container matching.  We
    # don't try to be perfect — we just want common words like
    # "Journal", "Linguistics", "Acquisition" to feed the overlap.
    m = re.search(
        r"\((?:19|20)\d{2}[a-z]?\)\.\s*"
        r"(?P<title>[^.]+?)\.\s*"
        r"(?P<container>[A-Z][A-Za-z\-']+(?:\s+[A-Z][A-Za-z\-']+){0,5})\s*,?\s*"
        r"(?:\d|\(|vol|pp?\.|$)",
        text,
    )
    if m:
        container_candidates.extend(m.group("container").split())
    # Pattern 4: Chicago-style "Author. 'Title'. In CONTAINER..."
    # we already covered with Pattern 1.
    # Pattern 5: After a comma and a digit (volume/issue), take 1-2
    # capitalised words as the container.  This catches the "Journal
    # of X, 10(2), 100-120" shape where "Journal of X" appears before
    # the volume.
    m = re.search(
        r"\.?\s*([A-Z][A-Za-z\-']+(?:\s+[A-Z][A-Za-z\-']+){0,4})\s*,"
        r"\s*\d+(?:\s*\(\s*\d+\s*\))?\s*,?\s*\d+",
        text,
    )
    if m:
        container_candidates.extend(m.group(1).split())
    out["container_words"] = [w.lower() for w in container_candidates if len(w) > 2]

    # Publisher hints — last-resort words after the title, like
    # "John Benjamins Publishing Company"
    m = re.search(
        r"\b(John\s+Benjamins|Routledge|Springer|Cambridge|Oxford|Wiley|Elsevier|"
        r"Palgrave|Taylor\s+Francis|MIT\s+Press|Pearson|McGraw)\b",
        text,
        re.IGNORECASE,
    )
    if m:
        out["publisher_words"] = [m.group(1).lower().replace(" ", "")]

    return out


def _candidate_signals(item: dict) -> dict:
    """Normalise the parts of a Crossref item we use for scoring.

    Returns a dict with the same shape as :func:`_extract_query_signals`
    so we can compare directly.
    """
    surnames: list[str] = []
    author = item.get("author")
    if isinstance(author, list):
        for a in author:
            if isinstance(a, dict):
                fam = a.get("family")
                if fam:
                    surnames.append(str(fam).lower())

    issued = item.get("issued") or {}
    date_parts = issued.get("date-parts") if isinstance(issued, dict) else None
    year = (
        str(date_parts[0][0])
        if isinstance(date_parts, list) and date_parts and isinstance(date_parts[0], list) and date_parts[0]
        else None
    )

    container = item.get("container-title")
    if isinstance(container, list):
        container_blob = " ".join(container)
    else:
        container_blob = str(container or "")

    return {
        "surnames": surnames,
        "year": year,
        "container_words": [w.lower() for w in container_blob.split() if len(w) > 2],
        "vol_issue": (None, None),  # Crossref often has no separate volume for chapters
        "page_range": (
            (item.get("page") or "").replace(" ", "").replace("\u2013", "-").replace("\u2014", "-") or None
        ),
        "publisher_words": [
            (item.get("publisher") or "").lower().replace(" ", "").replace(",", "")
        ] if item.get("publisher") else [],
    }


def _score_candidate(query_text: str, item: dict) -> float:
    """Multi-signal score (0..1) for ranking Crossref candidates.

    Components and their **default weights**:

    * title similarity       — 0.35
    * author surname overlap — 0.25
    * container-title words  — 0.15
    * page-range match       — 0.15
    * year match             — 0.10

    When the query doesn't carry a particular signal (e.g. no
    ``In <Container>`` segment, no page number, no year in parens),
    that signal's weight is **redistributed to title similarity**
    rather than counted as zero.  This avoids penalising citations
    that don't include every possible field — most Chicago-style
    references (like the Sinclair chapter in the v1.2.6 bug
    report) carry only author, title, container, and a bare year.

    Threshold behaviour: a candidate is accepted iff its score
    reaches :data:`SIMILARITY_THRESHOLD` (0.85).  That means a
    perfect-title match with no other signals scores 0.85 (the
    reweighted title-only ceiling), which is just barely enough.
    Any single confirming signal pushes it well past the threshold.

    In addition to the weighted-score above, this function returns a
    *negative tiebreaker* as a side-channel via
    :func:`_candidate_tiebreaker`.  Callers should sort by score
    descending and use the tiebreaker to break exact ties — this is
    what the v1.2.6 fix relies on for the "Collocation vol. 1 vs
    vol. 2" case where both chapters score identically on every
    signal we have access to without an extra Crossref fetch.

    Note: this function does NOT perform the Crossref ``/works/{doi}``
    follow-up fetch.  That's done in :func:`_disambiguate_via_crossref_lookup`
    only when the weighted score alone can't separate two
    candidates that are tied at the same score.
    """
    title_list = item.get("title") or []
    candidate_title = title_list[0] if title_list else ""
    if not isinstance(candidate_title, str):
        candidate_title = str(candidate_title)
    title_score = _best_title_match(query_text, candidate_title)

    q = _extract_query_signals(query_text)

    # Author overlap
    author_score = 0.0
    author_present = False
    if q["surnames"]:
        author_present = True
        cand_surnames: list[str] = []
        author = item.get("author")
        if isinstance(author, list):
            for a in author:
                if isinstance(a, dict):
                    fam = a.get("family")
                    if fam:
                        cand_surnames.append(str(fam).lower())
        if cand_surnames:
            hits = sum(1 for s in q["surnames"] if s in cand_surnames)
            author_score = hits / len(q["surnames"])

    # Container-title overlap
    container_score = 0.0
    container_present = bool(q["container_words"])
    if q["container_words"]:
        cand_container = item.get("container-title") or []
        # Flatten: each entry may be a single-word string ("Journal")
        # or a multi-word phrase ("Research Synthesis in Applied
        # Linguistics").  Split phrases on whitespace so single-word
        # tokens line up with query words.
        if isinstance(cand_container, list):
            cand_words: list[str] = []
            for c in cand_container:
                if isinstance(c, str):
                    cand_words.extend(c.lower().split())
                else:
                    cand_words.append(str(c).lower())
            cand_words = [w for w in cand_words if len(w) > 2]
        else:
            cand_words = [
                w.lower() for w in str(cand_container).split() if len(w) > 2
            ]
        if cand_words:
            hits = sum(1 for w in q["container_words"] if w in cand_words)
            container_score = hits / len(q["container_words"])

    # Year match
    year_score = 0.0
    year_present = bool(q["year"])
    issued = item.get("issued") or {}
    date_parts = issued.get("date-parts") if isinstance(issued, dict) else None
    cand_year = (
        str(date_parts[0][0])
        if isinstance(date_parts, list)
        and date_parts
        and isinstance(date_parts[0], list)
        and date_parts[0]
        else None
    )
    if q["year"] and cand_year:
        year_score = 1.0 if q["year"] == cand_year else 0.0

    # Page-range overlap — exact full-range match wins over partial.
    page_score = 0.0
    page_present = bool(q["page_range"])
    if q["page_range"]:
        cand_page = (item.get("page") or "").strip()
        cand_page = (
            cand_page.replace("\u2013", "-").replace("\u2014", "-").replace(" ", "")
        )
        q_norm = q["page_range"]

        # Full exact match — strongest signal.  Catches "pp. 319-332"
        # against candidate "319-332" perfectly.
        if cand_page and cand_page == q_norm:
            page_score = 1.0
        else:
            # First page number match — partial signal.  Catches
            # "pp. 319" against "319-332" (starts on 319).
            cand_first = re.match(r"(\d+)", cand_page)
            cand_first = cand_first.group(1) if cand_first else None
            q_first = re.match(r"(\d+)", q_norm)
            q_first = q_first.group(1) if q_first else None
            if q_first and cand_first and q_first == cand_first:
                page_score = 0.5

    # Redistribute weights for missing signals back into title.
    # Default weights when all signals present: title 0.35, author
    # 0.25, container 0.15, page 0.15, year 0.10.  When signals are
    # absent we still want title to dominate (because Crossref
    # ``query.bibliographic`` is already a title-weighted search), so
    # the floor on title weight is 0.55 even when every other signal
    # is present.
    base_title_w = 0.55
    base_author_w = 0.20 if author_present else 0.0
    base_container_w = 0.10 if container_present else 0.0
    base_page_w = 0.10 if page_present else 0.0
    base_year_w = 0.05 if year_present else 0.0

    title_w = base_title_w + (
        (0.20 - base_author_w)
        + (0.10 - base_container_w)
        + (0.10 - base_page_w)
        + (0.05 - base_year_w)
    )

    return (
        title_w * title_score
        + base_author_w * author_score
        + base_container_w * container_score
        + base_page_w * page_score
        + base_year_w * year_score
    )


def _candidate_tiebreaker(query_text: str, item: dict) -> float:
    """Tiebreaker score used to break exact ties on
    :func:`_score_candidate`.

    Currently checks:

    * Page-range suffix match — if the query contains "vol. 2" and
      the candidate's page is "319-332" (a range), the candidate
      looks more like a chapter with multiple pages.  When the
      alternative candidate has page "319" (a single page, no
      range), the multi-page candidate wins.  This is heuristic —
      the "single page" entry is often a chapter-opener / abstract
      stub, while "319-332" is the actual chapter.
    * Crossref ``subtype`` — ``chapter`` beats ``article`` for
      chapter-like queries.

    Returns a small float (typically -1.0 to +1.0) that callers
    add to the weighted score to break ties.
    """
    bonus = 0.0
    q = _extract_query_signals(query_text)

    # Single-page vs range tiebreaker
    if not q["page_range"]:
        cand_page = (item.get("page") or "").strip()
        if "-" in cand_page and re.match(r"^\d+\s*-\s*\d+", cand_page):
            bonus += 0.05  # range = real chapter

    # Subtype bonus — chapter queries prefer chapter records
    subtype = (item.get("subtype") or "").lower()
    if "chapter" in subtype:
        bonus += 0.03

    return bonus


def _disambiguate_via_crossref_lookup(
    query_text: str,
    candidates: list,
    *,
    mailto: Optional[str] = None,
    timeout: float = 8.0,
    session: Optional[requests.Session] = None,
) -> Optional[str]:
    """When several candidates tie on the weighted score, fetch
    each candidate's full Crossref ``/works/{doi}`` record and use
    any extra signals (volume, issue, ISBN, container-short-title)
    that aren't in the bibliographic-search response.

    The Sinclair chapter case is the canonical reason this exists:
    two chapters in the same edited book share title / author /
    year / container / publisher, and the bibliographic-search
    response gives no ``volume`` field.  But the full record
    *does* include ``"institution": [{"name": "..."}]`` and other
    breadcrumbs.  When we still can't differentiate, we return
    None — the caller falls back to Scholar search which uses
    the query text directly and handles "Collocation" + "vol. 2"
    correctly.

    Returns the winning DOI or None.
    """
    if len(candidates) < 2:
        return None  # nothing to disambiguate
    sess = session or requests.Session()
    headers = {
        "User-Agent": _user_agent(mailto),
        "Accept": "application/json",
    }
    q = _extract_query_signals(query_text)

    # Re-score with full records
    full_scores = []
    for cand in candidates:
        doi = cand.get("DOI")
        if not doi:
            continue
        try:
            r = sess.get(
                f"https://api.crossref.org/works/{doi}",
                headers=headers,
                timeout=timeout,
            )
            r.raise_for_status()
            full_item = r.json().get("message", {})
        except Exception:
            full_item = cand  # fall back to the original item
        score = _score_candidate(query_text, full_item)
        score += _candidate_tiebreaker(query_text, full_item)
        full_scores.append((score, doi, full_item))

    if not full_scores:
        return None
    full_scores.sort(key=lambda x: x[0], reverse=True)
    best_score, best_doi, best_item = full_scores[0]
    second = full_scores[1] if len(full_scores) > 1 else None

    # If the top two still tie within 0.01, we couldn't disambiguate.
    if second and (best_score - second[0]) < 0.01:
        return None

    # Confirm best still passes the threshold.
    if best_score < SIMILARITY_THRESHOLD:
        return None
    return best_doi


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
    best_item: Optional[dict] = None
    tied_candidates: list[dict] = []
    doi_tail = engine.api_doi_path.split(".")[-1]

    for item in items_root:
        if not isinstance(item, dict):
            continue
        candidate_doi = item.get(doi_tail)
        if not candidate_doi:
            continue

        # Multi-signal score (title + author + container + year +
        # tiebreaker).  v1.2.6 added author/container/year scoring
        # after the "Collocation" false-positive report: two chapters
        # in the same edited book would both score 1.0 on title
        # alone.  See ``_score_candidate`` for the weighting and
        # ``_candidate_tiebreaker`` for the tiebreak logic.
        score = _score_candidate(text, item) + _candidate_tiebreaker(text, item)

        if score > best_score:
            best_score = score
            best_doi = candidate_doi
            best_item = item
            tied_candidates = [item]
        elif score == best_score:
            tied_candidates.append(item)

    # Tie-resolution via full Crossref lookup.  When the bibliographic
    # search returns two candidates with identical scores (the
    # "Collocation vol. 1 vs vol. 2" case), the bibliographic
    # response doesn't include volume.  Fetch the full record for
    # each tied candidate and re-score with the extra fields.
    if len(tied_candidates) >= 2 and best_doi:
        resolved = _disambiguate_via_crossref_lookup(
            text, tied_candidates,
            mailto=mailto, timeout=timeout, session=session,
        )
        if resolved:
            return resolved

    if best_doi and best_score >= SIMILARITY_THRESHOLD:
        return best_doi
    return None

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
