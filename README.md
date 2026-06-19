# citationHop

> **A fully cross-platform menu-bar / system-tray app for opening academic papers.**
> **Works natively on macOS, Windows, and Linux — one codebase, three native UIs.**

| ![macOS](https://img.shields.io/badge/macOS-000000?style=flat-square&logo=apple&logoColor=white) | ![Windows](https://img.shields.io/badge/Windows-0078D6?style=flat-square&logo=windows&logoColor=white) | ![Linux](https://img.shields.io/badge/Linux-FCC624?style=flat-square&logo=linux&logoColor=black) |
| :---: | :---: | :---: |
| Menu bar (top right) | System tray (bottom right) | Status notifier (panel) |
| ✅ Native (rumps) | ✅ Native (Win32) | ✅ Native (AppIndicator) |

![CI](https://img.shields.io/badge/CI-9%20OS%E2%80%93Python%20combos%20%E2%9C%93-success?style=flat-square)
![Python](https://img.shields.io/badge/python-3.10%20%E2%80%93%203.13-blue?style=flat-square&logo=python&logoColor=white)
![License](https://img.shields.io/badge/license-MIT-green?style=flat-square)

> Select a citation anywhere. Press a hotkey. Get the paper.

`citationHop` is a tiny **fully cross-platform** menu-bar / system-tray app
that runs **natively on macOS, Windows, and Linux** from a single Python
codebase. Select any chunk of text that looks like a literature reference
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

## 🌍 Cross-platform — truly, not "cross-platform on paper"

`citationHop` was built from day one as a **single Python codebase that
compiles to a native experience on every desktop OS**. There is no
Electron wrapper, no web view, no per-OS fork — just one
`tray.py` that talks to whichever native tray backend your OS provides.

| Layer | macOS | Windows | Linux |
|---|---|---|---|
| **Tray icon** | Menu bar (rumps) | System tray (pystray + Win32) | Status notifier (pystray + AppIndicator) |
| **Global hotkey** | pynput HID event tap | pynput Win32 hook | pynput X11 hook |
| **Selection capture** | AppleScript (`osascript`) | `Ctrl+C` via pyperclip | `Ctrl+C` via pyperclip |
| **Notification** | Native Notification Center (rumps) | Native Windows toast (plyer) | libnotify (plyer) |
| **Config dir** | `~/Library/Application Support/citationHop/` | `%APPDATA%\citationHop\` | `~/.config/citationHop/` |

**CI matrix — all 9 jobs green on every commit:**
`ubuntu-latest` × 3.11/3.12/3.13 · `macos-latest` × 3.11/3.12/3.13 ·
`windows-latest` × 3.11/3.12/3.13. See
`.github/workflows/test.yml`.

---

## Highlights

* **🌍 Fully cross-platform.** macOS menu bar, Windows system tray,
  Linux status notifier — one codebase, three native UIs. **CI tested
  on 9 OS × Python combinations** (3 OSes × Python 3.11/3.12/3.13).
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

> **Important — Accessibility permission is required.** `pynput`
> uses the macOS HID event tap to receive your global hotkey, and
> that tap only fires if your terminal / launcher app has been
> granted Accessibility.  Without it, the app starts and the icon
> appears, but the hotkey does nothing.  v1.1.1 adds a one-time
> startup check that pops a notification and opens the right
> Settings pane if it can't get the permission.

The hotkey needs two permissions; macOS will pop up the prompts the
first time you press the hotkey.

| Permission | Why | Where to grant |
|---|---|---|
| **Accessibility** | `pynput` uses the macOS HID event tap to receive the global hotkey.  We also use AppleScript (`osascript`) — **not** `pynput` `Controller` — to send the `Cmd+C` that captures your selection, because pynput's `Controller` re-enters the HID event tap and crashes with `SIGILL` (`zsh: illegal hardware instruction`) on macOS 15 / Apple Silicon. | System Settings → Privacy & Security → Accessibility |
| **Automation → System Events** | The AppleScript `keystroke "c" using command down` for the selection capture.  This is now the **primary** macOS path, not a fallback. | System Settings → Privacy & Security → Automation |

If the Accessibility prompt never shows up, open
**System Settings → Privacy & Security → Accessibility** manually and
toggle the entry for the Terminal / Python that ran the app.

#### Troubleshooting: `zsh: illegal hardware instruction` on hotkey press

> **Symptom:** the app launches fine, the icon appears, but pressing
> the hotkey kills the process with `zsh: illegal hardware
> instruction` (SIGILL).
>
> **Root cause:** pynput's `Controller` posts a synthetic CGEvent
> via `CGEventPost`.  If that post happens from inside pynput's
> own CFRunLoop (which is exactly the case inside the hotkey
> handler), macOS 15 / Apple Silicon re-enters the HID event tap
> and the process dies with SIGILL.
>
> **Mitigation (already in v1.1.1):**
> 1.  The hotkey handler now dispatches its work to a dedicated
>     worker thread, so pynput's CFRunLoop returns to its event
>     loop immediately and never re-enters from a blocking call.
> 2.  `simulate_copy()` on macOS uses AppleScript (`osascript -e
>     'tell application "System Events" to keystroke "c" using
>     command down'`) instead of pynput's `Controller`.  AppleScript
>     is delivered through WindowServer and does not re-enter.
> 3.  A SIGILL / SIGSEGV / SIGBUS signal handler logs a clear,
>     actionable hint to stderr if a native crash still slips
>     through.

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
