# citationHop

> Select a citation anywhere. Press a hotkey. Get the paper.

`citationHop` is a tiny cross-platform menu-bar / system-tray app.
Select any chunk of text that looks like a literature reference
(APA / MLA / Chicago / BibTeX / RIS) and press the global hotkey
(default **Cmd + Shift + L** on macOS, **Ctrl + Shift + L** on
Windows / Linux). It will:

1. Extract a DOI directly from the text if one is present
2. Otherwise call the **Crossref API** to look it up
3. **Open the paper in your default browser** (via your chosen DOI service)
4. **Copy the bare DOI to your clipboard**
5. If no DOI can be found, open a **configurable search engine** for the
   title + first author + year

No GUI, no dock icon, no friction. A blue "C" pin sits in your menu bar
(macOS) or system tray (Windows / Linux).

---

## Highlights

* **Cross-platform.** macOS menu bar, Windows system tray, Linux
  status notifier — all share the same code path.
* **Customisable search engines.** Ships with 15 mainstream platforms
  (Crossref, doi.org, Google Scholar, Semantic Scholar, OpenAlex,
  arXiv, PubMed, DBLP, BASE, Connected Papers, Litmaps, ResearchGate,
  CORE, Dimensions, plus Sci-Hub opt-in). Enable / disable from the
  tray menu, or edit the JSON config to reorder, rename, or add your
  own URL template.
* **Three-stage pipeline.** DOI resolution → DOI URL → fallback
  search engine. Each stage is engine-driven and configurable.

---

## Installation

The project uses a `src/` layout, so you install it as a real Python
package (this makes `import citation_hop` work from anywhere, not just
the project root).

```bash
# 1. Clone & create a clean virtualenv
git clone https://github.com/ShiyangZheng/citation-hop
cd citation-hop
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\Activate.ps1

# 2. Install (editable + dev/test deps)
pip install -e ".[dev]"

# 3. Run
python -m citation_hop
```

**Windows shortcut:** just run `.\scripts\windows_setup.ps1` from
PowerShell — it does the same four steps and also runs the smoke test
and the full pytest suite.  See `TESTING.md` for the full Windows test
checklist.

A blue "C" icon appears in your menu bar / system tray. That's it.

### Platform-specific notes

| Platform | Tray location | What you may need |
|---|---|---|
| **macOS** | Menu bar (top right) | Grant **Accessibility** permission to your terminal / Python on first hotkey press (see below) |
| **Windows** | System tray (bottom right) | Nothing — `pystray` uses the native Win32 tray.  A toast notification will pop on each lookup. |
| **Linux** | Status notifier (panel) | `AppIndicator` is required on GNOME.  Install `gnome-shell-extension-appindicator` or `libappindicator3-1`. |

### macOS one-time permission setup

The hotkey needs two permissions; macOS will pop up the prompts the
first time you press the hotkey.

| Permission | Why | Where to grant |
|---|---|---|
| **Accessibility** | `pynput` simulates `Cmd+C` to read the current selection | System Settings → Privacy & Security → Accessibility |
| **Automation → System Events** | Fallback `AppleScript` keystroke for apps that ignore the synthetic `Cmd+C` | System Settings → Privacy & Security → Automation |

If the Accessibility prompt never shows up, open
**System Settings → Privacy & Security → Accessibility** manually and
toggle the entry for the Terminal / Python that ran the app.

### Windows one-time setup

No special permissions are needed. The icon appears in the system tray
on launch. If you want the app to start automatically on login, drop a
shortcut to `python -m citation_hop` (or your activated venv) into
`shell:startup`.

---

## Usage

1. Select a citation in any app (PDF reader, browser, Word, Slack, …).
2. Press the hotkey.
3. The paper opens in your browser; the DOI is in your clipboard.

To change the hotkey, click the tray icon → **Hotkey** → **Change hotkey…**
→ confirm to open the config file.  Edit the `hotkey` field with
`pynput` syntax (e.g. `cmd+alt+d`, `ctrl+shift+x`) and save.

---

## Supported input formats

| Format | How DOI is found |
|---|---|
| Plain text with embedded DOI | regex on `10.xxxx/...` |
| `https://doi.org/...` URL | regex on the URL |
| BibTeX (`@article{...}`) | `doi = {…}` field |
| RIS (`TY  - JOUR` …) | `DO  - …` field |
| APA / MLA / Chicago (no DOI) | Crossref bibliographic search, with title-similarity threshold (default 0.85) |
| Nothing matched | User-configured search engine (Google Scholar by default) |

---

## Customising search engines

The tray icon has a **Search engines** sub-menu. Each engine has a
checkbox you can click to enable or disable it on the fly. The change
is persisted to your config file immediately.

To reorder, rename, or add a *new* engine, open the config file (menu →
**Open config file**) and edit the `engines` list. The schema:

```jsonc
{
  "id":      "my_engine",                         // unique, lowercase
  "name":    "My Lab Search",                     // menu label
  "stage":   "doi_url" | "doi_resolver" | "search_url",
  "enabled": true,
  "order":   0,                                   // ascending = first
  "url_template": "https://my-lab.example/?doi={doi}&q={query}"
}
```

Templates support these placeholders, all URL-encoded automatically:

* `{doi}`     — the resolved DOI (e.g. `10.1038/nature12373`)
* `{query}`   — the full selected text
* `{title}`, `{author}`, `{year}` — parsed fields
* `{mailto}`  — your Crossref polite-pool email

### Adding a custom search engine

Example: add an institutional link resolver.

```json
{
  "id": "my_university",
  "name": "My University Library",
  "stage": "doi_url",
  "enabled": true,
  "order": 1,
  "url_template": "https://library.myuni.edu/doi/{doi}"
}
```

Save the file, then either restart the app or toggle any engine in
the menu to trigger a config refresh.

---

## Configuration

The config file lives at the OS-appropriate per-user location:

| Platform | Path |
|---|---|
| macOS | `~/Library/Application Support/citationHop/config.json` |
| Windows | `%APPDATA%\citationHop\config.json` |
| Linux | `~/.config/citationHop/config.json` |

Use **Open config file** in the tray menu to jump there.

```json
{
  "hotkey": "cmd+shift+l",
  "mailto": "syz@shiyangzheng.top",
  "similarity_threshold": 0.85,
  "engines": [
    /* see "Customising search engines" above */
  ]
}
```

* `hotkey` — `pynput` GlobalHotKeys syntax.  `cmd+shift+l`, `ctrl+alt+d`, …
* `mailto` — Crossref polite-pool email.  Change to your own address
  if you fork.
* `similarity_threshold` — `0.0` (loose) … `1.0` (exact match) for
  the Crossref title-similarity filter.  Lower = more results, more
  false positives.

---

## Running the tests

```bash
# Headless smoke test (no GUI required, runs anywhere)
python scripts/smoke_test.py

# Full pytest suite (58 tests, ~0.3 s)
python -m pytest -v
```

`smoke_test.py` exercises every cross-platform code path that doesn't
need a real display.  Run it on every OS before launching the GUI to
catch import / config / engine-rendering regressions.  The full pytest
suite adds tests for the citation parser / detector / extractor and the
Crossref resolver.

---

## Limitations

* **Sci-Hub is opt-in for a reason.**  It is disabled by default and
  carries an explicit warning in the menu.  Enable only if you have
  the right to access the papers in your jurisdiction.
* **No offline mode.** Crossref lookup requires internet.  The text
  never leaves your machine apart from the Crossref request itself.
* **Some apps block synthetic `Cmd+C` / `Ctrl+C`.**  Sandboxed apps
  (e.g. some macOS PDF readers) won't respond.  On macOS, the
  AppleScript fallback covers most cases; if that also fails, copy
  the citation manually and it will land on the clipboard — the
  hotkey will pick it up.

---

## License

MIT.
