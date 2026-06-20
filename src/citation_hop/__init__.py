"""citationHop — citation-to-DOI clipboard utility.

A cross-platform menu-bar / system-tray app.  Press a global hotkey
while any text is selected and citationHop will find the DOI and
open the paper in your default browser.

v1.3 highlights — Zotero integration overhaul
---------------------------------------------
* **Zotero ``zotero://select`` deep-link** — when the resolved DOI
  exists in your local Zotero library, citationHop now opens the
  item directly inside Zotero (``zotero://select/library/items/<KEY>``).
  Zotero itself handles its own URL scheme, so the right item is
  brought to the front regardless of which PDF was previously open.
  This **completely bypasses** Zotero's browser-connector
  interception chain — the browser is no longer involved at all.
* **Publisher-direct URL fallback** — when the DOI is *not* in your
  Zotero library, we follow ``doi.org/{doi}`` server-side and hand
  the publisher URL (``tandfonline.com/doi/full/...``, etc.) to
  ``webbrowser.open``.  Many Zotero connector configurations only
  intercept ``doi.org`` URLs (not publisher domains), so this usually
  works.  ``chooser.crossref.org`` and other intermediary landing
  pages are filtered out automatically.
* **Multi-signal Crossref scoring** — when the user selects a
  reference that doesn't carry an explicit DOI, the Crossref
  candidate-scoring function now weighs **title**, **author**,
  **container-title**, **page range**, and **year** instead of just
  title similarity.  This correctly disambiguates cases like
  Sinclair (vol. 1) vs. Sinclair (vol. 2), where two chapters share
  the exact same title and author.
* **Cleaner Zotero annotation handling** — Zotero's PDF reader
  appends noise like ``2 📊. https://doi.org/...`` to the clipboard
  on copy.  ``clean_zotero_noise()`` strips this before feeding the
  text to Scholar, so the search query stays focused on the actual
  citation.

v1.2 highlights
---------------
* **In-text citation short-circuit** — selecting ``(Smith, 2020)`` or
  ``Smith et al. (2020)`` now bypasses Crossref and routes the parsed
  author + year straight to the search engines.  Faster and more
  accurate than a 15-character bibliographic query against Crossref.
* **APA title extraction** — plain-text references like
  ``Smith, J. (2020). The title. Journal, 12(3), 1-10.`` now have
  their title auto-extracted, so the Scholar fallback URL is
  ``?q=TITLE+AUTHOR+YEAR`` (unique per reference) instead of
  ``?q=+AUTHOR+YEAR`` (which often maps to the dominant paper on
  the topic).
* **Clipboard sentinel** — Cmd+C now uses a UUID marker to detect a
  silent no-op (no selection) instead of reading whatever stale
  content was in the macOS pasteboard.  Fixes the "always jumps to
  the same paper" class of bugs.
* **Routing modes** (``route_mode`` in config) — pick between
  ``auto`` (in-text → search, full ref → DOI), ``search_always``
  (everything → search engine, avoids Zotero's doi.org interceptor),
  and ``doi_always`` (everything → DOI URL).  Switch from the tray
  menu under "Routing".

v1.1 highlights
----------------
* **Cross-platform** — macOS (menu bar), Windows (system tray), and
  Linux (status notifier) all share the same code path.
* **Customisable search engines** — the default list covers Crossref
  (DOI lookup), doi.org, Google Scholar, Semantic Scholar, OpenAlex,
  arXiv, PubMed, DBLP, BASE, Connected Papers, Litmaps, ResearchGate,
  CORE, Dimensions, plus Sci-Hub (opt-in).  Enable / disable from the
  menu, or edit the JSON config to reorder, rename, or add your own.
"""

__version__ = "1.3.0"
__all__ = ["__version__"]
