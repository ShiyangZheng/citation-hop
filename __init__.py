"""citationHop — citation-to-DOI clipboard utility.

A cross-platform menu-bar / system-tray app.  Press a global hotkey
while any text is selected and citationHop will find the DOI and
open the paper in your default browser.

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

__version__ = "1.1.0"
__all__ = ["__version__"]
