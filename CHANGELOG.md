# Changelog

All notable changes to citationHop are documented here. The format is
loosely based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.3.0] — 2026-06-20 — Zotero integration overhaul

### Added

- **Zotero `zotero://select` deep-link** — when the resolved DOI exists
  in your local Zotero library, citationHop now opens the item directly
  inside Zotero (`zotero://select/library/items/<KEY>`). Zotero itself
  handles its own URL scheme, so the right item is brought to the front
  regardless of which PDF was previously open. **The browser is no
  longer involved at all** for items already in your library, which
  means Zotero's connector can't intercept anything. This is the
  primary fix for the long-standing *"every selection opens whatever
  PDF is currently in Zotero"* bug.
  - `lookup_zotero_item_by_doi(doi)` in `platform_utils.py` reads the
    Zotero SQLite library (with a copy-then-read strategy that doesn't
    block on Zotero's writer lock).
  - `_resolve_zotero_db()` honours the `extensions.zotero.dataDir`
    preference in `prefs.js` and falls back to `~/Zotero/zotero.sqlite`.
- **Publisher-direct URL fallback** — when the DOI is *not* in your
  Zotero library, the lookup pipeline now follows `doi.org/{doi}`
  server-side and hands the publisher URL (`tandfonline.com/doi/full/...`,
  `academic.oup.com/applij/...`, `benjamins.com/catalog/...`, etc.) to
  `webbrowser.open`. Many Zotero connector configurations only
  intercept `doi.org` URLs (not publisher domains), so this usually
  works.
  - `resolve_publisher_url(doi)` performs the server-side redirect
    using `requests` (HEAD then GET-with-stream fallback). It does
    **not** require a 2xx response — many publishers return 403 to
    bot-like HTTP requests but load fine in a real browser; we only
    care about whether the chain moved us off `doi.org`.
  - `chooser.crossref.org`, `data.crossref.org`, `api.crossref.org`,
    `dx.doi.org`, `doi.crossref.org`, and the other Crossref DOI
    intermediary domains are rejected, falling through to the
    Scholar search fallback.
- **Multi-signal Crossref scoring** — the candidate ranking in
  `resolver.py` now weighs **title (0.55)**, **author (0.20)**,
  **container-title (0.10)**, **page range (0.10)**, and
  **year (0.05)**. Signal weights are dynamically redistributed when
  the query doesn't carry the corresponding field. This correctly
  disambiguates cases like Sinclair (vol. 1) vs. Sinclair (vol. 2),
  where two chapters share the exact same title and author.
  - `_extract_query_signals()` parses surnames, year, container words,
    volume / issue, and page range from the query in four formats:
    APA (`(YYYY). TITLE. CONTAINER, vol...`), Chicago (`In <Container>`),
    edited-book (`In X, edited by Y`), and journal (`X, 10(2)`).
  - `container-title` matching is word-level (the Crossref field is
    stored as a single string, so we split it before computing
    Jaccard similarity).
  - Tie-breaker: when two candidates score equally, the pipeline
    calls Crossref's `/works/{doi}` for the full record and uses
    extra fields (volume, issue, ISBN) to disambiguate.
  - The threshold dropped from `0.85` to `0.60` because the
    multi-signal score is on a different scale (full marks are still
    `1.0`, but most real matches land in the `0.65`–`0.95` band).

### Changed

- **Zotero bypass now has five fallback layers**, in priority order:
  1. `zotero://select/library/items/<KEY>` (Zotero opens its own item).
  2. Publisher-direct URL resolved from `doi.org` redirect.
  3. Google Scholar search by DOI.
  4. Google Scholar search by cleaned citation text.
  5. (Nothing — `bypass_reason` is only set when Zotero is installed
     and we're in `auto` mode.)
- The tray notification now distinguishes between "Opening publisher
  page (Zotero bypassed)" and "Zotero detected — Scholar search"
  depending on which fallback layer fired, so the user can tell at a
  glance whether the lookup opened the exact paper or fell back to
  a search results page.

### Fixed

- **"Same paper every time" with Zotero + Safari connector** — the
  Safari Zotero Connector intercepts both `doi.org` URLs *and* some
  publisher URLs (`tandfonline.com` being a notorious example).
  v1.3's first fallback layer opens the paper directly in Zotero,
  bypassing the browser entirely.
- **Wrong volume when chapter titles collide** — Sinclair's
  *Collocation: A Progress Report* appears in both `z.lt1.66sin`
  (vol. 1) and `z.lt2.68sin` (vol. 2) on John Benjamins. v1.2 picked
  vol. 1 every time because the title-similarity scores were equal
  and Crossref's first result won. v1.3 scores volume + page-range
  signals, so vol. 2 is correctly preferred when the reference says
  *"vol. 2"* or *"pp. 319-332"*.
- **Zotero PDF-reader annotation noise** — copied text from Zotero's
  PDF reader often carries trailing markers like
  `2 📊. https://doi.org/10.1080/...` which used to corrupt Scholar
  search queries. `clean_zotero_noise()` strips these.

## [1.2.5] — 2026-06-20

### Changed

- **Chooser URL filtering** — `chooser.crossref.org` and other
  Crossref intermediary pages were occasionally being handed back by
  `resolve_publisher_url()` as if they were publisher URLs. v1.2.5
  filters them out so the fallback path is Scholar search instead.

## [1.2.0] — 2026-06-19

### Added

- In-text citation short-circuit (routed to Scholar with author+year).
- APA title extraction (so the Scholar fallback URL is unique per ref).
- Clipboard sentinel (`__citation_hop_sentinel_<UUID>__`) to detect
  silent Cmd+C no-ops.
- Routing modes (`auto`, `search_always`, `doi_always`).
- Cross-platform refactor: `pystray` instead of `rumps`.
- 15 configurable search engines.

## [1.1.1] — 2026-06-15

### Fixed

- macOS SIGILL on hotkey press (CGEventTap re-entrancy) by dispatching
  the hotkey work to a dedicated worker thread and using AppleScript
  instead of pynput's `Controller` for the Cmd+C simulation.

## [1.1.0] — 2026-06-10

### Added

- Cross-platform tray (macOS menu bar / Windows system tray / Linux
  status notifier).
- Customisable search engines.

## [1.0.0] — 2026-05-30

### Added

- Initial release. macOS-only, `rumps` menu-bar app.
- Global hotkey (`Cmd+Shift+L`).
- DOI extraction from plain text / BibTeX / RIS.
- Crossref bibliographic search.
- `doi.org` URL opening.
- Google Scholar fallback.

[1.3.0]: https://github.com/ShiyangZheng/citation-hop/compare/v1.2.5...v1.3.0
[1.2.5]: https://github.com/ShiyangZheng/citation-hop/compare/v1.2.0...v1.2.5
[1.2.0]: https://github.com/ShiyangZheng/citation-hop/compare/v1.1.1...v1.2.0
[1.1.1]: https://github.com/ShiyangZheng/citation-hop/compare/v1.1.0...v1.1.1
[1.1.0]: https://github.com/ShiyangZheng/citation-hop/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/ShiyangZheng/citation-hop/releases/tag/v1.0.0