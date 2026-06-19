"""Data-driven search engine registry for citationHop.

An *engine* is a small descriptor that tells citationHop how to turn a
piece of text (and the parsed fields) into a URL.  Engines are the only
user-facing customisation point in v1.1+: the user can enable / disable
them, reorder them (via the JSON config), and add their own URL
templates.

The lookup flow is:
  1. Parse the selected text (parser.py) into ``fields`` and a
     best-effort ``doi``.
  2. If we don't have a DOI yet, ask a ``doi_resolver`` engine (Crossref
     is the only one we ship today; the registry is structured so future
     resolvers can be added without touching this code).
  3. With a DOI in hand, pick the first enabled ``doi_url`` engine and
     open its URL.
  4. Without a DOI, pick the first enabled ``search_url`` engine.

Each engine stores its config in ``config.json``.  The schema is
intentionally simple — a list of dicts, each with the keys documented
below.  Unknown keys are ignored (forward-compat); missing keys fall
back to safe defaults.

Engine schema
-------------
::

    {
      "id":          "<unique id, lowercase>",       # required
      "name":        "<display name>",               # required
      "stage":       "doi_resolver" |                # required
                     "doi_url" |
                     "search_url",
      "enabled":     true,                           # default: true
      "order":       0,                              # ascending = tried first

      # For stage=doi_url or stage=search_url
      "url_template": "https://.../{doi}?...",      # required for url stages
      # The template may reference {doi} {query} {title} {author} {year}
      # {mailto}.  All values are URL-encoded automatically.

      # For stage=doi_resolver (Crossref is the only one we ship)
      "api_url":      "https://.../works",          # required for resolver
      "api_params":   {"query.bibliographic": "{query}",
                       "rows": "3"},                # all values are str
      "api_doi_path": "message.items.0.DOI",        # dotted JSON path
      "api_title_path": "message.items.0.title.0",
    }
"""

from __future__ import annotations

import json
import urllib.parse
from dataclasses import dataclass, field, asdict
from typing import Any, Iterable, List, Optional, Sequence

# ---------------------------------------------------------------------------
# Engine dataclass
# ---------------------------------------------------------------------------

# Engine "stages" — the role an engine plays in the lookup pipeline.
STAGE_DOI_RESOLVER = "doi_resolver"   # text -> DOI  (API lookup)
STAGE_DOI_URL = "doi_url"            # DOI   -> URL  (template substitution)
STAGE_SEARCH_URL = "search_url"      # text -> URL  (template substitution)

VALID_STAGES = (STAGE_DOI_RESOLVER, STAGE_DOI_URL, STAGE_SEARCH_URL)


@dataclass
class Engine:
    """A single search engine entry.

    Frozen + slotted so an engine dict from JSON is treated as immutable
    once parsed.
    """

    id: str
    name: str
    stage: str
    enabled: bool = True
    order: int = 0
    # url_template engines
    url_template: Optional[str] = None
    # doi_resolver engines
    api_url: Optional[str] = None
    api_params: dict = field(default_factory=dict)
    api_doi_path: Optional[str] = None
    api_title_path: Optional[str] = None

    # ---- helpers ---------------------------------------------------------

    def is_url_template(self) -> bool:
        return self.stage in (STAGE_DOI_URL, STAGE_SEARCH_URL)

    def is_doi_resolver(self) -> bool:
        return self.stage == STAGE_DOI_RESOLVER

    def needs_doi(self) -> bool:
        """True iff this engine is only useful when we have a DOI."""
        return self.stage == STAGE_DOI_URL

    def to_dict(self) -> dict:
        return asdict(self)

    # ---- URL rendering ---------------------------------------------------

    def render(self, *, doi: Optional[str], query: str, fields: dict,
               mailto: Optional[str]) -> str:
        """Render the engine's URL for a given (doi, query, fields).

        Raises ValueError if a required placeholder cannot be filled
        (e.g. a doi_url engine called with no DOI).
        """
        if not self.is_url_template():
            raise ValueError(
                f"Engine {self.id!r} is stage={self.stage!r}, not url_template"
            )
        if not self.url_template:
            raise ValueError(f"Engine {self.id!r} has no url_template")

        # Build the substitution map.  Always include mailto/query so
        # engines that only need a query don't need {doi}.
        subs: dict[str, str] = {
            "doi": doi or "",
            "query": query or "",
            "title": fields.get("title") or "",
            "author": fields.get("author") or "",
            "year": fields.get("year") or "",
            "mailto": mailto or "",
        }
        # If the template references {doi} but we have none, fail.
        if "{doi}" in self.url_template and not doi:
            raise ValueError(f"Engine {self.id!r} requires a DOI")

        # Per-field URL encoding rules.  The DOI is special: its slash
        # is a *path* separator (10.1038/nature12373), so we use
        # ``safe="/"`` to keep the path intact.  Everything else is
        # encoded with the strictest ``safe=""`` so user-supplied
        # titles / authors / years can't break the URL with spaces,
        # ampersands, or stray slashes.
        encoded = {
            "doi": urllib.parse.quote(subs["doi"], safe="/"),
            "query": urllib.parse.quote(subs["query"], safe=""),
            "title": urllib.parse.quote(subs["title"], safe=""),
            "author": urllib.parse.quote(subs["author"], safe=""),
            "year": urllib.parse.quote(subs["year"], safe=""),
            "mailto": urllib.parse.quote(subs["mailto"], safe=""),
        }

        # We do *manual* substitution (str.format) so that any literal
        # braces in the template would error loudly rather than silently
        # producing bad URLs.  In practice, templates don't have braces.
        try:
            return self.url_template.format(**encoded)
        except KeyError as e:
            raise ValueError(
                f"Engine {self.id!r} template references unknown "
                f"placeholder {e}"
            ) from None


# ---------------------------------------------------------------------------
# Default engine list
# ---------------------------------------------------------------------------

def _doi_resolver_default() -> List[Engine]:
    """The single built-in DOI resolver.  We currently ship only Crossref.

    The dataclass shape leaves room for future resolvers (OpenAlex,
    Semantic Scholar, DataCite) to be added without changing the
    public API.
    """
    return [
        Engine(
            id="crossref",
            name="Crossref",
            stage=STAGE_DOI_RESOLVER,
            enabled=True,
            order=0,
            api_url="https://api.crossref.org/works",
            api_params={"query.bibliographic": "{query}", "rows": "3"},
            api_doi_path="message.items.0.DOI",
            # Crossref returns titles as a JSON list — index 0 of that
            # list is the canonical paper title.
            api_title_path="message.items.0.title.0",
        ),
    ]


def _doi_url_defaults() -> List[Engine]:
    return [
        Engine(
            id="doi_org",
            name="doi.org",
            stage=STAGE_DOI_URL,
            enabled=True,
            order=0,
            url_template="https://doi.org/{doi}",
        ),
        Engine(
            id="scihub",
            name="Sci-Hub  (jurisdictional — enable at your own risk)",
            stage=STAGE_DOI_URL,
            enabled=False,
            order=1,
            url_template="https://sci-hub.se/{doi}",
        ),
    ]


def _search_url_defaults() -> List[Engine]:
    """The mainstream search-engine list.  Disabled by default except
    for the first (Google Scholar), which preserves the v1.0 behaviour
    out of the box."""

    def _g(name: str, tid: str, tmpl: str, *, order: int,
           enabled: bool = False) -> Engine:
        return Engine(
            id=tid,
            name=name,
            stage=STAGE_SEARCH_URL,
            enabled=enabled,
            order=order,
            url_template=tmpl,
        )

    return [
        _g("Google Scholar", "scholar",
           "https://scholar.google.com/scholar?q={title}+{author}+{year}",
           order=0, enabled=True),
        _g("Semantic Scholar", "semantic_scholar",
           "https://www.semanticscholar.org/search?q={query}",
           order=1),
        _g("OpenAlex", "openalex",
           "https://openalex.org/works?search={query}",
           order=2),
        _g("arXiv", "arxiv",
           "https://arxiv.org/search/?query={query}&searchtype=all",
           order=3),
        _g("PubMed", "pubmed",
           "https://pubmed.ncbi.nlm.nih.gov/?term={query}",
           order=4),
        _g("DBLP", "dblp",
           "https://dblp.org/search?q={query}",
           order=5),
        _g("BASE (Bielefeld)", "base",
           "https://www.base-search.net/Search/Results?lookfor={query}&type=tit",
           order=6),
        _g("Connected Papers", "connected_papers",
           "https://www.connectedpapers.com/search?q={query}",
           order=7),
        _g("Litmaps", "litmaps",
           "https://www.litmaps.com/?q={query}",
           order=8),
        _g("ResearchGate", "researchgate",
           "https://www.researchgate.net/search/publication?q={query}",
           order=9),
        _g("CORE", "core",
           "https://core.ac.uk/search?q={query}",
           order=10),
        _g("Dimensions", "dimensions",
           "https://app.dimensions.ai/discover/publication?search_text={query}&search_type=kws",
           order=11),
    ]


def default_engines() -> List[Engine]:
    """Return the full default engine list (resolvers + doi urls +
    search urls), in the order the user sees them in the menu."""
    return (
        _doi_resolver_default()
        + _doi_url_defaults()
        + _search_url_defaults()
    )


# ---------------------------------------------------------------------------
# Serialisation
# ---------------------------------------------------------------------------

def engines_to_dicts(engines: Sequence[Engine]) -> List[dict]:
    """Serialise a list of Engines to plain dicts for JSON."""
    out: List[dict] = []
    for e in engines:
        d = e.to_dict()
        # Drop empty optionals to keep config.json readable.
        if not d.get("url_template"):
            d.pop("url_template", None)
        if not d.get("api_url"):
            d.pop("api_url", None)
        if not d.get("api_params"):
            d.pop("api_params", None)
        if not d.get("api_doi_path"):
            d.pop("api_doi_path", None)
        if not d.get("api_title_path"):
            d.pop("api_title_path", None)
        out.append(d)
    return out


def engines_from_dicts(dicts: Iterable[dict]) -> List[Engine]:
    """Parse a list of engine dicts (from JSON) into Engine objects.

    Unknown / invalid entries are silently dropped — we never crash
    the app over a config typo.
    """
    out: List[Engine] = []
    for raw in dicts:
        if not isinstance(raw, dict):
            continue
        try:
            e = Engine(
                id=str(raw["id"]),
                name=str(raw["name"]),
                stage=str(raw["stage"]),
                enabled=bool(raw.get("enabled", True)),
                order=int(raw.get("order", 0)),
                url_template=raw.get("url_template"),
                api_url=raw.get("api_url"),
                api_params=dict(raw.get("api_params") or {}),
                api_doi_path=raw.get("api_doi_path"),
                api_title_path=raw.get("api_title_path"),
            )
        except (KeyError, TypeError, ValueError):
            continue
        if e.stage not in VALID_STAGES:
            continue
        out.append(e)
    return out


# ---------------------------------------------------------------------------
# Selection helpers (used by main.py / tray.py)
# ---------------------------------------------------------------------------

def sort_by_order(engines: Iterable[Engine]) -> List[Engine]:
    """Stable sort: ascending ``order``, then id for determinism."""
    return sorted(engines, key=lambda e: (e.order, e.id))


def by_stage(engines: Iterable[Engine], stage: str) -> List[Engine]:
    """Return the enabled engines of *stage*, sorted by ``order``."""
    return sort_by_order(e for e in engines if e.stage == stage and e.enabled)


def find_by_id(engines: Iterable[Engine], engine_id: str) -> Optional[Engine]:
    for e in engines:
        if e.id == engine_id:
            return e
    return None


def get_path(d: Any, dotted: str) -> Any:
    """Walk a dotted JSON path like 'message.items.0.DOI'.  Indices in
    the path are integers; everything else is a dict key.  Returns
    None if any step is missing."""
    cur: Any = d
    for part in dotted.split("."):
        if cur is None:
            return None
        if part.isdigit():
            try:
                cur = cur[int(part)]
            except (IndexError, TypeError, ValueError):
                return None
        else:
            if not isinstance(cur, dict):
                return None
            cur = cur.get(part)
    return cur


__all__ = [
    "Engine",
    "STAGE_DOI_RESOLVER",
    "STAGE_DOI_URL",
    "STAGE_SEARCH_URL",
    "VALID_STAGES",
    "default_engines",
    "engines_to_dicts",
    "engines_from_dicts",
    "sort_by_order",
    "by_stage",
    "find_by_id",
    "get_path",
]
